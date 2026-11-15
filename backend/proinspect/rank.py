"""Deterministic ranking: which warehouses fit this user's requirements, and why. No LLM in here.

1. Facts.  Every warehouse gets a dict of facts {name: Fact(value, source, confidence)}. CoStar's catalog gives the
   first set; the scouts add measured facts (Matterport, Vexcel) that take precedence when confident. When a
   measured fact and CoStar disagree, both are kept so the fit card can show "measured vs CoStar".
2. Checks.  Each criterion becomes pass / near / fail / unknown against its fact.
3. Filter.  A failed deal-breaker removes the warehouse, with the reason. An unknown deal-breaker keeps it, flagged.
4. Score.   Weighted mean of check scores (pass 1, near 0.5, fail 0). An unknown counts 0.5 and widens the band
            to [0, 1] for that check, so thin data shows up as uncertainty, not as a good or bad score.
"""
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

from proinspect.requirements import Criterion, Requirements

NEAR = 0.10          # within 10% of a min/max counts as a near miss

LABELS = {             # how the UI and the voice agent name each fact
    "size_sqft": "Size", "clear_height_ft": "Clear height", "docks_total": "Dock doors",
    "docks_per_10ksf": "Docks per 10k SF", "drive_ins": "Drive-in doors", "drive_in_h_ft": "Drive-in door height",
    "truck_court_ft": "Truck court depth", "power_amps": "Power (amps)", "heavy_power": "Heavy power",
    "power_phases": "Power phases", "sprinklers": "Sprinklers", "cranes": "Cranes", "year_built": "Year built",
    "lot_acres_per_ksf": "Yard (acres per 1k SF)", "parking_spaces_per_ksf": "Parking per 1k SF",
    "rent_psf_yr": "Asking rent ($/SF/yr)", "price": "Asking price", "availability": "Availability",
    "roof_condition": "Roof condition (1-5)", "column_spacing_ft": "Column spacing",
}
UNITS = {"size_sqft": "SF", "clear_height_ft": "ft", "truck_court_ft": "ft", "drive_in_h_ft": "ft",
         "power_amps": "A", "rent_psf_yr": "$/SF/yr", "price": "$"}


@dataclass
class Fact:
    value: object
    source: str                 # costar | matterport | vexcel | derived
    confidence: float = 0.8     # 0..1
    detail: Optional[str] = None
    alt: list = field(default_factory=list)   # other sources' values for the same fact
    bound: Optional[str] = None               # "lower": the true value is at least this (e.g. doors we could see)


def _f(v, src="costar", conf=0.8, detail=None):
    return Fact(v, src, conf, detail) if v not in (None, "") else None


def catalog_facts(w: dict) -> dict:
    """Facts CoStar gives us for one catalog record."""
    rba = w.get("rba_sqft") or w.get("gba_sqft")
    docks = None
    if w.get("docks_external") is not None or w.get("docks_internal") is not None:
        docks = (w.get("docks_external") or 0) + (w.get("docks_internal") or 0)
    elif w.get("has_docks") is False:
        docks = 0
    lease = [l for l in w.get("listings", []) if l.get("kind") == "lease" and l.get("status") == "Active"]
    sale = [l for l in w.get("listings", []) if l.get("kind") == "sale" and l.get("status") == "Active"]
    rent = min([l["rent_low_psf_yr"] for l in lease if l.get("rent_low_psf_yr")] or [None], key=lambda x: x or 0)
    price = min([l["price_low"] for l in sale if l.get("price_low")] or [None], key=lambda x: x or 0)
    sprk = w.get("sprinklers")
    facts = {
        "size_sqft": _f(rba, detail="RBA" if w.get("rba_sqft") else "GBA"),
        "clear_height_ft": _f(w.get("clear_height_ft")),
        "docks_total": _f(docks),
        "docks_per_10ksf": _f(round(docks / rba * 10_000, 2) if docks is not None and rba else None, "derived"),
        "drive_ins": _f(w.get("drive_ins")),
        "drive_in_h_ft": _f(w.get("drive_in_h_ft")),
        "truck_court_ft": _f(w.get("truck_court_ft")),
        "power_amps": _f(w.get("power_amps")),
        "heavy_power": _f(w.get("heavy_power")),
        "power_phases": _f(w.get("power_phases")),
        "sprinklers": _f(sprk if isinstance(sprk, str) else ("yes" if sprk else ("none" if sprk is False else None))),
        "cranes": _f(bool(w["cranes"]) if w.get("cranes") is not None else None),
        "year_built": _f(w.get("year_built")),
        "lot_acres_per_ksf": _f(round(w["lot_acres"] / rba * 1000, 3) if w.get("lot_acres") and rba else None,
                                "derived"),
        "parking_spaces_per_ksf": _f(w.get("parking_ratio") or (round(w["parking_spaces"] / rba * 1000, 2)
                                                                if w.get("parking_spaces") and rba else None)),
        "rent_psf_yr": _f(rent, detail="lowest active asking rent"),
        "price": _f(price, detail="lowest active asking price"),
    }
    return {k: v for k, v in facts.items() if v is not None}


def merge(costar: dict, measured: dict) -> dict:
    """Measured facts win when at least as confident; the other value is kept in `alt`."""
    out = dict(costar)
    for k, m in (measured or {}).items():
        c = out.get(k)
        if c is None:
            out[k] = m
        elif m.confidence >= c.confidence:
            m.alt = [{"value": c.value, "source": c.source}] + c.alt
            out[k] = m
        else:
            c.alt = c.alt + [{"value": m.value, "source": m.source}]
    return out


def check(name: str, crit: Criterion, fact: Optional[Fact]) -> dict:
    need = {"min": crit.min, "max": crit.max, "value": crit.value}
    row = {"fact": name, "label": LABELS.get(name, name), "unit": UNITS.get(name), "level": crit.level,
           "weight": crit.weight, "need": {k: v for k, v in need.items() if v is not None},
           "have": None, "source": None, "confidence": None, "alt": [], "status": "unknown", "score": 0.5}
    if fact is None or fact.value is None:
        return row
    v = fact.value
    row.update({"have": v, "source": fact.source, "confidence": fact.confidence, "alt": fact.alt})
    status = "pass"
    if crit.value is not None:
        if isinstance(crit.value, bool):
            ok = bool(v) == crit.value
        else:
            ok = str(crit.value).lower() in str(v).lower()
        status = "pass" if ok else "fail"
    else:
        x = float(v)
        if fact.bound == "lower" and crit.min is not None and x < crit.min:
            row.update({"status": "unknown", "score": 0.5, "note": f"at least {v:g} seen; may be more"})
            return row
        if crit.min is not None and x < crit.min:
            status = "near" if x >= crit.min * (1 - NEAR) else "fail"
        if crit.max is not None and x > crit.max:
            status = "near" if x <= crit.max * (1 + NEAR) else "fail"
    row["status"] = status
    row["score"] = {"pass": 1.0, "near": 0.5, "fail": 0.0}[status]
    return row


def _miles(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 7917.6 * math.asin(math.sqrt(a))


def availability(w: dict, deal: Optional[str]) -> dict:
    kinds = {"lease": {"lease"}, "buy": {"sale"}, "either": {"lease", "sale"}}.get(deal or "either")
    active = [l for l in w.get("listings", []) if l.get("status") == "Active"]
    if any(l.get("kind") in kinds for l in active):
        s, txt = 1.0, "Available now"
    elif active:
        s, txt = 0.5, "Listed, but not for " + ("lease" if deal == "lease" else "sale" if deal == "buy" else "this deal")
    elif w.get("listings"):
        s, txt = 0.35, "Off market (was listed)"
    else:
        s, txt = 0.25, "Not listed: approach the owner" + (
            f" (last sold {w['last_sale']['date'][:4]})" if w.get("last_sale") else "")
    return {"fact": "availability", "label": "Availability", "level": "N", "weight": 3, "need": {"deal": deal},
            "have": txt, "source": "costar", "confidence": 0.9, "alt": [],
            "status": "pass" if s == 1.0 else "near" if s >= 0.5 else "fail", "score": s}


def rank(req: Requirements, catalog: list[dict], measured: Optional[dict] = None) -> list[dict]:
    """-> one row per warehouse (kept first, best first; then excluded), each with its checks and reasons.
    `measured` maps propertyId -> {fact name: Fact} from the scouts."""
    crits = req.derived_criteria()
    rows = []
    for w in catalog:
        why_out = []
        if req.market and req.market != "Either" and w.get("market") != req.market:
            continue                                         # other city: not shown at all
        if req.area and w.get("lat") is not None:
            d = _miles(req.area.lat, req.area.lon, w["lat"], w["lon"])
            if d > req.area.radius_mi:
                continue
        if req.available_only and not any(l.get("status") == "Active" for l in w.get("listings", [])):
            why_out.append("not listed right now")
        if req.inspectable_only and not w.get("inspectable"):
            why_out.append("no warehouse-floor 3D scan")
        facts = merge(catalog_facts(w), (measured or {}).get(w["id"], {}))
        checks = [check(n, c, facts.get(n)) for n, c in crits.items()]
        checks.append(availability(w, req.deal))
        for c in checks:
            if c["level"] == "D" and c["status"] == "fail":
                need, have = c["need"], c["have"]
                if "max" in need and isinstance(have, (int, float)) and have > need["max"]:
                    bound = f"≤ {need['max']:,.0f}"                 # failed the upper bound
                elif "min" in need:
                    bound = f"≥ {need['min']:,.0f}"
                elif "max" in need:
                    bound = f"≤ {need['max']:,.0f}"
                else:
                    bound = str(need.get("value"))
                hv = f"{have:,.0f}" if isinstance(have, (int, float)) and not isinstance(have, bool) else str(have)
                why_out.append(f"{c['label']} {hv}{(' ' + c['unit']) if c['unit'] else ''} (needs {bound})")
        wsum = sum(c["weight"] for c in checks) or 1
        point = sum(c["weight"] * c["score"] for c in checks) / wsum
        lo = sum(c["weight"] * (0 if c["status"] == "unknown" else c["score"]) for c in checks) / wsum
        hi = sum(c["weight"] * (1 if c["status"] == "unknown" else c["score"]) for c in checks) / wsum
        rows.append({
            "id": w["id"], "address": w.get("address"), "market": w.get("market"),
            "lat": w.get("lat"), "lon": w.get("lon"),
            "score": round(point * 100), "band": [round(lo * 100), round(hi * 100)],
            "excluded": bool(why_out), "excluded_because": why_out,
            "unverified_dealbreakers": [c["label"] for c in checks if c["level"] == "D" and c["status"] == "unknown"],
            "counts": {s: sum(c["status"] == s for c in checks) for s in ("pass", "near", "fail", "unknown")},
            "inspectable": w.get("inspectable"), "checks": checks,
            "facts": {k: asdict(v) for k, v in facts.items()},
        })
    browse = not crits                      # nothing to rank by: an honest "unranked" order, not a fake score
    for r in rows:
        r["mode"] = "browse" if browse else "ranked"
    if browse:
        rows.sort(key=lambda r: (r["excluded"], -r["checks"][-1]["score"], not r["inspectable"],
                                 -((r["facts"].get("size_sqft") or {}).get("value") or 0)))
    else:
        rows.sort(key=lambda r: (r["excluded"], -r["score"], len(r["unverified_dealbreakers"]), -r["band"][0]))
    n = 0
    for r in rows:
        if not r["excluded"]:
            n += 1
            r["rank"] = n
        else:
            r["rank"] = None
    return rows
