"""Interior twin: what the Matterport scan says about a warehouse's inside. Method: docs/research/measurement.md §2.

    measure(model_id) -> dict with floor, deck / clear height (triple), coverage QC, columns + bay spacing,
                         free floor area, and a 25 cm occupancy grid saved for the simulator.

Frame: the mesh's own metric frame (z up), rotated so walls align with x/y (Manhattan axes).
Every number carries a confidence and the QC values that justify it. Nothing here is calibrated to CoStar;
validation against CoStar happens in proinspect.validate, never inside the measurement.
"""
import math

import numpy as np
from scipy import ndimage

from proinspect.config import BUILD
from proinspect.tools.matterport import mesh_obj, model_overview, normals, parse_obj, wall_axes

M_TO_FT = 3.28084
GRID = 0.25            # occupancy grid for the simulator (m)
CELL = 1.0             # analysis cell for ceilings (m)
SPACING = 0.10         # surface sample spacing (m)


# ------------------------------------------------------------------ geometry helpers
def load_mesh(model_id, res="500k"):
    """The densest mesh that actually covers the building. Asset labels are not trusted: some '500k' assets are
    tiny stubs (e.g. BNGoeNCMHtk: 252 vertices, 5 m across) while the '50k' one is the full model."""
    cands = []
    for r in dict.fromkeys([res, "500k", "50k"]):
        try:
            V, F = parse_obj(mesh_obj(model_id, r))
        except Exception:
            continue
        if len(F):
            span = float(np.ptp(V[:, :2], axis=0).max())
            cands.append((span >= 10 and len(F) >= 10_000, len(F), r, V, F, span))
    if not cands:
        raise ValueError(f"model {model_id} has no usable mesh")
    ok, nf, r, V, F, span = max(cands, key=lambda c: (c[0], c[1]))
    note = None if r == res else f"'{res}' mesh unusable ({[(c[2], c[1], round(c[5], 1)) for c in cands]}); used '{r}'"
    return V, F, r, note


def sample_surface(V, F, spacing=SPACING, seed=0):
    """Area-weighted points on the mesh surface, with their triangle normals."""
    n, area = normals(V, F)
    keep = area > 1e-6
    F, n, area = F[keep], n[keep], area[keep]
    rng = np.random.default_rng(seed)
    counts = np.floor(area / spacing ** 2 + rng.random(len(area))).astype(int)
    idx = np.repeat(np.arange(len(F)), counts)
    r1, r2 = rng.random(len(idx)), rng.random(len(idx))
    s = np.sqrt(r1)
    a, b, c = V[F[idx, 0]], V[F[idx, 1]], V[F[idx, 2]]
    P = (1 - s)[:, None] * a + (s * (1 - r2))[:, None] * b + (s * r2)[:, None] * c
    return P, n[idx]


def rotate_xy(P, theta):
    c, s = math.cos(-theta), math.sin(-theta)
    Q = P.copy()
    Q[:, 0], Q[:, 1] = c * P[:, 0] - s * P[:, 1], s * P[:, 0] + c * P[:, 1]
    return Q


def floor_plane(P, N):
    """Main floor: biggest peak of upward-facing area in z, refined by a least-squares plane on its inliers."""
    up = N[:, 2] > 0.95
    z = P[up, 2]
    bins = np.arange(z.min(), z.max() + 0.02, 0.02)
    hist, edges = np.histogram(z, bins=bins)
    z0 = float(edges[np.argmax(hist)] + 0.01)
    near = up & (np.abs(P[:, 2] - z0) < 0.10)
    A = np.c_[P[near, 0], P[near, 1], np.ones(near.sum())]
    coef, *_ = np.linalg.lstsq(A, P[near, 2], rcond=None)
    for _ in range(2):                                   # trim outliers, refit
        res = P[near, 2] - A @ coef
        ok = np.abs(res) < max(0.02, 2.5 * np.std(res))
        coef, *_ = np.linalg.lstsq(A[ok], P[near, 2][ok], rcond=None)
    res = P[near, 2] - A @ coef
    tilt = math.degrees(math.atan(math.hypot(coef[0], coef[1])))
    return coef, {"z0": z0, "tilt_deg": round(tilt, 3), "rms_cm": round(float(np.std(res)) * 100, 1),
                  "inliers": int(near.sum())}


def cluster_1d(x, tol=0.4):
    x = np.sort(np.asarray(x))
    groups, cur = [], [x[0]] if len(x) else []
    for v in x[1:]:
        if v - cur[-1] <= tol:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    if cur:
        groups.append(cur)
    return [(float(np.mean(g)), len(g)) for g in groups]


def fit_spacing(lines, lo=6.0, hi=22.0):
    """Robust bay spacing s from grid-line positions: minimise Huber residuals of x - (x0 + k s)."""
    xs = np.array([p for p, _ in lines])
    if len(xs) < 3:
        return None
    # Every sub-multiple of the true spacing fits equally well (lines at 0, 50, 100 also sit on a 25 grid), so
    # take the LARGEST spacing whose residuals are all small; fall back to the best robust fit.
    fits = []
    for s in np.arange(lo, hi, 0.05):
        k = np.round((xs - xs[0]) / s)
        if len(set(k)) < len(xs):                         # two lines on one grid node: spacing too large
            continue
        r = xs - (xs[0] + k * s)
        r = r - np.median(r)
        h = np.where(np.abs(r) < 0.3, 0.5 * r ** 2, 0.3 * (np.abs(r) - 0.15))
        fits.append((float(h.sum()), float(s), float(np.sqrt(np.mean(r ** 2))), float(np.abs(r).max())))
    if not fits:
        return None
    tight = sorted((f for f in fits if f[3] < 0.35), key=lambda f: f[1])
    if tight:
        # spacings that fit form intervals; take the interval of the largest spacing, then its best fit
        group = [tight[-1]]
        for f in reversed(tight[:-1]):
            if group[-1][1] - f[1] > 0.051:
                break
            group.append(f)
        cost, s, rms, _ = min(group, key=lambda f: f[2])
    else:
        cost, s, rms, _ = min(fits, key=lambda f: f[0])
    return {"spacing_m": round(s, 2), "residual_m": round(rms, 3), "lines": len(xs),
            "method": "largest consistent spacing" if tight else "best robust fit"}


# ------------------------------------------------------------------ the measurement
def measure(model_id, res="500k", save=True):
    """Not disk-cached on purpose (downloads are): a changed method must never serve a stale measurement.
    The result is written to build/twins/interior/<model>.json for the API."""
    V, F, res, mesh_note = load_mesh(model_id, res)
    theta = wall_axes(V, F)
    P, N = sample_surface(V, F)
    P = rotate_xy(P, theta)
    N = rotate_xy(N, theta)
    coef, fqc = floor_plane(P, N)
    h = P[:, 2] - (coef[0] * P[:, 0] + coef[1] * P[:, 1] + coef[2])     # height above the floor plane

    # 1 m analysis cells
    x0, y0 = P[:, 0].min(), P[:, 1].min()
    ix = ((P[:, 0] - x0) / CELL).astype(int)
    iy = ((P[:, 1] - y0) / CELL).astype(int)
    nx, ny = ix.max() + 1, iy.max() + 1
    cid = ix * ny + iy
    floor_pts = (N[:, 2] > 0.95) & (np.abs(h) < 0.15)
    floor_cell = np.zeros(nx * ny, bool)
    floor_cell[np.unique(cid[floor_pts])] = True

    over = h > 4.0
    deck = np.full(nx * ny, np.nan)
    np.fmax.at(deck, cid[over], h[over])
    # offices / mezzanines: a ceiling (downward-facing surface) between 2.0 and 4.0 m over the floor
    lowc = (h > 2.0) & (h < 4.0) & (N[:, 2] < -0.9)
    low_ceiling = np.zeros(nx * ny, bool)
    low_ceiling[np.unique(cid[lowc])] = True
    roofed = floor_cell & (~np.isnan(deck) | low_ceiling)     # floor with something overhead (yards have sky)
    office = roofed & low_ceiling
    indoor = roofed & ~low_ceiling & ~np.isnan(deck)          # the high-bay warehouse floor
    coverage = float(roofed.sum() / max(floor_cell.sum(), 1))
    deck_in = deck[indoor]
    deck_m = float(np.median(deck_in)) if len(deck_in) else None

    # clear height: lowest overhead obstruction in the band [deck-4.5, deck], excluding floor-connected stacks
    clear = None
    band_lo = max(deck_m - 4.5, 4.0) if deck_m else None     # below ~13 ft is not warehouse clear height
    if deck_m and indoor.sum() >= 20:
        vb = 0.25
        nb = int(math.ceil(max(band_lo, 0.5) / vb))
        occ = np.zeros((nx * ny, nb), bool)
        sel = (h > 0.3) & (h < band_lo)
        occ[cid[sel], ((h[sel] - 0.3) / vb).astype(int).clip(0, nb - 1)] = True
        floor_connected = occ[:, :max(nb - 1, 1)].all(axis=1)            # racks / walls / columns reach the band
        obs = (h >= band_lo) & (h <= deck_m + 0.5) & ((N[:, 2] < -0.3) | (np.abs(N[:, 2]) > 0.9))
        low = np.full(nx * ny, np.nan)
        np.fmin.at(low, cid[obs], h[obs])
        ok = indoor & ~floor_connected & ~np.isnan(low)
        if ok.sum() >= 20:
            v = low[ok]
            clear = {"p5_ft": round(float(np.percentile(v, 5)) * M_TO_FT, 1),
                     "p50_ft": round(float(np.percentile(v, 50)) * M_TO_FT, 1),
                     "cells": int(ok.sum())}

    # columns: in a low slice AND a high slice, 0.2-0.8 m across
    columns, bays = [], {}
    if deck_m:
        r = 0.05
        gx = ((P[:, 0] - x0) / r).astype(int)
        gy = ((P[:, 1] - y0) / r).astype(int)
        shape = (gx.max() + 1, gy.max() + 1)

        def slice_mask(lo, hi):
            m = np.zeros(shape, bool)
            s = (h > lo) & (h < hi) & (np.abs(N[:, 2]) < 0.5)
            m[gx[s], gy[s]] = True
            return ndimage.binary_closing(m, iterations=1)
        s1, s2 = slice_mask(1.2, 2.0), slice_mask(deck_m - 1.5, deck_m - 0.5)
        s2d = ndimage.binary_dilation(s2, iterations=int(0.3 / r))
        lab, n = ndimage.label(s1)
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            if sl is None:
                continue
            w, d = (sl[0].stop - sl[0].start) * r, (sl[1].stop - sl[1].start) * r
            if not (0.2 <= w <= 0.8 and 0.2 <= d <= 0.8 and 0.5 <= w / d <= 2.0):
                continue
            comp = lab[sl] == i
            if not (s2d[sl] & comp).any():
                continue
            cx = (sl[0].start + sl[0].stop) / 2 * r + x0
            cy = (sl[1].start + sl[1].stop) / 2 * r + y0
            columns.append([round(cx, 2), round(cy, 2), round(max(w, d), 2)])

    # 25 cm occupancy grid for the simulator: free floor = indoor floor with nothing in 0.1-2.0 m
    gxs = ((P[:, 0] - x0) / GRID).astype(int)
    gys = ((P[:, 1] - y0) / GRID).astype(int)
    gshape = (gxs.max() + 1, gys.max() + 1)
    floor_g = np.zeros(gshape, bool)
    fp = floor_pts
    floor_g[gxs[fp], gys[fp]] = True
    floor_g = ndimage.binary_closing(floor_g, iterations=2)
    blocked = np.zeros(gshape, bool)
    b = (h > 0.1) & (h < 2.0)
    blocked[gxs[b], gys[b]] = True
    indoor_g = np.kron(indoor.reshape(nx, ny), np.ones((int(CELL / GRID),) * 2, bool))[:gshape[0], :gshape[1]]
    pad = np.zeros(gshape, bool)
    pad[:indoor_g.shape[0], :indoor_g.shape[1]] = indoor_g
    indoor_g = ndimage.binary_dilation(pad, iterations=1) & floor_g
    free = indoor_g & ~ndimage.binary_dilation(blocked, iterations=1)
    # structure = what a new occupant can't move: occupied both at 1.2-2.0 m AND near the deck (walls, columns,
    # full-height racking). Everything else blocked (pallets, low racks, clutter) is the current tenant's contents.
    structure = np.zeros(gshape, bool)
    if deck_m:
        lo_s = np.zeros(gshape, bool)
        hi_s = np.zeros(gshape, bool)
        s1 = (h > 1.2) & (h < 2.0)
        s2 = (h > max(deck_m - 1.5, 2.5)) & (h < deck_m - 0.3) & (np.abs(N[:, 2]) < 0.5)
        lo_s[gxs[s1], gys[s1]] = True
        hi_s[gxs[s2], gys[s2]] = True
        structure = ndimage.binary_dilation(lo_s, iterations=1) & ndimage.binary_dilation(hi_s, iterations=2)
        structure = ndimage.binary_dilation(structure, iterations=1) & indoor_g

    # columns within 1 m of the floor's edge are perimeter pilasters / door jambs, not free-standing columns;
    # bay spacing is fitted on interior columns only (wall points would invent a grid)
    edge_dist = ndimage.distance_transform_edt(ndimage.binary_fill_holes(indoor_g)) * GRID
    interior_cols, pilasters = [], []
    for c in columns:
        i, j = int((c[0] - x0) / GRID), int((c[1] - y0) / GRID)
        inside = 0 <= i < gshape[0] and 0 <= j < gshape[1]
        (interior_cols if inside and edge_dist[i, j] > 1.0 else pilasters).append(c)
    if len(interior_cols) >= 4:
        cols = np.array(interior_cols)
        for axis, name in ((0, "x"), (1, "y")):
            lines = [ln for ln in cluster_1d(cols[:, axis]) if ln[1] >= 2]
            fit = fit_spacing(lines)
            if fit:
                fit["spacing_ft"] = round(fit["spacing_m"] * M_TO_FT, 1)
                fit["confident"] = fit["lines"] >= 3 and fit["residual_m"] < 0.3
            bays[name] = fit
    good_view = coverage >= 0.6 and indoor_g.sum() * GRID ** 2 > 500
    span = "clear span (no interior columns found)" if not interior_cols and good_view else \
        "columns found" if interior_cols else "unknown (scan coverage too thin)"
    out_dir = BUILD / "twins" / "interior"
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / f"{model_id}.npz", floor=indoor_g, blocked=blocked & indoor_g, free=free,
                        structure=structure,
                        origin=np.array([x0, y0]), theta=np.array([theta]), grid=np.array([GRID]))

    sqft = GRID ** 2 * 10.7639
    ov = model_overview(model_id)
    conf_clear = "high" if clear and coverage >= 0.6 and fqc["tilt_deg"] < 0.5 else \
        "medium" if clear and coverage >= 0.3 else "low"
    out = {
        "model_id": model_id, "mesh": res, "mesh_note": mesh_note, "scan_created": ov.get("created"),
        "office_sqft": round(office.sum() * CELL ** 2 * 10.7639),
        "axis_deg": round(math.degrees(theta), 2),
        "floor": {**fqc, "indoor_sqft": round(indoor_g.sum() * sqft), "free_sqft": round(free.sum() * sqft),
                  "pct_free": round(100 * free.sum() / max(indoor_g.sum(), 1), 1),
                  "with_sky_sqft": round((floor_cell & np.isnan(deck)).sum() * CELL ** 2 * 10.7639)},
        "overhead_coverage": round(coverage, 2),
        "deck_ft": round(deck_m * M_TO_FT, 1) if deck_m else None,
        "deck_range_ft": [round(float(np.percentile(deck_in, 10)) * M_TO_FT, 1),
                          round(float(np.percentile(deck_in, 90)) * M_TO_FT, 1)] if deck_m else None,
        "clear": clear, "clear_confidence": conf_clear,
        "columns": {"interior": len(interior_cols), "pilasters": len(pilasters), "structure": span, "bays": bays,
                    "positions": interior_cols[:400], "pilaster_positions": pilasters[:400]},
        "room_heights_ft": sorted({r["ceiling_ft"] for r in ov["rooms"] if r.get("ceiling_ft")}, reverse=True)[:5],
        "grid": {"file": f"twins/interior/{model_id}.npz", "cell_m": GRID, "shape": list(gshape)},
        "as_scanned": True,
    }
    if save:
        import json
        (out_dir / f"{model_id}.json").write_text(json.dumps(out, default=float))
    return out
