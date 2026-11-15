"""Vexcel profile of one warehouse: everything the aerial data says about the site, each value sourced.

    build(pid, force=False) -> dict        cached to build/vexcel/{pid}.json (contract: docs/CONTRACTS.md)
    load(pid) -> dict | None               precomputed profile only (no network)
    nearby_hits(lat, lon, key|text, radius_m, exclude=None) -> [hit]   calibrated semantic search
    python -m proinspect.twin.vexcel_profile [--all] [--force] [ids...]  batch (resumable, 4 workers)

Every value is a fact: {value, unit, source, date, method, confidence[, note, alt]}; unknown is value None + note.

UNITS (verified on 94 parcels, 2026-11-14): `property/extract` with unit-system=imperial returns
`footprint.area` in SF but `hardscapes.area` in m² (hardscapes.percent == area / parcel m² on every sample).
Vexcel "hardscapes" is a residential class (patios, walks): 1% on 12896757 whose court is ~57% paved, so it is
reported as `parking.vexcel_hardscape_sqft`, never as the paved area. Paved area comes from the exterior twin's
paved mask (DSM - DTM < 0.5 m and not green on the ortho, inside the parcel, outside buildings).

EMBEDDING CALIBRATION (Vexcel embedding/extract `score`, zoom-19 ~60 m tiles; thumbnails inspected by eye
2026-11-14 around 12896757 Pearland, 238885 Houston, 278605 Vernon LA, plus known places). Scores are not
comparable across query texts, so each term has its own thresholds. A hit is NEVER certain:
  rail_yard    "railroad yard with freight trains"   Englewood yard HOU 0.230-0.237, Hobart yard LA 0.192-0.200,
               rail lines/spurs 0.15-0.20; no rail within 3 km of 12896757: best 0.065.  likely >= 0.19, possible >= 0.15
  port_container "stacked shipping containers"       Barbours Cut 0.237-0.247, Pier 400 LA 0.224-0.228; container/
               trailer storage yards 0.17-0.19 (one false hit: a white roof at 0.192); industrial negative 0.134.
               likely >= 0.21, possible >= 0.17
  highway_ramp "highway interchange ramp"            stack interchange near 12896757 0.264-0.268; plain freeway
               segments 0.205-0.23 (correct as highway, not always a ramp).  likely >= 0.235, possible >= 0.20
  truck_stop   "truck stop with many parked semi trucks"  finds truck/trailer parking yards and busy dock courts
               0.155-0.183 (all trucking yards by eye), industrial negative 0.108. It cannot tell a fuel stop from a
               trucking terminal.  likely >= 0.17, possible >= 0.155
  grocery      "supermarket with large parking lot"  every top hit is a big-box store or commercial box with a car
               park (0.19-0.223), including at an industrial control (0.213). It cannot tell a supermarket from
               other big-box retail.  likely >= 0.215 (means "big-box store with large car park"), possible >= 0.19
  residential  "residential neighborhood houses"     HOU subdivisions 0.259-0.272, LA single-family blocks
               0.235-0.239, all houses by eye.  likely >= 0.23, possible >= 0.21
Hits whose tile touches the property's own parcel are dropped (a warehouse's own dock court matches truck_stop).
"""
import hashlib
import json
import math
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage
from shapely import wkt as shp_wkt
from shapely.ops import transform, unary_union

from proinspect.config import BUILD
from proinspect.tools import elevation as el
from proinspect.tools import vexcel as vx

VERSION = 4
OUT = BUILD / "vexcel"
THUMBS = OUT / "thumbs"
M2_TO_SF = 10.7639
M_TO_FT = 3.28084
SF_PER_STALL = 350          # gross parking SF per car incl. aisles (planning rule of thumb, 300-400)
RADIUS_M = 3000
CHANGE_M = 2.5
CHANGE_MIN_M2 = 150          # smaller blobs are vehicles / noise

VOCAB = {
    "grocery": {"text": "supermarket with large parking lot", "likely": 0.215, "possible": 0.19,
                "label": "big-box store with a large car park (could be a supermarket)",
                "caveat": "Aerial search cannot tell a supermarket from other big-box retail.",
                "aliases": ["grocery", "grocery store", "supermarket", "food store", "big box", "retail"]},
    "highway_ramp": {"text": "highway interchange ramp", "likely": 0.235, "possible": 0.20,
                     "label": "highway interchange / on-ramp",
                     "caveat": "Possible hits can be a plain freeway segment rather than a ramp.",
                     "aliases": ["highway", "freeway", "interstate", "ramp", "interchange", "highway ramp"]},
    "rail_yard": {"text": "railroad yard with freight trains", "likely": 0.19, "possible": 0.15,
                  "label": "rail yard / freight rail",
                  "caveat": "Possible hits are often a single rail line or spur, not a yard.",
                  "aliases": ["rail", "railroad", "rail yard", "train", "railway", "intermodal"]},
    "truck_stop": {"text": "truck stop with many parked semi trucks", "likely": 0.17, "possible": 0.155,
                   "label": "truck / trailer parking yard (truck stop or trucking terminal)",
                   "caveat": "Cannot tell a fuel stop from a trucking terminal or another warehouse's dock court.",
                   "aliases": ["truck stop", "truck parking", "trucking", "fuel stop", "truck yard"]},
    "port_container": {"text": "stacked shipping containers", "likely": 0.21, "possible": 0.17,
                       "label": "container terminal / container stacks",
                       "caveat": "Possible hits are often trailer or container storage yards, not a port.",
                       "aliases": ["port", "container", "containers", "container yard", "shipping", "terminal"]},
    "residential": {"text": "residential neighborhood houses", "likely": 0.23, "possible": 0.21,
                    "label": "residential neighborhood",
                    "caveat": "Matches single-family housing; apartments may score lower.",
                    "aliases": ["residential", "houses", "homes", "neighborhood", "housing"]},
}
EMBED_SOURCE = "Vexcel embedding/extract (semantic search over zoom-19 ortho tiles)"


def resolve_place(text):
    """Free text -> VOCAB key, or None when the text isn't a calibrated term."""
    t = (text or "").strip().lower().replace("_", " ")
    for k, v in VOCAB.items():
        if t == k.replace("_", " ") or t in v["aliases"]:
            return k
    for k, v in VOCAB.items():
        if any(a in t for a in v["aliases"]):
            return k
    return None


# ------------------------------------------------------------------ helpers
def fact(value, unit=None, source=None, date=None, method=None, confidence=None, **extra):
    d = {"value": value, "unit": unit, "source": source, "date": date, "method": method, "confidence": confidence}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def unknown(why, unit=None, source=None):
    return fact(None, unit, source, None, None, 0.0, note=why)


def local(geom, lat0, lon0):
    """lon/lat shapely geometry -> metres east/north of (lat0, lon0)."""
    return transform(lambda x, y, z=None: el.to_local(y, x, lat0, lon0), geom)


def area_m2(geom):
    """Area of a lon/lat geometry in m² (local equirectangular frame at its centroid)."""
    if geom is None or geom.is_empty:
        return 0.0
    c = geom.centroid
    return float(local(geom, c.y, c.x).area)


def dist_m(lat, lon, lat2, lon2):
    x, y = el.to_local(lat2, lon2, lat, lon)
    return float(math.hypot(float(x), float(y)))


def _ms_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else None


def _image_meta(res):
    imgs = res.get("images") or []
    m = (imgs[0].get("metadata") or {}) if imgs else {}
    return {"date": _ms_date(m.get("last-capture-date")), "collection": m.get("collection")}


def masks_to_geoms(res, view="ortho"):
    """detect_raw result -> [(label, confidence, lon/lat polygon)]."""
    out = []
    for f in res.get("features", []):
        label = f["properties"]["label"]
        for d in f["properties"]["detections"]:
            g = None
            if d.get("pixel-mask"):
                g = vx.mask_to_wkt(d["pixel-mask"], d["bbox"], view)
            g = shp_wkt.loads(g or d["bbox"])
            if not g.is_valid:
                g = g.buffer(0)
            out.append((label, float(d.get("confidence") or 0), g))
    return out


def likelihood(score, key):
    v = VOCAB[key]
    if score is None:
        return None
    return "likely" if score >= v["likely"] else "possible" if score >= v["possible"] else None


def thumb(tile_wkt):
    """Ortho crop of a search tile (+20 m context) saved under build/vexcel/thumbs; returns the /media URL."""
    name = hashlib.sha1(tile_wkt.encode()).hexdigest()[:16] + ".jpg"
    p = THUMBS / name
    if not p.exists():
        try:
            THUMBS.mkdir(parents=True, exist_ok=True)
            p.write_bytes(vx.ortho(tile_wkt, 20, None, "256,256"))
        except Exception:
            return None
    return f"/media/vexcel/thumbs/{name}"


def nearby_hits(lat, lon, key_or_text, radius_m=RADIUS_M, exclude=None, top=20, thumbs=True):
    """Semantic search around a point. Calibrated terms get likely/possible from VOCAB thresholds; free text is
    uncalibrated, so every hit is at most 'possible'. Hits beyond radius_m or touching `exclude` are dropped."""
    key = key_or_text if key_or_text in VOCAB else resolve_place(key_or_text)
    text = VOCAB[key]["text"] if key else key_or_text
    half = max(200, min(int(radius_m), 5000))
    hits = vx.embedding_search(round(lat, 6), round(lon, 6), text, half, top)
    out = []
    for h in hits:
        d = dist_m(lat, lon, h["lat"], h["lon"])
        if d > radius_m:
            continue
        if exclude is not None and shp_wkt.loads(h["tile_wkt"]).intersects(exclude):
            continue
        lk = likelihood(h["score"], key) if key else "possible"
        if lk is None:
            continue
        out.append({"lat": round(h["lat"], 6), "lon": round(h["lon"], 6), "score": round(h["score"], 4),
                    "distance_m": round(d), "likelihood": lk, "tile_wkt": h["tile_wkt"],
                    "place": key, "label": VOCAB[key]["label"] if key else key_or_text,
                    "calibrated": bool(key)})
    out.sort(key=lambda r: r["distance_m"])
    if thumbs:
        for r in out[:5]:
            r["thumb_url"] = thumb(r["tile_wkt"])
    return out


# ------------------------------------------------------------------ sections
def _site(pid):
    cat = {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}
    if pid not in cat:
        raise KeyError(f"{pid} is not in the warehouse catalog")
    w = cat[pid]
    from proinspect.twin import exterior as ex             # same parcel as the exterior twin, incl. the fallback for
    try:                                                   # a CoStar point on the wrong lot (OSM-guided re-lookup)
        _, props, parcel, sts = ex._site(pid)
    except ValueError:
        return w, None, None, []
    return w, props, parcel, sts or []


def _twin(pid):
    p = BUILD / "twins" / "exterior" / f"{pid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _vehicles(twin, parcel):
    """Vehicles from the exterior twin's SAM3 run (cached), counted inside the parcel only."""
    from proinspect.twin.exterior import VEHICLE_LABELS
    fr = twin["frame"]
    res = vx.detect_raw(vx.square_around(fr["lat0"], fr["lon0"], fr["half_m"]), VEHICLE_LABELS, "ortho", 0.4)
    counts = {l: 0 for l in VEHICLE_LABELS}
    for label, conf, g in masks_to_geoms(res):
        if parcel.contains(g.centroid):
            counts[label] += 1
    return counts, _image_meta(res)


def _trailers(parcel):
    """SAM3 'trailer' on the parcel ortho. 'semi trailer' never fired on any of 108 sites, and 'trailer' also
    catches cars, so keep only detections the size of a trailer (>= 18 m², long side >= 8 m) inside the parcel."""
    res = vx.detect_raw(parcel.envelope.wkt, ["trailer"], "ortho", 0.3)
    n = 0
    for label, conf, g in masks_to_geoms(res):
        if not parcel.contains(g.centroid):
            continue
        c = g.centroid
        rect = local(g, c.y, c.x).minimum_rotated_rectangle
        xs = list(rect.exterior.coords)
        long_side = max(math.dist(a, b) for a, b in zip(xs[:-1], xs[1:])) if len(xs) > 2 else 0
        if area_m2(g) >= 18 and long_side >= 8:
            n += 1
    return n, _image_meta(res)


def _parking_lot(parcel, footprints):
    res = vx.detect_raw(parcel.envelope.wkt, ["parking lot"], "ortho", 0.2)
    polys = [g for label, conf, g in masks_to_geoms(res) if g.area > 0]
    confs = [conf for label, conf, g in masks_to_geoms(res)]
    lot = unary_union(polys).intersection(parcel) if polys else None
    if lot is not None and footprints:
        lot = lot.difference(unary_union(footprints))
    return (area_m2(lot) if lot is not None else 0.0), len(polys), (max(confs) if confs else None), _image_meta(res)


def _change(lat0, lon0, half, parcel):
    """DSM year-to-year change inside the parcel: newest vs oldest DSM flight."""
    picks = el.pick_collections(lat0, lon0, product="dsm")
    if len(picks) < 2:
        return {"years": [el.year_of(p) for p in picks], "new_structure_m2": None, "removed_m2": None,
                "note": "fewer than 2 DSM years at this site"}
    a, b = el.year_of(picks[-1]), el.year_of(picks[0])
    hg = el.heightgrids(lat0, lon0, half, 0.5, years=[a, b])
    (zb, mb), (za, ma) = hg[b], hg[a]
    from proinspect.twin.exterior import Frame
    fr = Frame(lat0, lon0, half)
    inside = fr.raster([local(parcel, lat0, lon0)])
    if inside.shape != zb.shape:
        return {"years": [a, b], "new_structure_m2": None, "removed_m2": None, "note": "grid mismatch"}
    d = zb - za
    cell = 0.25
    out = {}
    for name, m in (("new", d > CHANGE_M), ("removed", d < -CHANGE_M)):
        m = ndimage.binary_opening(np.nan_to_num(m) & inside, iterations=2)
        lab, n = ndimage.label(m)
        sizes = ndimage.sum(m, lab, range(1, n + 1)) * cell if n else np.array([])
        keep = sizes[sizes >= CHANGE_MIN_M2]
        out[name] = (round(float(keep.sum())), int(len(keep)), round(float(keep.max())) if len(keep) else 0)
    return {"years": [a, b], "dsm_dates": [ma.get("last"), mb.get("last")],
            "new_structure_m2": out["new"][0], "new_blobs": out["new"][1],
            "removed_m2": out["removed"][0], "removed_blobs": out["removed"][1],
            "note": (f"Areas inside the parcel where the surface rose (new) or fell (removed) by > {CHANGE_M} m "
                     f"between the {a} and {b} DSM flights, blobs >= {CHANGE_MIN_M2} m² only. Parked trailers and "
                     f"tree growth can also cause change."),
            "source": "Vexcel DSM (historical, per flight year)", "method": "DSM difference, datum-aligned"}


def _nearby(lat, lon, parcel):
    out = {}
    for key, v in VOCAB.items():
        try:
            hits = nearby_hits(lat, lon, key, RADIUS_M, exclude=parcel, thumbs=False)
        except Exception as e:  # one failed search must not sink the profile
            out[key] = {"found": False, "label": v["label"], "error": str(e)[:200]}
            continue
        likely = [h for h in hits if h["likelihood"] == "likely"]
        nearest = (likely or hits or [None])[0]
        best = max(hits, key=lambda h: h["score"]) if hits else None
        for h in {id(x): x for x in (nearest, best) if x}.values():
            h["thumb_url"] = thumb(h["tile_wkt"])
        strip = (lambda h: {k: h[k] for k in ("lat", "lon", "score", "distance_m", "likelihood", "thumb_url")}
                 if h else None)
        out[key] = {"found": bool(hits), "label": v["label"], "query": v["text"],
                    "nearest": strip(nearest), "best": strip(best),
                    "n_likely": len(likely), "n_possible": len(hits) - len(likely),
                    "thresholds": {"likely": v["likely"], "possible": v["possible"]},
                    "radius_m": RADIUS_M, "caveat": v["caveat"],
                    "note": None if hits else f"no {v['label']} scored above 'possible' within {RADIUS_M / 1000:g} km",
                    "source": EMBED_SOURCE, "method": "text-to-image embedding similarity, calibrated thresholds",
                    "confidence": "likely/possible only; never certain"}
    return out


# ------------------------------------------------------------------ parcel check
MISMATCH_NOTE = "Vexcel parcel at CoStar's address point doesn't match this property"


def parcel_check(vexcel_building_sqft, vexcel_acres, costar_sqft, costar_acres):
    """Is the Vexcel parcel at CoStar's point really this property? Mismatch (ok=False) only when the Vexcel
    buildings are < 30% of CoStar's RBA (GBA if no RBA). A parcel > 2x larger/smaller than CoStar's lot is NOT a
    mismatch (CoStar lots often span several parcels, or one parcel holds several CoStar buildings): it adds
    `acres_note`, a caveat that parcel-based values describe the Vexcel parcel only."""
    why = None
    if costar_sqft and (vexcel_building_sqft or 0) < 0.3 * costar_sqft:
        why = (f"Vexcel buildings on the parcel total {round(vexcel_building_sqft or 0):,} SF vs CoStar "
               f"{round(costar_sqft):,} SF (< 30%)")
    acres_note = None
    if costar_acres and vexcel_acres and not (0.5 <= vexcel_acres / costar_acres <= 2.0):
        acres_note = (f"Vexcel parcel {vexcel_acres:g} ac vs CoStar lot {costar_acres:g} ac: parcel-based values "
                      f"describe the Vexcel parcel only")
    return {"ok": not why, "vexcel_building_sqft": round(vexcel_building_sqft or 0),
            "costar_sqft": round(costar_sqft) if costar_sqft else None, "vexcel_acres": vexcel_acres,
            "costar_acres": costar_acres, "acres_note": acres_note,
            "note": (MISMATCH_NOTE + ": " + why) if why else
                    ("Vexcel parcel agrees with CoStar" if (costar_sqft or costar_acres) else
                     "no CoStar size to check against")}


def _flag(section_facts, label, note, cap, mismatch=False):
    for f in section_facts.values():
        if isinstance(f, dict) and "value" in f:
            c = f.get("confidence")
            f["confidence"] = min(c, cap) if isinstance(c, (int, float)) else cap
            f["confidence_label"] = label
            if mismatch:
                f["parcel_mismatch"] = True
            f["note"] = note + (f". {f['note']}" if f.get("note") else "")


def _flag_low(section_facts):
    _flag(section_facts, "low", MISMATCH_NOTE, 0.2, mismatch=True)


# ------------------------------------------------------------------ the profile
def build(pid, force=False):
    pid = int(pid)
    path = OUT / f"{pid}.json"
    if path.exists() and not force:
        prof = json.loads(path.read_text())
        if prof.get("version") == VERSION:
            return prof
    t0 = time.time()
    w, pp, parcel, sts = _site(pid)
    if pp is None:
        prof = {"id": pid, "version": VERSION, "error": "no Vexcel parcel at the CoStar point",
                "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prof))
        return prof
    twin = _twin(pid)
    parcel_date = (pp.get("metadata.ortho-date-estimate") or "")[:10] or None
    PA = f"Vexcel property/extract (Elements, {pp.get('metadata.collection')})"
    footprints = [shp_wkt.loads(s["footprint.wkt"]) for s in sts if s.get("footprint.wkt")]
    parcel_m2 = area_m2(parcel)
    main = max(sts, key=lambda s: s.get("footprint.area") or 0) if sts else {}
    tw_img = (twin or {}).get("imagery", {})
    dsm_date = tw_img.get("dsm_date")
    pcheck = parcel_check(sum(s.get("footprint.area") or 0 for s in sts), round(parcel_m2 / 4046.86, 2),
                          w.get("rba_sqft") or w.get("gba_sqft"), w.get("lot_acres"))
    mismatch = not pcheck["ok"]
    conf_cap = 0.3 if mismatch else 1.0

    # ---- parking
    lot_m2, n_lot, lot_conf, lot_img = _parking_lot(parcel, footprints)
    lot_sf = round(lot_m2 * M2_TO_SF)
    hs = pp.get("hardscapes.area")
    paved_pct = (twin or {}).get("site", {}).get("paved_pct_of_parcel")
    parking = {
        "paved_area_sqft": (fact(round(paved_pct / 100 * parcel_m2 * M2_TO_SF), "sqft",
                                 "Vexcel DSM - DTM + ortho (exterior twin)", dsm_date,
                                 "cells with height above ground < 0.5 m and not green, inside the parcel, outside "
                                 "buildings (0.5 m grid)", round(0.6 * conf_cap, 2),
                                 alt={"vexcel_hardscape_sqft": round(hs * M2_TO_SF) if hs is not None else None})
                            if paved_pct is not None else unknown("no exterior twin for this site", "sqft")),
        "vexcel_hardscape_sqft": (fact(round(hs * M2_TO_SF), "sqft", PA, parcel_date,
                                       "Vexcel 'hardscapes.area' (returned in m² despite imperial units; converted)",
                                       0.5, note="Residential-style hardscape class (patios, walks); it undercounts "
                                                 "warehouse paving badly. Not a parking measure.")
                                  if hs is not None else unknown("Vexcel returned no hardscape area", "sqft", PA)),
        "parking_lot_area_sqft": fact(lot_sf, "sqft", "Vexcel ortho + SAM3 'parking lot' (elements/detect)",
                                      lot_img["date"], "union of SAM3 'parking lot' masks (threshold 0.2) clipped to "
                                      "the parcel, minus building footprints; area in a local metric frame",
                                      round(min(0.5, (lot_conf or 0)) * conf_cap if n_lot else 0.3 * conf_cap, 2),
                                      note=None if n_lot else "SAM3 found no 'parking lot' on the parcel ortho",
                                      detections=n_lot, collection=lot_img["collection"]),
        "car_stalls_est": fact(round(lot_sf / SF_PER_STALL) if lot_sf else 0, "stalls", "derived",
                               lot_img["date"], f"parking_lot_area_sqft / {SF_PER_STALL} SF per stall (incl. aisles)",
                               0.3 * conf_cap, estimate=True,
                               note="Estimate: SAM3's 'parking lot' can include truck courts and drive aisles. "
                                    "Against CoStar parking spaces on 75 warehouses: median 1.6x CoStar, "
                                    "correlation 0.42, so treat it as an upper bound."),
    }
    if twin:
        try:
            veh, vimg = _vehicles(twin, parcel)
        except Exception as e:
            veh, vimg = None, {"date": None}
            parking["vehicles_error"] = str(e)[:200]
        for k, label in (("cars_seen", "car"), ("trucks_seen", "truck")):
            parking[k] = (fact(veh[label], "count", "Vexcel ortho + SAM3 (elements/detect)", vimg["date"],
                               f"SAM3 '{label}' detections (threshold 0.4) whose centre is inside the parcel; "
                               f"a snapshot on the capture day", round(0.6 * conf_cap, 2))
                          if veh else unknown("vehicle detection failed", "count"))
        courts = twin.get("courts") or []
        if courts:
            c0 = courts[0]
            parking["truck_court_ft"] = fact(c0.get("depth_ft"), "ft", "Vexcel DSM + ortho + oblique doors "
                                             "(exterior twin)", dsm_date, "median of rays cast from the dock facade "
                                             "over the paved mask (measurement.md §1)",
                                             round({"high": 0.5, "medium": 0.35}.get(c0.get("confidence"), 0.2)
                                                   * conf_cap, 2),
                                             p10=c0.get("p10_ft"), p90=c0.get("p90_ft"), kind=c0.get("kind"),
                                             verdict_53ft=c0.get("verdict_53ft"))
        else:
            parking["truck_court_ft"] = unknown("no dock doors were detected in the obliques, so no court was "
                                                "measured", "ft")
    else:
        for k in ("cars_seen", "trucks_seen", "truck_court_ft"):
            parking[k] = unknown("no exterior twin for this site")
    try:
        ntr, timg = _trailers(parcel)
        parking["trailers_seen"] = fact(ntr, "count", "Vexcel ortho + SAM3 (elements/detect)", timg["date"],
                                        "SAM3 'trailer' (threshold 0.3) inside the parcel, kept only if trailer-"
                                        "sized (>= 18 m², long side >= 8 m); a snapshot on the capture day",
                                        round(0.4 * conf_cap, 2))
    except Exception as e:
        parking["trailers_seen"] = unknown(f"trailer detection failed: {str(e)[:120]}", "count")
    vc = pp.get("vehicles.count")
    parking["vexcel_vehicles_count"] = fact(vc, "count", PA, parcel_date, "Vexcel Elements parcel vehicle count",
                                            0.5) if vc is not None else unknown("not reported by Vexcel", "count", PA)

    # ---- building
    tb = (twin or {}).get("building", {})
    defects = sorted({k.split(".")[1] for k, v in main.items() if k.startswith("roof-defects.") and
                      k.endswith(".detected") and v and k.count(".") == 2})
    building = {
        "footprint_sqft": fact(round(main.get("footprint.area") or 0) or None, "sqft", PA, parcel_date,
                               "Vexcel structure footprint polygon area (largest structure on the parcel)",
                               round(0.8 * conf_cap, 2), structures_on_parcel=len(sts)),
        "height_ft_dsm": (fact(tb.get("height_ft_dsm_p90"), "ft", "Vexcel DSM - DTM (exterior twin)", dsm_date,
                               "P90 of height above ground inside the main footprint (roof top incl. parapets/"
                               "equipment, not clear height)", round(0.7 * conf_cap, 2))
                          if tb.get("height_ft_dsm_p90") is not None else unknown("no DSM height measured", "ft")),
        "roof_condition": fact(main.get("roof-characteristics.condition"), "1-5", PA, parcel_date,
                               "Vexcel roof condition score, 1 (poor) to 5 (excellent)", round(0.6 * conf_cap, 2),
                               precise=main.get("roof-characteristics.condition.precise-value")),
        "roof_material": fact(main.get("roof-characteristics.material.class"), None, PA, parcel_date,
                              "Vexcel roof material class", round(0.5 * conf_cap, 2)),
        "roof_defects": fact(defects, None, PA, parcel_date, "Vexcel roof-defects.*.detected flags",
                             round(0.6 * conf_cap, 2)),
        "solar": fact(main.get("roof-objects.solar.value"), None, PA, parcel_date, "Vexcel roof-objects.solar",
                      round(0.6 * conf_cap, 2)),
        "ac_units": fact(main.get("roof-objects.ac-unit.count"), "count", PA, parcel_date,
                         "Vexcel roof-objects.ac-unit.count", round(0.6 * conf_cap, 2)),
        "skylights": fact(main.get("roof-objects.skylight.count"), "count", PA, parcel_date,
                          "Vexcel roof-objects.skylight.count", round(0.6 * conf_cap, 2)),
    }

    # ---- site
    site = {
        "parcel_acres": fact(round(parcel_m2 / 4046.86, 2), "acres", PA, parcel_date,
                             "Vexcel parcel polygon area in a local metric frame", round(0.7 * conf_cap, 2)),
        "paved_pct": (fact(paved_pct, "%", "Vexcel DSM - DTM + ortho (exterior twin)", dsm_date,
                           "paved cells / parcel cells", round(0.6 * conf_cap, 2))
                      if paved_pct is not None else unknown("no exterior twin", "%")),
        "tree_cover_pct": fact(pp.get("parcel-tree-cover.percent"), "%", PA, parcel_date,
                               "Vexcel parcel-tree-cover.percent", 0.6),
    }

    # ---- history
    env = parcel.envelope.wkt
    hist = {}
    try:
        cols = vx.collections("ortho", env)
        dates = sorted({c["last"] for c in cols if c.get("last")}) if isinstance(cols, list) else []
        hist["ortho_dates"] = dates
        hist["ortho_source"] = "Vexcel ortho/collections"
    except Exception as e:
        hist["ortho_dates"], hist["ortho_error"] = [], str(e)[:200]
    try:
        if twin:
            fr = twin["frame"]
            lat0, lon0, half = fr["lat0"], fr["lon0"], fr["half_m"]
        else:
            c = parcel.centroid
            lat0, lon0 = c.y, c.x
            half = min(250, max(120, math.sqrt(parcel_m2)))
        hist["change"] = _change(lat0, lon0, half, parcel)
    except Exception as e:
        hist["change"] = {"years": None, "new_structure_m2": None, "removed_m2": None, "note": f"failed: {e}"[:200]}

    # ---- nearby (point-based: stays valid even when the parcel is wrong)
    nearby = _nearby(w["lat"], w["lon"], parcel)
    if mismatch:
        for sec in (parking, building, site):
            _flag_low(sec)
        ch = hist.get("change") or {}
        ch.update({"confidence": 0.2, "confidence_label": "low", "parcel_mismatch": True,
                   "note": MISMATCH_NOTE + ". " + (ch.get("note") or "")})
        hist["parcel_mismatch"] = True
    elif pcheck["acres_note"]:
        for sec in (parking, site):
            _flag(sec, "medium", pcheck["acres_note"], 0.5)

    prof = {
        "id": pid, "version": VERSION, "address": w.get("address"), "market": w.get("market"),
        "lat": w["lat"], "lon": w["lon"],
        "captured": {"ortho": lot_img["date"], "dsm": dsm_date, "parcel_attrs": parcel_date},
        "parking": parking, "building": building, "site": site, "history": hist, "nearby": nearby,
        "parcel_check": pcheck, "parcel_mismatch": mismatch,
        "warnings": ([pcheck["note"]] if mismatch else []) + ([pcheck["acres_note"]] if pcheck["acres_note"] else []),
        "sources": {"parcel_attrs": PA, "ortho": f"Vexcel ortho ({lot_img['collection']})",
                    "dsm": f"Vexcel DSM ({tw_img.get('dsm')})" if dsm_date else None,
                    "embedding": EMBED_SOURCE, "detect": "Vexcel elements/detect (SAM3)"},
        "confidence": {"parcel_match": 0.3 if mismatch else 0.9,
                       "note": "Per-value confidence is on each fact; embedding hits are likely/possible only."},
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "compute_s": round(time.time() - t0, 1),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(prof, default=float))
    tmp.replace(path)
    return prof


def load(pid):
    p = OUT / f"{int(pid)}.json"
    if not p.exists():
        return None
    prof = json.loads(p.read_text())
    return prof if prof.get("version") == VERSION else None


def load_all():
    out = {}
    for p in OUT.glob("*.json"):
        if p.stem.isdigit():
            try:
                prof = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if prof.get("version") == VERSION and not prof.get("error"):
                out[int(p.stem)] = prof
    return out


# ------------------------------------------------------------------ batch
_status_lock = threading.Lock()


def batch(ids=None, force=False, workers=4, first=(12896757,)):
    cat = json.loads((BUILD / "catalog" / "warehouses.json").read_text())
    ids = [int(i) for i in (ids or [w["id"] for w in cat])]
    ids = [i for i in first if i in ids] + [i for i in ids if i not in first]
    status_p = OUT / "_batch.json"
    OUT.mkdir(parents=True, exist_ok=True)
    status = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "total": len(ids),
              "done": 0, "skipped": 0, "failed": {}, "elapsed_s": 0}
    t0 = time.time()

    def save():
        status["elapsed_s"] = round(time.time() - t0)
        status_p.write_text(json.dumps(status, indent=1))

    def one(pid):
        if not force and load(pid):
            with _status_lock:
                status["skipped"] += 1
                save()
            return
        try:
            build(pid, force=force)
            with _status_lock:
                status["done"] += 1
                save()
        except Exception as e:
            with _status_lock:
                status["failed"][str(pid)] = f"{type(e).__name__}: {e}"[:300]
                save()
            (OUT / "_errors.log").open("a").write(f"--- {pid}\n{traceback.format_exc()}\n")

    for pid in [i for i in first if i in ids]:      # the demo property first, alone
        one(pid)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, [i for i in ids if i not in first]))
    status["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()
    return status


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    ids = [a for a in args if a.isdigit()]
    st = batch(ids or None, force=force)
    print(json.dumps({k: v for k, v in st.items() if k != "failed"}), "failed:", len(st["failed"]))
