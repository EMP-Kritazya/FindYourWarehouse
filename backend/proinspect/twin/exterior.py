"""Exterior twin: what Vexcel says about a warehouse's outside. Method: docs/research/measurement.md §1.

    measure(property_id) -> dict with
      building      main footprint, roof (condition, material, defects, rooftop units), DSM building height
      doors         per facade: deduplicated doors from all 4 obliques, each with metric size (from the camera
                    model) and a dock-high / drive-in call with the cues behind it
      courts        per dock facade: truck court depth from rays cast over a paved mask (median, P10, P90, why each
                    ray stopped), shared-court flag, 53' trailer verdict
      vehicles      trucks / trailers / cars seen on the ortho, trailers parked at the docks

Frame: local metres east (x) / north (y) of the main footprint's centroid. Grids are 0.5 m, row 0 = north.
Every number carries how it was measured; nothing is tuned to CoStar here (see proinspect.validate).
"""
import base64
import io
import json
import math
from collections import Counter

import numpy as np
from matplotlib.path import Path as MplPath
from PIL import Image
from scipy import ndimage
from shapely import wkt as shp_wkt
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from proinspect.config import BUILD
from proinspect.tools import elevation as el
from proinspect.tools import vexcel as vx

M_TO_FT = 3.28084
RES = 0.5
VIEW_DIR = {"oblique-north": (0, 1), "oblique-south": (0, -1), "oblique-east": (1, 0), "oblique-west": (-1, 0)}
DOOR_LABELS = ["loading dock door", "overhead garage door", "roll-up door"]
VEHICLE_LABELS = ["semi trailer", "truck", "car"]


# ------------------------------------------------------------------ frames and rasters
class Frame:
    """Local metric frame around (lat0, lon0) and a square raster of half-size `half` at RES."""

    def __init__(self, lat0, lon0, half):
        self.lat0, self.lon0, self.half = lat0, lon0, half
        self.n = int(round(2 * half / RES))

    def xy(self, lon, lat):
        x, y = el.to_local(lat, lon, self.lat0, self.lon0)
        return float(x), float(y)

    def geom(self, g):
        """lon/lat shapely geometry -> local metres."""
        from shapely.ops import transform
        return transform(lambda lon, lat, z=None: el.to_local(lat, lon, self.lat0, self.lon0), g)

    def rc(self, x, y):
        return int((self.half - y) / RES), int((x + self.half) / RES)

    def raster(self, polys):
        """Boolean raster of cells whose centres fall in any polygon (local metres)."""
        m = np.zeros((self.n, self.n), bool)
        xs = -self.half + (np.arange(self.n) + 0.5) * RES
        ys = self.half - (np.arange(self.n) + 0.5) * RES
        X, Y = np.meshgrid(xs, ys)
        pts = np.c_[X.ravel(), Y.ravel()]
        for p in polys:
            if p.is_empty:
                continue
            for part in getattr(p, "geoms", [p]):
                if part.geom_type != "Polygon":
                    continue
                minx, miny, maxx, maxy = part.bounds
                sel = (pts[:, 0] >= minx) & (pts[:, 0] <= maxx) & (pts[:, 1] >= miny) & (pts[:, 1] <= maxy)
                if sel.any():
                    inside = MplPath(np.asarray(part.exterior.coords)).contains_points(pts[sel])
                    m.ravel()[np.flatnonzero(sel)[inside]] = True
        return m


def _sqft_ll(g, lat0, lon0):
    """Area in SF of a lon/lat geometry (local metres around lat0/lon0)."""
    from shapely.ops import transform
    return transform(lambda lon, lat, z=None: el.to_local(lat, lon, lat0, lon0), g).area * 10.7639


def _osm_building_ll(w):
    """The CoStar building's outline (lon/lat) from OpenStreetMap: under or within 60 m of CoStar's point, with
    footprint x stories 0.6-1.6x CoStar's size. None when OSM has no such building (or is unreachable)."""
    from shapely.ops import transform
    from proinspect.yard import roads
    sf = w.get("rba_sqft") or w.get("gba_sqft")
    st = w.get("stories") or 1
    try:
        osm = roads.buildings(round(w["lat"], 5), round(w["lon"], 5), 450)
    except Exception:                                            # noqa: BLE001
        return None
    pt = Point(0, 0)
    best = None
    for b in osm:
        ll = Polygon([(lo, la) for la, lo in b["nodes"]])
        if not ll.is_valid:
            ll = ll.buffer(0)
        if ll.geom_type != "Polygon":
            continue
        loc = transform(lambda lon, lat, z=None: el.to_local(lat, lon, w["lat"], w["lon"]), ll)
        sqft = loc.area * 10.7639
        if sf and not (0.6 <= sqft * st / sf <= 1.6):
            continue
        d = loc.distance(pt)
        if d <= 60 and (best is None or d < best[0]):
            best = (d, ll, b["id"])
    return (best[1], best[2]) if best else None


def _site(property_id):
    cat = {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}
    w = cat[property_id]
    ext = vx.property_extract(w["lat"], w["lon"])
    if not ext:
        raise ValueError(f"no Vexcel parcel at property {property_id}")
    feat = ext["features"][0]
    parcel = shp_wkt.loads(vx.to_wkt(feat["geometry"]))
    sts = feat["properties"].get("structures", [])
    costar_sf = w.get("rba_sqft") or w.get("gba_sqft")
    seen = sum(_sqft_ll(shp_wkt.loads(s["footprint.wkt"]), w["lat"], w["lon"]) for s in sts if s.get("footprint.wkt"))
    if not (costar_sf and seen < 0.3 * costar_sf):
        return w, feat["properties"], parcel, sts
    # The parcel at CoStar's point holds other buildings (CoStar's point can sit on a neighbouring lot). Find the real
    # building in OpenStreetMap, then ask Vexcel for the parcel at ITS centre; fall back to the OSM outline itself.
    hit = _osm_building_ll(w)
    if not hit:
        return w, feat["properties"], parcel, sts
    osm, oid = hit
    c = osm.representative_point()
    ext2 = vx.property_extract(c.y, c.x)
    if ext2:
        f2 = ext2["features"][0]
        sts2 = f2["properties"].get("structures", [])
        seen2 = sum(_sqft_ll(shp_wkt.loads(s["footprint.wkt"]), c.y, c.x) for s in sts2 if s.get("footprint.wkt"))
        if seen2 >= 0.6 * costar_sf:
            props = {**f2["properties"], "_site_note": f"parcel found at the building's centre (OpenStreetMap way {oid}): "
                                                       "the Vexcel parcel at CoStar's point held other buildings"}
            return w, props, shp_wkt.loads(vx.to_wkt(f2["geometry"])), sts2
    sqft = _sqft_ll(osm, c.y, c.x)
    d = 40 / 111_320                                     # no matching Vexcel parcel: the outline + a 40 m apron
    par = shp_wkt.loads(vx.to_wkt(ext2["features"][0]["geometry"])) if ext2 else None
    # the property must hold the whole building + an apron (a Vexcel parcel at the centre can cover only part of it)
    par = osm.buffer(d) if par is None or not par.contains(c) else unary_union([par, osm.buffer(d)])
    if par.geom_type == "MultiPolygon":
        par = max(par.geoms, key=lambda g: g.area)
    props = {**(ext2["features"][0]["properties"] if ext2 else feat["properties"]),
             "_site_note": f"footprint from OpenStreetMap way {oid}: the Vexcel parcel at CoStar's point held other buildings"}
    return w, props, par, [{"footprint.wkt": osm.wkt, "footprint.area": sqft, "_source": "osm"}]


def _heights(fr):
    """nDSM (height above bare ground) on the frame grid, with the dates of both models."""
    picks = el.pick_collections(fr.lat0, fr.lon0, product="dsm")
    if not picks:
        return None, {}
    c = picks[0]
    dsm, _ = el.grid(fr.lat0, fr.lon0, fr.half, RES, c["collection"], c["layer"], "dsm")
    dtm, dmeta = el.ground(fr.lat0, fr.lon0, fr.half, RES)
    nd = dsm - dtm
    return nd, {"dsm": c["collection"], "dsm_date": c["last"], "dtm": dmeta.get("collection") if dmeta else None,
                "dtm_raw": dtm}


def _ortho(fr):
    img = vx.ortho(vx.square_around(fr.lat0, fr.lon0, fr.half), 0, None, f"{fr.n},{fr.n}")
    a = np.asarray(Image.open(io.BytesIO(img)).convert("RGB").resize((fr.n, fr.n)), dtype=np.float32)
    return a, img


VEHICLE_TILE_HALF_M = 140      # over a bigger window SAM3 sees trailers as a few pixels and misses most of them


def _vehicles(fr):
    """SAM3 vehicles on the ortho -> local polygons by label (transparent on the court). Large windows (1M+ SF sites)
    are tiled so a trailer stays big enough to detect; detections on tile seams are de-duplicated."""
    n = max(1, math.ceil(fr.half / VEHICLE_TILE_HALF_M))
    step = 2 * fr.half / n
    out = {l: [] for l in VEHICLE_LABELS}
    for i in range(n):
        for j in range(n):
            ox, oy = -fr.half + step * (i + 0.5), -fr.half + step * (j + 0.5)
            lat = fr.lat0 + oy / 110_574
            lon = fr.lon0 + ox / (111_320 * math.cos(math.radians(fr.lat0)))
            res = vx.detect_raw(vx.square_around(lat, lon, step / 2), VEHICLE_LABELS, "ortho", 0.4)
            for f in res.get("features", []):
                for d in f["properties"]["detections"]:
                    g = vx.mask_to_wkt(d["pixel-mask"], d["bbox"], "ortho") if d.get("pixel-mask") else d["bbox"]
                    poly = fr.geom(shp_wkt.loads(g or d["bbox"]))
                    lab = f["properties"]["label"]
                    if any(v["poly"].centroid.distance(poly.centroid) < 1.5 for v in out[lab]):
                        continue                                  # the same vehicle seen from two tiles
                    out[lab].append({"poly": poly, "conf": round(d["confidence"], 2)})
    return out


# ------------------------------------------------------------------ doors
def _edges(fp, bldg=0):
    """Footprint edges (>= 4 m) with outward unit normals, tagged with their building index."""
    out = []
    pts = list(fp.exterior.coords)
    for a, b in zip(pts[:-1], pts[1:]):
        L = math.dist(a, b)
        if L < 4:
            continue
        tx, ty = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        nx, ny = ty, -tx
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if fp.contains(Point(mid[0] + nx, mid[1] + ny)):
            nx, ny = -nx, -ny
        out.append({"bldg": bldg, "a": a, "b": b, "len": L, "t": (tx, ty), "n": (nx, ny),
                    "facing": "NESW"[int(((math.degrees(math.atan2(nx, ny)) + 45) % 360) // 90)]})
    return out


def response_scale(meta):
    """Native camera pixels per returned pixel. Vexcel caps the returned crop at 2048 px on its long side, so a large
    crop comes back downsampled (metadata `pixel-bbox` = the native crop, `width`/`height` = what was returned), and a
    detection's `pixel-bbox` is in RETURNED pixels. The native GSD applies to native pixels only."""
    try:
        _, _, nw, nh = (float(v) for v in str(meta.get("pixel-bbox")).split(","))
        sx = nw / float(meta["width"]) if meta.get("width") else 1.0
        sy = nh / float(meta["height"]) if meta.get("height") else sx
        return max(sx, 1.0), max(sy, 1.0)
    except (TypeError, ValueError, KeyError):
        return 1.0, 1.0


def _door_metrics(d, meta):
    """Door width / height in metres from the pixel bbox and the oblique camera model (45 deg nominal).
    Pixels are converted from returned to native pixels first (response_scale), then times the native GSD."""
    gsd = (meta.get("min-gsd", 0.066) + meta.get("max-gsd", 0.066)) / 2
    tilt = math.acos(max(-1, min(1, math.cos(meta.get("omega", 0)) * math.cos(meta.get("phi", 0.785)))))
    sx, sy = response_scale(meta)
    _, _, pw, ph = d["pixel-bbox"]
    return pw * sx * gsd, ph * sy * gsd / max(math.sin(tilt), 0.3), math.degrees(tilt)


def _classify(width_m, height_m, well_dip_m):
    """Dock-high vs drive-in from independent cues; returns (class, cues). 'uncertain' when cues disagree."""
    w_ft, h_ft = width_m * M_TO_FT, height_m * M_TO_FT
    votes, cues = [], []
    # SAM3 boxes include the dock seal / shelter (a 9 ft dock door reads ~11-12.5 ft wide), so width alone is not a
    # drive-in cue; height is (drive-ins are typically 14-16 ft tall). Between the windows: no vote ("uncertain").
    if h_ft >= 13 or w_ft >= 13.5:
        votes.append("drive_in")
        cues.append(f"large door ({w_ft:.0f}x{h_ft:.0f} ft)")
    elif 7 <= w_ft <= 12.5 and 7 <= h_ft <= 11.5:
        votes.append("dock_high")
        cues.append(f"dock-door size incl. seal ({w_ft:.0f}x{h_ft:.0f} ft)")
    if well_dip_m is not None and well_dip_m >= 0.8:
        votes.append("dock_high")
        cues.append(f"truck well: ground dips {well_dip_m:.1f} m toward the wall")
    c = Counter(votes)
    if not c:
        return "uncertain", cues or ["no decisive cue"]
    if len(c) > 1:
        return "uncertain", cues + ["cues disagree"]
    return c.most_common(1)[0][0], cues


def _doors(fr, area, edges, dtm):
    """Doors from all 4 obliques, attached to the facade facing each camera, merged across views.
    `area` is the local-metre region to search (the warehouse buildings plus a margin)."""
    from shapely.ops import transform
    wkt_ll = transform(lambda x, y, z=None: el.to_latlon(x, y, fr.lat0, fr.lon0)[::-1], area).wkt
    views = vx.oblique_directions(wkt_ll)
    raw = []
    for view in views:
        res = vx.detect_raw(wkt_ll, DOOR_LABELS, view, 0.35)
        meta = (res.get("images") or [{}])[0].get("metadata", {})
        vd = VIEW_DIR[view]
        for f in res.get("features", []):
            for d in f["properties"]["detections"]:
                c = fr.geom(shp_wkt.loads(d["bbox"])).centroid
                best = None
                for i, e in enumerate(edges):
                    if e["n"][0] * -vd[0] + e["n"][1] * -vd[1] < 0.5:     # this camera can't see that facade
                        continue
                    dist = LineString([e["a"], e["b"]]).distance(c)
                    if dist < 12 and (best is None or dist < best[1]):
                        best = (i, dist)
                if best is None:
                    continue
                e = edges[best[0]]
                s = (c.x - e["a"][0]) * e["t"][0] + (c.y - e["a"][1]) * e["t"][1]
                if not -1 <= s <= e["len"] + 1:
                    continue
                w_m, h_m, tilt = _door_metrics(d, meta)
                off = (c.x - e["a"][0]) * e["n"][0] + (c.y - e["a"][1]) * e["n"][1]
                raw.append({"edge": best[0], "s": s, "view": view, "label": f["properties"]["label"],
                            "conf": d["confidence"], "w": w_m, "h": h_m, "tilt": tilt, "off": off,
                            "scale": round(response_scale(meta)[0], 2), "date": meta.get("capture-date")})
    doors = []
    for ei in sorted({r["edge"] for r in raw}):
        rs = sorted((r for r in raw if r["edge"] == ei), key=lambda r: r["s"])
        groups, cur = [], [rs[0]]
        for r in rs[1:]:
            if r["s"] - cur[-1]["s"] <= 2.5:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
        e = edges[ei]
        for g in groups:
            s = float(np.mean([r["s"] for r in g]))
            w, h = float(np.median([r["w"] for r in g])), float(np.median([r["h"] for r in g]))
            px, py = e["a"][0] + e["t"][0] * s, e["a"][1] + e["t"][1] * s
            dip = _well_dip(fr, dtm, px, py, e["n"])
            cls, cues = _classify(w, h, dip)
            doors.append({"edge": ei, "building": e["bldg"], "facing": e["facing"], "s_m": round(s, 1), "x": round(px, 1),
                          "y": round(py, 1), "width_ft": round(w * M_TO_FT, 1), "height_ft": round(h * M_TO_FT, 1),
                          "class": cls, "cues": cues, "views": sorted({r["view"] for r in g}),
                          "confidence": round(max(r["conf"] for r in g), 2), "seen": len(g),
                          "offset_m": round(float(np.median([r["off"] for r in g])), 2),
                          "px_scale": max(r["scale"] for r in g)})
    return doors, views


def _well_dip(fr, dtm, x, y, n):
    """Ground at 2 m from the wall minus ground 15-20 m out (a truck well dips toward the wall)."""
    if dtm is None:
        return None

    def z(d):
        r, c = fr.rc(x + n[0] * d, y + n[1] * d)
        return float(dtm[r, c]) if 0 <= r < fr.n and 0 <= c < fr.n and np.isfinite(dtm[r, c]) else None
    near, far = z(2.0), [z(d) for d in (15, 17.5, 20)]
    far = [v for v in far if v is not None]
    if near is None or not far:
        return None
    return round(float(np.median(far) - near), 2)


# ------------------------------------------------------------------ truck court
def _court(fr, e, doors_on_edge, nd, paved, vehicle, others, parcel_m):
    """Rays every 2 m along the dock span, marching outward until the court ends."""
    s0 = max(min(d["s_m"] for d in doors_on_edge) - 3, 0)
    s1 = min(max(d["s_m"] for d in doors_on_edge) + 3, e["len"])
    rays = []
    for s in np.arange(s0, s1 + 0.01, 2.0):
        bx, by = e["a"][0] + e["t"][0] * s, e["a"][1] + e["t"][1] * s
        run, stop, dist = 0.0, "max_80m", 80.0
        soft = None
        for k in range(1, int(80 / 0.25)):
            d = k * 0.25
            x, y = bx + e["n"][0] * d, by + e["n"][1] * d
            r, c = fr.rc(x, y)
            if not (0 <= r < fr.n and 0 <= c < fr.n):
                stop, dist = "grid_edge", d
                break
            if soft is None and parcel_m is not None and not parcel_m[r, c]:
                soft = d                                    # left the parcel: drives often continue past it
            if others[r, c]:
                stop, dist = "building", d
                break
            h = nd[r, c] if nd is not None else 0.0
            if d > 5 and np.isfinite(h) and h > 1.0 and not vehicle[r, c]:
                stop, dist = "obstacle", d                  # first 5 m: dock canopies / roof overhang are allowed
                break
            if not paved[r, c] and not vehicle[r, c]:
                run += 0.25
                if run >= 3.0:
                    stop, dist = "landscape", d - run
                    break
            else:
                run = 0.0
        rays.append({"s": round(float(s), 1), "depth_m": round(dist, 2), "stop": stop,
                     "left_parcel_at_m": soft, "start": [round(bx, 1), round(by, 1)],
                     "end": [round(bx + e["n"][0] * dist, 1), round(by + e["n"][1] * dist, 1)]})
    dep = np.array([r["depth_m"] for r in rays])
    med = float(np.median(dep))
    iqr = float(np.percentile(dep, 75) - np.percentile(dep, 25))
    stops = Counter(r["stop"] for r in rays)
    hard = (stops["landscape"] + stops["obstacle"] + stops["building"]) / len(rays)
    ft = med * M_TO_FT
    conf = "high" if iqr / max(med, 1) < 0.15 and hard >= 0.7 else "medium" if hard >= 0.4 else "low"
    return {"edge": doors_on_edge[0]["edge"], "facing": e["facing"], "doors": len(doors_on_edge),
            "depth_ft": round(ft), "p10_ft": round(float(np.percentile(dep, 10)) * M_TO_FT),
            "p90_ft": round(float(np.percentile(dep, 90)) * M_TO_FT), "rays": len(rays), "stops": dict(stops),
            "shared_with_building": stops["building"] / len(rays) >= 0.5,
            "half_depth_ft": round(ft / 2) if stops["building"] / len(rays) >= 0.5 else None,
            "past_parcel_line": sum(r["left_parcel_at_m"] is not None and r["left_parcel_at_m"] < r["depth_m"]
                                    for r in rays),
            "confidence": conf,
            "verdict_53ft": "full 53' maneuvering (≥130 ft)" if ft >= 130 else
            "workable for 53' (120-130 ft)" if ft >= 120 else "tight for 53' (110-120 ft)" if ft >= 110 else
            "too shallow for 53' trailers (<110 ft)",
            "ray_detail": rays}


# ------------------------------------------------------------------ the measurement
def _fix_swallowed_roof(fr, fps, nd, dtm, hmeta):
    """On very large flat roofs (1M+ SF) the bare-ground model can take the roof for ground: the building then reads
    ~0 m tall and the strip outside its walls looks like neither ground nor building ('unpaved' in the yard grid).
    When the footprint's median height is under 2 m (a warehouse is 6 m+), rebuild the ground as a plane fitted to the
    lower 40% of the surface model around the buildings (sites here are flat) and recompute the heights."""
    inside = fr.raster(fps)
    if inside.sum() < 1000 or float(np.nanmedian(nd[inside])) >= 2.0:
        return nd, dtm
    dsm = nd + dtm
    rr, cc = np.nonzero(~fr.raster([f.buffer(3) for f in fps]) & np.isfinite(dsm))
    if len(rr) < 1000:
        return nd, dtm
    pick = np.random.default_rng(0).choice(len(rr), min(len(rr), 200_000), replace=False)
    rr, cc = rr[pick], cc[pick]
    z = dsm[rr, cc]
    low = z <= np.nanpercentile(z, 40)
    A = np.c_[cc[low], rr[low], np.ones(int(low.sum()))].astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, z[low].astype(np.float64), rcond=None)
    R, C = np.mgrid[0:dsm.shape[0], 0:dsm.shape[1]]
    ground = (coef[0] * C + coef[1] * R + coef[2]).astype(np.float32)
    nd2 = dsm - ground
    hmeta["dtm_note"] = (f"the bare-ground model took the roof for ground (building read "
                         f"{float(np.nanmedian(nd[inside])):.1f} m tall); ground refitted as a plane from the surface "
                         f"model around the building (now {float(np.nanmedian(nd2[inside])):.1f} m)")
    return nd2, ground


def _snap_to_roof(fr, poly, nd):
    """Fit an outline to the roof the surface model measures: cells above half the outline's median height, in the
    connected blob that overlaps the outline most. A rectangle-like outline (<= 6 corners) becomes the blob's minimum
    rotated rectangle; otherwise the outline is shifted by the blob's centroid offset. None when the fit is not
    plausible (area outside 0.8-1.3x the outline's)."""
    from shapely.geometry import MultiPoint
    inside = fr.raster([poly])
    if inside.sum() < 500:
        return None
    h = float(np.nanmedian(nd[inside]))
    if h < 4:
        return None
    tall = np.nan_to_num(nd) > max(6.0, 0.5 * h)                   # trailers (~4 m) and cars stay out
    tall = ndimage.binary_opening(tall, iterations=2)
    lab, n = ndimage.label(tall)
    if not n:
        return None
    hits = np.bincount(lab[inside & tall], minlength=n + 1)
    k = int(np.argmax(hits[1:])) + 1
    blob = lab == k
    area_ratio = blob.sum() / inside.sum()
    if not 0.8 <= area_ratio <= 1.3:
        return None
    rr, cc = np.nonzero(blob)
    xs = -fr.half + (cc + 0.5) * RES
    ys = fr.half - (rr + 0.5) * RES
    if len(poly.exterior.coords) <= 7:
        pts = MultiPoint(np.c_[xs, ys][::7]).convex_hull
        return pts.minimum_rotated_rectangle
    from shapely.affinity import translate
    return translate(poly, xs.mean() - poly.centroid.x, ys.mean() - poly.centroid.y)


def measure(property_id, save=True):
    w, pprops, parcel_ll, sts = _site(property_id)
    if not sts:
        raise ValueError(f"no structures in Vexcel parcel for {property_id}")
    foot_ll = [shp_wkt.loads(s["footprint.wkt"]) for s in sts]
    main_i = int(np.argmax([s.get("footprint.area") or 0 for s in sts]))
    c = foot_ll[main_i].centroid
    minx, miny, maxx, maxy = parcel_ll.bounds
    reach = max(math.hypot((x - c.x) * 111_320 * math.cos(math.radians(c.y)), (y - c.y) * 110_574)
                for x in (minx, maxx) for y in (miny, maxy))
    fr = Frame(c.y, c.x, min(max(reach + 40, 120), 420))     # up to 840 m across: 1M+ SF buildings fit
    fps = [fr.geom(f) for f in foot_ll]
    fp = fps[main_i]
    parcel = fr.geom(parcel_ll)

    nd, hmeta = _heights(fr)
    dtm = hmeta.pop("dtm_raw", None)
    if nd is not None and dtm is not None:
        nd, dtm = _fix_swallowed_roof(fr, fps, nd, dtm, hmeta)
    if nd is not None and sts[main_i].get("_source") == "osm":   # OSM outlines can sit metres off the real roof edge
        snapped = _snap_to_roof(fr, fps[main_i], nd)
        if snapped is not None:
            fps[main_i], fp = snapped, snapped
            hmeta["footprint_note"] = "OpenStreetMap outline snapped to the roof measured in the Vexcel surface model"
    if nd is not None:                                     # vertical datum: ground cells near 0
        outside = ~fr.raster(fps) & fr.raster([parcel])
        if outside.sum() > 100:
            nd = nd - float(np.nanpercentile(nd[outside], 20))
    rgb, ortho_jpg = _ortho(fr)
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    exg = (2 * G - R - B) / np.maximum(R + G + B, 1)
    veh = _vehicles(fr)
    vehicle = fr.raster([v["poly"] for vs in veh.values() for v in vs])
    vegetation = exg > 0.06
    low = (nd < 0.5) if nd is not None else np.ones_like(vegetation)
    paved = ndimage.binary_closing(low & ~vegetation, iterations=2)
    others = fr.raster([f for i, f in enumerate(fps) if i != main_i])
    parcel_m = fr.raster([parcel])

    # every warehouse-sized building on the parcel (>= 3,000 SF) gets its facades checked
    sig = [i for i, f in enumerate(fps) if f.area * 10.7639 >= 3000] or [main_i]
    edges = [e for i in sig for e in _edges(fps[i], i)]
    doors, views = _doors(fr, unary_union([fps[i] for i in sig]).buffer(15).convex_hull, edges, dtm)
    other_rasters = {i: fr.raster([f for j, f in enumerate(fps) if j != i]) for i in sig}
    courts = []
    for ei in sorted({d["edge"] for d in doors}):
        on = [d for d in doors if d["edge"] == ei]
        e = edges[ei]
        c = _court(fr, e, on, nd, paved, vehicle, other_rasters[e["bldg"]], parcel_m)
        c["building"] = e["bldg"]
        c["kind"] = "dock court" if sum(d["class"] == "dock_high" for d in on) >= 2 else "door apron"
        courts.append(c)
    courts.sort(key=lambda c: (-c["doors"], -c["depth_ft"]))

    # trailers parked at a dock facade (within 25 m)
    dock_lines = [LineString([edges[c["edge"]]["a"], edges[c["edge"]]["b"]]) for c in courts]
    buildings = [{"index": i, "footprint_sqft": round(fps[i].area * 10.7639), "main": i == main_i,
                  "roof_condition": sts[i].get("roof-characteristics.condition"),
                  "roof_material": sts[i].get("roof-characteristics.material.class"),
                  "footprint_local": [[round(v, 2) for v in p] for p in fps[i].exterior.coords]} for i in sig]
    trailers_at_docks = sum(1 for v in veh["semi trailer"] if any(v["poly"].distance(L) < 25 for L in dock_lines))

    s = sts[main_i]
    defects = sorted({k.split(".")[1] for k, v in s.items() if k.startswith("roof-defects.") and
                      k.endswith(".detected") and v and k.count(".") == 2})
    height = None
    if nd is not None:
        inside = fr.raster([fp])
        if inside.sum() > 20:
            height = round(float(np.nanpercentile(nd[inside], 90)) * M_TO_FT, 1)
    counts = Counter(d["class"] for d in doors)
    costar_sf = w.get("rba_sqft") or w.get("gba_sqft")
    seen_sf = sum(f.area for f in fps) * 10.7639
    parcel_mismatch = bool(costar_sf and seen_sf < 0.3 * costar_sf)
    out = {
        "property_id": property_id,
        "imagery": {"parcel_collection": pprops.get("metadata.collection"),
                    "parcel_date": (pprops.get("metadata.ortho-date-estimate") or "")[:10] or None,
                    "obliques": views, **hmeta},
        "frame": {"lat0": fr.lat0, "lon0": fr.lon0, "half_m": fr.half, "res_m": RES},
        "building": {"footprint_sqft": round(fp.area * 10.7639), "structures_on_parcel": len(sts),
                     "height_ft_dsm_p90": height,
                     "roof_condition": s.get("roof-characteristics.condition"),
                     "roof_condition_scale": "Vexcel 1 (poor) - 5 (excellent)",
                     "roof_material": s.get("roof-characteristics.material.class"), "roof_defects": defects,
                     "rooftop_ac_units": s.get("roof-objects.ac-unit.count"),
                     "skylights": s.get("roof-objects.skylight.count"),
                     "solar": s.get("roof-objects.solar.value"),
                     "footprint_local": [list(map(lambda v: round(v, 2), p)) for p in fp.exterior.coords]},
        "parcel_mismatch": parcel_mismatch,
        "parcel_note": (f"Vexcel parcel at the CoStar point has {round(seen_sf):,} SF of buildings vs CoStar "
                        f"{round(costar_sf):,} SF: this may be the wrong parcel" if parcel_mismatch else None),
        "site_note": pprops.get("_site_note"),
        "buildings": buildings,
        "doors": {"total": len(doors), "dock_high": counts["dock_high"], "drive_in": counts["drive_in"],
                  "uncertain": counts["uncertain"], "items": doors,
                  "note": "Counted from oblique photos; doors hidden by parked trailers or canopies are missed."},
        "courts": courts,
        "vehicles": {k: len(v) for k, v in veh.items()} | {"trailers_at_docks": trailers_at_docks},
        "site": {"parcel_acres": round(parcel.area / 4046.86, 2),
                 "paved_pct_of_parcel": round(100 * float((paved & parcel_m & ~fr.raster(fps)).sum())
                                              / max(parcel_m.sum(), 1), 1),
                 "tree_cover_pct_vexcel": pprops.get("parcel-tree-cover.percent")},
    }
    if save:
        d = BUILD / "twins" / "exterior"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{property_id}.json").write_text(json.dumps(out, default=float))
        (d / f"{property_id}_ortho.jpg").write_bytes(ortho_jpg)
        np.savez_compressed(d / f"{property_id}.npz", paved=paved, vehicle=vehicle, others=others,
                            ndsm=np.nan_to_num(nd if nd is not None else np.zeros_like(exg)).astype(np.float16))
    return out
