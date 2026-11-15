"""Dock store v3 (docs/CONTRACTS_v3.md section 1): every truck door of a warehouse in the EXTERIOR twin frame.

    load(pid) -> store         builds the proposals when there is no file yet, and caches them in build/docks/
    build(pid) -> store        (re)builds the proposals; doors the user touched (confirmed / rejected / user_added),
                               a user gate and a user-confirmed registration are kept
    save(pid, body) -> store   validates {doors:[...], gate?, registration?} from the 2D editor and writes it
    usable_doors(pid, kinds)   confirmed, user_added, proposed and inferred doors (never rejected) for the yard
    evidence(pid) -> summary   CoStar counts, detections, docked trailers, confirmed doors and the fused estimate

Frame: local metres east (x) / north (y) of the exterior twin's frame.lat0/lon0 (build/twins/exterior/<pid>.json).
Edges are the footprint edges of the twin's warehouse-sized buildings, numbered exactly as twin.exterior._edges numbers
them (concatenated in building order), so a twin door's `edge` is a store edge id.

Evidence sources for a proposed door (each with a confidence; nothing here is tuned to CoStar):
  oblique      SAM3 door detection on the Vexcel obliques (twin.exterior._doors), sizes from the camera model
  trailer      a semi trailer on the ortho parked nose-in at the facade: an occupied dock position (dock-high)
  well         the ground dips toward the wall (DTM): a truck well, dock-high
  costar_span  only with a CoStar count N: the gaps of an evidenced run of doors at its observed pitch ("inferred")
  vision       Claude vision on the oblique facade crop (only if adopted; never the only source of a position)
Missing is unknown, never 0. Detections are a LOWER BOUND: doors hidden by trailers or canopies are missed.
"""
import json
import math
from datetime import datetime, timezone

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from proinspect.config import BUILD
from proinspect.tools import elevation as el

OUT = BUILD / "docks"
EXT = BUILD / "twins" / "exterior"
VERSION = 3
BUILD_TAG = "v3.1"               # bump when the proposal method changes (precompute_docks rebuilds)
STATUSES = ("proposed", "inferred", "confirmed", "rejected", "user_added")
KINDS = ("dock_high", "drive_in", "unknown")
EVIDENCE = ("oblique", "trailer", "well", "costar_span", "vision", "user")
USER_STATUSES = ("confirmed", "rejected", "user_added")
SNAP_M = 1.5                     # a saved door must lie within this distance of its edge
MERGE_M = 2.5                    # evidence this close along one edge is one door
PITCH_RANGE_M = (3.4, 5.2)       # dock door centres 11-17 ft [U: industry typical 12-14 ft]
MIN_EDGE_M = 4.0


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(pid):
    return OUT / f"{int(pid)}.json"


def _twin(pid):
    p = EXT / f"{int(pid)}.json"
    if not p.exists():
        raise FileNotFoundError(f"no exterior twin for property {pid}")
    return json.loads(p.read_text())


def _catalog(pid):
    p = BUILD / "catalog" / "warehouses.json"
    if not p.exists():
        return None
    return next((w for w in json.loads(p.read_text()) if w["id"] == int(pid)), None)


def facing_of(nx, ny):
    return "NESW"[int(((math.degrees(math.atan2(nx, ny)) + 45) % 360) // 90)]


# ------------------------------------------------------------------ geometry
def buildings_of(twin):
    """[{id, outline, edges:[{id, a, b, facing, len_m, normal, t}]}] with edge ids as twin.exterior numbers them."""
    from proinspect.twin.exterior import _edges
    blds = twin.get("buildings") or [{"index": 0, "footprint_local": twin["building"]["footprint_local"]}]
    out, k = [], 0
    for b in blds:
        fp = Polygon(b["footprint_local"])
        if not fp.is_valid:
            fp = fp.buffer(0)
        edges = []
        for e in _edges(fp, b["index"]) if fp.geom_type == "Polygon" else []:
            edges.append({"id": k, "a": [round(e["a"][0], 2), round(e["a"][1], 2)],
                          "b": [round(e["b"][0], 2), round(e["b"][1], 2)], "facing": e["facing"],
                          "len_m": round(e["len"], 2), "normal": [round(e["n"][0], 4), round(e["n"][1], 4)],
                          "t": [round(e["t"][0], 4), round(e["t"][1], 4)]})
            k += 1
        out.append({"id": b["index"], "outline": [[round(v, 2) for v in p[:2]] for p in b["footprint_local"]],
                    "footprint_sqft": b.get("footprint_sqft"), "main": b.get("main"), "edges": edges})
    return out


def edge_index(store):
    return {e["id"]: (b["id"], e) for b in store["buildings"] for e in b["edges"]}


def _at(e, s):
    return e["a"][0] + e["t"][0] * s, e["a"][1] + e["t"][1] * s


def _lonlat(frame, x, y):
    lat, lon = el.to_latlon(x, y, frame["lat0"], frame["lon0"])
    return round(float(lon), 7), round(float(lat), 7)


def _door(frame, bid, e, s, **kw):
    s = float(min(max(s, 0.0), e["len_m"]))
    x, y = _at(e, s)
    lon, lat = _lonlat(frame, x, y)
    d = {"id": kw.pop("id", None), "building": bid, "edge": e["id"], "facing": e["facing"], "s_m": round(s, 2),
         "x": round(x, 2), "y": round(y, 2), "lon": lon, "lat": lat, "normal": list(e["normal"]),
         "width_ft": None, "height_ft": None, "kind": "unknown", "kind_conf": 0.0, "status": "proposed",
         "evidence": []}
    d.update(kw)
    return d


# ------------------------------------------------------------------ evidence -> proposals
def _oblique_doors(twin, frame, eidx):
    out = []
    for i, t in enumerate(twin.get("doors", {}).get("items") or []):
        if t["edge"] not in eidx:
            continue
        bid, e = eidx[t["edge"]]
        kind = {"dock_high": "dock_high", "drive_in": "drive_in"}.get(t["class"], "unknown")
        cues = t.get("cues") or []
        ev = [{"type": "oblique", "conf": t.get("confidence"),
               "ref": ",".join(t.get("views") or []),
               "note": f"SAM3 door seen {t.get('seen', 1)}x; " + "; ".join(c for c in cues if "truck well" not in c)}]
        well = [c for c in cues if "truck well" in c]
        if well:
            ev.append({"type": "well", "conf": 0.6, "ref": "dtm", "note": well[0]})
        kc = 0.0 if kind == "unknown" else (0.7 if len(cues) >= 2 and "cues disagree" not in cues else 0.5)
        out.append(_door(frame, bid, e, t["s_m"], width_ft=t.get("width_ft"), height_ft=t.get("height_ft"),
                         kind=kind, kind_conf=kc, evidence=ev))
    return out


def merge_along(doors, gap=MERGE_M):
    """Cluster doors on the same edge closer than `gap` along it; evidence is pooled, the strongest kind wins."""
    out = []
    for eid in sorted({d["edge"] for d in doors}):
        rs = sorted((d for d in doors if d["edge"] == eid), key=lambda d: d["s_m"])
        groups, cur = [], [rs[0]]
        for r in rs[1:]:
            if r["s_m"] - cur[-1]["s_m"] <= gap:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
        for g in groups:
            if len(g) == 1:
                out.append(g[0])
                continue
            # a trailer pins the position best (its end touches the dock); else the mean
            pin = [d for d in g if any(v["type"] == "trailer" for v in d["evidence"])]
            base = dict(pin[0] if pin else g[0])
            base["s_m"] = round(float(np.mean([d["s_m"] for d in (pin or g)])), 2)
            base["evidence"] = [v for d in g for v in d["evidence"]]
            kinds = sorted(g, key=lambda d: -d["kind_conf"])
            base["kind"], base["kind_conf"] = kinds[0]["kind"], kinds[0]["kind_conf"]
            ws = [d["width_ft"] for d in g if d.get("width_ft")]
            hs = [d["height_ft"] for d in g if d.get("height_ft")]
            base["width_ft"] = round(float(np.median(ws)), 1) if ws else None
            base["height_ft"] = round(float(np.median(hs)), 1) if hs else None
            out.append(base)
    return out


def _place(frame, eidx, d):
    """Recompute x, y, lon, lat, normal, facing from (edge, s_m)."""
    bid, e = eidx[d["edge"]]
    return {**d, **{k: v for k, v in _door(frame, bid, e, d["s_m"]).items()
                    if k in ("building", "s_m", "x", "y", "lon", "lat", "normal", "facing")}}


def _number(doors):
    for i, d in enumerate(sorted(doors, key=lambda d: (d["edge"], d["s_m"])), 1):
        if not d.get("id"):
            d["id"] = f"k{i}"
    return doors


TRAILER_MIN_AREA_M2 = 18.0       # 'trailer' also catches cars: keep trailer-sized masks (as twin/vexcel_profile.py)
TRAILER_MIN_LONG_M = 8.0
TRAILER_MAX_ANGLE = 15.0         # long axis within this of the facade normal: parked nose-in
TRAILER_MAX_GAP_M = 2.5          # its near end this close to the footprint edge: at the door (footprints are +-1 m)
TRAILER_MAX_INSIDE_M = 3.0       # an end slightly inside the footprint is allowed (roof overhang, footprint error)


def _vehicle_masks(pid, twin):
    """Cached SAM3 ortho detections that can be trailers: ['trailer'] on the parcel envelope (vexcel_profile) and
    'semi trailer'/'truck' on the twin frame (twin.exterior._vehicles). Cache only: no paid call here."""
    from proinspect import cache
    from proinspect.tools import vexcel as vx
    from proinspect.twin import exterior as ex
    from proinspect.twin.vexcel_profile import masks_to_geoms
    out, miss = [], []
    fr = twin["frame"]
    with cache.cache_only():
        try:
            _, _, parcel, _ = ex._site(pid)
            res = vx.detect_raw(parcel.envelope.wkt, ["trailer"], "ortho", 0.3)
            out += [("trailer", c, g, "ortho trailer") for _, c, g in masks_to_geoms(res)]
        except Exception as e:                                   # noqa: BLE001
            miss.append(f"trailer: {type(e).__name__}")
        try:
            res = vx.detect_raw(vx.square_around(fr["lat0"], fr["lon0"], fr["half_m"]), ex.VEHICLE_LABELS,
                                "ortho", 0.4)
            out += [(lab, c, g, f"ortho {lab}") for lab, c, g in masks_to_geoms(res) if lab != "car"]
        except Exception as e:                                   # noqa: BLE001
            miss.append(f"vehicles: {type(e).__name__}")
    return out, miss


def docked_trailers(pid, twin, store):
    """Trailers parked nose-in at a facade -> [(edge, s, conf, ref, note)]. Trailers parked in rows away from the
    building, or parallel to it, are not docks."""
    from shapely.ops import transform
    masks, miss = _vehicle_masks(pid, twin)
    fr = store["frame"]
    edges = [e for b in store["buildings"] for e in b["edges"]]
    fps = [Polygon(b["outline"]).buffer(0) for b in store["buildings"]]
    hits, seen = [], []
    for label, conf, g in ((m[0], m[1], m[2]) for m in masks):
        gl = transform(lambda lon, lat, z=None: el.to_local(lat, lon, fr["lat0"], fr["lon0"]), g)
        if gl.is_empty or gl.area < TRAILER_MIN_AREA_M2:
            continue
        rect = gl.minimum_rotated_rectangle
        xs = list(rect.exterior.coords)
        sides = sorted(((math.dist(a, b), a, b) for a, b in zip(xs[:-1], xs[1:])), key=lambda t: -t[0])
        L, a, b = sides[0]
        W = sides[-1][0]
        if L < TRAILER_MIN_LONG_M or W > 4.5 or W < 1.8:
            continue
        if any(gl.centroid.distance(p) < 2.0 for p in seen):          # same trailer from two queries
            continue
        ax = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        c = rect.centroid
        best = None
        for e in edges:
            n = e["normal"]
            ang = math.degrees(math.acos(min(1.0, abs(ax[0] * n[0] + ax[1] * n[1]))))
            if ang > TRAILER_MAX_ANGLE:
                continue
            # the near end of the trailer along the normal, relative to the wall line (positive = outside)
            off_c = (c.x - e["a"][0]) * n[0] + (c.y - e["a"][1]) * n[1]
            gap = off_c - L / 2
            if not (-TRAILER_MAX_INSIDE_M <= gap <= TRAILER_MAX_GAP_M) or off_c <= 0:
                continue
            s = (c.x - e["a"][0]) * e["t"][0] + (c.y - e["a"][1]) * e["t"][1]
            if not 0.5 <= s <= e["len_m"] - 0.5:
                continue
            if any(fp.contains(c) for fp in fps):
                continue
            if best is None or abs(gap) < abs(best[2]):
                best = (e, s, gap, ang)
        if best is None:
            continue
        e, s, gap, ang = best
        seen.append(gl.centroid)
        hits.append((e["id"], s, conf, label,
                     f"{label} {L:.0f} m x {W:.1f} m on the ortho, nose-in ({ang:.0f} deg off the facade normal), "
                     f"near end {gap:+.1f} m from the wall",
                     [[round(float(x), 2), round(float(y), 2)] for x, y in rect.exterior.coords]))
    return hits, miss


FACADE_CHUNK_M = 55.0            # facade crops this long come back at native resolution (Vexcel caps at 2048 px)
FACADE_MIN_EDGE_M = 10.0
FACADE_OUT_M, FACADE_IN_M = 12.0, 3.0
FACADE_THRESHOLD = 0.3


def _ms_date(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def crop_id_of(wkt, view):
    """The facade crop's id = the disk-cache key of its SAM3 detect_raw call (same args as facade_detections)."""
    from proinspect import cache
    from proinspect.twin.exterior import DOOR_LABELS
    return cache._key("vexcel", "detect_raw", [wkt, DOOR_LABELS, view, FACADE_THRESHOLD], {}).name


def _best_view(n):
    from proinspect.twin.exterior import VIEW_DIR
    v = max(VIEW_DIR, key=lambda k: -(n[0] * VIEW_DIR[k][0] + n[1] * VIEW_DIR[k][1]))
    vd = VIEW_DIR[v]
    return v, -(n[0] * vd[0] + n[1] * vd[1])


def _chunks(L, size=FACADE_CHUNK_M, overlap=3.0):
    k = max(1, math.ceil(L / size))
    step = L / k
    return [(max(-1.0, i * step - overlap), min(L + 1.0, (i + 1) * step + overlap)) for i in range(k)]


def _theil_sen(px, s):
    """Robust line s = a + b * px over the detections of one crop (median of pairwise slopes)."""
    sl = [(s[j] - s[i]) / (px[j] - px[i]) for i in range(len(px)) for j in range(i + 1, len(px)) if px[j] != px[i]]
    if not sl:
        return None
    b = float(np.median(sl))
    a = float(np.median([si - b * pi for pi, si in zip(px, s)]))
    return a, b


def facade_detections(pid, store, edges=None, cache_only=False, workers=4):
    """SAM3 doors on a TIGHT oblique crop of each facade (<= 55 m chunks, from 3 m inside to 12 m outside the wall),
    from the camera that faces it best. Whole-site crops come back downsampled up to ~6x and miss most doors.
    Positions: the server-projected bbox centroid, unless it disagrees by > 1.5 m with a robust pixel-x -> s line
    fitted over the crop's detections (>= 3), then the line. Returns raw detections [{edge, s, w, h, conf, view, ...}]."""
    from concurrent.futures import ThreadPoolExecutor

    from shapely import wkt as shp_wkt

    from proinspect import cache
    from proinspect.tools import vexcel as vx
    from proinspect.twin.exterior import DOOR_LABELS, _door_metrics, response_scale
    fr = store["frame"]
    es = [e for b in store["buildings"] for e in b["edges"]
          if e["len_m"] >= FACADE_MIN_EDGE_M and (edges is None or e["id"] in edges)]
    jobs = []
    for e in es:
        view, facing = _best_view(e["normal"])
        if facing < 0.5:
            continue
        n, t, a = e["normal"], e["t"], e["a"]
        for s0, s1 in _chunks(e["len_m"]):
            pts = [(a[0] + t[0] * ss + n[0] * d, a[1] + t[1] * ss + n[1] * d)
                   for ss, d in ((s0, -FACADE_IN_M), (s1, -FACADE_IN_M), (s1, FACADE_OUT_M), (s0, FACADE_OUT_M))]
            ll = [el.to_latlon(x, y, fr["lat0"], fr["lon0"]) for x, y in pts]
            wkt = Polygon([(round(float(lo), 8), round(float(la), 8)) for la, lo in ll]).wkt
            jobs.append((e, view, s0, s1, wkt))

    def run(job):
        e, view, s0, s1, wkt = job
        try:
            if cache_only:
                with cache.cache_only():
                    return job, vx.detect_raw(wkt, DOOR_LABELS, view, FACADE_THRESHOLD), None
            return job, vx.detect_raw(wkt, DOOR_LABELS, view, FACADE_THRESHOLD), None
        except Exception as ex_:                                # noqa: BLE001
            return job, None, f"{type(ex_).__name__}: {str(ex_)[:80]}"
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(run, jobs))
    raw, errors = [], []
    crops = {}
    for (e, view, s0, s1, wkt), res, err in results:
        if res is None:
            errors.append(err)
            continue
        meta = (res.get("images") or [{}])[0].get("metadata", {})
        cid = crop_id_of(wkt, view)
        crops[cid] = {"crop_id": cid, "wkt": wkt, "view": view, "edge": e["id"],
                      "date": _ms_date(meta.get("capture-date")), "image": meta.get("image-name"),
                      "width": meta.get("width"), "height": meta.get("height"),
                      "hits": [{"px_bbox": list(d["pixel-bbox"]), "label": f["properties"]["label"],
                                "conf": round(float(d["confidence"]), 3)}
                               for f in res.get("features", []) for d in f["properties"]["detections"]]}
        dets = []
        for f in res.get("features", []):
            for d in f["properties"]["detections"]:
                c = shp_wkt.loads(d["bbox"]).centroid
                x, y = (float(v) for v in el.to_local(c.y, c.x, fr["lat0"], fr["lon0"]))
                s = (x - e["a"][0]) * e["t"][0] + (y - e["a"][1]) * e["t"][1]
                px = d["pixel-bbox"][0] + d["pixel-bbox"][2] / 2
                w_m, h_m, tilt = _door_metrics(d, meta)
                dets.append({"edge": e["id"], "s": s, "px": px, "conf": float(d["confidence"]), "w": w_m, "h": h_m,
                             "view": view, "label": f["properties"]["label"], "scale": response_scale(meta)[0],
                             "date": meta.get("capture-date"), "crop_id": cid,
                             "px_bbox": list(d["pixel-bbox"])})
        uniq = sorted({round(d["px"]) for d in dets})
        if len(uniq) >= 3:
            byp = {}
            for d in dets:
                byp.setdefault(round(d["px"]), d["s"])
            fit = _theil_sen(list(byp), list(byp.values()))
            if fit:
                for d in dets:
                    sp = fit[0] + fit[1] * d["px"]
                    if abs(sp - d["s"]) > 1.5:
                        d["s_projected"], d["s"] = d["s"], sp
        raw += [d for d in dets if max(-0.5, s0) <= d["s"] <= min(e["len_m"] + 0.5, s1) and d["w"] >= 1.5]
    if store is not None:
        store["proof_crops"] = crops
    return raw, errors, len(jobs)


def facade_doors(pid, twin, store, cache_only=False):
    """Facade-crop detections -> doors (clustered within 2.5 m along an edge; three labels often fire on one door)."""
    from proinspect.twin.exterior import M_TO_FT, _classify
    eidx = edge_index(store)
    raw, errors, calls = facade_detections(pid, store, cache_only=cache_only)
    out = []
    for eid in sorted({r["edge"] for r in raw}):
        rs = sorted((r for r in raw if r["edge"] == eid), key=lambda r: r["s"])
        groups, cur = [], [rs[0]]
        for r in rs[1:]:
            if r["s"] - cur[-1]["s"] <= 2.0:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
        bid, e = eidx[eid]
        for g in groups:
            s = float(np.mean([r["s"] for r in g]))
            w, h = float(np.median([r["w"] for r in g])), float(np.median([r["h"] for r in g]))
            cls, cues = _classify(w, h, None)
            kind = {"dock_high": "dock_high", "drive_in": "drive_in"}.get(cls, "unknown")
            conf = max(r["conf"] for r in g)
            out.append(_door(store["frame"], bid, e, s, width_ft=round(w * M_TO_FT, 1),
                             height_ft=round(h * M_TO_FT, 1), kind=kind, kind_conf=0.5 if kind != "unknown" else 0.0,
                             evidence=[{"type": "oblique", "conf": round(conf, 2), "ref": f"facade-crop:{g[0]['view']}",
                                        "crop_ids": sorted({r["crop_id"] for r in g}),
                                        "hits": [{"crop_id": r["crop_id"], "px_bbox": r["px_bbox"],
                                                  "conf": round(r["conf"], 3), "label": r["label"]} for r in g],
                                        "note": f"SAM3 door on a tight {g[0]['view']} crop of this facade "
                                                f"({len(g)} hits); " + "; ".join(cues)}]))
    note = (f"facade-crop door search: {calls} crops, {len(raw)} raw hits, {len(out)} doors"
            + (f"; {len(errors)} crops failed ({errors[0]})" if errors else ""))
    return out, note


def trailer_doors(pid, twin, store):
    """Docked-trailer evidence -> proposed dock-high doors (merged later with oblique doors on the same edge)."""
    eidx = edge_index(store)
    hits, miss = docked_trailers(pid, twin, store)
    out = []
    for eid, s, conf, label, note, poly in hits:
        bid, e = eidx[eid]
        out.append(_door(store["frame"], bid, e, s, kind="dock_high", kind_conf=0.85,
                         evidence=[{"type": "trailer", "conf": round(min(0.9, 0.5 + conf / 2), 2),
                                    "ref": f"ortho:{label}", "note": note, "poly_local": poly,
                                    "crop_ids": ["ortho"]}]))
    note = (f"docked-trailer evidence: {len(out)} trailers parked nose-in at a facade"
            + (f" (cache missing: {', '.join(miss)})" if miss else ""))
    return out, note


def span_fill(store, doors):
    """CoStar count N + a regular run of evidenced dock doors on one facade -> the missing doors inside that run, at the
    run's own pitch (status 'inferred', evidence costar_span). Never beyond N in total, never outside the evidenced run,
    never on a facade without evidence. Pitch = median of the gaps within PITCH_RANGE_M; a gap of k pitches (+-25%)
    gets k-1 doors."""
    n = (store.get("costar") or {}).get("docks")
    if not n:
        return []
    eidx = edge_index(store)
    capable = [d for d in doors if d["kind"] in ("dock_high", "unknown") and d.get("status") != "rejected"]
    budget = n - len(capable)
    if budget <= 0:
        return []
    plans = []
    for eid in {d["edge"] for d in capable}:
        ss = sorted(d["s_m"] for d in capable if d["edge"] == eid)
        if len(ss) < 2:
            continue
        gaps = np.diff(ss)
        ok = [g for g in gaps if PITCH_RANGE_M[0] <= g <= PITCH_RANGE_M[1]]
        if not ok:
            continue
        p = float(np.median(ok))
        fills = []
        for s0, g in zip(ss[:-1], gaps):
            k = int(round(g / p))
            if k >= 2 and abs(g - k * p) <= 0.25 * p:
                step = g / k
                fills += [s0 + step * j for j in range(1, k)]
        if fills:
            plans.append((len(ss), eid, p, ss, fills))
    out = []
    for nev, eid, p, ss, fills in sorted(plans, key=lambda t: -t[0]):     # the best-evidenced facade first
        bid, e = eidx[eid]
        for s in fills:
            if len(out) >= budget:
                return out
            out.append(_door(store["frame"], bid, e, s, kind="dock_high", kind_conf=0.4, status="inferred",
                             evidence=[{"type": "costar_span", "conf": 0.4, "ref": f"costar:{n}",
                                        "note": f"gap in a run of {nev} evidenced doors at a {p:.1f} m pitch on this "
                                                f"facade; CoStar lists {n} docks, so a door is likely here"}]))
    return out


# ------------------------------------------------------------------ summary
def _costar(w):
    if not w:
        return {"docks": None, "docks_internal": None, "has_docks": None, "drive_ins": None, "sqft": None,
                "stories": None}
    return {"docks": w.get("docks_external"), "docks_internal": w.get("docks_internal"),
            "has_docks": w.get("has_docks"), "drive_ins": w.get("drive_ins"),
            "sqft": w.get("rba_sqft") or w.get("gba_sqft"), "stories": w.get("stories"),
            "lat": w.get("lat"), "lon": w.get("lon")}


MAX_DOORS_PER_10KSF = 4.0        # above this on the property's building the detections are not credible [U]
MIN_DOOR_PITCH_M = 3.6           # geometric max: dock-capable facade length / this


def property_building(store):
    """The building on the parcel that IS the CoStar property: footprint x stories closest (log ratio) to CoStar's
    RBA/GBA; without a CoStar size, the largest ('main') building. Parcels often hold several buildings."""
    blds = store.get("buildings") or []
    if not blds:
        return None
    cs = store.get("costar") or {}
    sf = cs.get("sqft")
    if sf:
        st = cs.get("stories") or 1
        err = {b["id"]: abs(math.log(max(b.get("footprint_sqft") or Polygon(b["outline"]).area * 10.7639, 1)
                                     * st / sf)) for b in blds}
        near = [b for b in blds if err[b["id"]] <= min(err.values()) + 0.15]
        if len(near) > 1 and cs.get("lat") is not None and store.get("frame"):
            # same-size buildings (a park of twins): the one under CoStar's map point
            px, py = (float(v) for v in el.to_local(cs["lat"], cs["lon"], store["frame"]["lat0"],
                                                    store["frame"]["lon0"]))
            return min(near, key=lambda b: Polygon(b["outline"]).distance(Point(px, py)))["id"]
        return min(blds, key=lambda b: err[b["id"]])["id"]
    main = [b for b in blds if b.get("main")]
    return (main or [max(blds, key=lambda b: Polygon(b["outline"]).area)])[0]["id"]


def _implausible(capable, b_sf, bmeta):
    """Why a dock-door count on one building is not credible, or None."""
    k = len(capable)
    if b_sf:
        dens = k / b_sf * 1e4
        if dens > MAX_DOORS_PER_10KSF and k > 2:                 # a small building may still have 1-2 doors
            return (f"that is {dens:.1f} doors per 10,000 SF on a {b_sf:,.0f} SF building (more than "
                    f"{MAX_DOORS_PER_10KSF:.0f} is not plausible for docks)")
    if bmeta:
        eids = {d["edge"] for d in capable}
        L = sum(e["len_m"] for e in bmeta["edges"] if e["id"] in eids)
        gmax = int(L // MIN_DOOR_PITCH_M) + len(eids)
        if k > gmax:
            return (f"the facades carrying them are only {L:.0f} m long, room for at most {gmax} doors at a "
                    f"{MIN_DOOR_PITCH_M} m pitch")
    return None


def summarize(store, w=None):
    cs = store.get("costar") or _costar(w)
    if w and "sqft" not in cs:
        cs = {**cs, **{k: v for k, v in _costar(w).items() if k in ("sqft", "stories")}}
        store["costar"] = cs
    live_all = [d for d in store["doors"] if d["status"] != "rejected"]
    pb = property_building(store)
    store["property_building"] = pb
    live = [d for d in live_all if pb is None or d["building"] == pb]          # the estimate: property building only
    others = len(live_all) - len(live)
    det = [d for d in live_all if any(v["type"] in ("oblique", "vision") for v in d["evidence"])]
    trl_all = [d for d in live_all if any(v["type"] == "trailer" for v in d["evidence"])]
    trl = [d for d in live if any(v["type"] == "trailer" for v in d["evidence"])]
    conf = [d for d in store["doors"] if d["status"] in ("confirmed", "user_added")]
    conf_pb = [d for d in conf if pb is None or d["building"] == pb]
    capable = [d for d in live if d["kind"] in ("dock_high", "unknown") and d["status"] != "inferred"]
    sure_dock = [d for d in capable if d["kind"] == "dock_high"]
    bmeta = next((b for b in store.get("buildings") or [] if b["id"] == pb), None)
    b_sf = cs.get("sqft") or (bmeta or {}).get("footprint_sqft") or (
        Polygon(bmeta["outline"]).area * 10.7639 if bmeta else None)
    other_note = (f" {others} more doors seen on other buildings of the parcel are not counted (they may belong to "
                  "other properties)." if others else "")
    n = cs.get("docks")
    if n is not None:
        est = {"value": n, "lo": n, "hi": n, "status": "verified",
               "basis": f"CoStar lists {n} external loading docks."
                        + (f" The imagery shows at least {len(capable)} dock positions on this building."
                           if capable else " The imagery shows none of them (often hidden by parked trailers).")}
    elif conf_pb and not [d for d in live if d["status"] in ("proposed", "inferred")]:
        k = sum(d["kind"] in ("dock_high", "unknown") for d in conf_pb)
        est = {"value": k, "lo": k, "hi": k, "status": "estimated",
               "basis": f"{k} dock doors confirmed by a person on the aerial view; CoStar has no count."}
    elif cs.get("has_docks") is False and not trl:
        seen = (f" The imagery shows {len(live)} truck doors on this building, which CoStar's record implies are "
                "drive-ins or other doors; a person should check them." if live else " The imagery shows none.")
        est = {"value": 0, "lo": 0, "hi": 0, "status": "verified",
               "basis": "CoStar records no loading docks (hasLoadingDocks = false)." + seen}
    elif capable and (implausible := _implausible(capable, b_sf, bmeta)):
        est = {"value": None, "lo": None, "hi": None, "status": "unknown",
               "basis": f"CoStar has no dock count. The imagery proposes {len(capable)} dock-capable doors on this "
                        f"building, but {implausible}, so the detections are not credible as a count (likely "
                        "drive-ins, roll-up bays or windows); a person should check them." + other_note}
    elif capable:
        est = {"value": None, "lo": len(capable), "hi": None, "status": "lower_bound",
               "basis": f"CoStar has no dock count; the imagery shows at least {len(capable)} dock-capable doors "
                        f"({len(sure_dock)} called dock-high, the rest of unknown kind). Doors hidden by trailers or "
                        "canopies are missed, so this is a lower bound." + other_note
                        + (f" Note: CoStar's record says the building has no loading docks, but {len(trl)} trailers "
                           "are parked nose-in at a wall." if cs.get("has_docks") is False else "")}
    else:
        est = {"value": None, "lo": None, "hi": None, "status": "unknown",
               "basis": "CoStar has no dock count and no dock doors were found in the imagery (unknown, not zero)."}
    return {"costar_docks": n, "costar_docks_internal": cs.get("docks_internal"),
            "costar_has_docks": cs.get("has_docks"), "costar_drive_ins": cs.get("drive_ins"),
            "detected_doors": len(det), "docked_trailers": len(trl_all), "confirmed": len(conf),
            "proposed": sum(d["status"] == "proposed" for d in store["doors"]),
            "inferred": sum(d["status"] == "inferred" for d in store["doors"]),
            "rejected": sum(d["status"] == "rejected" for d in store["doors"]),
            "dock_high": sum(d["kind"] == "dock_high" for d in live_all),
            "drive_in": sum(d["kind"] == "drive_in" for d in live_all),
            "unknown_kind": sum(d["kind"] == "unknown" for d in live_all),
            "facades": sorted({d["facing"] for d in live_all}),
            "estimate": est}


# ------------------------------------------------------------------ gate + registration
def _gate(pid, doors, frame):
    try:
        from proinspect.yard import site as ys
        yard = ys.load(pid)
        cap = [d for d in doors if d["kind"] != "drive_in"] or doors
        dock = (float(np.mean([d["x"] for d in cap])), float(np.mean([d["y"] for d in cap]))) if cap else (0.0, 0.0)
        (pt, _n), method = ys.entrance(yard, dock)
        lon, lat = _lonlat(frame, pt[0], pt[1])
        return {"x": round(float(pt[0]), 2), "y": round(float(pt[1]), 2), "lon": lon, "lat": lat,
                "source": "inferred", "note": method}
    except Exception as e:                                       # noqa: BLE001  (best effort; null = unknown)
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def _registration(pid, w):
    """Scan -> footprint registration from twin.fuse (best hypothesis), or null when there is no usable scan."""
    scans = [s for s in (w or {}).get("scans") or [] if s.get("class") == "warehouse_floor"]
    for s in scans:
        mid = s["model_id"]
        if not (BUILD / "twins" / "interior" / f"{mid}.npz").exists():
            continue
        try:
            from proinspect.twin.fuse import register
            reg = register(pid, mid)
        except Exception as e:                                   # noqa: BLE001
            return {"model_id": mid, "flip": 0, "confirmed_by_user": False, "confidence": 0.0,
                    "note": f"registration failed: {type(e).__name__}: {str(e)[:120]}"}
        if not reg.get("phi") and reg.get("phi") != 0:
            continue
        tied = [h for h in reg.get("all") or [] if h["building"] == reg["building"] and h["k"] == (reg["k"] + 2) % 4]
        return {"model_id": mid, "flip": 0, "confirmed_by_user": False, "confidence": reg.get("confidence"),
                "phi": round(float(reg["phi"]), 5), "t": [round(float(v), 2) for v in reg["t"]],
                "building": reg["building"], "ok": reg.get("ok"),
                "phi_flipped": round(float(reg["phi"] + math.pi), 5),
                "t_flipped": [round(float(v), 2) for v in tied[0]["t"]] if tied else None,
                "note": ("scan floor fitted to the footprint: ENU = R(phi + flip) p_rot + t. "
                         + (reg.get("why") or "fit is unambiguous")
                         + ". A person should check the overlay and press Flip 180 if the scan is turned around.")}
    return None


# ------------------------------------------------------------------ build / load / save
def build(pid, keep_user=True, with_trailers=True, with_span=True, gate=True, registration=True, with_facades=True,
          facades_cache_only=True):
    pid = int(pid)
    twin = _twin(pid)
    w = _catalog(pid)
    frame = {"lat0": twin["frame"]["lat0"], "lon0": twin["frame"]["lon0"]}
    store = {"property_id": pid, "version": VERSION, "build_tag": BUILD_TAG, "updated_at": _now(), "frame": frame,
             "buildings": buildings_of(twin), "gate": None, "registration": None, "doors": [],
             "costar": _costar(w), "notes": []}
    eidx = edge_index(store)
    props = _oblique_doors(twin, frame, eidx)
    if with_trailers:
        try:
            tr, note = trailer_doors(pid, twin, store)
            props += tr
            if note:
                store["notes"].append(note)
        except Exception as e:                                   # noqa: BLE001
            store["notes"].append(f"docked-trailer evidence unavailable: {type(e).__name__}: {str(e)[:120]}")
    if with_facades:
        try:
            fd, note = facade_doors(pid, twin, store, cache_only=facades_cache_only)
            props += fd
            store["notes"].append(note)
        except Exception as e:                                   # noqa: BLE001
            store["notes"].append(f"facade-crop doors unavailable: {type(e).__name__}: {str(e)[:120]}")
    props = [_place(frame, eidx, d) for d in merge_along(props)]
    if with_span and store["costar"]["docks"]:
        props += [_place(frame, eidx, d) for d in span_fill(store, props)]
    if twin.get("parcel_mismatch"):
        store["notes"].append("WARNING: " + (twin.get("parcel_note") or "the Vexcel parcel may be the wrong one"))
    prev = None
    if keep_user and _path(pid).exists():
        try:
            prev = json.loads(_path(pid).read_text())
        except json.JSONDecodeError:
            prev = None
    user = [d for d in (prev or {}).get("doors", []) if d.get("status") in USER_STATUSES]
    if user:                                   # user-touched doors win; drop proposals they cover
        props = [p for p in props if not any(u["edge"] == p["edge"] and abs(u["s_m"] - p["s_m"]) <= MERGE_M
                                             for u in user)]
    store["doors"] = _number(user + props) if not user else user + _number(props)
    ids = [d["id"] for d in store["doors"]]
    if len(set(ids)) != len(ids):              # user ids may collide with new proposal numbers
        used = set(d["id"] for d in user)
        k = 1
        for d in store["doors"]:
            if d in user:
                continue
            while f"k{k}" in used:
                k += 1
            d["id"] = f"k{k}"
            used.add(d["id"])
    store["doors"].sort(key=lambda d: (d["building"], d["edge"], d["s_m"]))
    pg = (prev or {}).get("gate")
    store["gate"] = pg if pg and pg.get("source") == "user" else (_gate(pid, store["doors"], frame) if gate else None)
    if store["gate"] and "error" in store["gate"]:
        store["notes"].append(f"gate not inferred: {store['gate']['error']}")
        store["gate"] = None
    pr = (prev or {}).get("registration")
    store["registration"] = pr if pr and pr.get("confirmed_by_user") else (
        _registration(pid, w) if registration else None)
    store["summary"] = summarize(store, w)
    OUT.mkdir(parents=True, exist_ok=True)
    _path(pid).write_text(json.dumps(store, indent=1, default=float))
    return store


def load(pid):
    p = _path(pid)
    if p.exists():
        return json.loads(p.read_text())
    return build(pid)


class DockValidationError(ValueError):
    pass


def _validate_door(d, eidx, frame, i):
    if not isinstance(d, dict):
        raise DockValidationError(f"door {i}: not an object")
    st = d.get("status") or "user_added"
    if st not in STATUSES:
        raise DockValidationError(f"door {i}: status {st!r} is not one of {STATUSES}")
    kind = d.get("kind") or "unknown"
    if kind not in KINDS:
        raise DockValidationError(f"door {i}: kind {kind!r} is not one of {KINDS}")
    for v in d.get("evidence") or []:
        if v.get("type") not in EVIDENCE:
            raise DockValidationError(f"door {i}: evidence type {v.get('type')!r} is not one of {EVIDENCE}")
    if d.get("edge") not in eidx:
        raise DockValidationError(f"door {i}: edge {d.get('edge')!r} is not a footprint edge of this property")
    bid, e = eidx[d["edge"]]
    if d.get("building") is not None and d["building"] != bid:
        raise DockValidationError(f"door {i}: edge {d['edge']} belongs to building {bid}, not {d['building']}")
    if d.get("x") is not None and d.get("y") is not None:
        x, y = float(d["x"]), float(d["y"])
        dist = LineString([e["a"], e["b"]]).distance(Point(x, y))
        if dist > SNAP_M:
            raise DockValidationError(f"door {i}: ({x:.1f}, {y:.1f}) is {dist:.1f} m from edge {e['id']} "
                                      f"(must be within {SNAP_M} m)")
        s = (x - e["a"][0]) * e["t"][0] + (y - e["a"][1]) * e["t"][1]
    elif d.get("s_m") is not None:
        s = float(d["s_m"])
        if not -SNAP_M <= s <= e["len_m"] + SNAP_M:
            raise DockValidationError(f"door {i}: s_m {s:.1f} is off edge {e['id']} (length {e['len_m']:.1f} m)")
    else:
        raise DockValidationError(f"door {i}: needs x, y or s_m")
    kc = d.get("kind_conf")
    kc = 1.0 if st in ("confirmed", "user_added") and kc is None else float(kc or 0.0)
    out = _door(frame, bid, e, s, id=d.get("id"), status=st, kind=kind, kind_conf=max(0.0, min(1.0, kc)),
                width_ft=d.get("width_ft"), height_ft=d.get("height_ft"),
                evidence=list(d.get("evidence") or ([] if st != "user_added" else
                                                   [{"type": "user", "conf": 1.0, "ref": "dock editor",
                                                     "note": "added by a person on the aerial view"}])))
    return out


def save(pid, body):
    """Validate and write the editor's doors (and optional gate / registration). Returns the stored record."""
    pid = int(pid)
    store = load(pid)
    if not isinstance(body, dict) or not isinstance(body.get("doors"), list):
        raise DockValidationError("body must be {doors: [...], gate?, registration?}")
    eidx = edge_index(store)
    doors = [_validate_door(d, eidx, store["frame"], i) for i, d in enumerate(body["doors"])]
    used = {d["id"] for d in doors if d["id"]}
    if len(used) != sum(1 for d in doors if d["id"]):
        raise DockValidationError("door ids must be unique")
    k = 1
    for d in doors:
        if not d["id"]:
            while f"u{k}" in used:
                k += 1
            d["id"] = f"u{k}"
            used.add(d["id"])
    store["doors"] = doors
    if "gate" in body:
        g = body["gate"]
        if g is None:
            store["gate"] = None
        else:
            if g.get("x") is not None and g.get("y") is not None:
                x, y = float(g["x"]), float(g["y"])
            elif g.get("lon") is not None and g.get("lat") is not None:
                x, y = (float(v) for v in el.to_local(g["lat"], g["lon"], store["frame"]["lat0"],
                                                      store["frame"]["lon0"]))
            else:
                raise DockValidationError("gate needs x, y or lon, lat")
            if math.hypot(x, y) > 1000:
                raise DockValidationError("gate is more than 1 km from the site")
            lon, lat = _lonlat(store["frame"], x, y)
            store["gate"] = {"x": round(x, 2), "y": round(y, 2), "lon": lon, "lat": lat,
                             "source": g.get("source") if g.get("source") in ("inferred", "user") else "user"}
    if body.get("registration") is not None:
        r = body["registration"]
        if r.get("flip", 0) not in (0, 180):
            raise DockValidationError("registration.flip must be 0 or 180")
        reg = dict(store.get("registration") or {"model_id": r.get("model_id"), "confidence": None, "note": ""})
        reg["flip"] = int(r.get("flip", reg.get("flip", 0)))
        reg["confirmed_by_user"] = bool(r.get("confirmed_by_user", False))
        sh = r.get("shift")
        if sh is not None:
            try:
                sh = [float(sh[0]), float(sh[1])]
            except (TypeError, ValueError, IndexError):
                raise DockValidationError("registration.shift must be [dx, dy] metres")
            if max(abs(v) for v in sh) > 200:
                raise DockValidationError("registration.shift is limited to 200 m")
            reg["shift"] = [round(v, 2) for v in sh]
        store["registration"] = reg
    store["updated_at"] = _now()
    store["summary"] = summarize(store)
    OUT.mkdir(parents=True, exist_ok=True)
    _path(pid).write_text(json.dumps(store, indent=1, default=float))
    return store


def usable_doors(pid, kinds=("dock_high", "unknown")):
    store = load(pid)
    keep = ("confirmed", "user_added", "proposed", "inferred")
    order = {s: i for i, s in enumerate(keep)}
    out = [d for d in store["doors"] if d["status"] in keep and d["kind"] in kinds]
    return sorted(out, key=lambda d: (order[d["status"]], d["edge"], d["s_m"]))


def evidence(pid):
    return load(pid)["summary"]


# ------------------------------------------------------------------ proof (per-door photo evidence)
PROOF = OUT / "proof"
ACCENT = (79, 209, 197)            # #4fd1c5
CAMERA_DIR = {"oblique-north": "north", "oblique-south": "south", "oblique-east": "east", "oblique-west": "west",
              "ortho": "straight down"}
NO_PHOTO_INFERRED = "placed at the observed spacing to reach CoStar's count"


def _proof_url(pid, crop_id, door_id):
    return f"/api/docks/{int(pid)}/proof/{crop_id}.jpg?door={door_id}"


def proof_index(pid):
    """{door_id: [{crop_id, view, camera_dir, date, url, hits}] | {no_photo, why}} for every door in the store."""
    store = load(pid)
    crops = store.get("proof_crops") or {}
    out = {}
    for d in store["doors"]:
        items = []
        for v in d["evidence"]:
            if v["type"] == "oblique" and v.get("hits"):
                for cid in v.get("crop_ids") or sorted({h["crop_id"] for h in v["hits"]}):
                    c = crops.get(cid) or {}
                    items.append({"crop_id": cid, "view": c.get("view"), "camera_dir": CAMERA_DIR.get(c.get("view")),
                                  "date": c.get("date"), "source_image": c.get("image"),
                                  "url": _proof_url(pid, cid, d["id"]),
                                  "hits": [h for h in v["hits"] if h["crop_id"] == cid]})
            elif v["type"] == "trailer" and v.get("poly_local"):
                items.append({"crop_id": "ortho", "view": "ortho", "camera_dir": "straight down",
                              "date": None, "url": _proof_url(pid, "ortho", d["id"]),
                              "hits": [{"label": v.get("ref", "").split(":")[-1], "conf": v.get("conf"),
                                        "poly_local": v["poly_local"]}]})
        if items:
            out[d["id"]] = items
        elif d["status"] == "inferred":
            out[d["id"]] = {"no_photo": True, "why": NO_PHOTO_INFERRED}
        elif d["status"] == "user_added":
            out[d["id"]] = {"no_photo": True, "why": "added by a person on the aerial view"}
        elif any(v["type"] == "oblique" for v in d["evidence"]):
            out[d["id"]] = {"no_photo": True,
                            "why": "seen by SAM3 on a whole-site oblique (v2 twin) whose crop was not traced"}
        else:
            out[d["id"]] = {"no_photo": True, "why": "no photo evidence recorded for this door"}
    return {"property_id": int(pid), "doors": out}


def fit_boxes(boxes, det_wh, img_wh, tol=0.03):
    """Detection pixel boxes (in the detection's returned image, det_wh) -> the fetched image (img_wh).
    Returns (boxes or None, note). None when the framing differs (aspect ratios > tol apart): never draw misplaced boxes."""
    dw, dh = det_wh
    iw, ih = img_wh
    if not dw or not dh:
        return None, "detection image size unknown: boxes not drawn"
    if abs((iw / ih) / (dw / dh) - 1) > tol:
        return None, (f"image framing differs from the detection image ({iw}x{ih} vs {dw}x{dh}): boxes not drawn")
    sx, sy = iw / dw, ih / dh
    note = None if abs(sx - 1) < 1e-6 and abs(sy - 1) < 1e-6 else f"boxes rescaled x{sx:.3f}"
    return [[b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy] for b in boxes], note


def _font(size):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _footer(img, text):
    from PIL import Image, ImageDraw
    f = _font(max(13, img.width // 70))
    pad = 8
    lines, cur = [], ""
    dr = ImageDraw.Draw(img)
    for w in text.split(" "):
        t = (cur + " " + w).strip()
        if dr.textlength(t, font=f) > img.width - 2 * pad and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lh = int(f.size * 1.35)
    out = Image.new("RGB", (img.width, img.height + lh * len(lines) + 2 * pad), (18, 22, 28))
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        d.text((pad, img.height + pad + i * lh), ln, fill=(230, 235, 240), font=f)
    return out


def _oblique_proof(pid, store, cid, door):
    import io

    from PIL import Image, ImageDraw

    from proinspect.tools import vexcel as vx
    c = (store.get("proof_crops") or {}).get(cid)
    if not c:
        raise KeyError(f"crop {cid} is not in the dock store of {pid}")
    img = Image.open(io.BytesIO(vx.oblique(c["wkt"], c["view"], None, 0))).convert("RGB")
    mine = [h for v in door["evidence"] for h in v.get("hits") or [] if h["crop_id"] == cid]
    allb, note = fit_boxes([h["px_bbox"] for h in c["hits"]], (c.get("width"), c.get("height")), img.size)
    myb, _ = fit_boxes([h["px_bbox"] for h in mine], (c.get("width"), c.get("height")), img.size)
    dr = ImageDraw.Draw(img)
    lw = max(2, img.width // 300)
    if allb is not None:
        for x, y, w, h in allb:
            dr.rectangle([x, y, x + w, y + h], outline=(70, 70, 70), width=2)
        for x, y, w, h in myb or []:
            dr.rectangle([x - lw, y - lw, x + w + lw, y + h + lw], outline=ACCENT, width=lw + 1)
    best = max(mine, key=lambda h: h["conf"]) if mine else None
    text = (f"Vexcel oblique · camera looking {CAMERA_DIR.get(c['view'], c['view'])} · captured {c.get('date') or '?'}"
            + (f" · SAM3 '{best['label']}' {best['conf']:.2f}" if best else " · no SAM3 hit for this door in this crop")
            + f" · door {door['id']} (teal), other SAM3 hits in this crop grey"
            + (f" · NOTE: {note}" if note else ""))
    return _footer(img, text)


def _ortho_proof(pid, store, door):
    from PIL import Image, ImageDraw
    p = EXT / f"{int(pid)}_ortho.jpg"
    if not p.exists():
        raise FileNotFoundError(f"no cached ortho for {pid}")
    tw = _twin(pid)
    half = tw["frame"]["half_m"]
    img = Image.open(p).convert("RGB")
    n = img.width

    def px(x, y):
        return (x + half) / (2 * half) * n, (half - y) / (2 * half) * n
    dr = ImageDraw.Draw(img)
    for v in door["evidence"]:
        if v.get("poly_local"):
            dr.polygon([px(x, y) for x, y in v["poly_local"]], outline=ACCENT, width=3)
    cx, cy = px(door["x"], door["y"])
    r = 6
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=2)
    k = 40 / (2 * half) * n                                         # 40 m around the door
    img = img.crop((max(0, cx - k), max(0, cy - k), min(n, cx + k), min(n, cy + k))).resize((640, 640))
    tv = next((v for v in door["evidence"] if v["type"] == "trailer"), {})
    return _footer(img, f"Vexcel ortho (top-down) · SAM3 '{tv.get('ref', 'ortho:trailer').split(':')[-1]}' "
                        f"{tv.get('conf', 0):.2f} · trailer parked nose-in at door {door['id']} (teal outline, "
                        "door = white circle)")


def proof_image(pid, crop_id, door_id):
    """Render (once) and return the path of build/docks/proof/{pid}/{crop_id}_{door}.jpg."""
    store = load(pid)
    door = next((d for d in store["doors"] if d["id"] == door_id), None)
    if door is None:
        raise KeyError(f"door {door_id} is not in the dock store of {pid}")
    out = PROOF / str(int(pid)) / f"{crop_id}_{door_id}.jpg"
    if out.exists() and out.stat().st_mtime >= _path(pid).stat().st_mtime:
        return out
    im = _ortho_proof(pid, store, door) if crop_id == "ortho" else _oblique_proof(pid, store, crop_id, door)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=88)
    return out
