"""Equipment test core: does a vehicle fit and move through a floor? Pure grid maths in the sim Site frame.

Inputs: a bool `free` grid (cells a vehicle body may occupy), the `floor` grid (inside the building), 0.25 m cells.

Method (all numbers carry their basis in `assumptions`):
  * clearance  e(c) = distance from cell centre to the nearest blocked cell's edge (Euclidean distance transform;
    the building edge counts as a wall).
  * local width w(c) = min(horizontal, vertical) wall-to-wall free run through the cell. The grid is wall-aligned
    (interior twin), so for aisles and gaps this is their clear width.
  * passable     e(c) >= vehicle width / 2 + MARGIN                     (travel aisle = width + 2 x 0.3 m)
  * turning      a heading change (45 deg per cell) is allowed only in cells where the open space across
                 (min(2 e + cell, local width)) is >= outside turning radius + MARGIN (the turning centre sits at the vehicle's inside edge,
                 so the swept space across a 90 deg turn is about one turning radius).
  * search       Dijkstra over (cell, heading) states (8 headings, scipy csgraph), cost = length x (1 + a centre-line
                 preference) + a small turn cost. Multi-source for reachability.
  * if the strict search can't reach the target, a relaxed search (no margin, no turn rule) and then a
    "person can walk" search locate WHERE it fails, so every failure has a place and a number.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

FT = 0.3048
MARGIN = 0.3                  # m each side, travel aisle = vehicle width + 0.6 m (~2 ft)
DOOR_MARGIN = 0.15            # m each side through a door opening
HEIGHT_MARGIN_FT = 0.5        # overhead clearance above the lowered mast / overhead guard
TURN_COST = 0.4               # m-equivalent per 45 deg heading change
WALK_E = 0.3                  # a person can pass where clearance >= 0.3 m (diagnostics only)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


class FitError(ValueError):
    pass


@dataclass
class Vehicle:
    key: str
    name: str
    length_m: float
    width_m: float
    height_m: float               # mast lowered / overhead guard
    lift_m: float = None          # max fork height
    turn_r_m: float = None        # outside turning radius
    min_aisle_m: float = None     # right-angle stacking aisle
    speed_mps: float = 2.0
    notes: list = field(default_factory=list)

    def public(self):
        f = lambda v: round(v / FT, 1) if v is not None else None   # noqa: E731
        return {"key": self.key, "name": self.name, "length_ft": f(self.length_m), "width_ft": f(self.width_m),
                "height_lowered_ft": f(self.height_m), "lift_height_ft": f(self.lift_m),
                "turn_radius_ft": f(self.turn_r_m), "min_aisle_ft": f(self.min_aisle_m),
                "length_m": round(self.length_m, 2), "width_m": round(self.width_m, 2),
                "height_m": round(self.height_m, 2), "speed_mps": round(self.speed_mps, 2), "notes": self.notes}


def vehicle(spec):
    """A library key, or custom dims in ft {length, width, height_lowered, lift_height?, turn_radius?, min_aisle?}."""
    from proinspect.library import equipment
    lib = equipment()
    if isinstance(spec, str):
        if spec not in lib:
            raise FitError(f"unknown equipment {spec!r}; library keys: {sorted(lib)}")
        e = lib[spec]
        d = e.get("dims_ft") or {}
        if not d.get("width") or not d.get("length"):
            raise FitError(f"{spec} has no dimensions in the library")
        notes = []
        if e.get("indoor_ok") is False or e.get("category") == "road_vehicle":
            notes.append(f"{e['name']} is not an indoor warehouse vehicle")
        if e.get("category") == "fixed":
            notes.append(f"{e['name']} is fixed equipment: the route test treats it as if it could drive")
        sp = (e.get("speed_mph") or [4.5])[0] * 0.44704 * 0.8
        h = d.get("height_mast_lowered") or d.get("height") or 7.0
        return Vehicle(spec, e["name"], d["length"] * FT, d["width"] * FT, h * FT,
                       (e.get("lift_height_ft") or None) and e["lift_height_ft"] * FT,
                       (e.get("turn_radius_ft") or None) and e["turn_radius_ft"] * FT,
                       (e.get("min_aisle_ft") or None) and e["min_aisle_ft"] * FT, sp, notes)
    if not isinstance(spec, dict):
        raise FitError("equipment must be a library key or {length, width, height_lowered, ...} in ft")
    try:
        L, W = float(spec["length"]), float(spec["width"])
        H = float(spec.get("height_lowered") or spec.get("height") or 0) or None
    except (KeyError, TypeError, ValueError):
        raise FitError("custom equipment needs length and width (ft), and height_lowered (ft)")
    if H is None:
        raise FitError("custom equipment needs height_lowered (ft)")
    for nm, v in (("length", L), ("width", W), ("height_lowered", H)):
        if not (0.5 <= v <= 80):
            raise FitError(f"custom {nm} {v} ft is not plausible (0.5-80 ft)")
    opt = lambda k: float(spec[k]) * FT if spec.get(k) not in (None, "", 0) else None   # noqa: E731
    return Vehicle("custom", spec.get("name") or "Custom vehicle", L * FT, W * FT, H * FT, opt("lift_height"),
                   opt("turn_radius"), opt("min_aisle"), float(spec.get("speed_mps") or 2.0))


# ------------------------------------------------------------------ grid measures
def clearance(free, cell):
    """Distance (m) from each cell centre to the nearest blocked cell's edge; <= 0 on blocked cells."""
    d = ndimage.distance_transform_edt(np.pad(free, 1))[1:-1, 1:-1] * cell
    return np.where(free, d - cell / 2, 0.0)


def _runs(free, axis):
    """Length (cells) of the free run through each cell along `axis`."""
    a = free if axis == 0 else free.T
    out = np.zeros(a.shape, int)
    for j in range(a.shape[1]):
        col = a[:, j]
        if not col.any():
            continue
        lab, n = ndimage.label(col)
        if n:
            sizes = np.bincount(lab)
            sizes[0] = 0
            out[:, j] = sizes[lab]
    return out if axis == 0 else out.T


def local_width(free, cell):
    return np.minimum(_runs(free, 0), _runs(free, 1)) * cell


def _shift(a, dx, dy):
    """out[i, j] = a[i + dx, j + dy] (False outside)."""
    out = np.zeros_like(a)
    nx, ny = a.shape
    xs, xd = (slice(dx, nx), slice(0, nx - dx)) if dx >= 0 else (slice(0, nx + dx), slice(-dx, nx))
    ys, yd = (slice(dy, ny), slice(0, ny - dy)) if dy >= 0 else (slice(0, ny + dy), slice(-dy, ny))
    out[xd, yd] = a[xs, ys]
    return out


def state_graph(passable, turn_ok, cost):
    """(cell, heading) graph: straight moves keep the heading; a +-45 deg change needs a turn_ok source cell."""
    nx, ny = passable.shape
    N = nx * ny
    idx = np.arange(N).reshape(nx, ny)
    rows, cols, w = [], [], []
    tflat, cflat = turn_ok.ravel(), cost.ravel()
    for h, (dx, dy) in enumerate(DIRS):
        ok = passable & _shift(passable, dx, dy)
        if dx and dy:
            ok &= _shift(passable, dx, 0) & _shift(passable, 0, dy)
        src = idx[ok]
        dst = src + dx * ny + dy
        c = math.hypot(dx, dy) * 0.5 * (cflat[src] + cflat[dst])
        rows.append(src * 8 + h); cols.append(dst * 8 + h); w.append(c)
        t = tflat[src]
        for hp in ((h - 1) % 8, (h + 1) % 8):
            rows.append(src[t] * 8 + hp); cols.append(dst[t] * 8 + h); w.append(c[t] + TURN_COST)
    rows, cols, w = np.concatenate(rows), np.concatenate(cols), np.concatenate(w)
    return csr_matrix((w, (rows, cols)), shape=(N * 8, N * 8))


def cell_graph(passable, cost):
    nx, ny = passable.shape
    N = nx * ny
    idx = np.arange(N).reshape(nx, ny)
    rows, cols, w = [], [], []
    for dx, dy in DIRS:
        ok = passable & _shift(passable, dx, dy)
        if dx and dy:
            ok &= _shift(passable, dx, 0) & _shift(passable, 0, dy)
        src = idx[ok]
        dst = src + dx * ny + dy
        rows.append(src); cols.append(dst); w.append(math.hypot(dx, dy) * 0.5 * (cost.ravel()[src] + cost.ravel()[dst]))
    return csr_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N))


def _cost(e, cell):
    """Per-cell step cost (cells): 1 on open floor, up to 3 hugging an obstacle (prefer the aisle centre line)."""
    pref = 1.5
    return 1.0 + 2.0 * np.clip((pref - e) / pref, 0, 1)


# ------------------------------------------------------------------ the test
class Floor:
    """Precomputed measures of one floor grid (sim frame)."""

    def __init__(self, free, floor, cell=0.25):
        self.free = free.astype(bool) & floor.astype(bool)
        self.floor = floor.astype(bool)
        self.cell = cell
        self.e = clearance(self.free, cell)
        self.w = local_width(self.free, cell)
        self.cost = _cost(self.e, cell)
        self.open = np.where(self.free, np.minimum(2 * self.e + cell, self.w), 0.0)   # open space across a cell
        self.nx, self.ny = free.shape

    def uv(self, ij):
        i, j = ij
        return (i + 0.5) * self.cell, (j + 0.5) * self.cell

    def ij(self, u, v):
        return int(math.floor(u / self.cell)), int(math.floor(v / self.cell))

    def nearest(self, mask, u, v, max_m=6.0):
        """Nearest cell of `mask` to (u, v) within max_m, or None."""
        i0, j0 = self.ij(u, v)
        r = int(max_m / self.cell) + 1
        lo_i, hi_i = max(0, i0 - r), min(self.nx, i0 + r + 1)
        lo_j, hi_j = max(0, j0 - r), min(self.ny, j0 + r + 1)
        sub = mask[lo_i:hi_i, lo_j:hi_j]
        if not sub.any():
            return None
        I, J = np.nonzero(sub)
        I, J = I + lo_i, J + lo_j
        d = np.hypot((I + 0.5) * self.cell - u, (J + 0.5) * self.cell - v)
        k = int(np.argmin(d))
        return (int(I[k]), int(J[k])) if d[k] <= max_m else None


def _criteria(F, veh, margin=True, turns=True):
    hw = veh.width_m / 2 + (MARGIN if margin else 0.0)
    slack = F.cell / 2                      # the centre may sit between two cells (even-width gaps)
    passable = F.e >= hw - slack - 1e-9
    if turns and veh.turn_r_m:
        turn_ok = passable & (F.open >= veh.turn_r_m + MARGIN - 1e-9)
    else:
        turn_ok = passable
    return passable, turn_ok


def _search(F, passable, turn_ok, sources):
    G = state_graph(passable, turn_ok, F.cost)
    src = np.array([(s[0] * F.ny + s[1]) * 8 + h for s in sources for h in range(8)])
    dist, pred, _ = dijkstra(G, directed=True, indices=src, min_only=True, return_predecessors=True)
    return dist, pred


def _trace(pred, node):
    out = []
    while node >= 0:
        out.append(node)
        node = pred[node]
    return out[::-1]


def _best_state(dist, F, ij):
    n = ij[0] * F.ny + ij[1]
    d = dist[n * 8:n * 8 + 8]
    h = int(np.argmin(d))
    return (n * 8 + h) if np.isfinite(d[h]) else None


def _cells_of(states, F):
    return [((s // 8) // F.ny, (s // 8) % F.ny, s % 8) for s in states]


def _corners(path_cells, F):
    """Heading changes grouped within 2 m -> corners [(i, j, angle_deg, cells)]."""
    out, cur = [], None
    for k in range(1, len(path_cells)):
        dh = (path_cells[k][2] - path_cells[k - 1][2] + 4) % 8 - 4
        if dh == 0:
            continue
        if cur and k - cur["last"] <= int(2.0 / F.cell):
            cur["turn"] += dh
            cur["last"] = k
            cur["ks"].append(k - 1)
        else:
            if cur:
                out.append(cur)
            cur = {"first": k - 1, "last": k, "turn": dh, "ks": [k - 1]}
    if cur:
        out.append(cur)
    res = []
    for c in out:
        ang = abs(c["turn"]) * 45
        if ang < 45:
            continue
        mid = path_cells[c["ks"][len(c["ks"]) // 2]]
        cells = [path_cells[k] for k in c["ks"]]
        res.append((mid[0], mid[1], ang, cells))
    return res


def _turn_space(F, cells):
    """Open space across available near the turn cells (within 0.5 m)."""
    r = int(0.5 / F.cell)
    best = 0.0
    for i, j, _ in cells:
        sub = F.open[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]
        best = max(best, float(sub.max()))
    return best


def _check(kind, label, ok, need, have, unit, at=None, note=None, essential=True):
    return {"kind": kind, "label": label, "ok": ok, "need": need, "have": have, "unit": unit, "at": at, "note": note,
            "essential": essential}


def _ft(m):
    return None if m is None else round(m / FT, 1)


def _pinches(F, path_ij, need_m, k=1, spacing_m=3.0):
    """The narrowest spots (local width) along a path: up to k, spaced apart; every failing one is kept."""
    ws = np.array([F.w[i, j] for i, j in path_ij])
    order = np.argsort(ws, kind="stable")
    picked = []
    for o in order:
        i, j = path_ij[o]
        u, v = F.uv((i, j))
        fail = ws[o] < need_m - 1e-6
        if len(picked) >= k and not fail:
            break
        if any(math.hypot(u - p["u"], v - p["v"]) < spacing_m for p in picked):
            continue
        picked.append({"u": u, "v": v, "width_m": round(float(ws[o]), 2), "need_m": round(need_m, 2),
                       "ok": not fail})
        if len(picked) >= 8:
            break
    return picked


def evaluate(F, veh, start=None, target=None, doors=(), from_door=None, clear_ft=None, auto=False):
    """Run the equipment test in sim metres.

    start / target: (u, v) sim metres (target None with auto=True: reachability over the whole floor).
    doors: objects with id, u, v, nu, nv, width_m, height_m, kind (proinspect.doors.to_sim); from_door: one of them.
    Returns a dict in sim metres (positions as (u, v)); proinspect.fit.to_showcase converts it.
    """
    checks, assumptions = [], [
        f"travel aisle needed = vehicle width + 2 x {MARGIN} m; door opening = width + 2 x {DOOR_MARGIN} m",
        "turning: a heading change needs open space across >= outside turning radius + 0.3 m "
        "(the turning centre sits at the vehicle's inside edge)",
        "aisle widths are wall-to-wall free runs on the 25 cm scan grid (+-0.25 m)"]
    assumptions += veh.notes
    travel_need = veh.width_m + 2 * MARGIN
    # ---------------- overhead
    if clear_ft is None:
        checks.append(_check("clear_height", "Clear height vs vehicle (mast lowered)", None,
                             _ft(veh.height_m) + HEIGHT_MARGIN_FT, None, "ft", note="clear height unknown (?)"))
    else:
        need = round(_ft(veh.height_m) + HEIGHT_MARGIN_FT, 1)
        checks.append(_check("clear_height", "Clear height vs vehicle (mast lowered)", clear_ft >= need, need,
                             clear_ft, "ft", note="lowest overhead obstruction, P5 over the scanned high bay"))
    if veh.lift_m and veh.lift_m > 1.0:
        lift_ft = _ft(veh.lift_m)
        if clear_ft is None:
            checks.append(_check("lift", "Clear height vs top fork height", None, lift_ft, None, "ft",
                                 note="clear height unknown (?)", essential=False))
        else:
            ok = clear_ft >= lift_ft
            checks.append(_check("lift", "Clear height vs top fork height", ok, lift_ft, clear_ft, "ft",
                                 note=None if ok else f"forks can rise {lift_ft} ft but the lowest obstruction is "
                                 f"{clear_ft} ft: top rack levels above ~{round(clear_ft - 6, 0):.0f} ft "
                                 "(beam + 5 ft load + clearance) are out of reach of the full lift",
                                 essential=False))
    # ---------------- doors
    door_list = [from_door] if from_door is not None else ([d for d in doors if d.kind != "personnel"] if auto else [])
    for d in door_list:
        need_w = veh.width_m + 2 * DOOR_MARGIN
        at = (d.u, d.v)
        checks.append(_check("door", f"Door {d.id} clear width", d.width_m >= need_w, round(_ft(need_w), 1),
                             round(_ft(d.width_m), 1), "ft", at=at, note=f"{d.kind}, width marked in the scan"))
        need_h = round(_ft(veh.height_m) + HEIGHT_MARGIN_FT, 1)
        if d.height_m is None:
            checks.append(_check("door", f"Door {d.id} clear height", None, need_h, None, "ft", at=at,
                                 note="door height not marked: '?'", essential=False))
        else:
            checks.append(_check("door", f"Door {d.id} clear height", _ft(d.height_m) >= need_h, need_h,
                                 _ft(d.height_m), "ft", at=at))
    # ---------------- sources
    passable, turn_ok = _criteria(F, veh)
    if start is None and door_list:
        starts = []
        for d in door_list:
            s = F.nearest(passable, d.u - d.nu * 1.0, d.v - d.nv * 1.0, 4.0)
            if s is None:
                checks.append(_check("door", f"Door {d.id} to floor", False, round(_ft(travel_need), 1), None, "ft",
                                     at=(d.u, d.v), note="no floor space wide enough for the vehicle within 4 m "
                                     "inside this door (or the door is outside the scanned area)"))
            else:
                starts.append(s)
    elif start is not None:
        s = F.nearest(passable, *start, 6.0)
        starts = [s] if s else []
        if not s:
            raise FitError("no floor wide enough for this vehicle within 6 m of the start point")
    else:
        lab, n = ndimage.label(passable)
        if not n:
            starts = []
        else:
            big = 1 + int(np.argmax(ndimage.sum(passable, lab, range(1, n + 1))))
            I, J = np.nonzero(lab == big)
            k = len(I) // 2
            starts = [(int(I[k]), int(J[k]))]
            assumptions.append("no door marked: reachability measured from the largest open floor area")
    result = {"checks": checks, "path": [], "pinch_points": [], "reachable_pct": None, "heat": None,
              "assumptions": assumptions, "start": [F.uv(s) for s in starts]}
    if not starts:
        if not any(c["kind"] == "door" and c["ok"] is False for c in checks):
            checks.append(_check("corridor", "Floor access", False, round(_ft(travel_need), 1), None, "ft",
                                 note="no floor wide enough for this vehicle"))
        result["reachable_pct"] = 0.0
        return result

    dist, pred = _search(F, passable, turn_ok, starts)
    centre = np.isfinite(dist.reshape(-1, 8)).any(axis=1).reshape(F.nx, F.ny)
    # floor the vehicle can serve = within half its width + margin of a reachable centre position
    reach = F.free & (ndimage.distance_transform_edt(~centre) * F.cell <= veh.width_m / 2 + MARGIN + 1e-6)
    floor_n = max(int(F.floor.sum()), 1)
    result["reachable_pct"] = round(100.0 * reach.sum() / floor_n, 1)
    result["reach"] = reach
    result["centre"] = centre

    if auto or target is None:
        # pinch points of the whole floor: free regions this vehicle can't get to, and the narrowest gap on a
        # walking route into each
        walk = F.e >= WALK_E
        cut = walk & ~reach
        lab, n = ndimage.label(cut)
        pins = []
        if n:
            sizes = ndimage.sum(cut, lab, range(1, n + 1)) * F.cell ** 2
            G = cell_graph(walk, F.cost)
            wd, wp = dijkstra(G, directed=True, indices=[s[0] * F.ny + s[1] for s in starts], min_only=True,
                              return_predecessors=True)[:2]
            for k in np.argsort(-sizes)[:6]:
                if sizes[k] < 15:
                    break
                I, J = np.nonzero(lab == k + 1)
                ns = I * F.ny + J
                finite = np.isfinite(wd[ns])
                if not finite.any():
                    continue
                tgt = int(ns[finite][np.argmax(wd[ns][finite])])           # deep inside the region
                chain = _trace(wp, tgt)
                ij = [(c // F.ny, c % F.ny) for c in chain]
                # the bottleneck: lowest clearance on the walking route, away from its two ends
                skip = int(2.0 / F.cell)
                mid = ij[skip:-skip] if len(ij) > 2 * skip + 1 else ij
                blocked = [p for p in mid if not passable[p]]
                if not blocked:
                    continue
                p = min(blocked, key=lambda q: F.e[q])
                gap = min(float(F.w[p]), 2 * float(F.e[p]) + F.cell)
                u, v = F.uv(p)
                pins.append({"u": u, "v": v, "width_m": round(gap, 2), "need_m": round(travel_need, 2),
                             "ok": False, "area_m2": round(float(sizes[k]), 0)})
        result["pinch_points"] = pins
        unreached = float((F.floor & ~reach).sum()) * F.cell ** 2
        checks.append(_check("corridor", "Reachable floor", True, None,
                             result["reachable_pct"], "%", note=f"{unreached:.0f} m2 of floor out of reach "
                             f"(narrow gaps, contents, or floor too tight to turn)", essential=False))
        result["aisle_ok"] = (F.w >= (veh.min_aisle_m or travel_need) - 1e-6)
        return result

    # ---------------- route to a target
    tgt = F.nearest(reach, *target, 3.0)
    route_note = None
    if tgt is not None:
        node = _best_state(dist, F, tgt)
        path_cells = _cells_of(_trace(pred, node), F)
        strict = True
    else:
        strict = False
        # where does it fail? relaxed: no margin, no turning rule; then: where a person could walk
        p2, t2 = _criteria(F, veh, margin=False, turns=False)
        tgt_any = F.nearest(F.e >= WALK_E, *target, 3.0)
        if tgt_any is None:
            raise FitError("the target is not on open floor (within 3 m) in this floor model")
        path_cells = None
        for pas, tok, why in ((p2, t2, "relaxed"), (F.e >= WALK_E, F.e >= WALK_E, "walk")):
            s2 = [s for s in (F.nearest(pas, *F.uv(s0), 2.0) for s0 in starts) if s]
            t_ = F.nearest(pas, *target, 3.0)
            if not s2 or t_ is None:
                continue
            d2, pr2 = _search(F, pas, tok, s2)
            node = _best_state(d2, F, t_)
            if node is not None:
                path_cells = _cells_of(_trace(pr2, node), F)
                route_note = why
                break
        if path_cells is None:
            checks.append(_check("corridor", "Route to target", False, round(_ft(travel_need), 1), None, "ft",
                                 at=target, note="the target is not connected to the start on the scanned floor"))
            return result
    path_ij = [(i, j) for i, j, _ in path_cells]
    tu, tv = F.uv(path_ij[-1])
    if math.hypot(tu - target[0], tv - target[1]) > 0.75:
        assumptions.append(f"target moved {math.hypot(tu - target[0], tv - target[1]):.1f} m to the nearest floor "
                           "the vehicle can stand on")
    # corridor
    pins = _pinches(F, path_ij, travel_need)
    narrow = min(pins, key=lambda p: p["width_m"])
    corr_ok = strict or narrow["width_m"] >= travel_need - 1e-6
    checks.append(_check("corridor", "Narrowest point on the route", corr_ok, _ft(travel_need),
                         _ft(narrow["width_m"]), "ft", at=(narrow["u"], narrow["v"]),
                         note=None if corr_ok else "too narrow for this vehicle plus 1 ft each side"))
    # turns
    corners = _corners(path_cells, F)
    if veh.turn_r_m:
        need_t = veh.turn_r_m + MARGIN
        rows = []
        for i, j, ang, cells in corners:
            have = _turn_space(F, cells)
            u, v = F.uv((i, j))
            rows.append((have >= need_t - 1e-6, have, u, v, ang))
        bad = [r for r in rows if not r[0]]
        for ok, have, u, v, ang in bad[:5]:
            checks.append(_check("turn", f"{ang:.0f} deg turn", False, _ft(need_t), _ft(have), "ft", at=(u, v),
                                 note=f"needs {_ft(need_t)} ft of open space across to turn, has {_ft(have)} ft"))
            pins.append({"u": u, "v": v, "width_m": round(have, 2), "need_m": round(need_t, 2), "ok": False,
                         "what": "turn"})
        if rows and not bad:
            ok, have, u, v, ang = min(rows, key=lambda r: r[1])
            checks.append(_check("turn", f"Tightest turn ({len(rows)} on the route)", True, _ft(need_t), _ft(have),
                                 "ft", at=(u, v)))
        if not rows:
            checks.append(_check("turn", "Turns", True, _ft(need_t), None, "ft", note="straight route, no turns"))
    # working aisle at the target
    if veh.min_aisle_m:
        wt = float(F.w[path_ij[-1]])
        ok = wt >= veh.min_aisle_m - 0.05
        checks.append(_check("aisle", "Working aisle at the target (right-angle stacking)", ok, _ft(veh.min_aisle_m),
                             _ft(wt), "ft", at=(tu, tv),
                             note=None if ok else "the vehicle can get here but cannot turn 90 deg to put away a pallet"))
        if not ok:
            pins.append({"u": tu, "v": tv, "width_m": round(wt, 2), "need_m": round(veh.min_aisle_m, 2), "ok": False,
                         "what": "aisle"})
    if not strict:
        checks.append(_check("corridor", "Route to target", False, None, None, "",
                             at=(tu, tv), note="no route meets the vehicle's width and turning needs; the route "
                             f"shown is the closest ({'ignoring margins and turning' if route_note == 'relaxed' else 'where a person could walk'})"))
    result["pinch_points"] = pins
    result["path_uv"] = [F.uv((i, j)) + (h,) for i, j, h in path_cells]
    result["strict"] = strict
    return result


def verdict(checks):
    ess = [c for c in checks if c.get("essential", True)]
    if any(c["ok"] is False for c in ess):
        return False
    if any(c["ok"] is None for c in ess):
        return None
    return True


def simplify(points, tol=0.3):
    """Ramer-Douglas-Peucker on [(u, v)] -> indices kept."""
    P = np.asarray(points, float)
    if len(P) < 3:
        return list(range(len(P)))
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        d = P[b] - P[a]
        L = math.hypot(*d) or 1e-9
        seg = P[a + 1:b] - P[a]
        dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return list(np.nonzero(keep)[0])
