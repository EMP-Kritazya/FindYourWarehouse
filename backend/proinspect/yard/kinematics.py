"""Truck kinematics for the yard test: can this truck back into this door on this court?

Method (docs/research/simulation.md §4, the "back-in trick"):
  1. Put the truck in the docked pose: body square to the door, rear at the wall.
  2. Drive it FORWARD out of the door with a simple steering law (straight s metres, then turn toward the drive aisle at
     a fraction of full lock, then hold the aisle heading until the trailer has straightened).
  3. Kinematic models without dynamics are time-reversible, so playing that path backwards is a feasible back-in.
  4. The swept path is the union of the body footprints along it, plus a clearance buffer; it is checked against the
     yard grid (buildings, objects > 1 m that are not vehicles, landscaping). A family of steering-law candidates is
     tried per door and the one with the largest clearance margin is kept, which is roughly what a driver does.

Models (SI units, metres / radians; frame: x east, y north, heading = math angle from +x, counter-clockwise):
  * Tractor-semitrailer, off-axle hitch: tractor rear-axle pose (x, y, th0), wheelbase L0, steer delta;
        th0' = v tan(delta) / L0
        kingpin K = rear axle + M (cos th0, sin th0)   (M > 0: fifth wheel AHEAD of the drive-tandem centre)
        th1' = (v sin(gamma) + M th0' cos(gamma)) / L1,  gamma = th0 - th1
    The sign of the M term follows from the trailer axle's no-slip constraint and is verified numerically in
    tests/test_yard.py (the trailer axle never moves sideways).
  * Rigid truck / van: kinematic bicycle on the rear axle.

Parameters: WB-67 per AASHTO Green Book / TxDOT RDM 4.3.3 [V]: overall 73.5 ft, width 8.5 ft, front overhang 4 ft,
rear overhang 4.5 ft, minimum design (outside) turning radius 45 ft. WB1 21.5 ft and kingpin-to-tandem 45.5 ft are [U]
(memo); the kingpin 3 ft behind the trailer nose is the usual 36 in setting [U]; M = 2.0 ft is then forced by the
73.5 ft overall length. Full-lock steer is derived so that the outer front corner sweeps the 45 ft design radius.
Box truck / van dimensions come from library/equipment.yaml; their wheelbases are [U] typical values.
"""
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from scipy import ndimage

FT = 0.3048
CLEARANCE_M = 0.6           # buffer around the body; the memo suggests 2 ft
REVERSE_SPEED = 1.0         # m/s while backing (about 2.2 mph) [U]
DT = 0.1                    # s, footprint sampling step
HITCH_MAX = math.radians(70)  # articulation the steering law will not exceed [U]


@dataclass(frozen=True)
class VehicleModel:
    key: str
    name: str
    width: float
    wheelbase: float          # tractor / truck wheelbase (front axle to rear axle or tandem centre)
    front_overhang: float     # front bumper ahead of the front axle
    rear_overhang: float      # body (or tractor frame) behind the rear axle
    turn_radius: float        # outside design turning radius the full-lock steer is derived from
    articulated: bool = False
    hitch_offset: float = 0.0     # M: kingpin ahead of the tractor rear axle
    trailer_wheelbase: float = 0.0  # L1: kingpin to trailer axle (tandem centre)
    trailer_front: float = 0.0    # trailer nose ahead of the kingpin
    trailer_rear: float = 0.0     # trailer rear behind its axle
    assumptions: tuple = field(default_factory=tuple)

    @property
    def delta_max(self):
        """Full-lock steer whose outer front corner sweeps `turn_radius` (rear-axle turn radius R solved first)."""
        lf = self.wheelbase + self.front_overhang
        r_out_sq = self.turn_radius ** 2 - lf ** 2
        R = math.sqrt(max(r_out_sq, 1e-6)) - self.width / 2
        return math.atan(self.wheelbase / max(R, 0.5))

    @property
    def overall_length(self):
        if not self.articulated:
            return self.wheelbase + self.front_overhang + self.rear_overhang
        return (self.front_overhang + self.wheelbase - self.hitch_offset
                + self.trailer_wheelbase + self.trailer_rear)

    def bodies(self):
        """Body rectangles in their own frames: (x_rear, x_front, half_width), x along the body heading.
        Tractor / truck: origin at the rear axle. Trailer: origin at the kingpin."""
        out = {"tractor" if self.articulated else "body":
               (-self.rear_overhang, self.wheelbase + self.front_overhang, self.width / 2)}
        if self.articulated:
            out["trailer"] = (-(self.trailer_wheelbase + self.trailer_rear), self.trailer_front, self.width / 2)
        return out

    def geometry(self):
        """For the front end: box extents relative to the animated reference point (the kingpin for a semi, the rear
        axle for rigid trucks), each along its own heading."""
        if self.articulated:
            return {"reference": "kingpin", "tractor": {"rear_m": -(self.hitch_offset + self.rear_overhang),
                                                        "front_m": self.wheelbase + self.front_overhang - self.hitch_offset,
                                                        "width_m": self.width},
                    "trailer": {"rear_m": -(self.trailer_wheelbase + self.trailer_rear),
                                "front_m": self.trailer_front, "width_m": self.width}}
        return {"reference": "rear_axle", "tractor": {"rear_m": -self.rear_overhang,
                                                      "front_m": self.wheelbase + self.front_overhang,
                                                      "width_m": self.width}, "trailer": None}


def _yaml():
    import yaml
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "library" / "equipment.yaml"
    d = yaml.safe_load(p.read_text())
    return d.get("equipment", d)


@lru_cache(maxsize=None)
def model(key):
    """VehicleModel for an equipment.yaml road vehicle."""
    eq = _yaml()[key]
    dims = eq["dims_ft"]
    w = dims["width"] * FT
    tr = eq["turn_radius_ft"] * FT
    if key == "semi_53":
        return VehicleModel(
            key, eq["name"], w, wheelbase=21.5 * FT, front_overhang=4.0 * FT, rear_overhang=2.0 * FT,
            turn_radius=tr, articulated=True, hitch_offset=2.0 * FT, trailer_wheelbase=45.5 * FT,
            trailer_front=3.0 * FT, trailer_rear=4.5 * FT,
            assumptions=("WB-67 [V]: 73.5 ft overall, 8.5 ft wide, 4 ft front / 4.5 ft rear overhang, 45 ft design "
                         "turning radius (AASHTO Green Book, TxDOT RDM 4.3.3)",
                         "WB-67 [U]: tractor wheelbase 21.5 ft, kingpin-to-tandem 45.5 ft, kingpin 3 ft behind the "
                         "trailer nose, fifth wheel 2.0 ft ahead of the drive tandem (forced by the 73.5 ft length), "
                         "tractor frame 2 ft behind the tandem",
                         f"articulation limited to {math.degrees(HITCH_MAX):.0f} deg by the steering law [U]"))
    wb = {"box_truck_26": 20.5, "last_mile_van": 12.3}.get(key, dims["length"] * 0.58)
    fo = {"box_truck_26": 4.0, "last_mile_van": 3.3}.get(key, dims["length"] * 0.12)
    ro = dims["length"] - wb - fo
    return VehicleModel(key, eq["name"], w, wheelbase=wb * FT, front_overhang=fo * FT, rear_overhang=ro * FT,
                        turn_radius=tr,
                        assumptions=(f"{eq['name']}: {dims['length']} x {dims['width']} ft and {eq['turn_radius_ft']} ft "
                                     f"turning radius from equipment.yaml (typical values); wheelbase {wb} ft and front "
                                     f"overhang {fo} ft are [U] typical values",))


# ------------------------------------------------------------------ integration
def step(m, s, v, delta, dt):
    """One Euler step of the model. s = [x, y, th0, th1] (th1 unused for rigid)."""
    x, y, th0, th1 = s
    w0 = v * math.tan(delta) / m.wheelbase
    nx, ny, nth0 = x + v * math.cos(th0) * dt, y + v * math.sin(th0) * dt, th0 + w0 * dt
    nth1 = th1
    if m.articulated:
        g = th0 - th1
        nth1 = th1 + (v * math.sin(g) + m.hitch_offset * w0 * math.cos(g)) / m.trailer_wheelbase * dt
    return [nx, ny, nth0, nth1]


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def pullout(m, straight_m, turn_dir, steer_frac, v=REVERSE_SPEED, t_max=240.0):
    """Forward pull-out from the canonical docked pose: door at the origin, wall along the x axis, building at y < 0,
    the truck pointing +y with its rear at y = 0. Returns poses [[t, x, y, th0, th1]] at DT (rear-axle reference)."""
    rear = m.trailer_wheelbase + m.trailer_rear if m.articulated else m.rear_overhang
    th = math.pi / 2
    if m.articulated:     # trailer axle at y = rear overhang, kingpin L1 further out, tractor axle M behind the kingpin
        s = [0.0, m.trailer_rear + m.trailer_wheelbase - m.hitch_offset, th, th]
    else:
        s = [0.0, rear, th, th]
    target = th - turn_dir * math.pi / 2       # +1: turn right (toward +x), -1: left
    dmax = m.delta_max * steer_frac
    out = [[0.0, *s]]
    t, travelled, phase, settle = 0.0, 0.0, "straight", 0.0
    sub = 5
    while t < t_max:
        if phase == "straight" and travelled >= straight_m:
            phase = "turn"
        if phase == "straight":
            delta = 0.0
        else:
            err = _wrap(target - s[2])
            if phase == "turn" and abs(err) < math.radians(2):
                phase = "hold"
            delta = max(-dmax, min(dmax, 2.0 * err)) if phase == "hold" else -turn_dir * dmax
            if m.articulated:
                g = _wrap(s[2] - s[3])
                room = (HITCH_MAX - abs(g)) / math.radians(10)
                if room < 1 and np.sign(delta) == np.sign(g):
                    delta *= max(room, 0.0)
        for _ in range(sub):
            s = step(m, s, v, delta, DT / sub)
        t += DT
        travelled += v * DT
        out.append([round(t, 2), *s])
        if phase == "hold":
            g = abs(_wrap(s[2] - s[3])) if m.articulated else 0.0
            if g < math.radians(3) and abs(_wrap(target - s[2])) < math.radians(3):
                settle += v * DT
                if settle >= 2.0:
                    break
    return np.array(out)


def poses_bodies(m, poses):
    """Per pose: {body: (cx, cy, heading)} origins of each body rectangle (tractor at rear axle, trailer at kingpin)."""
    x, y, th0, th1 = poses[:, 1], poses[:, 2], poses[:, 3], poses[:, 4]
    if not m.articulated:
        return {"body": (x, y, th0)}
    kx, ky = x + m.hitch_offset * np.cos(th0), y + m.hitch_offset * np.sin(th0)
    return {"tractor": (x, y, th0), "trailer": (kx, ky, th1)}


@lru_cache(maxsize=None)
def _body_samples(xr, xf, hw, spacing=0.4):
    xs = np.linspace(xr, xf, max(2, int(math.ceil((xf - xr) / spacing)) + 1))
    ys = np.linspace(-hw, hw, max(2, int(math.ceil(2 * hw / spacing)) + 1))
    X, Y = np.meshgrid(xs, ys)
    return np.c_[X.ravel(), Y.ravel()]


def sample_points(m, poses, every=1):
    """Body sample points along the path (canonical frame), shape (N, 2)."""
    pts = []
    rects = m.bodies()
    for name, (cx, cy, h) in poses_bodies(m, poses[::every]).items():
        loc = _body_samples(*rects[name])
        c, s = np.cos(h)[:, None], np.sin(h)[:, None]
        px = cx[:, None] + c * loc[:, 0] - s * loc[:, 1]
        py = cy[:, None] + s * loc[:, 0] + c * loc[:, 1]
        pts.append(np.c_[px.ravel(), py.ravel()])
    return np.vstack(pts)


def footprints(m, poses, every=1):
    """Shapely polygons of every body at every (subsampled) pose."""
    from shapely.geometry import Polygon
    rects = m.bodies()
    polys = []
    for name, (cx, cy, h) in poses_bodies(m, poses[::every]).items():
        xr, xf, hw = rects[name]
        corners = np.array([[xr, -hw], [xf, -hw], [xf, hw], [xr, hw]])
        for i in range(len(cx)):
            c, s = math.cos(h[i]), math.sin(h[i])
            polys.append(Polygon([(cx[i] + c * a - s * b, cy[i] + s * a + c * b) for a, b in corners]))
    return polys


def swept(m, poses, clearance=CLEARANCE_M):
    from shapely.ops import unary_union
    return unary_union(footprints(m, poses)).buffer(clearance, join_style=2).simplify(0.05)


# ------------------------------------------------------------------ candidates and fit
STRAIGHTS = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.5, 9.0, 10.5, 12.0, 13.5, 15.0, 17.0, 19.5, 22.0)
FRACS = (1.0, 0.9, 0.8, 0.7, 0.6)


@lru_cache(maxsize=None)
def candidates(key):
    """Canonical pull-outs for a vehicle: [(params, poses, sample points)]."""
    m = model(key)
    out = []
    for d in (1, -1):
        for s in STRAIGHTS:
            for f in FRACS:
                p = pullout(m, s, d, f)
                out.append(({"straight_m": s, "turn": "right" if d == 1 else "left", "steer_frac": f},
                            p, sample_points(m, p)))
    return out


class YardGrid:
    """Obstacles on a square grid (row 0 = north, RES metres, centred on the local frame origin, half-size `half`).
    `kind` holds the obstacle class per cell: 0 free, 1 building, 2 object > 1 m (not a vehicle), 3 landscaping
    (unpaved). `parcel` (optional bool) is the soft limit."""

    KINDS = {1: "building", 2: "object over 1 m (wall, fence, tree, structure)", 3: "unpaved / landscaping",
             4: "truck parked at the next door"}

    def __init__(self, kind, half, res, parcel=None):
        self.kind, self.half, self.res, self.parcel = kind.astype(np.uint8), half, res, parcel
        self.n = kind.shape[0]
        obst = self.kind > 0
        d_out, idx = ndimage.distance_transform_edt(~obst, return_indices=True)
        d_in = ndimage.distance_transform_edt(obst)
        self.sd = np.where(obst, -(d_in * res - res / 2), d_out * res - res / 2).astype(np.float32)
        self.nearest = idx

    def rc(self, x, y):
        r = np.clip(((self.half - y) / self.res).astype(int), 0, self.n - 1)
        c = np.clip(((x + self.half) / self.res).astype(int), 0, self.n - 1)
        return r, c

    def xy(self, r, c):
        return -self.half + (c + 0.5) * self.res, self.half - (r + 0.5) * self.res

    def carve_slot(self, door_xy, normal, width, depth=2.5):
        """A copy whose building cells in the door opening (and the dock-wall face) are free, so a docked truck may
        touch the wall at its own door."""
        n = np.asarray(normal, float)
        t = np.array([n[1], -n[0]])
        rs, cs = np.nonzero(self.kind == 1)
        if not len(rs):
            return self
        X, Y = self.xy(rs, cs)
        dx, dy = X - door_xy[0], Y - door_xy[1]
        along, out = dx * t[0] + dy * t[1], dx * n[0] + dy * n[1]
        sel = (np.abs(along) <= width / 2 + CLEARANCE_M + self.res) & (out >= -depth) & (out <= self.res)
        k = self.kind.copy()
        k[rs[sel], cs[sel]] = 0
        return YardGrid(k, self.half, self.res, self.parcel)


    def with_parked(self, door_xy, normal, offsets, length=53 * FT, width=8.5 * FT):
        """A copy with trucks docked at the neighbouring doors (offsets along the facade, metres)."""
        if not offsets:
            return self
        n = np.asarray(normal, float) / np.linalg.norm(normal)
        t = np.array([n[1], -n[0]])
        reach = max(abs(o) for o in offsets) + width + length
        r0, c0 = self.rc(np.array([door_xy[0] - reach]), np.array([door_xy[1] + reach]))
        r1, c1 = self.rc(np.array([door_xy[0] + reach]), np.array([door_xy[1] - reach]))
        R, C = np.mgrid[r0[0]:r1[0] + 1, c0[0]:c1[0] + 1]
        X, Y = self.xy(R, C)
        dx, dy = X - door_xy[0], Y - door_xy[1]
        along, out = dx * t[0] + dy * t[1], dx * n[0] + dy * n[1]
        k = self.kind.copy()
        for o in offsets:
            sel = (np.abs(along - o) <= width / 2) & (out >= 0) & (out <= length) & (k[R, C] == 0)
            k[R[sel], C[sel]] = 4
        return YardGrid(k, self.half, self.res, self.parcel)


def to_world(pts_or_poses, door_xy, normal, poses=False):
    """Canonical frame (door at origin, outward normal +y) -> local ENU."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    # canonical +y -> n, canonical +x -> n rotated -90 deg
    ex, ey = np.array([n[1], -n[0]]), n
    a = np.asarray(pts_or_poses, float)
    if poses:
        out = a.copy()
        out[:, 1] = door_xy[0] + a[:, 1] * ex[0] + a[:, 2] * ey[0]
        out[:, 2] = door_xy[1] + a[:, 1] * ex[1] + a[:, 2] * ey[1]
        rot = math.atan2(ex[1], ex[0])
        out[:, 3] = a[:, 3] + rot
        out[:, 4] = a[:, 4] + rot
        return out
    return np.c_[door_xy[0] + a[:, 0] * ex[0] + a[:, 1] * ey[0], door_xy[1] + a[:, 0] * ex[1] + a[:, 1] * ey[1]]


def parked_length(key):
    """How far a truck docked at a door reaches out from the wall: the trailer alone for a semi (the tractor is
    usually dropped or pulls away [U]), the whole body for a rigid truck."""
    m = model(key)
    if m.articulated:
        return m.trailer_front + m.trailer_wheelbase + m.trailer_rear
    return m.wheelbase + m.front_overhang + m.rear_overhang


def fit(key, grid, door_xy, normal, door_width=None, clearance=CLEARANCE_M, neighbors=(), neighbor_key=None):
    """Best back-in for vehicle `key` at a door. Returns dict with ok, margin_m (clearance left beyond the buffer;
    negative = how far the buffered body would intrude), conflicts, params, poses (ENU, back-in order: time runs
    from the drive aisle INTO the door), swept polygon (ENU, buffered), parcel crossing (soft)."""
    m = model(key)
    g = grid.carve_slot(door_xy, normal, max(door_width or 0, m.width)).with_parked(
        door_xy, normal, list(neighbors), length=parked_length(neighbor_key or key))
    best = None
    for params, poses, pts in candidates(key):
        w = to_world(pts, door_xy, normal)
        r, c = g.rc(w[:, 0], w[:, 1])
        sd = g.sd[r, c]
        margin = float(sd.min()) - clearance
        score = (min(margin, 0.6), -poses[-1, 0])     # prefer clear, then the shorter maneuver
        if best is None or score > best[0]:
            best = (score, margin, params, poses, w, r, c, sd)
    _, margin, params, poses, w, r, c, sd = best
    world = to_world(poses, door_xy, normal, poses=True)
    conflicts = []
    bad = sd < clearance
    if bad.any():
        nr, nc = g.nearest[0][r[bad], c[bad]], g.nearest[1][r[bad], c[bad]]
        kinds = g.kind[nr, nc]
        cells = {}
        for k, rr, cc, depth in zip(kinds, nr, nc, sd[bad]):
            key_ = (int(rr) // 6, int(cc) // 6)            # merge to ~3 m clusters
            if key_ not in cells or depth < cells[key_][3]:
                cells[key_] = (int(k), int(rr), int(cc), float(depth))
        for k, rr, cc, depth in sorted(cells.values(), key=lambda v: v[3])[:12]:
            x, y = g.xy(rr, cc)
            conflicts.append({"x": round(float(x), 2), "y": round(float(y), 2), "what": YardGrid.KINDS.get(k, "obstacle"),
                              "intrusion_m": round(clearance - depth, 2)})
    parcel_out = 0
    if g.parcel is not None:
        parcel_out = int((~g.parcel[r, c]).sum())
    back = world[::-1].copy()
    back[:, 0] = back[0, 0] - back[:, 0]                   # time-reversed: t = 0 on the aisle, end at the door
    poly = swept(m, poses)
    from shapely import affinity
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    ang = math.degrees(math.atan2(-n[0], n[1]))            # canonical +y -> n
    poly = affinity.translate(affinity.rotate(poly, ang, origin=(0, 0)), door_xy[0], door_xy[1])
    return {"vehicle": key, "ok": margin >= 0, "margin_m": round(margin, 2), "conflicts": conflicts,
            "params": params, "poses": back, "swept": poly,
            "crosses_parcel_line": parcel_out > 0,
            "maneuver_s": round(float(back[-1, 0]), 1),
            "depth_needed_m": round(_depth_needed(m, poses, clearance), 2)}


def _depth_needed(m, poses, clearance):
    pts = sample_points(m, poses)
    return float(pts[:, 1].max()) + clearance


def synthetic_court(depth_ft, width_m=160.0, res=0.5, beyond=3):
    """A test yard: building south of y = 0, paved court 0 <= y <= depth, `beyond` (3 landscaping, 2 a fence/wall)
    past it, door at (0, 0)."""
    half = max(width_m / 2, depth_ft * FT + 20)
    n = int(round(2 * half / res))
    ys = half - (np.arange(n) + 0.5) * res
    kind = np.zeros((n, n), np.uint8)
    kind[ys < 0, :] = 1
    kind[ys > depth_ft * FT, :] = beyond
    return YardGrid(kind, half, res)
