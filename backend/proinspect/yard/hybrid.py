"""Hybrid A* for the yard trucks: a search over articulated truck states (x, y, th_tractor, th_trailer).

    prims(key)                         motion primitives (cached per vehicle type)
    Heuristic(grid, m, seeds)          obstacle-aware Dijkstra cost-to-go on a 1 m grid (reused per goal)
    search(m, grid, start, goal, h)    -> {ok, poses (N x 6: t, x, y, th0, th1, v) or None, explored, closest, reason}
    LineGoal / PointGoal               goal regions

Primitives: ~2.5 m forward arcs with the steer at {-max, -1/2, 0, +1/2, +max} of full lock (plus the same in reverse for
rigid trucks), integrated with kinematics.step and tabulated per 5 deg hitch bin in the tractor's local frame. An
expansion is a rigid transform of the table rows (numpy). The hitch is carried continuously: the table gives the hitch
at the end of a primitive started at the bin centre, corrected by the start offset times the trailer's forward decay
exp(-s / L1). The final path is RE-INTEGRATED exactly with kinematics.step from the real start state (by distance,
0.05 m steps), re-checked on the SDF and time-parameterised (acceleration, curvature and gear-change stops), so every
animated pose is the motion model's output.
Collision during the search: discs along each body's centreline (1 m apart, radius covering the half width) at 3 poses
per primitive, against the YardGrid signed distance field with a driving buffer (0.3 m).
Lattice for duplicate detection: 0.5 m x 72 headings x 15 hitch bins (10 deg).
"""
import heapq
import math
import time
from functools import lru_cache

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from proinspect.yard import kinematics as kin
from proinspect.yard import pursuit as pp

STEP_M = 2.5
FRACS = (-1.0, -0.5, 0.0, 0.5, 1.0)
HITCH_BIN = 5.0
HITCH_MAX_DEG = 70.0
BUFFER_M = 0.3
CELL = 0.5
NH = 72
NG = 15
W_H = 2.0                 # heuristic inflation (weighted A*: faster, near-optimal)
REV_COST = 2.0            # reverse metres cost double (rigid trucks only)
SWITCH_COST = 6.0         # a gear change costs this many metres
STEER_COST = 0.15         # per metre per unit steer: prefer straight driving
BUDGET = 30000
TIME_S = 4.0


def _wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


def _discs(m):
    """Disc centres (local body frame) and radius covering each body rectangle."""
    out = {}
    for name, (xr, xf, hw) in m.bodies().items():
        n = max(2, int(math.ceil((xf - xr) / 1.0)) + 1)
        xs = np.linspace(xr + 0.0, xf, n)
        step = (xf - xr) / (n - 1)
        out[name] = (xs, math.sqrt(hw ** 2 + (step / 2) ** 2))
    return out


def body_disc_points(m, x, y, th0, th1):
    """Arrays of disc centres (N, K, 2) for poses arrays x, y, th0, th1 (N,), and the disc radius (scalar max)."""
    d = _discs(m)
    pts, rad = [], 0.0
    for name, (xs, r) in d.items():
        rad = max(rad, r)
        if name == "trailer":
            ox, oy, h = x + m.hitch_offset * np.cos(th0), y + m.hitch_offset * np.sin(th0), th1
        else:
            ox, oy, h = x, y, th0
        c, s = np.cos(h)[:, None], np.sin(h)[:, None]
        pts.append(np.stack([ox[:, None] + c * xs, oy[:, None] + s * xs], axis=-1))
    return np.concatenate(pts, axis=1), rad


@lru_cache(maxsize=None)
def prims(key):
    """Table per hitch bin: end (dx, dy, dth0, hitch_end), validity, disc points of 3 poses in the start frame.
    Returns dict(bins (B,), dirs (P,), fracs (P,), end (B, P, 4), ok (B, P), pts (B, P, 3*K, 2), rad)."""
    m = kin.model(key)
    dirs = [1] if m.articulated else [1, -1]
    combos = [(dv, f) for dv in dirs for f in FRACS]
    bins = np.arange(-HITCH_MAX_DEG, HITCH_MAX_DEG + 0.1, HITCH_BIN) if m.articulated else np.array([0.0])
    n_sub = int(STEP_M / 0.05)
    B, P = len(bins), len(combos)
    end = np.zeros((B, P, 4))
    ok = np.ones((B, P), bool)
    snaps = []
    for bi, hb in enumerate(bins):
        row = []
        for pi, (dv, f) in enumerate(combos):
            s = [0.0, 0.0, 0.0, -math.radians(hb)]
            keep = []
            for k in range(1, n_sub + 1):
                s = kin.step(m, s, dv * 1.0, f * m.delta_max, 0.05)
                if m.articulated and abs(kin._wrap(s[2] - s[3])) > math.radians(HITCH_MAX_DEG):
                    ok[bi, pi] = False
                if k in (n_sub // 3, 2 * n_sub // 3, n_sub):
                    keep.append(list(s))
            g = kin._wrap(s[2] - s[3]) if m.articulated else 0.0
            end[bi, pi] = [s[0], s[1], s[2], g]
            row.append(keep)
        snaps.append(row)
    S = np.array(snaps)                               # (B, P, 3, 4)
    pts, rad = body_disc_points(m, S[..., 0].ravel(), S[..., 1].ravel(), S[..., 2].ravel(), S[..., 3].ravel())
    K = pts.shape[1]
    pts = pts.reshape(B, P, 3 * K, 2)
    return {"bins": np.radians(bins), "dirs": np.array([c[0] for c in combos]),
            "fracs": np.array([c[1] for c in combos]), "end": end, "ok": ok, "pts": pts, "rad": rad,
            "decay": math.exp(-STEP_M / m.trailer_wheelbase) if m.articulated else 0.0}


# ------------------------------------------------------------------ heuristic
class Heuristic:
    """Cost-to-go (m) from every cell of a 1 m grid to the seed points, over cells where a disc of the truck's half
    width + buffer fits (sd sampled at the cell centre). Unreachable = inf."""

    def __init__(self, grid, m, seeds, buffer=BUFFER_M, cell=1.0):
        k = int(round(cell / grid.res))
        n = grid.n // k
        self.k, self.n, self.cell, self.grid = k, n, cell, grid
        sd = grid.sd[k // 2::k, k // 2::k][:n, :n]
        free = sd >= m.width / 2 + buffer
        idx = -np.ones((n, n), int)
        rs, cs = np.nonzero(free)
        idx[rs, cs] = np.arange(len(rs))
        rows, cols, w = [], [], []
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            r2, c2 = rs + dr, cs + dc
            okm = (r2 >= 0) & (r2 < n) & (c2 >= 0) & (c2 < n)
            a = idx[rs[okm], cs[okm]]
            b = idx[r2[okm], c2[okm]]
            sel = b >= 0
            L = math.hypot(dr, dc) * cell
            rows += [a[sel], b[sel]]
            cols += [b[sel], a[sel]]
            w += [np.full(sel.sum() * 2, L)]
        N = len(rs)
        G = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
        src = set()
        for x, y in seeds:
            r, c = self.rc(x, y)
            # nearest free cell within 3 m
            best = None
            for dr in range(-3, 4):
                for dc in range(-3, 4):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < n and 0 <= cc < n and idx[rr, cc] >= 0:
                        dd = dr * dr + dc * dc
                        if best is None or dd < best[0]:
                            best = (dd, idx[rr, cc])
            if best:
                src.add(int(best[1]))
        H = np.full((n, n), np.inf)
        if src:
            dist = dijkstra(G, indices=sorted(src), min_only=True)
            H[rs, cs] = dist
        self.H = H

    def rc(self, x, y):
        g = self.grid
        r = int(np.clip((g.half - y) / self.cell, 0, self.n - 1))
        c = int(np.clip((x + g.half) / self.cell, 0, self.n - 1))
        return r, c

    def at(self, x, y):
        g = self.grid
        r = np.clip(((g.half - y) / self.cell).astype(int), 0, self.n - 1)
        c = np.clip(((x + g.half) / self.cell).astype(int), 0, self.n - 1)
        return self.H[r, c]


def dubins_to_point(x, y, th, px, py, R):
    """Length of the shortest forward turn-then-straight path (turning radius R) from poses (x, y, th) to the point
    (px, py), ignoring obstacles and the final heading; a lower bound for a forward-only truck (vectorised)."""
    best = None
    for side in (1.0, -1.0):                       # +1 left circle, -1 right circle
        cx = x - side * R * np.sin(th)
        cy = y + side * R * np.cos(th)
        dx, dy = px - cx, py - cy
        dc = np.hypot(dx, dy)
        t = np.sqrt(np.maximum(dc ** 2 - R ** 2, 0.0))
        # angle of the tangent point on the circle, measured from the start point around the centre
        a_start = np.arctan2(y - cy, x - cx)
        a_pt = np.arctan2(dy, dx)
        beta = np.arccos(np.clip(R / np.maximum(dc, 1e-6), -1, 1))
        a_tan = a_pt - side * beta
        turn = np.mod(side * (a_tan - a_start), 2 * np.pi)
        L = R * turn + t
        L = np.where(dc < R, R * np.pi + (R - dc), L)          # inside the circle: at least a half turn
        best = L if best is None else np.minimum(best, L)
    return best


# ------------------------------------------------------------------ goals
class LineGoal:
    """Reached when the tractor axle is on one of the lead-in lines: within `lat` m of the line through a pull-past
    pose Q (heading H), between `lead` m before Q and Q itself, heading within `dth` and hitch within `dg` of Q's."""

    def __init__(self, goals, lat=0.4, dth=4.0, dg=3.0, settle_len=None, min_snap=0.0):
        a = np.array([[q[0], q[1], q[2], q[3], q[4]] for q in goals], float)   # x, y, th0, hitch, lead
        self.a, self.lat, self.dth, self.dg = a, lat, math.radians(dth), math.radians(dg)
        self.settle_len = settle_len          # trailer L1: a larger hitch is fine if enough straight is left
        self.min_snap = np.minimum(min_snap, a[:, 4] * 0.5)   # leave room for the straight snap onto the pose

    def target(self):
        i = 0
        return self.a[i, 0] - self.a[i, 4] * math.cos(self.a[i, 2]), self.a[i, 1] - self.a[i, 4] * math.sin(self.a[i, 2])

    def seeds(self):
        out = []
        for x, y, th, g, lead in self.a:
            for d in np.arange(0, lead + 0.1, 2.0):
                out.append((x - d * math.cos(th), y - d * math.sin(th)))
        return out

    def check(self, x, y, th, g):
        """Vectorised over children: index of the goal reached (or -1)."""
        a = self.a
        dx = x[:, None] - a[None, :, 0]
        dy = y[:, None] - a[None, :, 1]
        c, s = np.cos(a[:, 2]), np.sin(a[:, 2])
        along = dx * c + dy * s
        lat = -dx * s + dy * c
        dg = self.dg
        if self.settle_len:                   # the straight still to drive decays the hitch by exp(-d / L1)
            dg = np.minimum(self.dg * np.exp(np.clip(-along, 0, None) / self.settle_len), math.radians(25))
        okm = ((np.abs(lat) <= self.lat) & (along <= -self.min_snap[None, :]) & (along >= -a[None, :, 4])
               & (np.abs(_wrap(th[:, None] - a[None, :, 2])) <= self.dth)
               & (np.abs(g[:, None] - a[None, :, 3]) <= dg))
        hit = okm.any(axis=1)
        return np.where(hit, np.argmax(okm, axis=1), -1)

    def dist(self, x, y, th):
        d = np.hypot(x - self.a[:, 0][:, None], y - self.a[:, 1][:, None]).min(axis=0)
        return d


class PointGoal:
    """Reached within `tol` m of (x, y) with the heading within `dth` deg of `th` (None: any heading)."""

    def __init__(self, x, y, th=None, tol=3.0, dth=40.0):
        self.x, self.y, self.th, self.tol, self.dth = x, y, th, tol, math.radians(dth)

    def target(self):
        return self.x, self.y

    def seeds(self):
        return [(self.x, self.y)]

    def check(self, x, y, th, g):
        okm = np.hypot(x - self.x, y - self.y) <= self.tol
        if self.th is not None:
            okm &= np.abs(_wrap(th - self.th)) <= self.dth
        return np.where(okm, 0, -1)

    def dist(self, x, y, th):
        return np.hypot(x - self.x, y - self.y)


# ------------------------------------------------------------------ search
def search(m, grid, start, goal, heur, buffer=BUFFER_M, budget=BUDGET, time_s=TIME_S, shot=None, shot_r=35.0,
           shot_every=25, shot_max=40):
    """Hybrid A* from state start = [x, y, th0, th1]. Returns dict(ok, seq [(dir, frac)], explored, closest
    (x, y, dist_m), reason)."""
    T = prims(m.key)
    bins, end, okT, pts, rad = T["bins"], T["end"], T["ok"], T["pts"], T["rad"]
    dirs, fracs = T["dirs"], T["fracs"]
    decay = T["decay"]
    need = rad + buffer
    sd = grid.sd
    half, res, n = grid.half, grid.res, grid.n
    t_start = time.time()
    R_turn = m.wheelbase / math.tan(m.delta_max)
    tx, ty = goal.target()
    fwd_only = m.articulated
    starts = [start] if np.ndim(start[0]) == 0 else list(start)
    X, Y, TH, G, PAR, PRIM, COST, DIR, ROOT = [], [], [], [], [], [], [], [], []

    def key(x, y, th, g):
        return (int(round(x / CELL)), int(round(y / CELL)), int(round(th / (2 * math.pi / NH))) % NH,
                int(round(g / math.radians(10))) if m.articulated else 0)
    openl, best_g = [], {}
    closest = None
    for ri, st in enumerate(starts):
        x0, y0, a0, b0 = map(float, st)
        g0 = kin._wrap(a0 - b0) if m.articulated else 0.0
        X.append(x0), Y.append(y0), TH.append(a0), G.append(g0), PAR.append(-1), PRIM.append(-1), COST.append(0.0)
        DIR.append(0), ROOT.append(ri)
        d0 = float(goal.dist(np.array([x0]), np.array([y0]), np.array([a0]))[0])
        if closest is None or d0 < closest[2]:
            closest = (x0, y0, d0)
        h0 = float(heur.at(np.array([x0]), np.array([y0]))[0])
        if np.isfinite(h0):
            heapq.heappush(openl, (W_H * h0, len(X) - 1, len(X) - 1))
            best_g[key(x0, y0, a0, g0)] = 0.0
    if not openl:
        return {"ok": False, "explored": 0, "closest": closest,
                "reason": "the start is not connected to the goal through space a truck fits in (aerial grid)"}
    explored = 0
    n_shots, last_shot = 0, -10 ** 9

    def trace(q):
        seq = []
        while PAR[q] >= 0:
            seq.append((int(dirs[PRIM[q]]), float(fracs[PRIM[q]])))
            q = PAR[q]
        return seq[::-1]
    while openl:
        f, _, i = heapq.heappop(openl)
        x, y, th, g, c0 = X[i], Y[i], TH[i], G[i], COST[i]
        if c0 > best_g.get(key(x, y, th, g), np.inf) + 1e-9:
            continue
        explored += 1
        if (shot is not None and n_shots < shot_max and explored - last_shot >= shot_every
                and float(goal.dist(np.array([x]), np.array([y]), np.array([th]))[0]) <= shot_r):
            last_shot, n_shots = explored, n_shots + 1
            if shot([x, y, th, th - g if m.articulated else th]):
                return {"ok": True, "seq": trace(i), "explored": explored, "goal_i": 0, "root": ROOT[i],
                        "closest": closest, "end": (x, y, th, g), "shot": True}
        if explored > budget or (explored % 200 == 0 and time.time() - t_start > time_s):
            break
        bi = int(np.clip(round((g - bins[0]) / math.radians(HITCH_BIN)), 0, len(bins) - 1)) if m.articulated else 0
        ct, st = math.cos(th), math.sin(th)
        e = end[bi]
        cx = x + ct * e[:, 0] - st * e[:, 1]
        cy = y + st * e[:, 0] + ct * e[:, 1]
        cth = _wrap(th + e[:, 2])
        if m.articulated:
            cg = e[:, 3] + (g - bins[bi]) * decay
        else:
            cg = np.zeros(len(cx))
        valid = okT[bi] & (np.abs(cg) <= math.radians(HITCH_MAX_DEG))
        P = pts[bi]                                       # (P, M, 2)
        wx = x + ct * P[..., 0] - st * P[..., 1]
        wy = y + st * P[..., 0] + ct * P[..., 1]
        r = np.clip(((half - wy) / res).astype(int), 0, n - 1)
        c = np.clip(((wx + half) / res).astype(int), 0, n - 1)
        valid &= (sd[r, c] >= need).all(axis=1)
        if not valid.any():
            continue
        idxs = np.nonzero(valid)[0]
        cx, cy, cth, cg = cx[idxs], cy[idxs], cth[idxs], cg[idxs]
        cd = dirs[idxs]
        step = STEP_M * (1 + STEER_COST * np.abs(fracs[idxs])) * np.where(cd < 0, REV_COST, 1.0)
        step = step + np.where((DIR[i] != 0) & (cd != DIR[i]), SWITCH_COST, 0.0)
        hv = heur.at(cx, cy)
        if fwd_only:
            hv = np.maximum(hv, dubins_to_point(cx, cy, cth, tx, ty, R_turn) - 3.0)
        reached = goal.check(cx, cy, cth, cg)
        dists = goal.dist(cx, cy, cth)
        jbest = int(np.argmin(dists))
        if dists[jbest] < closest[2]:
            closest = (float(cx[jbest]), float(cy[jbest]), float(dists[jbest]))
        for j in range(len(idxs)):
            if not np.isfinite(hv[j]):
                continue
            nc = c0 + float(step[j])
            k = key(cx[j], cy[j], cth[j], cg[j])
            if nc >= best_g.get(k, np.inf) - 1e-9:
                continue
            best_g[k] = nc
            X.append(float(cx[j]))
            Y.append(float(cy[j]))
            TH.append(float(cth[j]))
            G.append(float(cg[j]))
            PAR.append(i)
            PRIM.append(int(idxs[j]))
            COST.append(nc)
            DIR.append(int(cd[j]))
            ROOT.append(ROOT[i])
            ni = len(X) - 1
            if reached[j] >= 0:
                return {"ok": True, "seq": trace(ni), "explored": explored, "goal_i": int(reached[j]), "root": ROOT[ni],
                        "closest": closest, "end": (X[ni], Y[ni], TH[ni], G[ni])}
            heapq.heappush(openl, (nc + W_H * float(hv[j]), ni, ni))
    why = ("search budget used up" if explored > budget else
           "search time used up" if openl else "every reachable position was explored")
    return {"ok": False, "explored": explored, "closest": closest, "reason": why, "_nodes": (X, Y)}


# ------------------------------------------------------------------ exact path
def integrate(m, start, seq, ds=0.05):
    """Re-integrate a primitive sequence exactly from `start`. Returns poses by distance: (N, 6) [s, x, y, th0, th1,
    dir] and the gear per pose."""
    s = list(map(float, start))
    out = [[0.0, *s, seq[0][0] if seq else 1]]
    dist = 0.0
    n_sub = int(round(STEP_M / ds))
    for dv, f in seq:
        for _ in range(n_sub):
            s = kin.step(m, s, dv * 1.0, f * m.delta_max, ds)
            dist += ds
            out.append([dist, *s, dv])
    return np.array(out)


def timed(P, v_fwd=pp.V_MAX, v_rev=kin.REVERSE_SPEED, dt=kin.DT):
    """Time-parameterise a by-distance path (s, x, y, th0, th1, dir): segments of one gear, each starting and ending at
    rest, with acceleration / braking limits and a curvature speed cap. Returns poses (N, 6) [t, x, y, th0, th1, gear]."""
    gears = P[:, 5]
    cut = [0] + [i for i in range(1, len(P)) if gears[i] != gears[i - 1]] + [len(P) - 1]
    rows, t = [], 0.0
    for a, b in zip(cut[:-1], cut[1:]):
        seg = P[a:b + 1] if b > a else P[a:a + 1]
        gear = int(seg[-1, 5]) if len(seg) > 1 else int(seg[0, 5])
        s = seg[:, 0] - seg[0, 0]
        L = float(s[-1])
        if L < 1e-6:
            continue
        vmax = v_fwd if gear > 0 else v_rev
        dth = np.abs(np.gradient(np.unwrap(seg[:, 3]))) / np.maximum(np.gradient(s), 1e-6)
        vcap = np.minimum(vmax, np.sqrt(pp.A_LAT / np.maximum(dth, 1e-6)))
        vcap = np.maximum(vcap, min(1.0, vmax))
        v = vcap.copy()
        v[0] = 0.0
        for i in range(1, len(v)):
            v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * pp.A_ACC * (s[i] - s[i - 1])))
        v[-1] = 0.0
        for i in range(len(v) - 2, -1, -1):
            v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * pp.A_DEC * (s[i + 1] - s[i])))
        v = np.maximum(v, 0.15)
        tt = np.r_[0, np.cumsum(np.diff(s) / ((v[1:] + v[:-1]) / 2))]
        times = np.arange(0, tt[-1] + 1e-9, dt)
        if times[-1] < tt[-1] - 1e-6:
            times = np.r_[times, times[-1] + dt]
        for q in times:
            j = int(np.clip(np.searchsorted(tt, q), 0, len(tt) - 1))
            rows.append([t + q, *seg[j, 1:5], gear])
        t += times[-1] + dt
    return np.array(rows)
