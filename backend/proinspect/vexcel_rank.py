"""Rank warehouses by a Vexcel metric over the precomputed profiles (build/vexcel/*.json).

Metrics are DATA (METRICS below: label, unit, definition, source, order, alternatives), so the agent can say exactly
which metric it used and offer the alternative for an ambiguous superlative ("biggest parking lot" ->
parking_area_sqft, alternatives car_stalls_est / paved_area_sqft).

    metrics() -> [definition]
    metric_for(phrase) -> (metric, [alternatives])        superlative words -> a defined metric
    rank(metric, market=None, top=10) -> {metric: definition, market, results: [row], unknown: [row], coverage}
      row = {rank, id, address, market, value, unit, source, date, confidence, note?, hit?}
    `distance_to:<place>` ranks by the nearest LIKELY embedding hit within 3 km (place = a VOCAB key or alias:
    grocery, highway_ramp, rail_yard, truck_stop, port_container, residential); sites with no likely hit are unknown.
"""
import json
from functools import lru_cache

from proinspect.config import BUILD
from proinspect.twin import vexcel_profile as vp

METRICS = {
    "parking_area_sqft": {
        "label": "parking lot area", "unit": "sqft", "path": ("parking", "parking_lot_area_sqft"), "order": "desc",
        "definition": "Area of the parcel that SAM3 marks as 'parking lot' on the newest Vexcel ortho, minus buildings.",
        "source": "Vexcel ortho + SAM3", "alternatives": ["car_stalls_est", "paved_area_sqft"]},
    "paved_area_sqft": {
        "label": "paved area", "unit": "sqft", "path": ("parking", "paved_area_sqft"), "order": "desc",
        "definition": "All flat, non-green ground on the parcel outside buildings (parking + truck courts + drives), "
                      "from Vexcel DSM - DTM and the ortho.",
        "source": "Vexcel DSM/DTM + ortho", "alternatives": ["parking_area_sqft", "truck_court_ft"]},
    "car_stalls_est": {
        "label": "estimated car stalls", "unit": "stalls", "path": ("parking", "car_stalls_est"), "order": "desc",
        "definition": f"Estimate: parking lot area / {vp.SF_PER_STALL} SF per stall.",
        "source": "derived from Vexcel ortho + SAM3", "alternatives": ["parking_area_sqft"]},
    "cars_seen": {
        "label": "cars seen on the parcel", "unit": "count", "path": ("parking", "cars_seen"), "order": "desc",
        "definition": "Cars SAM3 detected on the parcel in the newest ortho (one snapshot day).",
        "source": "Vexcel ortho + SAM3", "alternatives": ["car_stalls_est"]},
    "trucks_seen": {
        "label": "trucks seen on the parcel", "unit": "count", "path": ("parking", "trucks_seen"), "order": "desc",
        "definition": "Trucks SAM3 detected on the parcel in the newest ortho (one snapshot day).",
        "source": "Vexcel ortho + SAM3", "alternatives": ["trailers_seen"]},
    "trailers_seen": {
        "label": "semi trailers seen on the parcel", "unit": "count", "path": ("parking", "trailers_seen"),
        "order": "desc", "definition": "Semi trailers SAM3 detected on the parcel in the newest ortho.",
        "source": "Vexcel ortho + SAM3", "alternatives": ["trucks_seen"]},
    "truck_court_ft": {
        "label": "truck court depth", "unit": "ft", "path": ("parking", "truck_court_ft"), "order": "desc",
        "definition": "Median depth of paved apron in front of detected dock doors (rays over the paved mask).",
        "source": "Vexcel DSM + ortho + obliques", "alternatives": ["paved_area_sqft"]},
    "roof_condition": {
        "label": "roof condition", "unit": "1-5", "path": ("building", "roof_condition"), "order": "desc",
        "definition": "Vexcel roof condition score, 1 (poor) to 5 (excellent).",
        "source": "Vexcel Elements (property/extract)", "alternatives": []},
    "solar": {
        "label": "solar panels on roof", "unit": "yes/no", "path": ("building", "solar"), "order": "desc",
        "definition": "Vexcel roof-objects.solar flag (1 = solar detected).",
        "source": "Vexcel Elements (property/extract)", "alternatives": []},
    "tree_cover_pct": {
        "label": "tree cover", "unit": "%", "path": ("site", "tree_cover_pct"), "order": "desc",
        "definition": "Share of the parcel under tree canopy.", "source": "Vexcel Elements (property/extract)",
        "alternatives": []},
    "building_height_ft": {
        "label": "building height (roof top)", "unit": "ft", "path": ("building", "height_ft_dsm"), "order": "desc",
        "definition": "P90 of DSM - DTM over the main footprint: roof top, not clear height.",
        "source": "Vexcel DSM/DTM", "alternatives": []},
    "footprint_sqft": {
        "label": "building footprint", "unit": "sqft", "path": ("building", "footprint_sqft"), "order": "desc",
        "definition": "Footprint area of the largest structure on the parcel.", "source": "Vexcel Elements",
        "alternatives": []},
    "parcel_acres": {
        "label": "parcel size", "unit": "acres", "path": ("site", "parcel_acres"), "order": "desc",
        "definition": "Area of the Vexcel parcel polygon.", "source": "Vexcel Elements", "alternatives": []},
    "new_structure_m2": {
        "label": "new structure since the oldest DSM", "unit": "m2", "path": None, "order": "desc",
        "definition": "Area on the parcel that rose > 2.5 m between the oldest and newest DSM flights.",
        "source": "Vexcel historical DSM", "alternatives": []},
}
# values whose meaning depends on the parcel's extent (get the acres caveat when CoStar's lot differs > 2x)
PARCEL_AREA_METRICS = {"parking_area_sqft", "paved_area_sqft", "car_stalls_est", "cars_seen", "trucks_seen",
                       "trailers_seen", "tree_cover_pct", "parcel_acres", "new_structure_m2"}
DISTANCE = {
    "label": "distance to the nearest likely {place}", "unit": "m", "order": "asc",
    "definition": "Straight-line distance from the property to the nearest Vexcel semantic-search tile scoring "
                  "'likely' for {place} within 3 km. Aerial similarity, not a map lookup: likely, never certain.",
    "source": vp.EMBED_SOURCE, "alternatives": []}

SUPERLATIVES = [   # (words, metric) checked in order; first match wins
    (("lot size", "acre", "land", "parcel"), "parcel_acres"),
    (("stalls", "spaces"), "car_stalls_est"),
    (("parking", "car park"), "parking_area_sqft"),
    (("paved", "pavement", "yard", "outdoor storage"), "paved_area_sqft"),
    (("truck court", "court", "apron"), "truck_court_ft"),
    (("trailer",), "trailers_seen"),
    (("truck",), "trucks_seen"),
    (("roof",), "roof_condition"),
    (("solar",), "solar"),
    (("tree", "green", "shade"), "tree_cover_pct"),
    (("tall", "height", "highest"), "building_height_ft"),
    (("newest", "recent", "new "), "new_structure_m2"),
    (("footprint", "biggest building", "largest building"), "footprint_sqft"),
]


def metrics():
    out = [{"metric": k, **{kk: vv for kk, vv in v.items() if kk != "path"}} for k, v in METRICS.items()]
    out.append({"metric": "distance_to:<place>", **DISTANCE, "places": {k: v["label"] for k, v in vp.VOCAB.items()}})
    return out


def definition(metric):
    if metric.startswith("distance_to:"):
        key = vp.resolve_place(metric.split(":", 1)[1])
        if not key:
            raise ValueError(f"unknown place in {metric!r}; use one of {sorted(vp.VOCAB)}")
        lab = vp.VOCAB[key]["label"]
        return {"metric": f"distance_to:{key}", "place": key, "label": DISTANCE["label"].format(place=lab),
                "unit": "m", "order": "asc", "definition": DISTANCE["definition"].format(place=lab),
                "source": DISTANCE["source"], "caveat": vp.VOCAB[key]["caveat"], "alternatives": []}
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; use one of {sorted(METRICS) + ['distance_to:<place>']}")
    return {"metric": metric, **{k: v for k, v in METRICS[metric].items() if k != "path"}}


def metric_for(phrase):
    """'closest to a grocery store' -> ('distance_to:grocery', []); 'biggest parking lot' -> parking_area_sqft."""
    t = (phrase or "").lower()
    if any(w in t for w in ("closest", "nearest", "near ", "distance", "close to", "next to")):
        key = vp.resolve_place(t)
        if key:
            return f"distance_to:{key}", []
    for words, m in SUPERLATIVES:
        if any(w in t for w in words):
            return m, METRICS[m]["alternatives"]
    key = vp.resolve_place(t)
    if key:
        return f"distance_to:{key}", []
    return None, []


@lru_cache(maxsize=1)
def _catalog():
    return {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}


def _market_ok(w, market):
    if not market:
        return True
    m = str(market).strip().lower()
    alias = {"hou": "houston", "houston": "houston", "14": "houston", "la": "los angeles", "lax": "los angeles",
             "los angeles": "los angeles", "20": "los angeles"}
    return (w.get("market") or "").lower() == alias.get(m, m)


def value_of(prof, metric):
    """-> (value, fact dict) for one profile; value None when unknown."""
    if metric.startswith("distance_to:"):
        key = metric.split(":", 1)[1]
        n = (prof.get("nearby") or {}).get(key) or {}
        near = n.get("nearest")
        if near and near.get("likelihood") == "likely":
            return near["distance_m"], {"source": n.get("source"), "date": None, "confidence": "likely",
                                        "hit": {**near, "label": n.get("label")}}
        why = (f"no likely {n.get('label', key)} within 3 km" +
               (f" (nearest possible: {near['distance_m']} m)" if near else "")) if n else "not searched"
        return None, {"note": why, "hit": near}
    if metric == "new_structure_m2":
        ch = (prof.get("history") or {}).get("change") or {}
        return ch.get("new_structure_m2"), {"source": ch.get("source"), "date": "/".join(ch.get("dsm_dates") or []),
                                            "confidence": 0.5, "note": ch.get("note")}
    sec, key = METRICS[metric]["path"]
    f = (prof.get(sec) or {}).get(key) or {}
    v = f.get("value")
    if metric == "solar" and v is not None:
        v = 1 if v else 0
    return v, f


def rank(metric, market=None, top=10, profiles=None):
    d = definition(metric)
    metric = d["metric"]
    profiles = profiles if profiles is not None else vp.load_all()
    cat = _catalog()
    rows, unknown = [], []
    for pid, prof in profiles.items():
        w = cat.get(pid, {"market": prof.get("market"), "address": prof.get("address")})
        if not _market_ok(w, market):
            continue
        v, f = value_of(prof, metric)
        if prof.get("parcel_mismatch") and not metric.startswith("distance_to:"):
            pc = prof.get("parcel_check") or {}
            unknown.append({"id": pid, "address": w.get("address") or prof.get("address"), "market": w.get("market"),
                            "value": None, "unit": d["unit"], "excluded": "parcel_mismatch",
                            "note": pc.get("note") or vp.MISMATCH_NOTE, "measured_value": v})
            continue
        row = {"id": pid, "address": w.get("address") or prof.get("address"), "market": w.get("market"),
               "value": v, "unit": d["unit"], "source": f.get("source"), "date": f.get("date"),
               "confidence": f.get("confidence")}
        for k in ("note", "hit", "estimate"):
            if f.get(k) is not None:
                row[k] = f[k]
        an = (prof.get("parcel_check") or {}).get("acres_note")
        if an and not metric.startswith("distance_to:") and metric in PARCEL_AREA_METRICS:
            row["note"] = an + (f". {row['note']}" if row.get("note") else "")
            row["confidence_label"] = "medium"
        (rows if v is not None else unknown).append(row)
    rows.sort(key=lambda r: r["value"], reverse=d["order"] == "desc")
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    in_market = sum(1 for w in cat.values() if _market_ok(w, market))
    return {"metric": d, "market": market, "results": rows[:top] if top else rows, "unknown": unknown,
            "coverage": {"ranked": len(rows), "unknown": len(unknown), "profiled": len(rows) + len(unknown),
                         "warehouses_in_market": in_market}}
