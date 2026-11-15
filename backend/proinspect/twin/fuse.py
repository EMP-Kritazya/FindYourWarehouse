"""Fusion: put the scanned floor where it is on the aerial footprint, and the dock doors where Vexcel saw them.

    register(property_id, model_id) -> dict   scan (interior npz, wall-aligned 'rot' frame) -> local ENU of the
                                              exterior twin: ENU = R(phi) @ p_rot + t, with a confidence
    fused_site(property_id, model_id) -> (Site, report)   site.from_interior with doors from the obliques, or the
                                              existing assumed doors (clearly flagged) when fusion is not trustworthy

Registration (pattern adapted from SiteWalk's register.py, not imported):
  * the scan's wall axes must line up with the footprint's dominant edge direction -> 4 candidate rotations;
  * for each rotation and each building on the parcel, a translation search (FFT cross-correlation on a 0.5 m raster)
    maximises  inside = share of scan floor inside the footprint  +  0.5 * walls = share of scan boundary on the
    footprint outline, minus a weak penalty for distance to the Matterport geocoordinate (+-15 m) when it exists;
  * confidence = margin between the best and the runner-up hypothesis (another rotation or building), plus the
    translation ambiguity (how far apart near-best translations are). Scans cover a median 22% of a building, so a
    small scan inside a big empty box is genuinely ambiguous and is reported as such.
Door detection is a LOWER BOUND (CLAUDE.md): missing doors are expected; a facade with no detected doors is never
treated as "no doors".
"""
import json
import math

import numpy as np
from scipy import ndimage, signal
from shapely.geometry import Polygon

from proinspect.config import BUILD
from proinspect.sim.export import grid_to_array
from proinspect.sim.site import Door, from_interior

RES = 0.5                    # registration raster (m)
MIN_MARGIN = 0.08            # best - runner-up score below this: rotation is not trustworthy
MIN_INSIDE = 0.80            # share of scan floor inside the footprint
MAX_AMBIGUITY_M = 6.0        # near-best translations spread wider than this: position is not trustworthy
DOOR_SNAP_M = 4.0            # a door must lie within this distance of the scan wall it is assigned to
SIDE_NORMAL = {"S": (0, -1), "N": (0, 1), "W": (-1, 0), "E": (1, 0)}     # npz grid sides, rot frame


def _rot(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def dominant_angle(coords):
    """Length-weighted footprint edge direction, mod 90 deg (radians)."""
    c = np.asarray(coords, float)
    d = np.diff(c, axis=0)
    ang = np.arctan2(d[:, 1], d[:, 0])
    w = np.hypot(d[:, 0], d[:, 1])
    return (math.atan2(np.sum(w * np.sin(4 * ang)), np.sum(w * np.cos(4 * ang))) / 4) % (math.pi / 2)


def _raster(poly, x0, y0, nx, ny):
    """Polygon -> bool [nx, ny] on cells of RES with lower-left corner (x0, y0)."""
    from matplotlib.path import Path
    xs = x0 + (np.arange(nx) + 0.5) * RES
    ys = y0 + (np.arange(ny) + 0.5) * RES
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    return Path(np.asarray(poly.exterior.coords)).contains_points(np.c_[X.ravel(), Y.ravel()]).reshape(nx, ny)


def scan_floor(model_id):
    g = np.load(BUILD / "twins" / "interior" / f"{model_id}.npz")
    floor = g["floor"].astype(bool)
    lab, n = ndimage.label(floor)
    if n > 1:
        floor = lab == (1 + int(np.argmax(ndimage.sum(floor, lab, range(1, n + 1)))))
    return floor, [float(v) for v in g["origin"]], float(g["theta"][0]), float(g["grid"][0])


def _geo_prior(model_id, frame):
    try:
        from proinspect.tools.elevation import to_local
        from proinspect.tools.matterport import model_overview
        geo = model_overview(model_id).get("geo")
    except Exception:                                    # offline / no key: no prior, said in the report
        return None
    if not geo or geo.get("latitude") is None:
        return None
    x, y = to_local(geo["latitude"], geo["longitude"], frame["lat0"], frame["lon0"])
    return float(x), float(y)


def register(property_id, model_id, ext=None):
    ext = ext or json.loads((BUILD / "twins" / "exterior" / f"{property_id}.json").read_text())
    floor, origin, theta, cell = scan_floor(model_id)
    ij = np.argwhere(floor)
    p_rot = np.c_[origin[0] + (ij[:, 0] + 0.5) * cell, origin[1] + (ij[:, 1] + 0.5) * cell]
    # scan boundary cells (walls of the scanned floor)
    edge = floor & ~ndimage.binary_erosion(floor)
    ije = np.argwhere(edge)
    e_rot = np.c_[origin[0] + (ije[:, 0] + 0.5) * cell, origin[1] + (ije[:, 1] + 0.5) * cell]
    prior = _geo_prior(model_id, ext["frame"])
    hyps = []
    for b in ext.get("buildings") or []:
        fp = Polygon(b["footprint_local"]).buffer(0)
        if fp.area < 100:
            continue
        th_b = dominant_angle(b["footprint_local"])
        minx, miny, maxx, maxy = fp.bounds
        pad = 10 + float(np.ptp(p_rot, axis=0).max())
        fx0, fy0 = minx - pad, miny - pad
        fnx, fny = int((maxx - minx + 2 * pad) / RES) + 1, int((maxy - miny + 2 * pad) / RES) + 1
        F = _raster(fp.buffer(0.75), fx0, fy0, fnx, fny).astype(float)
        Fin = _raster(fp, fx0, fy0, fnx, fny)
        FB = ndimage.binary_dilation(Fin & ~ndimage.binary_erosion(Fin), iterations=3).astype(float)   # +-1.5 m
        for k in range(4):
            phi = th_b + k * math.pi / 2
            R = _rot(phi)
            q, qe = p_rot @ R.T, e_rot @ R.T
            sx0, sy0 = q.min(axis=0) - RES
            snx = int((q[:, 0].max() - sx0) / RES) + 2
            sny = int((q[:, 1].max() - sy0) / RES) + 2
            S = np.zeros((snx, sny))
            S[((q[:, 0] - sx0) / RES).astype(int), ((q[:, 1] - sy0) / RES).astype(int)] = 1
            SE = np.zeros((snx, sny))
            SE[((qe[:, 0] - sx0) / RES).astype(int), ((qe[:, 1] - sy0) / RES).astype(int)] = 1
            inside = signal.correlate(F, S, mode="full", method="fft") / S.sum()
            walls = signal.correlate(FB, SE, mode="full", method="fft") / SE.sum()
            score = inside + 0.5 * walls
            # shift index (a, b): S[u, v] lands on F[u + a - (snx - 1), v + b - (sny - 1)]
            A, B = np.meshgrid(np.arange(score.shape[0]), np.arange(score.shape[1]), indexing="ij")
            tx = fx0 - sx0 + (A - (snx - 1)) * RES
            ty = fy0 - sy0 + (B - (sny - 1)) * RES
            if prior is not None:
                cx, cy = q.mean(axis=0)
                dist = np.hypot(tx + cx - prior[0], ty + cy - prior[1])
                score = score - 0.1 * np.clip(dist / 15.0 - 1.0, 0, None)       # free within +-15 m
            best = np.unravel_index(int(np.argmax(score)), score.shape)
            s_best = float(score[best])
            near = score >= s_best - 0.02
            spread = float(max(np.ptp(tx[near]), np.ptp(ty[near])))
            hyps.append({"building": b["index"], "k": k, "phi": phi, "score": round(s_best, 4),
                         "inside": round(float(inside[best]), 3), "walls": round(float(walls[best]), 3),
                         "t": [float(tx[best]), float(ty[best])], "ambiguity_m": round(spread, 1)})
    if not hyps:
        return {"ok": False, "why": "no Vexcel footprint to register to"}
    hyps.sort(key=lambda h: -h["score"])
    best = hyps[0]
    runner = hyps[1] if len(hyps) > 1 else None
    margin = best["score"] - runner["score"] if runner else best["score"]
    flip = None
    # A rectangular scan in a rectangular footprint fits about as well turned 180 (or, if square, 90) deg. If the
    # ambiguity is only among rotations on one building, the scan itself can break it: the facade with the detected
    # doors must be a FULL-HEIGHT exterior wall in the scan (office fronts / mezzanines stop well below the deck).
    if runner and margin < MIN_MARGIN:
        tied = [h for h in hyps if h["building"] == best["building"] and best["score"] - h["score"] < MIN_MARGIN]
        others = [h["score"] for h in hyps if h not in tied]
        if len(tied) >= 2 and (not others or best["score"] - max(others) >= MIN_MARGIN):
            flip = _resolve_flip(model_id, ext, tied)
            if flip["decisive"]:
                pick = tied[flip["pick"]]
                runner = next(h for h in tied if h is not pick)
                best = pick
                margin = flip["gap"]                    # decided by the wall cue: its gap is the margin
    ok = margin >= MIN_MARGIN and best["inside"] >= MIN_INSIDE and best["ambiguity_m"] <= MAX_AMBIGUITY_M
    why = None if ok else "; ".join(w for w, bad in (
        (f"rotation ambiguous (margin {margin:.2f} < {MIN_MARGIN})"
         + (f"; 180-deg flip not resolved: {flip['note']}" if flip else ""), margin < MIN_MARGIN),
        (f"scan only {best['inside']:.0%} inside the footprint", best["inside"] < MIN_INSIDE),
        (f"position ambiguous over {best['ambiguity_m']} m", best["ambiguity_m"] > MAX_AMBIGUITY_M)) if bad)
    conf = float(min(1.0, margin / 0.3)) * best["inside"] * (1.0 if best["ambiguity_m"] <= 2 else 0.7)
    if flip and flip["decisive"]:
        conf *= 0.7                                     # the flip was decided by a scan cue, not by the footprint
    return {"ok": bool(ok), "why": why, "confidence": round(conf, 2), "margin": round(float(margin), 3), **best,
            "theta_scan": theta, "geo_prior": prior is not None, "flip": flip,
            "runner_up": runner and {k: runner[k] for k in ("building", "k", "score", "inside", "walls")},
            "hypotheses": len(hyps), "all": hyps}


def wall_heights(model_id):
    """Per scan side (npz frame): share of the wall run that has a vertical surface between 70% of the roof deck and
    the deck (a full-height exterior wall), and between 0.3 and 2.5 m. Cached in build/twins/fuse/."""
    out_p = BUILD / "twins" / "fuse" / f"{model_id}_walls.json"
    if out_p.exists():
        return json.loads(out_p.read_text())
    from proinspect.twin import interior as I
    g = np.load(BUILD / "twins" / "interior" / f"{model_id}.npz")
    deck_ft = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text()).get("deck_ft")
    if not deck_ft:
        return None
    deck = deck_ft / 3.28084
    V, Fc, _, _ = I.load_mesh(model_id)
    P, N = I.sample_surface(V, Fc)
    th = float(g["theta"][0])
    P, N = I.rotate_xy(P, th), I.rotate_xy(N, th)
    coef, _ = I.floor_plane(P, N)
    h = P[:, 2] - (coef[0] * P[:, 0] + coef[1] * P[:, 1] + coef[2])
    floor, origin, _, cell = scan_floor(model_id)
    walls = _side_walls(floor, origin, cell)
    ij = np.argwhere(floor)
    lo = np.array(origin) + ij.min(0) * cell
    hi = np.array(origin) + (ij.max(0) + 1) * cell
    res = {}
    for side, (a, w) in walls.items():
        k = 1 if a == "y" else 0
        al = 1 - k
        d = (P[:, k] - w) * (1 if side in "NE" else -1)
        near = (d > -1.0) & (d < 2.0) & (np.abs(N[:, k]) > 0.8)
        bins = np.arange(lo[al], hi[al] + 0.5, 0.5)
        high = near & (h > 0.7 * deck) & (h < deck - 0.3)
        low = near & (h > 0.3) & (h < 2.5)
        res[side] = {"full_height": round(float((np.histogram(P[high, al], bins)[0] > 0).mean()), 2),
                     "low": round(float((np.histogram(P[low, al], bins)[0] > 0).mean()), 2)}
    out = {"model_id": model_id, "deck_m": round(deck, 2), "sides": res}
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(out))
    return out


def _door_facade_normal(ext, building):
    """Outward normal (ENU) of the facade of `building` with the most detected doors, and its door count."""
    from collections import Counter
    from proinspect.twin.exterior import _edges
    items = [d for d in (ext.get("doors") or {}).get("items") or [] if d["building"] == building]
    if not items:
        return None, 0, None
    edges = []
    for b in ext.get("buildings") or []:
        edges += _edges(Polygon(b["footprint_local"]).buffer(0), b["index"])
    ei, n = Counter(d["edge"] for d in items).most_common(1)[0]
    if ei >= len(edges):
        return None, 0, None
    return np.array(edges[ei]["n"]), n, edges[ei]["facing"]


def _side_of(phi, n_enu):
    n_rot = _rot(-phi) @ n_enu
    s = max(SIDE_NORMAL, key=lambda k: float(np.dot(SIDE_NORMAL[k], n_rot)))
    return s if float(np.dot(SIDE_NORMAL[s], n_rot)) >= 0.8 else None


def _resolve_flip(model_id, ext, tied, min_gap=0.3):
    n, count, facing = _door_facade_normal(ext, tied[0]["building"])
    if n is None:
        return {"decisive": False, "note": "no detected doors on this building to orient by"}
    try:
        wh = wall_heights(model_id)
    except Exception as e:                               # mesh unavailable offline: stay undecided
        return {"decisive": False, "note": f"wall heights unavailable ({type(e).__name__})"}
    if not wh:
        return {"decisive": False, "note": "no roof deck measured"}
    sides = [_side_of(h["phi"], n) for h in tied]
    fh = [wh["sides"][sd]["full_height"] if sd else -1.0 for sd in sides]
    order = sorted(range(len(tied)), key=lambda i: -fh[i])
    gap = fh[order[0]] - fh[order[1]]
    note = (f"door facade ({facing}, {count} doors) would be scan wall "
            + ", ".join(f"{sd} (full-height {f})" for sd, f in zip(sides, fh)))
    return {"decisive": bool(gap >= min_gap), "pick": order[0], "gap": round(float(gap), 2), "note": note,
            "sides": sides, "full_height": fh}


def rot_to_enu(reg, x, y):
    p = np.c_[np.atleast_1d(x), np.atleast_1d(y)] @ _rot(reg["phi"]).T + np.array(reg["t"])
    return p[:, 0], p[:, 1]


def enu_to_rot(reg, x, y):
    p = (np.c_[np.atleast_1d(x), np.atleast_1d(y)] - np.array(reg["t"])) @ _rot(-reg["phi"]).T
    return p[:, 0], p[:, 1]


def _side_walls(floor, origin, cell):
    """Per npz side: the wall line coordinate (rot frame) = median first/last floor cell along that side."""
    out = {}
    cols = np.where(floor.any(axis=1))[0]
    rows = np.where(floor.any(axis=0))[0]
    first_y = np.array([np.argmax(floor[i]) for i in cols])
    last_y = np.array([floor.shape[1] - np.argmax(floor[i][::-1]) for i in cols])
    first_x = np.array([np.argmax(floor[:, j]) for j in rows])
    last_x = np.array([floor.shape[0] - np.argmax(floor[:, j][::-1]) for j in rows])
    out["S"] = ("y", origin[1] + np.median(first_y) * cell)
    out["N"] = ("y", origin[1] + np.median(last_y) * cell)
    out["W"] = ("x", origin[0] + np.median(first_x) * cell)
    out["E"] = ("x", origin[0] + np.median(last_x) * cell)
    return out


def match_doors(reg, ext, model_id):
    """Exterior doors -> (scan side, position along that side in the rot frame). Doors must face the same way as
    the scan wall and sit within DOOR_SNAP_M of it, inside the scan's extent along the wall."""
    from proinspect.twin.exterior import _edges
    floor, origin, theta, cell = scan_floor(model_id)
    walls = _side_walls(floor, origin, cell)
    ij = np.argwhere(floor)
    xmin, ymin = origin[0] + ij.min(axis=0) * cell
    xmax, ymax = origin[0] + (ij[:, 0].max() + 1) * cell, origin[1] + (ij[:, 1].max() + 1) * cell
    edges = []
    for b in ext.get("buildings") or []:
        edges += _edges(Polygon(b["footprint_local"]).buffer(0), b["index"])
    Rinv = _rot(-reg["phi"])
    out, dropped = [], 0
    for d in (ext.get("doors") or {}).get("items") or []:
        e = edges[d["edge"]] if d["edge"] < len(edges) and edges[d["edge"]]["bldg"] == d["building"] else None
        n_enu = np.array(e["n"]) if e else np.array({"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}[d["facing"]])
        n_rot = Rinv @ n_enu
        x, y = (v[0] for v in enu_to_rot(reg, d["x"], d["y"]))
        side = max(SIDE_NORMAL, key=lambda s: float(np.dot(SIDE_NORMAL[s], n_rot)))
        if float(np.dot(SIDE_NORMAL[side], n_rot)) < 0.8:
            dropped += 1
            continue
        axis, wall = walls[side]
        off = abs((y if axis == "y" else x) - wall)
        along = x if axis == "y" else y
        lo, hi = (xmin, xmax) if axis == "y" else (ymin, ymax)
        if d["building"] != reg["building"] or off > DOOR_SNAP_M or not lo + 1.0 <= along <= hi - 1.0:
            dropped += 1
            continue
        out.append({"side": side, "x_rot": float(x), "y_rot": float(y), "off_m": round(float(off), 1),
                    "kind": d["class"], "confidence": d["confidence"], "facing": d["facing"], "edge": d["edge"],
                    "width_m": round(d["width_ft"] * 0.3048, 2) if d.get("width_ft") else None})
    return out, dropped


def _door_along_sim(side, x_rot, y_rot, site_tf, origin, cell):
    """rot-frame door point -> metres along the sim dock wall (sim x)."""
    i, j = (x_rot - origin[0]) / cell, (y_rot - origin[1]) / cell
    p, _ = grid_to_array(i, j, site_tf["rot90"], site_tf["shape0"])
    return float((p - site_tf["crop"][0]) * cell)


def costar_docks(property_id):
    """CoStar's external dock count for the property (None = unknown)."""
    p = BUILD / "catalog" / "warehouses.json"
    if not p.exists():
        return None
    w = next((w for w in json.loads(p.read_text()) if w["id"] == property_id), None)
    return (w or {}).get("docks_external") or None


def _assumed(model_id, property_id, why, **kw):
    """from_interior with assumed door positions; the door COUNT comes from CoStar when it has one."""
    n = costar_docks(property_id)
    site = from_interior(model_id, n_doors=n, **kw)
    if n:
        site.assumptions.append(f"door count from CoStar ({n} external docks), capped by what fits 4 m apart on the "
                                f"scanned dock wall: {len(site.doors)} doors")
    site.assumptions.append(why)
    return site


# Dock walls verified by a person in the Matterport walk-through, used when the aerial fusion has no doors to place.
# Each entry says what was seen; door POSITIONS along the wall are still assumed (CoStar count, 4 m apart).
SCAN_VERIFIED_DOCK_WALL = {
    "QHMtBN5Nnrc": ("E", "dock wall identified visually in the Matterport walk-through: overhead dock doors with "
                         "bumpers are on this wall (the automatic longest-wall pick chose a block wall with windows)"),
}


def fused_site(property_id, model_id, min_pitch_m=3.5):
    """The sim Site for a scan with doors from the obliques when the fusion is trustworthy, else the assumed doors.
    Returns (site, report); every decision lands in site.assumptions."""
    report = {"property_id": property_id, "model_id": model_id, "doors_source": "assumed"}
    p = BUILD / "twins" / "exterior" / f"{property_id}.json"
    ext_doors = 0
    if p.exists():
        ext_doors = (json.loads(p.read_text()).get("doors") or {}).get("total", 0)
    if model_id in SCAN_VERIFIED_DOCK_WALL and not ext_doors:
        side, why = SCAN_VERIFIED_DOCK_WALL[model_id]
        site = _assumed(model_id, property_id, why + "; door positions along it are assumed", dock_side=side)
        report.update(doors_source="scan_verified_wall", dock_side=side, why=why)
        return site, report
    if not p.exists():
        report["why"] = "no exterior twin for this property"
        site = _assumed(model_id, property_id, "doors ASSUMED: no aerial (Vexcel) twin for this property yet")
        return site, report
    ext = json.loads(p.read_text())
    if ext.get("parcel_mismatch"):
        report["why"] = "Vexcel parcel probably wrong (parcel_mismatch)"
        site = _assumed(model_id, property_id, "doors ASSUMED: the Vexcel parcel looks wrong for this property")
        return site, report
    reg = register(property_id, model_id, ext)
    report["registration"] = reg
    if not reg.get("ok"):
        report["why"] = f"registration not trustworthy: {reg.get('why')}"
        site = _assumed(model_id, property_id, f"doors ASSUMED: scan-to-aerial registration failed ({reg.get('why')})")
        return site, report
    matched, dropped = match_doors(reg, ext, model_id)
    report.update(matched=len(matched), dropped=dropped)
    conf_note = f"registration confidence {reg['confidence']}, margin {reg['margin']}"
    if matched:
        by_side = {}
        for m in matched:
            by_side.setdefault(m["side"], []).append(m)
        side = max(by_side, key=lambda s: (len(by_side[s]), sum(m["confidence"] for m in by_side[s])))
        site = from_interior(model_id, dock_side=side, doors=[])
        _, origin, _, cell = scan_floor(model_id)
        width = site.nx * site.cell
        cands = sorted(((_door_along_sim(side, m["x_rot"], m["y_rot"], site.transform, origin, cell), m)
                        for m in by_side[side]), key=lambda c: -c[1]["confidence"])
        chosen = []
        for x, m in cands:
            if 1.5 <= x <= width - 1.5 and all(abs(x - c[0]) >= min_pitch_m for c in chosen):
                chosen.append((x, m))
        chosen.sort(key=lambda c: c[0])
        if chosen:
            site.doors = [Door(round(x, 2), m["kind"], "vexcel", m["width_m"]) for x, m in chosen]
            kinds = sorted({m["kind"] for _, m in chosen})
            site.assumptions.append(
                f"{len(chosen)} doors placed from Vexcel oblique detections on the '{side}' scan wall "
                f"(facing {chosen[0][1]['facing']}; {conf_note}; door classes {', '.join(kinds)}). Detection is a "
                f"lower bound: real doors may be more")
            if dropped or len(chosen) < len(matched):
                site.assumptions.append(f"{dropped + len(matched) - len(chosen)} detected doors not used (outside the "
                                        "scanned area, on another wall, or closer than 3.5 m to a stronger detection)")
            report.update(doors_source="vexcel", dock_side=side, doors=len(chosen))
            return site, report
    # registration ok but no usable door on a scanned wall: use the truck-court facade if it maps to a scan wall
    courts = ext.get("courts") or []
    side = None
    if courts:
        from proinspect.twin.exterior import _edges
        edges = []
        for b in ext.get("buildings") or []:
            edges += _edges(Polygon(b["footprint_local"]).buffer(0), b["index"])
        c = courts[0]
        if c["edge"] < len(edges) and c.get("building") == reg["building"]:
            n_rot = _rot(-reg["phi"]) @ np.array(edges[c["edge"]]["n"])
            s = max(SIDE_NORMAL, key=lambda k: float(np.dot(SIDE_NORMAL[k], n_rot)))
            if float(np.dot(SIDE_NORMAL[s], n_rot)) >= 0.8:
                side = s
    if side:
        site = _assumed(model_id, property_id,
                        f"dock wall = the scan wall facing the Vexcel truck court ({courts[0]['facing']}; {conf_note}); "
                        "door POSITIONS are assumed (no detections on the scanned part)", dock_side=side)
        report.update(doors_source="court_side_assumed_positions", dock_side=side)
        return site, report
    site = _assumed(model_id, property_id, f"doors ASSUMED: registration ok ({conf_note}) but no Vexcel door or "
                    "truck court maps to a scanned wall")
    report["why"] = "no detected door on a scanned wall"
    return site, report
