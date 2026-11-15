"""Pure-pursuit tracking of a reference path with the kinematic truck models of kinematics.py.

    track(m, s0, path_xy, ...) -> dict(ok, poses [[t, x, y, th0, th1, v]] at kinematics.DT, reason, max_dev_m, ...)

Every pose comes from integrating `kinematics.step` (tractor rear axle + trailer heading for a semi, bicycle for a rigid
truck), so whatever is animated is what the motion model produced. Controller (forward gear only):
  * steering: pure pursuit on the tractor rear axle, lookahead Ld = clip(k_v v + L_0, ...) metres along the path;
    the wheel angle is limited to the model's full lock and to STEER_RATE rad/s; the articulation guard of
    kinematics.pullout (no steering that grows the hitch angle beyond 70 deg) is applied;
  * speed: a curvature profile (lateral acceleration A_LAT), a braking profile to stop at the path end (A_DEC) and an
    acceleration limit (A_ACC), floor CREEP near the end.
A leg fails (ok False) when the axle drifts > MAX_DEV_M from the path or the integration does not reach the end.
"""
import math

import numpy as np

from proinspect.yard import kinematics as kin

V_MAX = 3.5          # m/s forward in the yard (about 8 mph) [U]
A_LAT = 0.8          # m/s2 lateral acceleration on curves [U]
A_ACC = 0.5          # m/s2 [U]
A_DEC = 0.7          # m/s2 [U]
CREEP = 0.3          # m/s minimum speed until the end is reached
STEER_RATE = 0.5     # rad/s at the road wheels [U]
MAX_DEV_M = 3.5


def densify(path, step=0.25):
    p = np.asarray(path, float)
    out = [p[0]]
    for a, b in zip(p[:-1], p[1:]):
        L = float(np.hypot(*(b - a)))
        n = max(1, int(math.ceil(L / step)))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
    p = np.array(out)
    keep = np.r_[True, np.hypot(*np.diff(p, axis=0).T) > 1e-6]
    return p[keep]


def chaikin(path, iters=3, keep_end_m=0.0):
    """Corner cutting with fixed end points. The last `keep_end_m` metres (a lead-in straight) are kept exact."""
    p = np.asarray(path, float)
    tail = None
    if keep_end_m > 0 and len(p) >= 2:
        d = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
        cut = d[-1] - keep_end_m
        if cut > 0:
            i = int(np.searchsorted(d, cut))
            i = max(i, 1)
            a, b = p[i - 1], p[i]
            f = (cut - d[i - 1]) / max(d[i] - d[i - 1], 1e-9)
            q = a + (b - a) * f
            tail = np.vstack([q, p[i:]])
            p = np.vstack([p[:i], q])
    for _ in range(iters):
        if len(p) < 3:
            break
        new = [p[0]]
        for a, b in zip(p[:-1], p[1:]):
            new += [0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b]
        new.append(p[-1])
        p = np.array(new)
    if tail is not None:
        p = np.vstack([p, tail[1:]])
    return p


def speed_profile(p, v0=0.0, v_end=0.0, v_max=V_MAX):
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    hd = np.unwrap(np.arctan2(np.diff(p[:, 1]), np.diff(p[:, 0])))
    hd = np.r_[hd, hd[-1]]
    # curvature over a +-2 m window
    k = np.zeros(len(p))
    w = max(1, int(2.0 / max(np.median(seg), 1e-3)))
    for i in range(len(p)):
        a, b = max(0, i - w), min(len(p) - 1, i + w)
        if s[b] - s[a] > 1e-3:
            k[i] = abs(hd[b] - hd[a]) / (s[b] - s[a])
    v = np.minimum(v_max, np.sqrt(A_LAT / np.maximum(k, 1e-6)))
    v = np.maximum(v, 1.0)
    v[-1] = v_end
    for i in range(len(p) - 2, -1, -1):          # braking
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * A_DEC * (s[i + 1] - s[i])))
    v[0] = min(v[0], max(v0, 0.0)) if v0 is not None else v[0]
    for i in range(1, len(p)):                     # accelerating
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * A_ACC * (s[i] - s[i - 1])))
    return s, v, k


def track(m, s0, path, v0=0.0, v_end=0.0, t0=0.0, v_max=V_MAX, lookahead=None, max_dev=MAX_DEV_M, t_max=900.0):
    """Drive model `m` forward from state s0 = [x, y, th0, th1] along `path` (ENU polyline starting near the axle).
    Returns {ok, poses (N x 6: t, x, y, th0, th1, v), reason, max_dev_m, end_err_m}."""
    p = densify(path)
    s, vref, _ = speed_profile(p, v0, v_end, v_max)
    s_end = s[-1]
    L = m.wheelbase
    if lookahead is None:
        lookahead = (0.8, 5.0, 4.0, 10.0) if m.articulated else (0.6, 3.0, 3.0, 7.0)
    kv, l0, lmin, lmax = lookahead
    st = list(map(float, s0))
    v, delta, t, j = max(v0, 0.0), 0.0, t0, 0
    out = [[t, *st, v]]
    dev_lim, max_dev = max_dev, 0.0
    sub = 5
    dt = kin.DT
    while t - t0 < t_max:
        # projection (monotonic window search)
        hi = min(len(p), j + 80)
        d2 = (p[j:hi, 0] - st[0]) ** 2 + (p[j:hi, 1] - st[1]) ** 2
        j = j + int(np.argmin(d2))
        dev = math.sqrt(float(d2.min()))
        max_dev = max(max_dev, dev)
        if dev > dev_lim and j > 4:
            return {"ok": False, "poses": np.array(out), "reason": f"tracking diverged {dev:.1f} m from the path",
                    "max_dev_m": dev, "at": (st[0], st[1])}
        rem = s_end - s[j]
        # along-track remaining distance from the axle (signed projection on the last segment near the end)
        if j >= len(p) - 2:
            tx, ty = p[-1] - p[-2]
            n = math.hypot(tx, ty) or 1.0
            rem = ((p[-1, 0] - st[0]) * tx + (p[-1, 1] - st[1]) * ty) / n
        if rem <= 0.02:
            break
        v_tgt = max(float(vref[j]), CREEP if rem > 0.02 else 0.0)
        v_tgt = min(v_tgt, math.sqrt(max(v_end, 0) ** 2 + 2 * A_DEC * max(rem, 0)) + CREEP)
        v = min(v_tgt, v + A_ACC * dt) if v_tgt > v else max(v_tgt, v - A_DEC * 1.5 * dt)
        v = max(v, CREEP)
        step_dt = dt
        last = False
        if v * dt >= rem:                          # final partial step lands on the end
            v = rem / dt
            last = True
        Ld = min(max(kv * v + l0, lmin), lmax)
        sl = s[j] + Ld
        if sl >= s_end:                            # beyond the end: aim along the final segment's extension
            tx, ty = p[-1] - p[-2]
            n = math.hypot(tx, ty) or 1.0
            ex = sl - s_end
            gx, gy = p[-1, 0] + tx / n * ex, p[-1, 1] + ty / n * ex
        else:
            i = int(np.searchsorted(s, sl))
            gx, gy = p[min(i, len(p) - 1)]
        alpha = kin._wrap(math.atan2(gy - st[1], gx - st[0]) - st[2])
        cmd = math.atan2(2 * L * math.sin(alpha), Ld)
        dmax = m.delta_max
        cmd = max(-dmax, min(dmax, cmd))
        if m.articulated:
            g = kin._wrap(st[2] - st[3])
            room = (kin.HITCH_MAX - abs(g)) / math.radians(10)
            if room < 1 and np.sign(cmd) == np.sign(g):
                cmd *= max(room, 0.0)
        rate = STEER_RATE * step_dt
        delta = delta + max(-rate, min(rate, cmd - delta))
        for _ in range(sub):
            st = kin.step(m, st, v, delta, step_dt / sub)
        t = round(t + step_dt, 6)
        out.append([t, *st, v])
        if last:
            break
    poses = np.array(out)
    poses[-1, 5] = 0.0
    end_err = math.hypot(poses[-1, 1] - p[-1, 0], poses[-1, 2] - p[-1, 1])
    ok = end_err < 0.5 and t - t0 < t_max
    return {"ok": ok, "poses": poses, "reason": None if ok else f"did not reach the end (off by {end_err:.1f} m)",
            "max_dev_m": max_dev, "end_err_m": end_err}


def clearance(m, grid, poses, every=2):
    """Minimum signed distance (m) from any body sample point to an obstacle cell, and where it occurs."""
    pts = kin.sample_points(m, poses[:, :5], every=every)
    r, c = grid.rc(pts[:, 0], pts[:, 1])
    sd = grid.sd[r, c]
    i = int(np.argmin(sd))
    return float(sd[i]), (float(pts[i, 0]), float(pts[i, 1])), int(grid.kind[grid.nearest[0][r[i], c[i]],
                                                                             grid.nearest[1][r[i], c[i]]])


def widen(path, m, factor, cap=3.0, keep_end_m=0.0):
    """Swing wide on curves: offset the tractor path outward by factor x the steady-state off-tracking of the trailer
    axle (about L1^2 kappa / 2), capped, so that the trailer cuts less across the inside of the turn. Ends kept."""
    p = densify(path, 0.5)
    if factor <= 0 or not m.articulated or len(p) < 5:
        return p
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    hd = np.unwrap(np.arctan2(np.diff(p[:, 1]), np.diff(p[:, 0])))
    hd = np.r_[hd, hd[-1]]
    w = 6
    k = np.zeros(len(p))
    for i in range(len(p)):
        a, b = max(0, i - w), min(len(p) - 1, i + w)
        if s[b] - s[a] > 1e-3:
            k[i] = (hd[b] - hd[a]) / (s[b] - s[a])
    off = -np.sign(k) * np.minimum(factor * np.abs(k) * m.trailer_wheelbase ** 2 / 2, cap)
    # smooth the offset so that it ramps in and out
    ker = np.hanning(41)
    ker /= ker.sum()
    off = np.convolve(np.pad(off, 20, mode="edge"), ker, mode="valid")
    taper = np.minimum(np.clip((s - 10.0) / 10.0, 0, 1), np.clip((s[-1] - keep_end_m - s) / 8.0, 0, 1))
    off *= taper
    nx, ny = -np.sin(hd), np.cos(hd)                 # left normal
    return np.c_[p[:, 0] + nx * off, p[:, 1] + ny * off]


def fillet(path, radius, keep_end_m=0.0):
    """Round each corner of a polyline with a circular arc of `radius` (smaller when the adjacent segments are short),
    so that the reference curvature stays within what the truck can follow. The last `keep_end_m` stays straight."""
    p = [np.asarray(q, float) for q in path]
    if len(p) < 3:
        return np.array(p)
    out = [p[0]]
    for i in range(1, len(p) - 1):
        a, b, c = p[i - 1], p[i], p[i + 1]
        u, w = b - a, c - b
        la, lb = float(np.hypot(*u)), float(np.hypot(*w))
        if la < 1e-6 or lb < 1e-6:
            continue
        u, w = u / la, w / lb
        turn = math.atan2(u[0] * w[1] - u[1] * w[0], float(np.dot(u, w)))
        if abs(turn) < math.radians(2):
            out.append(b)
            continue
        lim_b = lb * 0.5 if i + 1 < len(p) - 1 else lb - (keep_end_m if keep_end_m < lb else 0)
        lim = max(0.0, min(la * 0.5 if i > 1 else la, lim_b))
        d = min(radius * math.tan(abs(turn) / 2), lim)
        r = d / math.tan(abs(turn) / 2)
        p0 = b - u * d
        n = np.array([-u[1], u[0]]) * (1 if turn > 0 else -1)
        ctr = p0 + n * r
        a0 = math.atan2(p0[1] - ctr[1], p0[0] - ctr[0])
        k = max(2, int(abs(turn) * r / 0.5))
        for j in range(k + 1):
            ang = a0 + (turn) * j / k
            out.append(ctr + r * np.array([math.cos(ang), math.sin(ang)]))
    out.append(p[-1])
    return np.array(out)
