"""Space-time reservations for prioritized multi-truck planning.

Time is sliced at SLICE_S (0.2 s, the key-frame step). Every truck that has been planned reserves, per slice, the
oriented rectangles of its bodies (tractor + trailer, or the rigid body) inflated by INFLATE_M on every side. A later
truck's trajectory is checked against slices k-1, k, k+1 (a temporal buffer for motion between samples) with an exact
separating-axis test on the rectangles, so two reserved trucks never come closer than 2 x INFLATE_M.
"""
import math

import numpy as np

SLICE_S = 0.2
INFLATE_M = 0.3


def body_rects(m, poses):
    """poses (N x >=5: t, x, y, th0, th1, ...) -> (N, B, 5) rectangles [cx, cy, heading, half_len, half_wid]."""
    x, y, th0, th1 = poses[:, 1], poses[:, 2], poses[:, 3], poses[:, 4]
    rects = []
    for name, (xr, xf, hw) in m.bodies().items():
        if name == "trailer":
            ox, oy, h = x + m.hitch_offset * np.cos(th0), y + m.hitch_offset * np.sin(th0), th1
        else:
            ox, oy, h = x, y, th0
        mid = (xr + xf) / 2
        rects.append(np.c_[ox + mid * np.cos(h), oy + mid * np.sin(h), h,
                           np.full(len(x), (xf - xr) / 2 + INFLATE_M), np.full(len(x), hw + INFLATE_M)])
    return np.stack(rects, axis=1)


def overlap(a, b):
    """Vectorised SAT: a, b (..., 5) broadcastable -> bool (...) True when the rectangles overlap."""
    ca, sa = np.cos(a[..., 2]), np.sin(a[..., 2])
    cb, sb = np.cos(b[..., 2]), np.sin(b[..., 2])
    dx, dy = b[..., 0] - a[..., 0], b[..., 1] - a[..., 1]
    sep = np.zeros(np.broadcast(a[..., 0], b[..., 0]).shape, bool)
    for ux, uy in ((ca, sa), (-sa, ca), (cb, sb), (-sb, cb)):
        d = np.abs(dx * ux + dy * uy)
        ra = a[..., 3] * np.abs(ca * ux + sa * uy) + a[..., 4] * np.abs(-sa * ux + ca * uy)
        rb = b[..., 3] * np.abs(cb * ux + sb * uy) + b[..., 4] * np.abs(-sb * ux + cb * uy)
        sep |= d > ra + rb
    return ~sep


class Table:
    def __init__(self, horizon_s=3 * 3600, max_rects=24):
        self.T = int(horizon_s / SLICE_S) + 2
        self.R = np.full((self.T, max_rects, 5), np.nan)
        self.R[..., 3:] = 0.0
        self.owner = np.full((self.T, max_rects), -1, int)
        self.used = np.zeros(self.T, int)

    @staticmethod
    def slice_of(t):
        return int(round(t / SLICE_S))

    def add(self, k0, rects, owner):
        """rects (N, B, 5) occupying slices k0 .. k0 + N - 1."""
        n, B = rects.shape[:2]
        ks = np.arange(k0, k0 + n)
        for b in range(B):
            col = self.used[ks]
            if col.max() >= self.R.shape[1]:
                raise RuntimeError("reservation table full")
            self.R[ks, col] = rects[:, b]
            self.owner[ks, col] = owner
            self.used[ks] += 1

    def first_conflict(self, k0, rects):
        """First slice offset i (and the owner it hits) where rects[i] overlaps something reserved at k0+i-1..k0+i+1;
        None when clear."""
        n = len(rects)
        if k0 + n + 1 >= self.T:
            return 0, -1, 0
        width = int(self.used[max(k0 - 1, 0):k0 + n + 1].max(initial=0))
        if width == 0:
            return None
        for dk in (0, -1, 1):
            ks = np.clip(np.arange(k0, k0 + n) + dk, 0, self.T - 1)
            other = self.R[ks, :width]                         # (n, W, 5)
            hit = np.zeros((n, width), bool)
            for b in range(rects.shape[1]):
                ov = overlap(rects[:, b][:, None, :], other)
                hit |= ov & np.isfinite(other[..., 0])
            if hit.any():
                i = int(np.argmax(hit.any(axis=1)))
                j = int(np.argmax(hit[i]))
                return (i, int(self.owner[ks[i], j]), dk)
        return None


def pose_dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
