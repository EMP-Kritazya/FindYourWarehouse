"""Multi-truck yard maneuver test (POST /api/yard/maneuver, docs/CONTRACTS_v3.md section 3).

    maneuver(req) -> result dict             (real property: exterior twin + dock store / door fallback)
    maneuver(req, scene=synthetic_scene(..))  (tests)

Pipeline
  1. Scene: the yard grid + signed distance field (site.load), the gate (request > dock store > site.entrance if it is
     drivable > another drivable parcel crossing), doors (proinspect.docks.usable_doors > site.doors).
  2. Per door x truck type: the extended kinematic pull-out candidates (kinematics.pullout, held straight until the
     trailer is aligned) on the grid with the door opening carved. Failures are classed:
       geometric  - the clear depth in front of the door (rays on the aerial grid) is below the shallowest candidate's
                    need, or the dock position itself is blocked;
       assumption - a candidate clears the obstacles but not the 0.6 m buffer;
       planner    - the depth check passes but no candidate (or no tracked approach/exit) was found. Never "impossible".
  3. Legs, all integrated with the kinematic model (nothing is interpolated):
       enter/to_door  street spawn -> pull-past pose: corridor (route.CorridorGrid, right-hand lane offset, barrier
                      beyond the pull-past pose so the truck arrives going forward) tracked with pure pursuit;
       reverse        the time-reversed pull-out, rigidly placed on the tracked end pose;
       docked         dwell (default 90 s), extended while the way out is reserved by an earlier truck;
       pull_out/exit  the forward pull-out, then pursuit to the gate and out along the access (barrier behind).
  4. Coordination: prioritized space-time reservations (reserve.Table). Trucks plan in arrival order; each tries its
     doors (both pull-past sides) and takes the earliest conflict-free docking; when blocked it holds on the street
     before the gate (not drawn) or stays docked, retrying every 2 s; every wait is an event.
Frame: twin ENU metres (x east, y north). Output headings are compass degrees. The animated reference point is the
kingpin for a tractor-semitrailer and the rear axle for a rigid truck (vehicle_geometry).
"""
import json
import math
import time as _time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from proinspect.tools import elevation as el
from proinspect.yard import kinematics as kin
from proinspect.yard import pursuit as pp
from proinspect.yard import reserve as rv
from proinspect.yard import route as rt

TYPES = ("semi_53", "box_truck_26", "last_mile_van")
DWELL_S = 90.0
STAGGER_S = 30.0
RETRY_S = 2.0
WAIT_CAP_S = 1200.0
TRAVEL_CLEAR_M = 0.3          # minimum body-to-obstacle clearance while driving (the back-in keeps kin.CLEARANCE_M)
LANE_OFFSET_M = 1.8           # keep right of the aisle centre when the aisle is wide enough (two-way traffic)
STREET_M = 25.0               # access length drawn outside the gate
SETTLE_DEG = 1.0              # pull-past: trailer within this of the tractor before reversing
MAX_OPTIONS = 5
MAX_FAILED_DOORS = 6
CONGESTION_S = 60.0
PHASES = ("enter", "to_door", "reverse", "docked", "pull_out", "exit", "wait", "staging")


def _compass(th):
    return float((90.0 - math.degrees(th)) % 360.0)


def _unit(v):
    v = np.asarray(v, float)
    return v / (np.linalg.norm(v) or 1.0)


# ------------------------------------------------------------------ scene
@dataclass
class Scene:
    grid: kin.YardGrid
    road: np.ndarray
    parcel_mask: np.ndarray | None
    doors: list
    gate: dict                       # {x, y, nx, ny (outward), source, method}
    lat0: float
    lon0: float
    door_source: str = "assumed"
    notes: list = field(default_factory=list)
    property_id: int | None = None

    def ll(self, x, y):
        lat, lon = el.to_latlon(np.asarray(x, float), np.asarray(y, float), self.lat0, self.lon0)
        return round(float(lon), 7), round(float(lat), 7)

    def local(self, lon, lat):
        x, y = el.to_local(lat, lon, self.lat0, self.lon0)
        return float(x), float(y)


def _store_doors(pid):
    """Doors from the v3 dock store (S1). Returns (doors, notes) or (None, notes)."""
    try:
        from proinspect import docks
    except Exception:  # noqa: BLE001
        return None, ["dock store (proinspect.docks) not available: using the v2 door sources"]
    try:
        got = docks.usable_doors(pid)
    except Exception as exc:  # noqa: BLE001
        return None, [f"dock store failed ({exc}): using the v2 door sources"]
    if not got:
        return None, ["no usable doors in the dock store: using the v2 door sources"]
    out = []
    for i, d in enumerate(got):
        st = d.get("status") or "proposed"
        src = "confirmed" if st in ("confirmed", "user_added") else "proposed"
        n = d.get("normal") or [0, 1]
        n = [n["x"], n["y"]] if isinstance(n, dict) else list(n)
        w = d.get("width_ft")
        out.append({"id": str(d.get("id") or f"k{i + 1}"), "x": float(d["x"]), "y": float(d["y"]),
                    "normal": list(_unit(n)), "width_m": (w or 9.0) * kin.FT, "kind": d.get("kind") or "unknown",
                    "status": st, "source": src})
    return out, [f"doors: {len(out)} from the dock store ({sum(d['source'] == 'confirmed' for d in out)} confirmed)"]


def _store_gate(pid):
    try:
        from proinspect import docks
        g = (docks.load(pid) or {}).get("gate")
        if g and g.get("x") is not None:
            return g
    except Exception:  # noqa: BLE001
        pass
    return None


def _drivable_run(cg, x, y, nx, ny, inside=10.0, outside=15.0):
    """How far the corridor grid is free along the outward normal: (in_m, out_m)."""
    def run(sgn, lim):
        d = 0.0
        while d < lim:
            r, c = cg.node_rc(x + sgn * nx * d, y + sgn * ny * d)
            if not cg.free[r, c]:
                break
            d += 0.5
        return d
    return run(-1, inside), run(1, outside)


def _outward(parcel, x, y):
    from shapely.geometry import Point
    ring = parcel.exterior
    s = ring.project(Point(x, y))
    a, b = ring.interpolate(max(s - 1, 0)), ring.interpolate(min(s + 1, ring.length))
    tx, ty = b.x - a.x, b.y - a.y
    n = math.hypot(tx, ty) or 1
    nx, ny = ty / n, -tx / n
    if parcel.contains(Point(x + nx * 2, y + ny * 2)):
        nx, ny = -nx, -ny
    return nx, ny


def _free_dir(cg, x, y, away):
    """Outward direction for a user-placed gate: of 24 headings within 100 deg of `away` (from the doors to the gate),
    the one with the longest drivable run, ties to the most `away`."""
    best = None
    a0 = math.atan2(away[1], away[0])
    for k in range(24):
        a = 2 * math.pi * k / 24
        dif = abs(kin._wrap(a - a0))
        if dif > math.radians(100):
            continue
        _, o = _drivable_run(cg, x, y, math.cos(a), math.sin(a), 0.0, 40.0)
        sc = o - 5 * dif
        if best is None or sc > best[0]:
            best = (sc, math.cos(a), math.sin(a))
    return (best[1], best[2]) if best else (away[0], away[1])


def _wide_run(cg, x, y, nx, ny, lim=80.0, sd=3.0):
    """How far a wide corridor (corridor-grid clearance >= sd) continues outward from the gate."""
    d = 0.0
    while d < lim:
        r, c = cg.node_rc(x + nx * d, y + ny * d)
        if cg.sd[r, c] < sd or not cg.free[r, c]:
            break
        d += 1.0
    return d


def _in_notch(yard, x, y, reach=35.0):
    """True when the property's own buildings close in on the point from two opposite sides (a courtyard notch)."""
    from shapely.geometry import LineString
    polys = [p for _, p in getattr(yard, "footprints", [])]
    if not polys:
        return False
    hit = []
    for k in range(8):
        a = math.pi * k / 4
        ray = LineString([(x, y), (x + reach * math.cos(a), y + reach * math.sin(a))])
        hit.append(any(ray.intersects(p) for p in polys))
    return any(hit[k] and hit[(k + 4) % 8] for k in range(4))


def _gate(yard, cg, doors, override=None):
    from proinspect.yard import site
    dc = (float(np.mean([d["x"] for d in doors])), float(np.mean([d["y"] for d in doors])))
    if override is not None:
        x, y = override
        nx, ny = _free_dir(cg, x, y, _unit((x - dc[0], y - dc[1])))
        return {"x": x, "y": y, "nx": nx, "ny": ny, "source": "user", "method": "gate given in the request"}
    sg = _store_gate(yard.property_id)
    cands = []
    if sg is not None:
        x, y = float(sg["x"]), float(sg["y"])
        nx, ny = _outward(yard.parcel, x, y) if yard.parcel is not None else (0.0, 1.0)
        if sg.get("source") == "user":
            nx, ny = _free_dir(cg, x, y, _unit((x - dc[0], y - dc[1])))
            return {"x": x, "y": y, "nx": nx, "ny": ny, "source": "user", "method": "gate set by the user in the "
                                                                                    "dock editor"}
        cands.append((x, y, nx, ny, "gate from the dock store (inferred)"))
    (exy, en), method = site.entrance(yard, dc)
    cands.append((exy[0], exy[1], en[0], en[1], method))
    for (_, wide, cx, cy) in sorted(getattr(yard, "entrance_candidates", []), key=lambda c: -c[1]):
        if yard.parcel is not None:
            nx, ny = _outward(yard.parcel, cx, cy)
            cands.append((cx, cy, nx, ny, "gate inferred: the drivable paved crossing of the parcel line nearest "
                                           "a road-like pavement (aerial paved mask; no road map)"))
    out, seen = [], []

    def add(x, y, nx, ny, why):
        if any(math.hypot(x - a, y - b) < 8 for a, b in seen):
            return
        seen.append((x, y))
        out.append({"x": float(x), "y": float(y), "nx": float(nx), "ny": float(ny), "source": "inferred",
                    "method": why})
    for x, y, nx, ny, why in cands:
        i, o = _drivable_run(cg, x, y, nx, ny)
        if i >= 8 and o >= 12:
            add(x, y, nx, ny, why)
    # every drivable crossing of the parcel line (runs of 1 m samples), widest first
    if yard.parcel is not None:
        ring = yard.parcel.exterior
        runs, cur = [], []
        for s_ in np.arange(0, ring.length, 1.0):
            p = ring.interpolate(s_)
            nx, ny = _outward(yard.parcel, p.x, p.y)
            i, o = _drivable_run(cg, p.x, p.y, nx, ny, 12, 25)
            if i >= 8 and o >= 12:
                cur.append((p.x, p.y, nx, ny))
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for r in sorted(runs, key=len, reverse=True):
            x, y, nx, ny = r[len(r) // 2]
            add(x, y, nx, ny, f"gate inferred: a {len(r)} m drivable crossing of the parcel line in the aerial paved "
                              "mask (no road map; the street entrance heuristic point was not usable)")
    # a street entrance is not inside the property's own courtyard notch, and opens onto a wide corridor (a road);
    # prefer long road-like runs (>= 60 m of wide pavement outside)
    for i, g in enumerate(out):
        g["_notch"] = _in_notch(yard, g["x"], g["y"])
        g["_wide"] = _wide_run(cg, g["x"], g["y"], g["nx"], g["ny"])
        g["_i"] = i
    good = [g for g in out if not g["_notch"] and g["_wide"] >= 15]
    bad = sorted((g for g in out if g not in good), key=lambda g: (g["_notch"], -g["_wide"]))
    good.sort(key=lambda g: (g["_wide"] < 60, g["_i"]))
    out = good + bad
    for g in out:
        if g["_notch"]:
            g["method"] += " (inside a courtyard notch of the property: unlikely to be the street entrance)"
        for k in ("_notch", "_wide", "_i"):
            g.pop(k)
    if not out:
        x, y, nx, ny, why = cands[0]
        out.append({"x": float(x), "y": float(y), "nx": float(nx), "ny": float(ny), "source": "inferred",
                    "method": why + " (WARNING: not drivable for a truck in the aerial grid)"})
    return out


def scene_for(pid, gate_lonlat=None):
    from proinspect.yard import run as yrun
    from proinspect.yard import site
    yard = site.load(pid)
    notes = list(yard.notes)
    doors, dn = _store_doors(pid)
    notes += dn
    if doors is None:
        ds, src, n2 = site.doors(yard, yrun._model_ids(pid))
        notes += n2
        smap = {"marked_in_scan": "confirmed", "detected_oblique": "proposed"}
        doors = [{**d, "status": src, "source": smap.get(src, "assumed")} for d in ds]
    road = yard.paved | yard.vehicle
    cg = rt.CorridorGrid(yard.grid, road, yard.parcel_mask)
    ov = None
    if gate_lonlat:
        x, y = el.to_local(gate_lonlat["lat"], gate_lonlat["lon"], yard.lat0, yard.lon0)
        ov = (float(x), float(y))
    gates = _gate(yard, cg, doors, ov)
    if isinstance(gates, dict):
        gates = [gates]
    gate = gates[0]
    src = "confirmed" if all(d["source"] == "confirmed" for d in doors) else (
        "proposed" if all(d["source"] != "assumed" for d in doors) else "assumed")
    sc = Scene(yard.grid, road, yard.parcel_mask, doors, gate, yard.lat0, yard.lon0, src, notes, pid)
    sc._cg = cg
    sc._gates = gates
    return sc


def synthetic_scene(depth_ft, n_doors=6, pitch_m=8.0, width_m=240.0, block=None):
    """A test yard (kin.synthetic_court): building south of y = 0, court 0..depth, doors along y = 0, gate at the west
    end of the court. `block`: optional (x0, y0, x1, y1) rectangle of obstacle cells (kind 2)."""
    g = kin.synthetic_court(depth_ft, width_m=width_m, beyond=2)   # a fence: a real, measured limit
    if block is not None:
        k = g.kind.copy()
        x0, y0, x1, y1 = block
        R, C = np.mgrid[0:g.n, 0:g.n]
        X, Y = g.xy(R, C)
        k[(X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)] = 2
        g = kin.YardGrid(k, g.half, g.res)
    doors = [{"id": f"d{i + 1}", "x": (i - (n_doors - 1) / 2) * pitch_m, "y": 0.0, "normal": [0.0, 1.0],
              "width_m": 9 * kin.FT, "kind": "dock_high", "status": "confirmed", "source": "confirmed"}
             for i in range(n_doors)]
    gx = -width_m / 2 + STREET_M + 12
    gate = {"x": gx, "y": depth_ft * kin.FT / 2, "nx": -1.0, "ny": 0.0, "source": "user", "method": "synthetic"}
    road = g.kind == 0
    sc = Scene(g, road, None, doors, gate, 29.76, -95.37, "confirmed", [], None)
    sc._cg = rt.CorridorGrid(g, road, None)
    return sc


# ------------------------------------------------------------------ candidates
TURNS_DEG = (60.0, 75.0, 90.0, 105.0, 120.0)
STRAIGHTS_X = (0.0, 2.0, 4.0, 6.0, 9.0, 12.0, 15.0, 19.0, 24.0, 29.0, 35.0)
FRACS_X = (1.0, 0.85, 0.7)


def _pullout_t(m, straight_m, turn_dir, steer_frac, turn_deg, v=kin.REVERSE_SPEED, t_max=240.0):
    """kinematics.pullout generalised to a turn target of `turn_deg` (the canonical one turns exactly 90 deg)."""
    th = math.pi / 2
    s = [0.0, m.trailer_rear + m.trailer_wheelbase - m.hitch_offset, th, th] if m.articulated else \
        [0.0, m.rear_overhang, th, th]
    target = th - turn_dir * math.radians(turn_deg)
    dmax = m.delta_max * steer_frac
    out = [[0.0, *s]]
    t, travelled, phase, settle = 0.0, 0.0, "straight", 0.0
    while t < t_max:
        if phase == "straight" and travelled >= straight_m:
            phase = "turn"
        if phase == "straight":
            delta = 0.0
        else:
            err = kin._wrap(target - s[2])
            if phase == "turn" and abs(err) < math.radians(2):
                phase = "hold"
            delta = max(-dmax, min(dmax, 2.0 * err)) if phase == "hold" else -turn_dir * dmax
            if m.articulated:
                g = kin._wrap(s[2] - s[3])
                room = (kin.HITCH_MAX - abs(g)) / math.radians(10)
                if room < 1 and np.sign(delta) == np.sign(g):
                    delta *= max(room, 0.0)
        for _ in range(5):
            s = kin.step(m, s, v, delta, kin.DT / 5)
        t += kin.DT
        travelled += v * kin.DT
        out.append([round(t, 2), *s])
        if phase == "hold":
            g = abs(kin._wrap(s[2] - s[3])) if m.articulated else 0.0
            if g < math.radians(3) and abs(kin._wrap(target - s[2])) < math.radians(3):
                settle += v * kin.DT
                if settle >= 2.0:
                    break
    return np.array(out), target


def _settle(m, P, target):
    """Continue the forward pull-out holding the heading `target` until the trailer is aligned (SETTLE_DEG)."""
    s = list(P[-1, 1:5])
    t = P[-1, 0]
    out = []
    for _ in range(int(60 / (kin.REVERSE_SPEED * kin.DT))):
        err = kin._wrap(target - s[2])
        g = kin._wrap(s[2] - s[3]) if m.articulated else 0.0
        if abs(g) < math.radians(SETTLE_DEG) and abs(err) < math.radians(SETTLE_DEG * 0.75):
            break
        delta = max(-m.delta_max, min(m.delta_max, 2.0 * err))
        for _ in range(5):
            s = kin.step(m, s, kin.REVERSE_SPEED, delta, kin.DT / 5)
        t = round(t + kin.DT, 2)
        out.append([t, *s])
    return np.vstack([P, np.array(out)]) if out else P


def _disc_cloud(m, P, every=4):
    from proinspect.yard import hybrid as hy
    Q = P[::every]
    pts, rad = hy.body_disc_points(m, Q[:, 1], Q[:, 2], Q[:, 3], Q[:, 4])
    return pts.reshape(-1, 2), rad


def _cand_file(key):
    import hashlib
    from proinspect.config import BUILD
    h = hashlib.sha256((Path(__file__).read_bytes().split(b"# ------------------------------------------------------------------ candidates")[1]
                        .split(b"def _clear_depth")[0] + Path(kin.__file__).read_bytes())).hexdigest()[:10]
    return BUILD / "yard" / "cache" / f"cands_{key}_{h}.npz"


@lru_cache(maxsize=None)
def candidates(key):
    """Back-in family (forward pull-outs, settled): [(params, P canonical, disc cloud, disc radius, depth_needed)].
    Turn targets TURNS_DEG, straights <= 35 m, three steer fractions, both directions. Cached on disk."""
    m = kin.model(key)
    f = _cand_file(key)
    combos = [(d, sm, fr, td) for d in (1, -1) for sm in STRAIGHTS_X for fr in FRACS_X for td in TURNS_DEG]
    Ps = None
    if f.exists():
        try:
            z = np.load(f)
            Ps = [z[f"p{i}"] for i in range(len(combos))]
        except Exception:  # noqa: BLE001
            Ps = None
    if Ps is None:
        Ps = []
        for d, sm, fr, td in combos:
            P, target = _pullout_t(m, sm, d, fr, td)
            Ps.append(_settle(m, P, target))
        f.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(f, **{f"p{i}": P for i, P in enumerate(Ps)})
    out = []
    for (d, sm, fr, td), P in zip(combos, Ps):
        pts, rad = _disc_cloud(m, P)
        out.append(({"straight_m": sm, "turn": "right" if d == 1 else "left", "steer_frac": fr, "turn_deg": td},
                    P, pts, rad, float(pts[:, 1].max()) + rad + kin.CLEARANCE_M))
    return out


@lru_cache(maxsize=None)
def out_candidates(key):
    """Departure pull-outs (no settling): the classic 90 deg family, cut once the cab has swung 75 deg."""
    m = kin.model(key)
    out = []
    for params, P0, _ in kin.candidates(key):
        turned = np.abs(np.unwrap(P0[:, 3]) - P0[0, 3])
        i_cut = int(np.argmax(turned >= math.radians(75))) if (turned >= math.radians(75)).any() else len(P0) - 1
        P = P0[:max(i_cut, 10) + 1]
        pts, rad = _disc_cloud(m, P, every=3)
        out.append((params, P, pts, rad))
    return out


def fast_carve(grid, door_xy, normal, width, depth=2.5, half_win=45.0):
    """carve_slot without rebuilding the whole grid: the opening is cleared and the signed distance field is
    recomputed only in a window of +-half_win m around the door; outside the window the global field is kept (the
    carve can only raise distances near the door)."""
    from scipy import ndimage
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    t = np.array([n[1], -n[0]])
    r0, c0 = grid.rc(np.array([door_xy[0] - half_win]), np.array([door_xy[1] + half_win]))
    r1, c1 = grid.rc(np.array([door_xy[0] + half_win]), np.array([door_xy[1] - half_win]))
    r0, c0, r1, c1 = int(r0[0]), int(c0[0]), int(r1[0]) + 1, int(c1[0]) + 1
    K = grid.kind[r0:r1, c0:c1].copy()
    R, C = np.mgrid[r0:r1, c0:c1]
    X, Y = grid.xy(R, C)
    dx, dy = X - door_xy[0], Y - door_xy[1]
    along, out = dx * t[0] + dy * t[1], dx * n[0] + dy * n[1]
    sel = (K == 1) & (np.abs(along) <= width / 2 + kin.CLEARANCE_M + grid.res) & (out >= -depth) & (out <= grid.res)
    if not sel.any():
        return grid
    K[sel] = 0
    obst = K > 0
    d_out = ndimage.distance_transform_edt(~obst)
    d_in = ndimage.distance_transform_edt(obst)
    res = grid.res
    sdp = np.where(obst, -(d_in * res - res / 2), d_out * res - res / 2).astype(np.float32)
    # distance to the window border: beyond it the patch cannot see obstacles
    border = np.minimum.reduce([R - r0, r1 - 1 - R, C - c0, c1 - 1 - C]) * res
    glob = grid.sd[r0:r1, c0:c1]
    patch = np.where(sdp <= border, sdp, glob)

    class _G:
        pass
    g = _G()
    g.half, g.res, g.n, g.parcel = grid.half, grid.res, grid.n, getattr(grid, "parcel", None)
    g.kind = grid.kind.copy()
    g.kind[r0:r1, c0:c1] = K
    g.sd = grid.sd.copy()
    g.sd[r0:r1, c0:c1] = patch
    g.nearest = grid.nearest
    g.rc, g.xy = grid.rc, grid.xy
    g.carve_slot = lambda *a, **k: kin.YardGrid.carve_slot(grid, *a, **k)
    return g


def _clear_depth(grid, door, width):
    """Free distance straight out of the door (min over 5 rays across the opening) and what stops it."""
    n = _unit(door["normal"])
    t = np.array([n[1], -n[0]])
    best, what = None, None
    for off in np.linspace(-width / 2, width / 2, 5):
        d = 0.5
        while d < 120:
            x, y = door["x"] + t[0] * off + n[0] * d, door["y"] + t[1] * off + n[1] * d
            r, c = grid.rc(np.array([x]), np.array([y]))
            k = int(grid.kind[r[0], c[0]])
            if k:
                break
            d += 0.25
        if best is None or d < best:
            best, what = d, kin.YardGrid.KINDS.get(k, "the edge of the yard") if d < 120 else None
    return best, what


_SUB = {}


def _sub_pts(key, straight, turn, frac, P0):
    k = (key, straight, turn, frac)
    if k not in _SUB:
        _SUB[k] = kin.sample_points(kin.model(key), P0, every=3)
    return _SUB[k]


def _wall_face(grid, door, max_shift=2.0):
    """A door must start at the wall face. If the point just outside it (0.3 m along the normal) is inside a building
    cell, move it out along the normal up to `max_shift` m. Returns (door, note) or (None, door id) when it stays
    inside the footprint."""
    n = _unit(door["normal"])
    for o in np.arange(0.0, max_shift + 0.01, 0.25):
        x, y = door["x"] + n[0] * (o + 0.3), door["y"] + n[1] * (o + 0.3)
        r, c = grid.rc(np.array([x]), np.array([y]))
        if grid.kind[r[0], c[0]] != 1:
            if o == 0:
                return door, None
            return ({**door, "x": door["x"] + n[0] * o, "y": door["y"] + n[1] * o},
                    f"{door['id']} moved {o:.2f} m out to the wall face of the aerial footprint")
    return None, door["id"]


def door_options(scene, door, key):
    """Feasibility of one door for one vehicle type: {status, cls?, text, depth_clear_m, depth_need_m, sides:{turn: c}}
    where c = {params, margin, P (ENU forward pull-out), grid (carved)}."""
    m = kin.model(key)
    cache = scene.__dict__.setdefault("_door_cache", {})
    if (door["id"], key) in cache:
        return cache[(door["id"], key)]
    width = max(door.get("width_m") or 0, m.width)
    door, shift_note = _wall_face(scene.grid, door)
    if door is None:
        res = {"status": "fail", "cls": "assumption", "sides": {}, "out_sides": {}, "depth_clear_m": None,
               "depth_need_m": None, "text": f"{shift_note}: the door position looks off (it sits inside the building "
                                              "footprint); check it in the dock editor"}
        cache[(shift_note, key)] = res
        return res
    g = fast_carve(scene.grid, (door["x"], door["y"]), door["normal"], width)
    cands = candidates(key)
    need = min(c[4] for c in cands if c[0]["turn_deg"] == 90.0)     # the analytic standard: a 90 deg pull-out
    depth, what = _clear_depth(g, door, m.width)
    n = _unit(door["normal"])
    ex = np.array([n[1], -n[0]])
    R = np.array([[ex[0], ex[1]], [n[0], n[1]]])              # canonical (x, y) @ R -> ENU offset
    org = np.array([door["x"], door["y"]])

    def screen(cs, buffer):
        """Per candidate: min clearance of the disc cloud (disc edge to obstacle)."""
        out = []
        for c in cs:
            w = c[2] @ R + org
            r, cc = g.rc(w[:, 0], w[:, 1])
            out.append(float(g.sd[r, cc].min()) - c[3])
        return np.array(out)
    sc_in = screen(cands, kin.CLEARANCE_M)
    top = float(sc_in.max()) + 0.25 if len(sc_in) else -1e9      # disc slack: ~ the exact sample clearance
    sides = {}
    order = np.argsort([-(min(v - kin.CLEARANCE_M, 0.6) * 100 - cands[i][1][-1, 0] / 100) for i, v in enumerate(sc_in)])
    for i in order:
        if sc_in[i] < kin.CLEARANCE_M - 0.25:
            break
        params, P = cands[i][0], cands[i][1]
        name = f"{params['turn']}{int(params['turn_deg'])}"
        if name in sides or sum(1 for k in sides if k.startswith(params["turn"])) >= 2:
            continue
        Pw = kin.to_world(P, (door["x"], door["y"]), door["normal"], poses=True)
        sdf, _, _ = pp.clearance(m, g, Pw, every=1)
        if sdf >= kin.CLEARANCE_M - 0.05:
            sides[name] = {"params": {**params, "turn": params["turn"]}, "margin": round(sdf - kin.CLEARANCE_M, 2),
                           "P": Pw, "grid": g}
        if len(sides) >= 4:
            break
    outs = out_candidates(key)
    sc_out = screen(outs, TRAVEL_CLEAR_M)
    out_sides = {}
    for turn in ("right", "left"):
        idx = [i for i, c in enumerate(outs) if c[0]["turn"] == turn]
        for i in sorted(idx, key=lambda i: (-min(sc_out[i], 1.0), outs[i][1][-1, 0])):
            if sc_out[i] < TRAVEL_CLEAR_M - 0.25:
                break
            Pw = kin.to_world(outs[i][1], (door["x"], door["y"]), door["normal"], poses=True)
            sdf, _, _ = pp.clearance(m, g, Pw, every=1)
            if sdf >= TRAVEL_CLEAR_M - 0.05:
                out_sides[turn] = {"params": outs[i][0], "margin": round(sdf, 2), "P": Pw, "grid": g}
                break
    # dock slot itself (straight in, parked length)
    L = kin.parked_length(key) if not m.articulated else m.overall_length
    slot = np.array([[0, 0.2], [0, L - 0.2]])
    sx = np.linspace(-m.width / 2 + 0.1, m.width / 2 - 0.1, 4)
    sy = np.linspace(0.3, L - 0.3, int(L / 0.5))
    SX, SY = np.meshgrid(sx, sy)
    w = np.c_[SX.ravel(), SY.ravel()] @ R + np.array([door["x"], door["y"]])
    r, c = g.rc(w[:, 0], w[:, 1])
    slot_blocked = bool((g.sd[r, c] < 0).any())
    del slot
    res = {"depth_clear_m": round(depth, 1), "depth_need_m": round(need, 1), "sides": sides, "out_sides": out_sides,
           "door_eff": door, "shift_note": shift_note}
    # Only a building or a measured raised object (DSM) is a verified geometric limit. "Unpaved / landscaping" comes
    # from a colour test on the ortho (a dark truck well or a shadow can read as unpaved), so it stays an assumption.
    hard = not (what and ("unpaved" in str(what) or "landscap" in str(what)))
    soft_note = (" (the 'unpaved' call comes from a colour test on the aerial image; a dark truck well or shadow can "
                 "trigger it, so check the image before trusting this)")
    ft = lambda v: f"{v / kin.FT:.0f} ft"  # noqa: E731
    if sides:
        res.update(status="ok", text=f"{m.name} backs into {door['id']} (best margin "
                                     f"{max(s['margin'] for s in sides.values()) / kin.FT:.1f} ft beyond the "
                                     f"{kin.CLEARANCE_M / kin.FT:.0f} ft buffer)")
    elif slot_blocked and depth < 1.5 and what == kin.YardGrid.KINDS[1]:
        res.update(status="fail", cls="assumption",
                   text=f"{door['id']}: the door position looks off (a building is within {depth:.1f} m straight out "
                        "of it, so the door or its facing direction is probably misplaced); check it in the dock editor")
    elif slot_blocked:
        res.update(status="fail", cls="geometric" if hard else "assumption",
                   text=f"the dock position at {door['id']} is blocked by a {what or 'obstacle'} in the aerial grid"
                        + ("" if hard else soft_note))
    elif depth < need:
        res.update(status="fail", cls="geometric" if hard else "assumption",
                   text=f"{door['id']}: only {ft(depth)} clear in front of the door (to the {what}); a {m.name} "
                        f"needs at least {ft(need)} to back in with this model" + ("" if hard else soft_note))
    elif top >= 0:
        res.update(status="fail", cls="assumption",
                   text=f"{door['id']}: a back-in clears the obstacles but not the {kin.CLEARANCE_M / kin.FT:.0f} ft "
                        f"safety buffer (it would work only with a tighter margin)")
    else:
        res.update(status="fail", cls="planner",
                   text=f"{door['id']}: the court is deep enough ({ft(depth)} >= {ft(need)}) but none of the "
                        f"{len(cands)} steering candidates cleared; this is a planner limit, not proof it is impossible")
    cache[(door["id"], key)] = res
    return res


# ------------------------------------------------------------------ legs
def _lane_offset(cg, path, side, keep_start=8.0, keep_end=0.0):
    """Shift a path `side` (+1 right, -1 left of travel) by up to LANE_OFFSET_M where the aisle is wide enough."""
    p = pp.densify(path, 0.5)
    if len(p) < 5:
        return p
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    hd = np.arctan2(np.diff(p[:, 1]), np.diff(p[:, 0]))
    hd = np.r_[hd, hd[-1]]
    sdv = np.array([cg.sd[cg.node_rc(x, y)] for x, y in p])
    off = np.clip(sdv - 3.2, 0, LANE_OFFSET_M)
    ker = np.hanning(31)
    ker /= ker.sum()
    off = np.convolve(np.pad(off, 15, mode="edge"), ker, mode="valid")
    taper = np.minimum(np.clip(s / keep_start, 0, 1), np.clip((s[-1] - keep_end - s) / 8.0, 0, 1))
    off = off * taper
    rx, ry = np.sin(hd), -np.cos(hd)                     # right normal
    hdu = np.unwrap(hd)
    return np.c_[p[:, 0] + side * rx * off, p[:, 1] + side * ry * off] if np.all(np.isfinite(hdu)) else p


VARIANTS = tuple([{"lane": 1, "look": None, "radius": 18.0}, {"lane": 1, "look": None, "radius": 18.0, "wide": 0.6},
                  {"lane": 0, "look": None, "radius": 18.0, "wide": 1.0}, {"lane": 1, "look": None, "radius": 24.0}]
                 + [{"lane": 0, "look": look, "radius": rad, "wide": w}
                    for look in ((0.6, 4.0, 3.5, 8.0), (0.4, 3.0, 3.0, 6.0), (1.0, 6.0, 5.0, 11.0))
                    for rad in (12.0, 16.0, 22.0) for w in (0.3, 0.6, 1.0, 1.5)])


SEMI_WB = 21.5 * kin.FT


def _dev_limit(m):
    """Allowed axle drift from the reference: the semi value scaled by wheelbase for rigid trucks (1.2-2.5 m)."""
    return pp.MAX_DEV_M if m.articulated else float(np.clip(pp.MAX_DEV_M * m.wheelbase / SEMI_WB, 1.2, 2.5))


def _look(m, look):
    if m.articulated or look is None:
        return look
    f = max(m.wheelbase / SEMI_WB, 0.45)
    kv, l0, lmin, lmax = look
    return (kv * f, max(l0 * f, 2.0), max(lmin * f, 2.0), max(lmax * f, 4.0))


def _gate_in(scene):
    g = scene.gate
    return (g["x"] - g["nx"] * 6.0, g["y"] - g["ny"] * 6.0)


def _drive(scene, m, start, goal, lead, barrier_goal=None, barrier_start=None, v0=0.0, start_fwd=0.0, via=None,
           via_end=None):
    """Forward leg from state `start` [x,y,th0,th1] to the point `goal` (x, y, heading or None). Tries VARIANTS and
    returns (result, info) where result has poses (N x 6) or None with the reason."""
    cg = scene._cg
    bars = [b for b in (barrier_goal, barrier_start) if b is not None]
    g = scene.gate
    if via is not None or via_end is not None:    # the gate line: once in, the route stays inside
        bars.append((g["x"] + g["nx"] * 2.5, g["y"] + g["ny"] * 2.5, -g["ny"], g["nx"], 60.0))
    a = (start[0] + math.cos(start[2]) * start_fwd, start[1] + math.sin(start[2]) * start_fwd)
    head = [tuple(start[:2])] + ([a] if start_fwd else [])
    if via is not None:                    # straight in through the gate first
        head = [tuple(start[:2])]
        a = (scene.gate["x"], scene.gate["y"])
    if goal[2] is not None:
        L0 = (goal[0] - lead * math.cos(goal[2]), goal[1] - lead * math.sin(goal[2]))
    else:
        L0 = (scene.gate["x"], scene.gate["y"]) if via_end is not None else (goal[0], goal[1])
    r = cg.route(a, L0, barriers=bars)
    if r is None:
        return None, {"reason": "no drivable corridor between the two points (aerial grid)", "cls": "planner"}
    tail = [(goal[0], goal[1])] if goal[2] is not None else (
        [(goal[0], goal[1])] if via_end is not None else [])
    pts = head + r + tail
    worst = None
    seen_v = set()
    for v in VARIANTS:
        if not m.articulated:                  # swinging wide is for trailers: skip duplicate rigid variants
            sig = (v["lane"], v["look"], v["radius"])
            if sig in seen_v:
                continue
            seen_v.add(sig)
            v = {**v, "wide": 0}
        keep = lead if goal[2] is not None else 0.0
        path = pp.fillet(pts, v["radius"] * (1.0 if m.articulated else 0.6), keep_end_m=keep)
        if v["lane"]:
            path = _lane_offset(cg, path, 1, keep_start=max(start_fwd, 6.0), keep_end=keep)
        if v.get("wide"):
            path = pp.widen(path, m, v["wide"], cap=2.0 + v["wide"], keep_end_m=keep)
        res = pp.track(m, start, path, v0=v0, lookahead=_look(m, v["look"]), max_dev=_dev_limit(m))
        P = res["poses"]
        sd, where, k = pp.clearance(m, scene.grid, P)
        if res["ok"] and sd >= TRAVEL_CLEAR_M:
            return res, {"variant": v, "min_clear_m": round(sd, 2), "path": path}
        bad = {"reason": res.get("reason") or f"body would come within {sd:.1f} m of a "
                                               f"{kin.YardGrid.KINDS.get(k, 'obstacle')}",
               "where": where, "cls": "planner", "sd": sd}
        if worst is None or (bad["sd"] > worst["sd"]):
            worst = bad
    return None, worst


def _rigid(P, src, dst):
    """Rigidly move poses P (N x 5: t, x, y, th0, th1) so that pose `src` (x, y, th0) lands on `dst` (x, y, th0)."""
    d = dst[2] - src[2]
    c, s = math.cos(d), math.sin(d)
    out = P.copy()
    x, y = P[:, 1] - src[0], P[:, 2] - src[1]
    out[:, 1] = dst[0] + c * x - s * y
    out[:, 2] = dst[1] + s * x + c * y
    out[:, 3] = P[:, 3] + d
    out[:, 4] = P[:, 4] + d
    return out


def _even(P):
    """Pad a pose array to an even count (0.1 s steps -> whole 0.2 s slices) by holding the last pose."""
    if len(P) % 2:
        P = np.vstack([P, P[-1:]])
        P[-1, 0] = P[-2, 0] + kin.DT
    return P


def _lead(scene, Q, H, m):
    """The longest straight lead-in behind the pull-past pose where the truck fits (axle line clear of obstacles by
    half the width + 0.3 m): the search may join the lead-in anywhere along it."""
    g = scene.grid
    need = m.width / 2 + TRAVEL_CLEAR_M
    leads = (30.0, 20.0, 12.0, 6.0) if m.articulated else (20.0, 12.0, 6.0, 3.0)
    for L in leads:
        d = np.arange(1.0, L + 0.01, 0.5)
        r, c = g.rc(Q[1] - d * math.cos(H), Q[2] - d * math.sin(H))
        if (g.sd[r, c] >= need).all():
            return L
    return leads[-1]


def _heur(scene, m, tag, seeds):
    from proinspect.yard import hybrid as hy
    hc = scene.__dict__.setdefault("_heur", {})
    k = (tag, m.key)
    if k not in hc:
        t = _time.time()
        hc[k] = hy.Heuristic(scene.grid, m, seeds, buffer=hy.prims(m.key)["rad"] - m.width / 2 + hy.BUFFER_M)
        _tick(scene, "heuristic", t)
    return hc[k]


def _tick(scene, stage, t0):
    tm = scene.__dict__.setdefault("_timing", {})
    tm[stage] = tm.get(stage, 0.0) + (_time.time() - t0)


def _roots(scene, m, spawn):
    """Search roots at the street spawn: the inbound heading and +-45/90 deg (the street stub is not modelled, so a
    truck may arrive along it either way); only poses whose whole body clears obstacles by TRAVEL_CLEAR_M."""
    out = []
    for d in (0, 45, -45, 90, -90):
        th = spawn[2] + math.radians(d)
        if _body_clear(scene, m, spawn[0], spawn[1], th) >= TRAVEL_CLEAR_M:
            out.append([spawn[0], spawn[1], th, th])
    return out


def _fail_where(scene, r):
    c = r.get("closest")
    return (c[0], c[1]) if c else None


def _approach(scene, key, door, side, spawn):
    """spawn -> pull-past pose: Hybrid A* to the pull-past lead-in line, a straight pursuit snap onto the pull-past
    pose, then the placed back-in. Cached per (door, side)."""
    from proinspect.yard import hybrid as hy
    m = kin.model(key)
    Pw = side["P"]                                  # forward pull-out from the docked pose (ENU)
    Q = Pw[-1]
    H = Q[3]
    lead = _lead(scene, Q, H, m)
    gq = kin._wrap(Q[3] - Q[4]) if m.articulated else 0.0
    goal = hy.LineGoal([(Q[1], Q[2], H, gq, lead)], dth=3.0 if not m.articulated else 4.0,
                       settle_len=m.trailer_wheelbase * 1.3 if m.articulated else None,
                       min_snap=10.0 if m.articulated else 6.0)
    heur = _heur(scene, m, ("door", door["id"], side["params"]["turn"], side["params"].get("turn_deg")), goal.seeds())
    roots = _roots(scene, m, spawn)
    if not roots:
        return {"fail": {"reason": "the street spawn point outside the gate is blocked for this truck", "cls": "planner",
                         "leg": "approach", "where": spawn[:2]}}
    L0 = (Q[1] - lead * math.cos(H), Q[2] - lead * math.sin(H))

    def drive_in(st):
        """Analytic expansion: pure pursuit from a search state via the lead-in start onto the pull-past pose;
        accepted only when it lands on the pose (0.15 m, 1.5 deg, hitch) and stays clear."""
        if abs(kin._wrap(math.atan2(L0[1] - st[1], L0[0] - st[0]) - st[2])) > math.radians(75) and \
                math.hypot(L0[0] - st[0], L0[1] - st[1]) > 4:
            return None
        tr = pp.track(m, list(st), [(st[0], st[1]), L0, (Q[1], Q[2])], v0=0.0, max_dev=1.0,
                      lookahead=(0.3, 6.0, 5.0, 8.0) if not m.articulated else None)
        if not tr["ok"]:
            return None
        F = tr["poses"][-1]
        if (math.hypot(F[1] - Q[1], F[2] - Q[2]) > 0.15 or abs(kin._wrap(F[3] - Q[3])) > math.radians(1.5)
                or (m.articulated and abs(kin._wrap((F[3] - F[4]) - (Q[3] - Q[4]))) > math.radians(1.6))):
            return None
        sdv_, _, _ = pp.clearance(m, scene.grid, tr["poses"][:, :5], every=2)
        return tr if sdv_ >= TRAVEL_CLEAR_M else None
    t = _time.time()
    r = hy.search(m, scene.grid, roots, goal, heur, shot=lambda st: drive_in(st) is not None)
    _tick(scene, "search", t)
    scene.__dict__.setdefault("_explored", []).append(r["explored"])
    if not r["ok"]:
        c = r["closest"]
        return {"fail": {"reason": f"{r['reason']}: explored {r['explored']:,} truck positions and got within "
                                   f"{c[2]:.0f} m of the pull-past position", "cls": "planner", "leg": "approach",
                         "where": _fail_where(scene, r), "explored": r["explored"], "gap_m": round(c[2], 1)}}
    root = roots[r["root"]]
    P = hy.integrate(m, root, r["seq"]) if r["seq"] else np.array([[0.0, *root, 1]])
    E = P[-1]
    poses = hy.timed(P) if len(P) > 1 else np.array([[0.0, *root, 1]])
    if r.get("shot"):
        snap = drive_in(list(E[1:5]))
        if snap is None:
            return {"fail": {"reason": "the final drive onto the pull-past pose did not hold after exact "
                                       "re-integration", "cls": "planner", "leg": "approach", "where": (E[1], E[2])}}
    else:
        # snap: straight forward pursuit along the lead-in line onto the pull-past pose (exact model)
        snap = pp.track(m, list(E[1:5]), [(E[1], E[2]), (Q[1], Q[2])], v0=0.0,
                        lookahead=(0.3, 6.0, 5.0, 8.0) if not m.articulated else None, max_dev=_dev_limit(m))
    if len(snap["poses"]) > 1:
        sp_ = snap["poses"].copy()
        sp_[:, 0] += poses[-1, 0] + kin.DT
        sp_[:, 5] = 1
        poses = np.vstack([poses, sp_[1:]])
    Ef = poses[-1]
    resid = math.degrees(abs(kin._wrap((Ef[4] - Ef[3]) - (Q[4] - Q[3])))) if m.articulated else 0.0
    hd = math.degrees(abs(kin._wrap(Ef[3] - Q[3])))
    if math.hypot(Ef[1] - Q[1], Ef[2] - Q[2]) > 0.5 or resid > 2.0 or hd > 2.0:
        return {"fail": {"reason": f"the approach reached the lead-in but ended {math.hypot(Ef[1] - Q[1], Ef[2] - Q[2]):.2f}"
                                   f" m / heading {hd:.1f} deg / hitch {resid:.1f} deg off the pull-past pose",
                         "cls": "planner", "leg": "approach", "where": (Ef[1], Ef[2])}}
    sdv, where, _ = pp.clearance(m, scene.grid, poses[:, :5], every=1)
    if sdv < 0:
        return {"fail": {"reason": "the re-integrated approach clips an obstacle", "cls": "planner", "leg": "approach",
                         "where": where}}
    out_w = _rigid(Pw, (Q[1], Q[2], Q[3]), (Ef[1], Ef[2], Ef[3]))
    sd, where, k = pp.clearance(m, side["grid"], out_w, every=1)
    if sd < 0:
        return {"fail": {"reason": "placed back-in clips an obstacle", "cls": "planner", "where": where,
                         "leg": "reverse"}}
    rev = out_w[::-1].copy()
    rev[:, 0] = rev[0, 0] - rev[:, 0]
    ent = poses.copy()
    ent[:, 0] -= ent[0, 0]
    return {"ent": _even(ent), "rev": _even(rev[:, :5]), "dock": out_w[0, :5], "clear": round(sdv, 2),
            "margin": side["margin"], "turn": side["params"]["turn"], "resid_deg": resid,
            "explored": r["explored"], "root_heading": root[2]}


def _exit(scene, key, side, dock, exit_end):
    """Docked pose -> forward pull-out (cut once the cab has swung 75 deg) -> Hybrid A* to the street point."""
    from proinspect.yard import hybrid as hy
    m = kin.model(key)
    Pw = side["P"]
    out_w = _rigid(Pw, (Pw[0, 1], Pw[0, 2], Pw[0, 3]), (dock[1], dock[2], dock[3]))
    sd, where, k = pp.clearance(m, side["grid"], out_w, every=1)
    if sd < 0:
        return {"fail": {"reason": "placed pull-out clips an obstacle", "cls": "planner", "where": where,
                         "leg": "pull_out"}}
    Qe = out_w[-1]
    g = scene.gate
    goal = hy.PointGoal(exit_end[0], exit_end[1], None, tol=3.0)
    heur = _heur(scene, m, ("exit", round(exit_end[0], 1), round(exit_end[1], 1)), goal.seeds())
    t = _time.time()
    r = hy.search(m, scene.grid, [list(Qe[1:5])], goal, heur)
    _tick(scene, "search", t)
    scene.__dict__.setdefault("_explored", []).append(r["explored"])
    if not r["ok"]:
        c = r["closest"]
        return {"fail": {"reason": f"{r['reason']}: explored {r['explored']:,} truck positions and got within "
                                   f"{c[2]:.0f} m of the street", "cls": "planner", "leg": "exit",
                         "where": _fail_where(scene, r), "explored": r["explored"], "gap_m": round(c[2], 1)}}
    P = hy.integrate(m, list(Qe[1:5]), r["seq"])
    poses = hy.timed(P)
    sdv, where, _ = pp.clearance(m, scene.grid, poses[:, :5], every=1)
    if sdv < 0:
        return {"fail": {"reason": "the re-integrated exit clips an obstacle", "cls": "planner", "leg": "exit",
                         "where": where}}
    po = out_w.copy()
    po[:, 0] -= po[0, 0]
    exi = poses.copy()
    exi[:, 0] -= exi[0, 0]
    return {"po": _even(po[:, :5]), "exi": _even(exi), "clear": round(sdv, 2), "turn": side["params"]["turn"]}


def build_options(scene, key, door, sides, spawn, exit_end, cache, out_sides=None):
    """The first arrival side (90 deg first, then the others) whose approach is found, combined with the first
    departure side whose exit is found. Each leg is searched at most once."""
    out, fails = [], []
    order = sorted(sides, key=lambda k: (abs(sides[k]["params"].get("turn_deg", 90) - 90), -sides[k]["margin"]))
    for tin in order:
        sin = sides[tin]
        ck = ("in", door["id"], key, tin)
        if ck not in cache:
            cache[ck] = _approach(scene, key, door, sin, spawn)
        ap = cache[ck]
        if "fail" in ap:
            fails.append({**ap["fail"], "turn": tin})
            continue
        for tout, sout in (out_sides or sides).items():
            ck2 = ("out", door["id"], key, tin, tout)
            if ck2 not in cache:
                cache[ck2] = _exit(scene, key, sout, ap["dock"], exit_end)
            xp = cache[ck2]
            if "fail" in xp:
                fails.append({**xp["fail"], "turn": tout})
                continue
            gate = scene.gate
            sg = math.hypot(spawn[0] - gate["x"], spawn[1] - gate["y"])
            out.append({"key": key, "door": door, "turn": tin, "turn_out": tout, "margin": ap["margin"],
                        "street_len": sg + 2.0,
                        "legs": [("enter", ap["ent"], +1, None), ("reverse", ap["rev"], -1, None),
                                 ("pull_out", xp["po"], +1, None), ("exit", xp["exi"], +1, None)],
                        "dock_pose": ap["dock"], "clear_m": min(ap["clear"], xp["clear"]),
                        "dur_to_dock": float(ap["ent"][-1, 0] + ap["rev"][-1, 0]),
                        "dur_out": float(xp["po"][-1, 0] + xp["exi"][-1, 0])})
            return out, fails
    return out, fails


# ------------------------------------------------------------------ planning
def _rects(m, P):
    return rv.body_rects(m, P[::2])                  # 0.1 s poses -> 0.2 s slices


def _body_clear(scene, m, x, y, th):
    P = np.array([[0.0, x, y, th, th]])
    if m.articulated:                         # reference = tractor rear axle
        pass
    sd, _, _ = pp.clearance(m, scene.grid, P, every=1)
    return sd


def _spawn_exit(scene, key="semi_53"):
    """Spawn pose on the access outside the gate (inbound, right-hand lane) and the exit end point. The whole straight
    truck must clear obstacles by TRAVEL_CLEAR_M; the access is shortened until it does."""
    g = scene.gate
    m = kin.model(key)
    n = np.array([g["nx"], g["ny"]])
    din = -n
    right_in = np.array([din[1], -din[0]])
    hin = math.atan2(din[1], din[0])
    gp = np.array([g["x"], g["y"]])
    sp = None
    for out_len in np.arange(STREET_M, -12.0, -1.0):
        for lat in (LANE_OFFSET_M, 0.0):
            q = gp + n * out_len + right_in * lat
            if _body_clear(scene, m, q[0], q[1], hin) >= TRAVEL_CLEAR_M:
                sp = q
                break
        if sp is not None:
            break
    if sp is None:
        sp = gp + n * 6.0
    out_len = float(np.dot(sp - gp, n))
    ee = None
    for L in np.arange(max(out_len, 6.0), -12.0, -1.0):
        for lat in (LANE_OFFSET_M, 0.0):
            q = gp + n * L - right_in * lat
            fwd = q - n * (m.wheelbase + m.front_overhang)       # axle so the cab nose reaches q
            if _body_clear(scene, m, fwd[0], fwd[1], math.atan2(n[1], n[0])) >= TRAVEL_CLEAR_M:
                ee = fwd
                break
        if ee is not None:
            break
    if ee is None:
        ee = gp + n * 4.0
    return (float(sp[0]), float(sp[1]), hin), (float(ee[0]), float(ee[1]))


def _apron_cost(scene, doors, depth, factor=10.0):
    """Make the corridor avoid the zone in front of the doors where docked trucks stand (drive past their noses)."""
    cg = scene._cg
    if getattr(cg, "_apron", None) == (round(depth, 1), tuple(d["id"] for d in doors)):
        return
    if not hasattr(cg, "_cost0"):
        cg._cost0 = cg.cost.copy()
    cost = cg._cost0.copy()
    R, C = np.mgrid[0:cg.n, 0:cg.n]
    X, Y = cg.rc_xy(R, C)
    for d in doors:
        n = _unit(d["normal"])
        t = np.array([n[1], -n[0]])
        dx, dy = X - d["x"], Y - d["y"]
        along, out = dx * t[0] + dy * t[1], dx * n[0] + dy * n[1]
        zone = (np.abs(along) <= 10.0) & (out >= 0)
        cost[zone & (out <= depth)] = np.maximum(cost[zone & (out <= depth)], 50.0)
        cost[zone & (out > depth) & (out <= depth + 4.0)] *= 3.0
    cg.cost = cost
    cg._apron = (round(depth, 1), tuple(d["id"] for d in doors))


def maneuver(req, scene=None, internal=False):
    trucks = []
    for t in req.get("trucks") or []:
        if t.get("type") not in TYPES:
            raise ValueError(f"truck type must be one of {TYPES}")
        trucks += [t["type"]] * int(t.get("count") or 1)
    if not trucks:
        raise ValueError("give at least one truck")
    if len(trucks) > 12:
        raise ValueError("at most 12 trucks per test")
    dwell = float(req.get("dwell_s") or DWELL_S)
    stagger = float(req.get("stagger_s") or STAGGER_S)
    seed = int(req.get("seed") or 0)
    if scene is None:
        scene = scene_for(int(req["property_id"]), req.get("gate"))
    want = req.get("door_ids")
    doors = [d for d in scene.doors if not want or d["id"] in set(map(str, want))] or scene.doors
    longest = max(set(trucks), key=lambda k: kin.model(k).overall_length)
    _apron_cost(scene, doors, kin.model(longest).overall_length + 3.0)
    rng = np.random.default_rng(seed)
    arrivals = [round(round((i * stagger + (rng.uniform(-5, 5) if (seed and i) else 0)) / 0.2) * 0.2, 1)
                for i in range(len(trucks))]
    table = rv.Table(horizon_s=max(arrivals) + WAIT_CAP_S + 1800, max_rects=2 * len(trucks) + 2)
    issues, events = [], []
    door_res = {}
    for key in sorted(set(trucks), key=TYPES.index):
        for d in doors:
            door_res[(d["id"], key)] = door_options(scene, d, key)
    gates = getattr(scene, "_gates", None) or [scene.gate]
    t_g = _time.time()
    per_gate = []
    chosen = None
    for gi, gcand in enumerate(gates[:4]):          # the first gate from which a truck can reach a door
        scene.gate = gcand
        spawn, exit_end = _spawn_exit(scene, longest)
        leg_cache = {}
        per_gate.append((gcand, spawn, exit_end, leg_cache))
        if len(gates) == 1 or gcand["source"] == "user":
            chosen = per_gate[-1]
            break
        near = sorted((d for d in doors if door_res[(d["id"], longest)]["sides"]),
                      key=lambda d: math.hypot(d["x"] - gcand["x"], d["y"] - gcand["y"]))[:3]
        ok = False
        for d in near:
            dr = door_res[(d["id"], longest)]
            de = dr.get("door_eff") or d
            for tin, sin in dr["sides"].items():
                ck = ("in", de["id"], longest, tin)
                leg_cache[ck] = _approach(scene, longest, de, sin, spawn)
                if "fail" not in leg_cache[ck]:
                    ok = True
                    break
            if ok:
                break
        if ok:
            chosen = per_gate[-1]
            if gi:
                scene.notes.append(f"gate: {gi} earlier gate candidate(s) were skipped because no truck could be "
                                   "driven from them to a door")
            break
    if chosen is None:
        chosen = per_gate[0]
    scene.gate, spawn, exit_end, leg_cache = chosen
    door_opts = {}
    _tick(scene, "gate", t_g)

    def opts_for(d, key):
        ck = (d["id"], key)
        if ck not in door_opts:
            o, f = build_options(scene, key, door_res[ck].get("door_eff") or d, door_res[ck]["sides"], spawn,
                                 exit_end, leg_cache,
                                 door_res[ck]["out_sides"])
            door_opts[ck] = o
            if not o and f:
                door_res[ck]["leg_fail"] = f
        return door_opts[ck]

    gx, gy = scene.gate["x"], scene.gate["y"]
    plans = []
    for ti, key in enumerate(trucks):
        tid = f"T{ti + 1}"
        m = kin.model(key)
        a = arrivals[ti]
        # rank doors: not used by an earlier truck first, then nearest the gate; build legs lazily
        used = {p["opt"]["door"]["id"] for p in plans if p.get("opt")}
        order = sorted((d for d in doors if door_res[(d["id"], key)]["sides"]),
                       key=lambda d: (d["id"] in used, math.hypot(d["x"] - gx, d["y"] - gy)))
        cands, n_doors, n_fail = [], 0, 0
        for d in order:
            new = (d["id"], key) not in door_opts
            o = opts_for(d, key)
            if o:
                cands += o
                n_doors += 1
            elif new:
                n_fail += 1
            if n_doors >= MAX_OPTIONS or n_fail >= MAX_FAILED_DOORS:
                break
        cands.sort(key=lambda o: (o["door"]["id"] in used, o["dur_to_dock"] + 0.3 * o["dur_out"]))
        best, first_block = None, None
        for o in cands:
            pen = 20.0 if o["door"]["id"] in used else 0.0      # spread trucks over doors on a near tie
            res = _schedule(table, m, o, a, dwell, (best[1]["t_dock"] + best[2] - pen) if best else None)
            if res.get("first_block") and first_block is None:
                first_block = res["first_block"]
            if res.get("ok") and (best is None or res["t_dock"] + pen < best[1]["t_dock"] + best[2] - 1e-6):
                best = (o, res, pen)
                if res["t_dock"] <= a + o["dur_to_dock"] + 0.5 and not pen:
                    break                                   # no wait: nothing can be better
        if best is None:
            okd = [d for d in doors if door_res[(d["id"], key)]["status"] == "ok"]
            if not okd:
                cls = "geometric" if all(door_res[(d["id"], key)].get("cls") == "geometric" for d in doors) else \
                    ("assumption" if any(door_res[(d["id"], key)].get("cls") == "assumption" for d in doors)
                     else "planner")
                text = {"geometric": f"no tested door can take a {m.name}: the court in front of every door is "
                                     "too shallow or blocked (aerial geometry)",
                        "assumption": f"no door clears a {m.name} with the {kin.CLEARANCE_M / kin.FT:.0f} ft buffer",
                        "planner": f"the planner found no back-in for a {m.name} at any door; this is a planner "
                                   "limit, not proof that the yard cannot work"}[cls]
            elif not cands:
                cls, text = "planner", (f"the planner could not drive a {m.name} from the gate to any feasible door "
                                        "(approach or exit leg failed); a planner limit, not proof it is impossible")
            else:
                cls, text = "congestion", (f"{tid} could not get a conflict-free slot within "
                                           f"{WAIT_CAP_S / 60:.0f} min of arriving")
            plans.append({"id": tid, "key": key, "arrive": a, "status": "failed" if cls != "congestion" else "waiting",
                          "fail": {"class": cls, "text": text}})
            continue
        o, res, _ = best
        _commit(table, m, o, res, ti)
        plans.append({"id": tid, "key": key, "arrive": a, "status": "done", "opt": o, "res": res})
    return _output(scene, req, trucks, plans, door_res, doors, spawn, exit_end, dwell, arrivals, internal)


def _schedule(table, m, o, a, dwell, beat=None):
    """Earliest conflict-free plan for option o arriving at a. Returns {ok, t0, t_dock, t_dep, ...}."""
    legs = o["legs"]
    seg1 = np.concatenate([_rects(m, legs[0][1]), _rects(m, legs[1][1])])
    dock = _rects(m, legs[1][1][-1:])[0]
    seg3 = np.concatenate([_rects(m, legs[2][1]), _rects(m, legs[3][1])])
    n_dwell = int(round(dwell / rv.SLICE_S))
    step = int(round(RETRY_S / rv.SLICE_S))
    k_a = table.slice_of(a)
    first_block = None
    for k0 in range(k_a, k_a + int(WAIT_CAP_S / rv.SLICE_S), step):
        if beat is not None and (k0 + len(seg1)) * rv.SLICE_S >= beat:
            break
        c = table.first_conflict(k0, seg1)
        if c is not None:
            if first_block is None:
                first_block = {"slice": c[0], "owner": c[1], "pos": seg1[c[0], 0, :2].tolist(), "leg": "approach"}
            continue
        kd = k0 + len(seg1)
        c = table.first_conflict(kd, np.repeat(dock[None], n_dwell, axis=0))
        if c is not None:
            if first_block is None:
                first_block = {"slice": c[0], "owner": c[1], "pos": dock[0, :2].tolist(), "leg": "dock"}
            continue
        kdep = kd + n_dwell
        dep_block = None
        while kdep < kd + n_dwell + int(WAIT_CAP_S / rv.SLICE_S):
            c = table.first_conflict(kdep, seg3)
            if c is None:
                break
            if dep_block is None:
                dep_block = {"owner": c[1], "pos": seg3[c[0], 0, :2].tolist()}
            hold = np.repeat(dock[None], step, axis=0)
            if table.first_conflict(kdep, hold) is not None:
                kdep = None
                break
            kdep += step
        else:
            kdep = None
        if kdep is None:
            continue
        return {"ok": True, "k0": k0, "kd": kd, "kdep": kdep, "t0": k0 * rv.SLICE_S, "t_dock": kd * rv.SLICE_S,
                "t_dep": kdep * rv.SLICE_S, "first_block": first_block, "dep_block": dep_block,
                "n_dwell": n_dwell, "seg1": seg1, "seg3": seg3, "dock": dock}
    return {"ok": False, "first_block": first_block}


def _commit(table, m, o, res, owner):
    table.add(res["k0"], res["seg1"], owner)
    table.add(res["kd"], np.repeat(res["dock"][None], res["kdep"] - res["kd"], axis=0), owner)
    table.add(res["kdep"], res["seg3"], owner)


# ------------------------------------------------------------------ output
def _ref(m, x, y, th0):
    if m.articulated:
        return x + m.hitch_offset * math.cos(th0), y + m.hitch_offset * math.sin(th0)
    return x, y


def _timeline(m, o, res):
    """Absolute ENU timeline at 0.2 s: rows [t, x, y, th0, th1, gear, phase]."""
    rows = []
    legs = o["legs"]
    t = res["t0"]
    ent = legs[0][1]
    ent_street = np.r_[0, np.cumsum(np.hypot(*np.diff(ent[:, 1:3], axis=0).T))] <= o["street_len"]
    for i in range(0, len(ent), 2):
        rows.append([t + ent[i, 0], *ent[i, 1:5], 1, "enter" if ent_street[i] else "to_door"])
    t = res["t0"] + ent[-1, 0] + kin.DT
    rev = legs[1][1]
    for i in range(0, len(rev), 2):
        rows.append([t + rev[i, 0], *rev[i, 1:5], -1, "reverse"])
    kd = res["t_dock"]
    D = legs[1][1][-1]
    for k in range(res["kd"], res["kdep"]):
        rows.append([k * rv.SLICE_S, *D[1:5], 0, "docked"])
    t = res["t_dep"]
    po = legs[2][1]
    for i in range(0, len(po), 2):
        rows.append([t + po[i, 0], *po[i, 1:5], 1, "pull_out"])
    t = t + po[-1, 0] + kin.DT
    ex = legs[3][1]
    for i in range(0, len(ex), 2):
        rows.append([t + ex[i, 0], *ex[i, 1:5], 1, "exit"])
    del kd
    return rows


def _output(scene, req, trucks, plans, door_res, doors, spawn, exit_end, dwell, arrivals, internal):
    ll = scene.ll
    eq = kin._yaml()
    vehicles, events, issues = [], [], []
    door_lbl = {d["id"]: d for d in doors}
    tl_all = {}
    for vi, p in enumerate(plans):
        m = kin.model(p["key"])
        v = {"id": p["id"], "type": p["key"], "color_idx": vi, "status": p["status"], "arrive_s": p["arrive"]}
        if p["status"] != "done":
            v.update(target={"kind": "street", "id": None}, keys=[], path=[], fail=p["fail"])
            vehicles.append(v)
            gl = ll(scene.gate["x"], scene.gate["y"])
            issues.append({"class": p["fail"]["class"], "severity": "high", "where": list(gl), "text": p["fail"]["text"],
                           "trucks": [p["id"]]})
            continue
        o, res = p["opt"], p["res"]
        rows = _timeline(m, o, res)
        tl_all[p["id"]] = rows
        keys = []
        for t, x, y, th0, th1, gear, ph in rows:
            rx, ry = _ref(m, x, y, th0)
            keys.append([round(t, 1), *ll(rx, ry), round(_compass(th0), 1),
                         round(_compass(th1 if m.articulated else th0), 1), gear, ph])
        path = []
        for t, x, y, th0, th1, gear, ph in rows[::5]:
            if ph == "docked":
                continue
            rx, ry = _ref(m, x, y, th0)
            path.append([*ll(rx, ry), gear])
        v.update(target={"kind": "door", "id": o["door"]["id"]}, keys=keys, path=path,
                 door_side={"arrive": o["turn"], "leave": o["turn_out"]}, backin_margin_m=o["margin"], drive_clearance_m=o["clear_m"])
        vehicles.append(v)
        d = o["door"]
        gl = list(ll(scene.gate["x"], scene.gate["y"]))
        events.append({"t_s": round(p["arrive"], 1), "truck": p["id"], "kind": "arrive", "where": gl,
                       "text": f"{p['id']} ({m.name}) arrives at the gate"})
        wait_in = res["t0"] - p["arrive"]
        if wait_in > 0.05:
            fb = res.get("first_block") or {}
            who = f"T{fb['owner'] + 1}" if fb.get("owner", -1) >= 0 else None
            where = list(ll(*fb["pos"])) if fb.get("pos") else gl
            events.append({"t_s": round(p["arrive"], 1), "truck": p["id"], "kind": "conflict", "where": where,
                           "blocked_by": who, "text": f"{p['id']}'s path to {d['id']} crosses "
                                                     f"{who or 'another truck'} here"})
            events.append({"t_s": round(p["arrive"], 1), "truck": p["id"], "kind": "wait", "where": gl,
                           "blocked_by": who, "seconds": round(wait_in, 1),
                           "text": f"{p['id']} waits {wait_in / 60:.1f} min on the street before the gate "
                                   f"for {who or 'the court'} to clear"})
        events.append({"t_s": round(res["t_dock"], 1), "truck": p["id"], "kind": "dock",
                       "where": list(ll(d["x"], d["y"])), "text": f"{p['id']} docked at {d['id']}"})
        hold = res["t_dep"] - res["t_dock"] - dwell
        if hold > 0.05:
            db = res.get("dep_block") or {}
            who = f"T{db['owner'] + 1}" if db.get("owner", -1) >= 0 else None
            events.append({"t_s": round(res["t_dock"] + dwell, 1), "truck": p["id"], "kind": "wait",
                           "where": list(ll(d["x"], d["y"])), "blocked_by": who, "seconds": round(hold, 1),
                           "text": f"{p['id']} is ready but stays at {d['id']} {hold / 60:.1f} min until "
                                   f"{who or 'the way out'} clears"})
        events.append({"t_s": round(res["t_dep"], 1), "truck": p["id"], "kind": "depart",
                       "where": list(ll(d["x"], d["y"])), "text": f"{p['id']} pulls out of {d['id']}"})
        p["wait_s"] = wait_in + max(hold, 0)
        p["end"] = rows[-1][0]
    events.sort(key=lambda e: (e["t_s"], e["truck"]))

    # door-level issues (per type), merged across types
    types = sorted(set(trucks), key=TYPES.index)
    for d in doors:
        for key in types:
            dr = door_res[(d["id"], key)]
            if dr["status"] != "ok":
                issues.append({"class": dr["cls"], "severity": "med" if dr["cls"] != "geometric" else "high",
                               "where": list(ll(d["x"], d["y"])), "text": dr["text"], "trucks": [], "door": d["id"],
                               "vehicle": key})
            elif dr.get("leg_fail") and len(dr["leg_fail"]) >= len(dr["sides"]):
                lf = dr["leg_fail"][0]
                issues.append({"class": "planner", "severity": "low", "where": list(ll(*(lf.get("where") or
                                                                                          (d["x"], d["y"])))),
                               "text": f"{d['id']}: the {lf['leg']} leg for a {kin.model(key).name} was not found "
                                       f"({lf['reason']}); a planner limit, not proof it is impossible",
                               "trucks": [], "door": d["id"], "vehicle": key})
    waits = [(p["id"], p["wait_s"]) for p in plans if p.get("wait_s", 0) > CONGESTION_S]
    if waits:
        wt = [w for w in events if w["kind"] == "wait" and w.get("seconds", 0) > 0]
        worst = max(wt, key=lambda e: e["seconds"])
        issues.append({"class": "congestion", "severity": "high" if worst["seconds"] > 300 else "med",
                       "where": worst["where"], "trucks": [w[0] for w in waits],
                       "text": f"{', '.join(w[0] for w in waits)} waited more than {CONGESTION_S:.0f} s "
                               f"(worst {worst['truck']}: {worst['seconds'] / 60:.1f} min, {worst['text']})"})
    if scene.door_source == "assumed":
        issues.append({"class": "assumption", "severity": "med",
                       "where": list(ll(np.mean([d["x"] for d in doors]), np.mean([d["y"] for d in doors]))),
                       "text": "door positions are assumed (no confirmed or proposed doors): mark them in the dock "
                               "editor to test the real doors", "trucks": [], "door": None})
    if scene.gate["source"] != "user":
        issues.append({"class": "assumption", "severity": "low", "where": list(ll(scene.gate["x"], scene.gate["y"])),
                       "text": "the truck gate is inferred from the aerial paved mask; drag it in the dock editor if "
                               "trucks really enter elsewhere", "trucks": []})

    # summary
    done = [p for p in plans if p["status"] == "done"]
    on_site = _concurrency(tl_all)
    makespan = max((p["end"] for p in done), default=0.0) - min(arrivals)
    total_wait = sum(p.get("wait_s", 0) for p in done)
    worst = max(done, key=lambda p: p.get("wait_s", 0)) if done else None
    summary = {"trucks": len(plans), "completed": len(done), "concurrent_max": on_site["moving_or_docked"],
               "docked_max": on_site["docked"], "moving_max": on_site["moving"],
               "makespan_s": round(makespan, 1), "total_wait_s": round(total_wait, 1),
               "worst_wait": {"truck": worst["id"], "s": round(worst.get("wait_s", 0), 1)} if worst else None,
               "headline": _headline(plans, done, on_site, makespan, events, eq)}
    gate_ll = ll(scene.gate["x"], scene.gate["y"])
    doors_used = []
    for d in doors:
        lon, lat = ll(d["x"], d["y"])
        n = _unit(d["normal"])
        doors_used.append({"id": d["id"], "lon": lon, "lat": lat, "heading_deg": round(_compass(math.atan2(n[1], n[0])), 1),
                           "kind": d.get("kind"), "status": d.get("status"), "source": d.get("source", "assumed"),
                           "ok": {k: door_res[(d["id"], k)]["status"] == "ok" for k in types}})
    assumptions = list(scene.notes) + [
        f"gate: {scene.gate['method']}",
        f"arrivals every {req.get('stagger_s') or STAGGER_S:.0f} s at the gate; dwell {dwell:.0f} s at the door "
        "(a maneuver test, not a shift)",
        "every movement is integrated with the kinematic truck model (pure pursuit forward, the time-reversed "
        "forward pull-out for the back-in); nothing is interpolated",
        f"forward speed <= {pp.V_MAX} m/s with {pp.A_ACC} m/s2 acceleration and {pp.A_LAT} m/s2 on curves; backing "
        f"{kin.REVERSE_SPEED} m/s [U]",
        f"clearance: back-ins keep a {kin.CLEARANCE_M} m buffer from obstacles, driving legs {TRAVEL_CLEAR_M} m; "
        f"trucks keep {2 * rv.INFLATE_M:.1f} m apart (space-time reservations, 0.2 s slices)",
        "trucks plan in arrival order (first come, first served); a later truck waits on the street (not drawn) or "
        "at its door, retrying every 2 s",
        f"the street outside the gate is drawn {STREET_M:.0f} m long along the gate's outward direction "
        "(an assumption; the public road is not modelled)",
        f"before reversing the truck pulls past until the trailer is within {SETTLE_DEG} deg of straight; the "
        "back-in is placed on the reached pose (alignment error below 0.3 m / 1 deg)",
    ]
    for k in types:
        assumptions += list(kin.model(k).assumptions)
    out = {"frame": {"lat0": scene.lat0, "lon0": scene.lon0},
           "gate": {"lon": gate_ll[0], "lat": gate_ll[1], "source": scene.gate["source"]},
           "doors_used": doors_used, "door_source": scene.door_source,
           "vehicle_geometry": {k: kin.model(k).geometry() for k in types},
           "vehicles": vehicles, "events": events, "issues": issues, "summary": summary,
           "assumptions": assumptions, "cached": False}
    if internal:
        out["_internal"] = {"timelines": tl_all, "plans": plans, "door_res": door_res, "scene": scene}
    return out


def _concurrency(tl):
    if not tl:
        return {"moving_or_docked": 0, "docked": 0, "moving": 0}
    from collections import Counter
    on, dk, mv = Counter(), Counter(), Counter()
    for rows in tl.values():
        for r in rows:
            k = int(round(r[0] / rv.SLICE_S))
            on[k] += 1
            if r[6] == "docked":
                dk[k] += 1
            else:
                mv[k] += 1
    return {"moving_or_docked": max(on.values()), "docked": max(dk.values(), default=0),
            "moving": max(mv.values(), default=0)}


def _span(ids):
    nums = sorted(int(i[1:]) for i in ids)
    if len(nums) >= 3 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"trucks T{nums[0]}–T{nums[-1]}"
    return ("truck " if len(nums) == 1 else "trucks ") + ", ".join(f"T{n}" for n in nums)


def _headline(plans, done, on_site, makespan, events, eq):
    n = len(plans)
    if not done:
        f = plans[0].get("fail", {})
        return f"None of the {n} trucks could be planned: {f.get('text', 'no feasible door')}."
    s = (f"{len(done)} of {n} trucks docked and left in {makespan / 60:.1f} min; up to {on_site['docked']} were "
         f"docked at once and {on_site['moving']} moved at the same time.")
    waits = [e for e in events if e["kind"] == "wait" and e.get("seconds", 0) >= 1]
    if waits:
        street = [e for e in waits if "street" in e["text"]]
        if street:
            ws = max(e["seconds"] for e in street)
            s += f" {_span({e['truck'] for e in street})[0].upper()}{_span({e['truck'] for e in street})[1:]} " \
                 f"waited up to {ws / 60:.1f} min before the gate for the court to clear."
        dockw = [e for e in waits if "street" not in e["text"]]
        if dockw:
            s += f" {len(dockw)} truck(s) were held at their door for the way out."
    else:
        s += " No truck had to wait."
    failed = [p for p in plans if p["status"] != "done"]
    if failed:
        s += f" {len(failed)} could not be planned ({failed[0]['fail']['class']})."
    return s
