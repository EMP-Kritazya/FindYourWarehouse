"""Flood exposure for a warehouse, from measured elevation (Vexcel DTM) + the official FEMA flood zone.

Facts (each {value, unit, source, basis}):
    pad_ft        building pad above the surrounding ground: DTM 1-3 m outside the walls vs a 30-300 m ring (measured)
    lowest_ft     lowest ground within 10 m of the walls vs the pad: where water reaches the building first (measured)
    ponding       closed depressions >= 15 cm deep and >= 50 m2 within 60 m of the building (priority-flood sink fill on
                  the DTM): where rain water collects (measured)
    dock_wells_ft depth of the dock truck wells below the yard, at the dock-high doors of the dock store (measured)
    hand_ft       height of the pad above the nearest mapped waterway (OSM bayou / river / channel) within 1.6 km,
                  channel level = P10 of the DTM along it (measured; the channel line is OpenStreetMap)
    fema          FEMA NFHL flood zone at the building (record; via Esri Living Atlas' FEMA mirror, FEMA as fallback)

Indicator (deterministic, the rule that fired is returned): Higher / Moderate / Lower exposure, "partial" when inputs
are missing (never assumed safe). Always: not an insurance flood determination. Relative heights are used so the
DTM's vertical datum does not matter. Disabled with PROINSPECT_FLOOD=0.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
from pathlib import Path

import httpx
import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, MultiPoint, Point, Polygon

from proinspect.cache import cached
from proinspect.config import BUILD
from proinspect.tools import elevation as el

ENABLED = os.environ.get("PROINSPECT_FLOOD", "1") != "0"
OUT = BUILD / "flood"
EXT = BUILD / "twins" / "exterior"
M_TO_FT = 3.28084
NOTE = "Not an insurance flood determination: measured terrain + the FEMA map, for a first look."
ESRI_FEMA = ("https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Flood_Hazard_Reduced_Set_gdb/"
             "FeatureServer/0/query")
FEMA = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
HAND_RADIUS_M = 1600


def _code():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]


def _f(value, unit, source, basis):
    return {"value": value, "unit": unit, "source": source, "basis": basis}


# ------------------------------------------------------------------ FEMA zone
@cached("fema")
def _fema_query(url, lon, lat):
    r = httpx.get(url, timeout=25, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false", "f": "json",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DFIRM_ID"})
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(str(d["error"])[:120])
    return [f["attributes"] for f in d.get("features", [])]


def fema_zone(lat, lon):
    """{zone, subtype, sfha, bfe_ft, label, source_name} or {error}. No polygon in the mapped-hazard set = outside
    FEMA's mapped 1% / 0.2% flood areas (minimal hazard, Zone X unshaded), stated as such."""
    for url, name in ((ESRI_FEMA, "FEMA NFHL (Esri Living Atlas mirror)"), (FEMA, "FEMA NFHL")):
        try:
            feats = _fema_query(url, round(lon, 6), round(lat, 6))
        except Exception as e:                                      # noqa: BLE001
            err = f"{type(e).__name__}"
            continue
        if not feats:
            return {"zone": "X", "subtype": "outside mapped flood areas", "sfha": False, "bfe_ft": None,
                    "label": "Outside FEMA's mapped flood areas", "level": "minimal", "source_name": name}
        sf = [a for a in feats if a.get("SFHA_TF") == "T"]
        a = (sf or feats)[0]
        zone, sub = a.get("FLD_ZONE"), a.get("ZONE_SUBTY") or ""
        sfha = a.get("SFHA_TF") == "T"
        bfe = a.get("STATIC_BFE") if a.get("STATIC_BFE") not in (None, -9999) else None
        level = "sfha" if sfha else ("0.2pct" if "0.2" in sub else "minimal")
        label = (f"Zone {zone}" + (f", BFE {bfe:g} ft" if bfe else "") if sfha else
                 f"Zone {zone} (0.2% annual chance)" if level == "0.2pct" else f"Zone {zone}")
        return {"zone": zone, "subtype": sub, "sfha": sfha, "bfe_ft": bfe, "label": label, "level": level,
                "source_name": name}
    return {"error": f"FEMA flood map unavailable ({err})"}


# ------------------------------------------------------------------ terrain helpers
def _priority_flood(z):
    """Depression-filled surface (Barnes et al. priority flood): water spills out at the grid edge."""
    n0, n1 = z.shape
    filled = z.copy()
    seen = np.zeros(z.shape, bool)
    h = []
    for r in range(n0):
        for c in (0, n1 - 1):
            heapq.heappush(h, (float(z[r, c]), r, c)); seen[r, c] = True
    for c in range(1, n1 - 1):
        for r in (0, n0 - 1):
            heapq.heappush(h, (float(z[r, c]), r, c)); seen[r, c] = True
    while h:
        v, r, c = heapq.heappop(h)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < n0 and 0 <= cc < n1 and not seen[rr, cc]:
                seen[rr, cc] = True
                if filled[rr, cc] < v:
                    filled[rr, cc] = v
                heapq.heappush(h, (float(filled[rr, cc]), rr, cc))
    return filled


def _ring(F, poly, a, b):
    return F.raster([poly.buffer(b)]) & ~F.raster([poly.buffer(a)]) if a > 0 else F.raster([poly.buffer(b)])


def _block_mean(a, k):
    n0, n1 = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:n0, :n1].reshape(n0 // k, k, n1 // k, k).mean(axis=(1, 3))


# ------------------------------------------------------------------ main
def assess(pid, use_cache=True, compute=True):
    pid = int(pid)
    p = OUT / f"{pid}.json"
    if use_cache and p.exists():
        d = json.loads(p.read_text())
        if d.get("code") == _code():
            return d
    if not compute:
        return None
    from proinspect import racks
    from proinspect.twin.exterior import Frame
    w = racks._catalog(pid)
    facts, missing, layers = {}, [], {"ponding": [], "waterway": None}
    out = {"property_id": pid, "address": w.get("address"), "market": w.get("market"), "code": _code(), "note": NOTE}

    # FEMA (independent of the terrain)
    fz = fema_zone(w["lat"], w["lon"])
    if "error" in fz:
        missing.append("FEMA zone")
        facts["fema"] = _f(None, "", "unknown", fz["error"])
    else:
        facts["fema"] = _f(fz["label"], "", "recorded", f"{fz['source_name']} flood hazard zone at the CoStar point"
                           + (f" ({fz['subtype']})" if fz.get("subtype") else ""))
    out["fema"] = fz

    tp = EXT / f"{pid}.json"
    twin = json.loads(tp.read_text()) if tp.exists() else None
    if not twin:
        missing += ["pad height", "ponding", "dock wells", "height above waterway"]
        out.update({"facts": facts, "missing": missing, "layers": layers})
        return _finish(out, p)
    fr = twin["frame"]
    lat0, lon0, half = fr["lat0"], fr["lon0"], fr["half_m"]
    F = Frame(lat0, lon0, half)
    main = next((b for b in twin.get("buildings") or [] if b.get("main")), None) or {"footprint_local": twin["building"]["footprint_local"]}
    bld = Polygon(main["footprint_local"]).buffer(0)
    others = [Polygon(b["footprint_local"]).buffer(0) for b in twin.get("buildings") or [] if not b.get("main")]
    dtm, dmeta = el.ground(lat0, lon0, half, 0.5)                    # same grid as the twin: served from the cache
    if dtm is None:
        missing += ["pad height", "ponding", "dock wells"]
    else:
        dtm = dtm.astype(np.float32)
        bmask = F.raster([bld] + others)
        wall = _ring(F, bld, 1.0, 3.0) & ~bmask & np.isfinite(dtm)
        sur = _ring(F, bld, 30.0, 300.0) & ~bmask & np.isfinite(dtm)
        if wall.sum() > 20 and sur.sum() > 200:
            pad = float(np.nanmedian(dtm[wall]))
            surround = float(np.nanmedian(dtm[sur]))
            facts["pad_ft"] = _f(round((pad - surround) * M_TO_FT, 1), "ft", "measured",
                                 "ground at the building walls (1-3 m out) vs the median ground 30-300 m around it "
                                 f"(Vexcel DTM {dmeta.get('collection', '') if dmeta else ''})".strip())
            near = _ring(F, bld, 0.5, 10.0) & ~bmask & np.isfinite(dtm)
            facts["lowest_ft"] = _f(round((float(np.nanpercentile(dtm[near], 2)) - pad) * M_TO_FT, 1), "ft", "measured",
                                    "lowest ground within 10 m of the walls, relative to the pad (where water reaches "
                                    "the building first)")
            out["pad_elev_m"] = pad
        else:
            missing.append("pad height")
        # ponding: sink-fill on a 2 m grid, inside 60 m of the building, outside buildings
        k = 4
        z2 = _block_mean(np.nan_to_num(dtm, nan=float(np.nanmedian(dtm))), k)
        depth = _priority_flood(z2) - z2
        area = _block_mean(F.raster([bld.buffer(60)]).astype(np.float32), k) > 0.5
        area &= _block_mean(bmask.astype(np.float32), k) < 0.5
        wet = (depth >= 0.25) & area
        lab, n = ndimage.label(wet)
        spots = []
        cell = 0.5 * k
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            m = lab[sl] == i
            a_m2 = float(m.sum()) * cell * cell
            if a_m2 < 50:
                continue
            rr, cc = np.nonzero(m)
            rr, cc = rr + sl[0].start, cc + sl[1].start
            xs = -half + (cc + 0.5) * cell
            ys = half - (rr + 0.5) * cell
            hull = MultiPoint(np.c_[xs, ys]).convex_hull.buffer(cell / 2)
            ring = [el.to_latlon(x, y, lat0, lon0) for x, y in hull.exterior.coords] if hull.geom_type == "Polygon" else []
            spots.append({"area_m2": round(a_m2), "max_depth_m": round(float(depth[lab == i].max()), 2),
                          "polygon": [[round(lo, 7), round(la, 7)] for la, lo in ring]})
        spots.sort(key=lambda s: -s["area_m2"])
        layers["ponding"] = spots[:12]
        facts["ponding"] = _f(
            {"spots": len(spots), "area_sqft": round(sum(s["area_m2"] for s in spots) * 10.7639),
             "max_depth_ft": round(max((s["max_depth_m"] for s in spots), default=0) * M_TO_FT, 1)},
            "", "measured", "low spots >= 10 in deep and >= 540 SF within 200 ft of the building (sink fill on the "
                            "Vexcel DTM): where rain water collects first. Storm drains are not in the terrain model, so "
                            "these are low spots, not necessarily standing water")
        # dock wells: ground 2-4 m out from each dock-high door vs 25-35 m out (the yard)
        try:
            store = json.loads((BUILD / "docks" / f"{pid}.json").read_text())
            dips = []
            for d in store.get("doors") or []:
                if d.get("status") == "rejected" or d.get("kind") not in ("dock_high", "unknown"):
                    continue
                nx, ny = d["normal"]

                def at(o):
                    r, c = F.rc(d["x"] + nx * o, d["y"] + ny * o)
                    return float(dtm[r, c]) if 0 <= r < dtm.shape[0] and 0 <= c < dtm.shape[1] else float("nan")
                well = np.nanmean([at(o) for o in (2.0, 3.0, 4.0)])
                yard = np.nanmean([at(o) for o in (25.0, 30.0, 35.0)])
                if np.isfinite(well) and np.isfinite(yard):
                    dips.append(yard - well)
            if dips:
                q = float(np.percentile(np.array(dips), 75)) * M_TO_FT
                resolved = q >= 0.5
                facts["dock_wells_ft"] = _f(round(q, 1) if resolved else None, "ft", "measured" if resolved else "unknown",
                                            (f"truck wells below the yard at {len(dips)} dock doors (upper quartile; "
                                             "Vexcel DTM 2-4 m vs 25-35 m out from each door)") if resolved else
                                            (f"not resolved at {len(dips)} dock doors: the Vexcel ground model smooths "
                                             "over truck wells"))
        except FileNotFoundError:
            pass

    # height above the nearest mapped waterway (HAND)
    try:
        _hand(w, bld, lat0, lon0, facts, layers, out)
    except Exception as e:                                          # noqa: BLE001
        facts["hand_ft"] = _f(None, "ft", "unknown", f"could not compute ({type(e).__name__})")
    if facts.get("hand_ft", {}).get("value") is None:
        missing.append("height above waterway")
    out.update({"facts": facts, "missing": missing, "layers": layers})
    return _finish(out, p)


def _hand(w, bld, lat0, lon0, facts, layers, out):
    from proinspect.yard import roads
    c = bld.centroid
    clat, clon = el.to_latlon(c.x, c.y, lat0, lon0)
    ways = roads.waterways(round(clat, 5), round(clon, 5), HAND_RADIUS_M + 100)
    best = None
    for wy in ways:
        line = LineString([el.to_local(la, lo, clat, clon) for la, lo in wy["nodes"]])
        dist = line.distance(Point(0, 0))
        if dist <= HAND_RADIUS_M and (best is None or dist < best[0]):
            best = (dist, line, wy)
    if not best:
        facts["hand_ft"] = _f(None, "ft", "unknown", f"no mapped waterway within {HAND_RADIUS_M / 1609:.1f} mi (OpenStreetMap)")
        return
    dist, line, wy = best
    gh = min(max(dist + 300, 500), 1700)
    try:
        dtm, _ = el.ground(round(clat, 6), round(clon, 6), gh, 2.0)
    except Exception:                                               # noqa: BLE001  (a slow extract: retry coarser)
        dtm, _ = el.ground(round(clat, 6), round(clon, 6), gh, 4.0)
        gh_res = 4.0
    else:
        gh_res = 2.0
    if dtm is None:
        facts["hand_ft"] = _f(None, "ft", "unknown", "no Vexcel DTM over the waterway")
        return
    n = dtm.shape[0]

    def zat(x, y):
        col, row = int((x + gh) / gh_res), int((gh - y) / gh_res)
        return float(dtm[row, col]) if 0 <= row < n and 0 <= col < n else float("nan")
    near_pt = line.interpolate(line.project(Point(0, 0)))
    s0 = line.project(near_pt)
    chan = [zat(*line.interpolate(s).coords[0]) for s in np.arange(max(0, s0 - 400), min(line.length, s0 + 400), 8.0)]
    chan = [v for v in chan if np.isfinite(v)]
    bl = Polygon([el.to_local(*el.to_latlon(x, y, lat0, lon0), clat, clon) for x, y in bld.exterior.coords])
    wallz = [zat(*bl.exterior.interpolate(s).coords[0]) for s in np.arange(0, bl.exterior.length, 10.0)]
    # the wall ring itself can read roof on a giant flat roof: use points 2 m outside the walls
    out_ring = bl.buffer(2.0).exterior
    wallz = [zat(*out_ring.interpolate(s).coords[0]) for s in np.arange(0, out_ring.length, 10.0)]
    wallz = [v for v in wallz if np.isfinite(v)]
    if len(chan) < 5 or len(wallz) < 5:
        facts["hand_ft"] = _f(None, "ft", "unknown", "too few DTM samples along the channel")
        return
    chan_z, pad_z = float(np.percentile(chan, 10)), float(np.median(wallz))
    name = wy.get("name") or f"an unnamed {wy.get('kind') or 'waterway'}"
    facts["hand_ft"] = _f(round((pad_z - chan_z) * M_TO_FT, 1), "ft", "measured",
                          f"pad vs the low line of {name}, {dist / 1609:.2f} mi away (Vexcel DTM; channel from "
                          "OpenStreetMap)")
    out["waterway"] = {"name": name, "kind": wy.get("kind"), "distance_mi": round(dist / 1609, 2)}
    # waterway line for the map (the 1.6 km around the nearest point)
    seg = [line.interpolate(s).coords[0] for s in np.arange(max(0, s0 - 800), min(line.length, s0 + 800), 20.0)]
    layers["waterway"] = [[round(lo, 7), round(la, 7)] for la, lo in (el.to_latlon(x, y, clat, clon) for x, y in seg)]
    # cross-section: from the channel through the building and 150 m beyond
    v = np.array([0 - near_pt.x, 0 - near_pt.y])
    L = float(np.hypot(*v)) or 1.0
    u = v / L
    reach = L + max(bl.bounds[2] - bl.bounds[0], bl.bounds[3] - bl.bounds[1]) / 2 + 150
    prof, span = [], []
    for s in np.arange(-60, reach, 5.0):
        x, y = near_pt.x + u[0] * s, near_pt.y + u[1] * s
        z = zat(x, y)
        inside = bl.contains(Point(x, y))
        if inside:
            span.append(s)
            z = pad_z
        if np.isfinite(z):
            prof.append([round(float(s), 1), round((z - chan_z) * M_TO_FT, 2)])
    out["profile"] = {"points": prof, "building": [min(span), max(span)] if span else None,
                      "pad_ft": round((pad_z - chan_z) * M_TO_FT, 1), "channel": name,
                      "note": "terrain along a line from the channel through the building (Vexcel DTM; heights in ft "
                              "above the channel's low line; the DTM may read a very large flat roof as ground)"}


def _finish(out, path):
    f = out["facts"]
    fema = out.get("fema") or {}
    pad = (f.get("pad_ft") or {}).get("value")
    hand = (f.get("hand_ft") or {}).get("value")
    wells = (f.get("dock_wells_ft") or {}).get("value")
    pond = ((f.get("ponding") or {}).get("value") or {}).get("spots", 0)
    hi, mod = [], []
    if fema.get("sfha"):
        hi.append(f"in FEMA's 1% annual-chance flood area ({fema.get('label')})")
    wname = out.get("waterway", {}).get("name", "the nearest waterway")
    if pad is not None and pad < 0.5 and hand is not None and hand < 15:
        hi.append(f"at grade (pad {pad:g} ft above the surroundings) and only {hand:g} ft above {wname}")
    if hand is not None and hand < 5:
        hi.append(f"only {hand:g} ft above {out.get('waterway', {}).get('name', 'the nearest waterway')}")
    if fema.get("level") == "0.2pct":
        mod.append("in FEMA's 0.2% annual-chance flood area")
    if pad is not None and pad < 1 and not hi:
        mod.append(f"at grade: the pad sits only {pad:g} ft above the surrounding ground")
    elif pad is not None and 1 <= pad < 3:
        mod.append(f"pad {pad:g} ft above the surroundings")
    if hand is not None and 5 <= hand < 15 and not hi:
        mod.append(f"{hand:g} ft above {out.get('waterway', {}).get('name', 'the nearest waterway')}")
    if wells is not None and wells > 3 and pond:
        mod.append(f"dock wells {wells:g} ft below the yard with ponding nearby")
    level = "higher" if hi else "moderate" if mod else "lower"
    partial = bool(out.get("missing"))
    if level == "lower" and ("FEMA zone" in out.get("missing", []) and "height above waterway" in out.get("missing", [])):
        level = "unclear"
    why = hi or mod or ([f"outside FEMA's mapped flood areas" if fema.get("level") == "minimal" else None,
                         f"pad {pad:g} ft above the surroundings" if pad is not None else None,
                         f"{hand:g} ft above {out.get('waterway', {}).get('name')}" if hand is not None else None])
    out["indicator"] = {"level": level, "partial": partial, "why": [x for x in why if x],
                        "label": {"higher": "Higher exposure", "moderate": "Moderate exposure", "lower": "Lower exposure",
                                  "unclear": "Not enough data"}[level]}
    out["status"] = "computed"
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, default=float))
    return out
