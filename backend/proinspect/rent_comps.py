"""Rent for a warehouse, in honest tiers (never invented):

    1. asking   CoStar asking rent on the building's own listing                       (recorded)
    2. own      the building's own signed lease deals since RECENT_YEAR                  (recorded, contract rent)
    3. comps    signed industrial/flex lease deals in the same CoStar submarket           (estimate: median + IQR band)
    4. market   the same, market-wide (Houston / Los Angeles) when the submarket is thin  (estimate, weaker: says so)
    5. unknown  nothing usable: the cost model shows "rent unknown", never 0

Contract rents come from location-occupancy.leaseDeal.rateActual (rateAmountType 0 = per SF), normalised to $/SF/yr
(rateBasis 1 = monthly x12). Service types differ (NNN vs industrial gross), so each value carries its service-type
mix; asking, contract and effective rents are never mixed in one number (kit rule).

build() streams the two big JSONL files once and writes build/economics/rent_comps.json; rent_for() reads it.
"""
from __future__ import annotations

import json
import statistics as st
from collections import Counter, defaultdict
from functools import lru_cache

from proinspect.config import BUILD

OUT = BUILD / "economics" / "rent_comps.json"
DATA = BUILD.parent / "data"
RECENT_YEAR = 2020
MIN_COMPS = 3
INDUSTRIAL_TYPES = (2, 7)                 # PropertyType: Industrial, Flex
SERVICE = {0: "unknown", 1: "plus cleaning", 2: "plus electric & cleaning", 4: "plus electric", 5: "utilities & char",
           6: "plus utilities", 7: "full service", 8: "industrial gross", 9: "modified gross", 13: "triple net (NNN)"}


def _service_names():
    try:
        e = json.loads((BUILD.parent / "schemas" / "enums.json").read_text()).get("ServiceType") or {}
        return {int(k): str(v) for k, v in e.items()}
    except Exception:                                                # noqa: BLE001
        return {}


def build():
    """Stream the kit once: industrial/flex properties -> their recent signed lease deals -> per property / submarket."""
    names = {**SERVICE, **_service_names()}
    ptype, psub, pmkt = {}, {}, {}
    with open(DATA / "commercial-property.jsonl") as f:
        for line in f:
            r = json.loads(line)
            ptype[r["id"]] = r.get("propertyType")
            psub[r["id"]] = r.get("primarySubmarketId")
            pmkt[r["id"]] = {14: "Houston", 20: "Los Angeles"}.get((r.get("primaryAddress") or {}).get("researchMarketId"))
    ind = {p for p, t in ptype.items() if t in INDUSTRIAL_TYPES}
    deals = defaultdict(list)
    with open(DATA / "location-occupancy.jsonl") as f:
        for line in f:
            r = json.loads(line)
            ld = r.get("leaseDeal")
            pid = r.get("propertyId")
            if not ld or pid not in ind or ld.get("rateAmountType") != 0 or not (ld.get("rateActual") or 0) > 0:
                continue
            if (ld.get("monetaryUnit") or 0) not in (0, 1):         # USD only (kit: normalise currency first)
                continue
            y = int((ld.get("commencementDate") or "0")[:4] or 0)
            if y < RECENT_YEAR:
                continue
            rate = float(ld["rateActual"]) * (12 if ld.get("rateBasis") == 1 else 1)
            if not 1.0 <= rate <= 80.0:                             # $/SF/yr outside this is a data-entry outlier
                continue
            sf = sum((s.get("squareFeetOccupied") or 0) for s in r.get("spaces") or [])
            deals[pid].append({"rate": round(rate, 2), "year": y, "sf": round(sf) or None,
                               "service": names.get(ld.get("serviceType"), "unknown")})
    by_sub, by_mkt = defaultdict(list), defaultdict(list)
    for pid, ds in deals.items():
        for d in ds:
            by_sub[psub.get(pid)].append({**d, "property_id": pid})
            if pmkt.get(pid):
                by_mkt[pmkt[pid]].append({**d, "property_id": pid})
    out = {"version": 1, "recent_year": RECENT_YEAR, "min_comps": MIN_COMPS,
           "by_property": {str(k): v for k, v in deals.items()},
           "by_submarket": {str(k): _summary(v) for k, v in by_sub.items() if k is not None},
           "by_market": {k: _summary(v) for k, v in by_mkt.items()}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out))
    _load.cache_clear()
    return out


def _summary(ds):
    rates = sorted(d["rate"] for d in ds)
    q = st.quantiles(rates, n=4) if len(rates) >= 4 else [rates[0], st.median(rates), rates[-1]]
    return {"n": len(rates), "median": round(st.median(rates), 2), "p25": round(q[0], 2), "p75": round(q[-1], 2),
            "years": [min(d["year"] for d in ds), max(d["year"] for d in ds)],
            "service_mix": dict(Counter(d["service"] for d in ds).most_common()),
            "properties": len({d["property_id"] for d in ds})}


@lru_cache(maxsize=1)
def _load():
    if not OUT.exists():
        return build()
    return json.loads(OUT.read_text())


def _asking(w):
    vals, svc = [], set()
    for ln in w.get("listings") or []:
        for k in ("rent_low_psf_yr", "rent_high_psf_yr"):
            if ln.get(k):
                vals.append(float(ln[k]))
        if ln.get("service_type"):
            svc.add(str(ln["service_type"]))
    return vals, svc


def rent_for(w):
    """The best available rent for catalog warehouse `w` ({..., listings, submarket_id}), with its tier and basis."""
    vals, svc = _asking(w)
    if vals:
        lo, hi = min(vals), max(vals)
        return {"tier": "asking", "value": round(sum(vals) / len(vals), 2), "low": lo, "high": hi, "unit": "$/SF/yr",
                "source": "recorded", "kind": "asking rent",
                "basis": "CoStar asking rent on this building's listing" + (f" ({', '.join(sorted(svc))})" if svc else "")}
    data = _load()
    own = data["by_property"].get(str(w["id"])) or []
    if own:
        s = _summary([{**d, "property_id": w["id"]} for d in own])
        return {"tier": "own", "value": s["median"], "low": s["p25"], "high": s["p75"], "unit": "$/SF/yr",
                "source": "recorded", "kind": "contract rent", "n": s["n"], "years": s["years"],
                "service_mix": s["service_mix"],
                "basis": f"{s['n']} signed lease{'s' if s['n'] > 1 else ''} in this building, {s['years'][0]}-{s['years'][1]} "
                         f"(CoStar lease comps, contract rent)"}
    sub = data["by_submarket"].get(str(w.get("submarket_id")))
    if sub and sub["n"] >= data.get("min_comps", MIN_COMPS):
        return {"tier": "comps", "value": sub["median"], "low": sub["p25"], "high": sub["p75"], "unit": "$/SF/yr",
                "source": "estimate", "kind": "contract rent (submarket comps)", "n": sub["n"], "years": sub["years"],
                "service_mix": sub["service_mix"],
                "basis": f"median of {sub['n']} signed industrial leases in this CoStar submarket, {sub['years'][0]}-"
                         f"{sub['years'][1]} ({sub['properties']} buildings); not this building's own rent"}
    mk = data.get("by_market", {}).get(w.get("market"))
    if mk and mk["n"] >= 2 * data.get("min_comps", MIN_COMPS):
        return {"tier": "market", "value": mk["median"], "low": mk["p25"], "high": mk["p75"], "unit": "$/SF/yr",
                "source": "estimate", "kind": "contract rent (market-wide comps)", "n": mk["n"], "years": mk["years"],
                "service_mix": mk["service_mix"],
                "basis": f"{w.get('market')}-wide median of {mk['n']} signed industrial leases, {mk['years'][0]}-"
                         f"{mk['years'][1]}: this submarket has too few, so this is a rough estimate"}
    return {"tier": "unknown", "value": None, "unit": "$/SF/yr", "source": "unknown",
            "basis": "no asking rent, no signed lease here, and fewer than "
                     f"{data.get('min_comps', MIN_COMPS)} recent industrial leases in the submarket"}
