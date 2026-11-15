"""Warehouse catalog: one clean record per warehouse-type property, for the map, the ranker and the agents.

Joins (all on integer ids):
    commercial-property          specs, location, photos
    commercial-listing-summary   availability: for lease / for sale, asking rent or price   (propertyId)
    location-occupancy           current tenants (toDate null)                               (propertyId)
    company-detail               tenant names and industries                                 (tenantLocationId -> id)
    commercial-sale-comp         last sale                                                   (entities[].propertyId)
    build/spike/scan_coverage    Matterport scan coverage class per model

Every field keeps None for unknown (never 0 for "unknown"). Writes build/catalog/warehouses.json.

    python -m proinspect.catalog
"""
import json
from collections import defaultdict

from proinspect.config import BUILD, DATA
from proinspect.warehouses import dec, scans_by_property, warehouses

ACRE_SQFT = 43_560


def _lines(name):
    with open(DATA / f"{name}.jsonl") as f:
        for line in f:
            yield json.loads(line)


def _lot_acres(r):
    v, unit = r.get("lotSize"), dec("MeasurementUnit", r.get("lotSizeMeasurementType"))
    if not v:
        return None
    u = str(unit).lower()
    if "acre" in u:
        return round(v, 2)
    if "squaremeter" in u or "sqm" in u:
        return round(v * 10.7639 / ACRE_SQFT, 2)
    if "squarefoot" in u or "squarefeet" in u or "sf" in u:
        return round(v / ACRE_SQFT, 2)
    return None


def _year_built(r):
    ys = [e.get("year") for e in r.get("constructionEvents") or []
          if dec("ConstructionEventType", e.get("eventType")) == "ConstructionComplete" and e.get("year")]
    return min(ys) if ys else None


def _year_renovated(r):
    ys = [e.get("year") for e in r.get("constructionEvents") or []
          if dec("ConstructionEventType", e.get("eventType")) == "RenovationFinished" and e.get("year")]
    return max(ys) if ys else None


def _coverage():
    """{model_id: {class, floor_sqft, height_ft}} from the Phase 0 spike."""
    p = BUILD / "spike" / "scan_coverage.json"
    if not p.exists():
        return {}
    out = {}
    for row in json.loads(p.read_text()):
        if not row.get("ok"):
            out[row["model_id"]] = {"class": "unavailable"}
            continue
        fs, h = row["floor_sqft"], row["height_ft"] or 0
        cls = "warehouse_floor" if fs >= 5000 and h >= 16 else "partial" if fs >= 2000 and h >= 14 else "office_or_small"
        out[row["model_id"]] = {"class": cls, "floor_sqft": fs, "height_ft": row["height_ft"],
                                "panos": row.get("panos"), "name": row.get("name")}
    return out


def _listings(ids):
    out = defaultdict(list)
    for r in _lines("commercial-listing-summary"):
        pid = r.get("propertyId")
        if pid not in ids:
            continue
        lt = dec("ListingType", r.get("listingType"))
        item = {"listing_id": r["id"], "type": lt, "status": dec("ListingStatus", r.get("listingStatus")),
                "broker": r.get("brokerageCompanyName")}
        fl, fs = r.get("forLeaseSummary"), r.get("forSaleSummary")
        if fl:
            basis = dec("RateBasis", fl.get("askingRateBasis"))
            lo, hi = fl.get("askingRateLow") or None, fl.get("askingRateHigh") or None
            k = 12 if str(basis).lower().startswith("month") else 1
            item.update({"kind": "lease", "available_sqft": fl.get("totalAvailableSquareFeet"),
                         "min_divisible_sqft": fl.get("minDivisibleSquareFeet"),
                         "max_contiguous_sqft": fl.get("maxContiguousSquareFeet"),
                         "rent_low_psf_yr": round(lo * k, 2) if lo else None,
                         "rent_high_psf_yr": round(hi * k, 2) if hi else None,
                         "rent_unit": dec("MeasurementUnit2", fl.get("askingRateMeasurementType")),
                         "service_type": dec("ServiceType", fl.get("serviceType"))})
        elif fs:
            item.update({"kind": "sale", "sale_status": dec("SaleStatus", fs.get("saleStatus")),
                         "price_low": fs.get("askingPriceLow") or None, "price_high": fs.get("askingPriceHigh") or None,
                         "available_sqft": fs.get("totalAvailableSquareFeet"),
                         "cap_rate": fs.get("capitalizationRate")})
        else:
            item["kind"] = "other"
        out[pid].append(item)
    return out


def _tenants(ids):
    occ = defaultdict(list)
    for r in _lines("location-occupancy"):
        if r["propertyId"] in ids and not r.get("toDate"):
            occ[r["propertyId"]].append(r)
    need = {o["tenantLocationId"] for v in occ.values() for o in v if o.get("tenantLocationId")}
    names = {}
    for c in _lines("company-detail"):
        if c["id"] in need:
            prof = c.get("profile") or {}
            names[c["id"]] = {"name": prof.get("companyName") or c.get("name"),
                              "industry": dec("IndustryType", prof.get("industryType"))}
    out = {}
    for pid, v in occ.items():
        known = [names[o["tenantLocationId"]] for o in v if o.get("tenantLocationId") in names]
        sqft = sum(s.get("squareFeetOccupied") or 0 for o in v for s in o.get("spaces") or [])
        out[pid] = {"current_count": len(v), "known": known[:12], "occupied_sqft": round(sqft) or None}
    return out


def _last_sale(ids):
    best = {}
    for r in _lines("commercial-sale-comp"):
        for e in r.get("entities") or []:
            pid = e.get("propertyId")
            if pid in ids and r.get("soldDate") and (pid not in best or r["soldDate"] > best[pid]["date"]):
                best[pid] = {"comp_id": r["id"], "date": r["soldDate"][:10], "price": r.get("soldForPrice"),
                             "price_status": dec("SalePriceStatus", r.get("salePriceStatus")),
                             "portfolio": len(r.get("entities") or []) > 1}
    return best


def build():
    W, S, cov = warehouses(), scans_by_property(), _coverage()
    ids = set(W)
    listings, tenants, sales = _listings(ids), _tenants(ids), _last_sale(ids)
    out = []
    for pid, r in W.items():
        a = r.get("primaryAddress") or {}
        ind = r.get("propertyIndustrial") or r.get("propertyFlex") or {}
        st = ind.get("structural") or {}
        di, tw, cr, el = (ind.get("driveInBays") or {}), (ind.get("truckWells") or {}), (ind.get("cranes") or {}), \
            (ind.get("electric") or {})
        clear = st.get("ceilingHeightFeet")
        if clear is not None and st.get("ceilingHeightInches"):
            clear = round(clear + st["ceilingHeightInches"] / 12, 1)
        scans = [{"model_id": m, **cov.get(m, {"class": "unknown"})} for m in S.get(pid, [])]
        # small buildings: a scan covering a good share of the building IS its warehouse floor, even if under 5,000 SF
        size = r.get("rentableBuildingAreaSquareFeet") or r.get("grossBuildingAreaSquareFeet")
        for sc in scans:
            fs, h = sc.get("floor_sqft") or 0, sc.get("height_ft") or 0
            if sc.get("class") in ("partial", "office_or_small") and size and fs >= 1500 and h >= 16 and fs / size >= 0.25:
                sc["class"] = "warehouse_floor"
                sc["why"] = f"covers {100 * fs / size:.0f}% of a {size:,.0f} SF building"
        rank = {"warehouse_floor": 0, "partial": 1, "office_or_small": 2, "unknown": 3, "unavailable": 4}
        scans.sort(key=lambda s: (rank.get(s["class"], 9), -(s.get("floor_sqft") or 0)))
        lst = listings.get(pid, [])
        active = [x for x in lst if x["status"] == "Active"]
        parking = sum(p.get("totalSpaces") or 0 for p in r.get("parking") or []) or None
        out.append({
            "id": pid,
            "name": r.get("buildingName"),
            "address": " ".join(str(a[k]) for k in ("streetNumber", "streetName", "streetType") if a.get(k)),
            "city": a.get("city"), "state": a.get("state"), "zip": (a.get("postcode") or "")[:5] or None,
            "market": {14: "Houston", 20: "Los Angeles"}.get(a.get("researchMarketId")),
            "submarket_id": r.get("primarySubmarketId"),
            "lat": a.get("latitude"), "lon": a.get("longitude"),
            "subtype": dec("PropertySubtype", r.get("propertySubtype")),
            "status": dec("ConstructionStatus", r.get("constructionStatus")),
            "building_class": dec("BuildingClass", r.get("buildingClass")),
            "rating": r.get("buildingRating") or None,
            "rba_sqft": r.get("rentableBuildingAreaSquareFeet"),
            "gba_sqft": r.get("grossBuildingAreaSquareFeet"),
            "lot_acres": _lot_acres(r),
            "stories": r.get("numStoriesAboveGrade"),
            "year_built": _year_built(r), "year_renovated": _year_renovated(r),
            "clear_height_ft": clear,
            "docks_external": ind.get("numExternalLoadingDocks"),
            "docks_internal": ind.get("numInternalLoadingDocks"),
            "has_docks": ind.get("hasLoadingDocks"),
            "levelers": (ind.get("numExternalLevelers") or 0) + (ind.get("numInternalLevelers") or 0) or None,
            "cross_dock": ind.get("hasCrossDocks"),
            "drive_ins": di.get("numDriveInBays"), "drive_in_w_ft": di.get("numFeetWide"),
            "drive_in_h_ft": di.get("numFeetHigh"),
            "truck_wells": tw.get("total"), "truck_court_ft": tw.get("truckCourtLengthInFeet"),
            "rail": bool(ind.get("railAccess")) or None,
            "cranes": cr.get("numCranes") if ind.get("hasCranes") else (False if ind.get("hasCranes") is False else None),
            "crane_tons": cr.get("maxCraneWeightTons") or cr.get("minCraneWeightTons"),
            "power_amps": el.get("ampsHigh") or el.get("ampsLow"),
            "power_volts": el.get("voltsHigh") or el.get("voltsLow"),
            "power_phases": el.get("numElectricCurrentPhases"),
            "heavy_power": el.get("hasHeavyPower"),
            "sprinklers": dec("SprinklerType", r.get("sprinklerType")) if r.get("sprinklerType") else
            (r.get("hasSprinklers") if r.get("hasSprinklers") is not None else None),
            "column_spacing_ft": [st.get("columnWidthFeet"), st.get("columnDepthFeet")]
            if st.get("columnWidthFeet") else None,
            "floor_thickness_in": st.get("floorThicknessInches"),
            "parking_spaces": parking, "parking_ratio": r.get("parkingRatio"),
            "owner_occupied": ind.get("isOwnerOccupied"),
            "tenancy": dec("TenancyType", ind.get("tenancyType")) if ind.get("tenancyType") else None,
            "zoning": r.get("zoningDescription"),
            "photo": (r.get("primaryPhoto") or {}).get("attachmentUri"),
            "scans": scans,
            "inspectable": any(s["class"] == "warehouse_floor" for s in scans),
            "availability": "available" if active else ("off_market_listing" if lst else "not_listed"),
            "listings": lst,
            "tenants": tenants.get(pid, {"current_count": 0, "known": [], "occupied_sqft": None}),
            "last_sale": sales.get(pid),
        })
    out.sort(key=lambda x: x["id"])
    d = BUILD / "catalog"
    d.mkdir(parents=True, exist_ok=True)
    (d / "warehouses.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    cat = build()
    n = len(cat)

    def filled(k):
        return sum(1 for c in cat if c.get(k) not in (None, [], False))
    print(f"{n} warehouses -> build/catalog/warehouses.json")
    print("inspectable (warehouse-floor scan):", sum(c["inspectable"] for c in cat))
    print("availability:", {k: sum(c["availability"] == k for c in cat) for k in
                            ("available", "off_market_listing", "not_listed")})
    for k in ("clear_height_ft", "docks_external", "drive_ins", "power_amps", "lot_acres", "year_built",
              "sprinklers", "column_spacing_ft", "truck_court_ft", "parking_spaces", "last_sale"):
        print(f"  {k:<18} {filled(k)}/{n}")
    ex = next(c for c in cat if c["id"] == 238885)
    print(json.dumps({k: v for k, v in ex.items() if k not in ("listings", "scans")}, default=str)[:900])
    print("listings:", json.dumps(ex["listings"], default=str)[:500])
