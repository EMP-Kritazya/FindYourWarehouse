"""Truck corridors: a 1 m drivable grid with a centre-of-aisle preference, one-way barriers and Dijkstra.

    CorridorGrid(grid, road_mask, parcel_mask)       built once per scene
    .route(a_xy, b_xy, barriers=[...]) -> [(x, y)]   or None when unreachable

Unlike site.Router (a shortest path for a point), the edge cost grows near obstacles (cost x (1 + W (SD_PREF - sd)/SD_PREF)
below SD_PREF metres), so the reference path keeps to the middle of drive aisles and leaves room for the trailer's
off-tracking, and a barrier (a segment cut across the aisle) forces a truck to leave a pose going forward.
"""
import math

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString

SD_FREE = 1.6        # m, a cell is drivable for the reference axle when obstacles are this far (half truck + margin)
SD_PREF = 7.0        # m, preferred distance to obstacles
W_NEAR = 3.0
W_OFF_PARCEL = 1.5   # extra cost factor off the parcel (public street / neighbour's pavement)


class CorridorGrid:
    def __init__(self, grid, road, parcel=None, cell=1.0):
        k = int(round(cell / grid.res))
        n = grid.n // k
        self.k, self.n, self.cell, self.grid = k, n, cell, grid
        sd = grid.sd[:n * k, :n * k].reshape(n, k, n, k).min(axis=(1, 3))
        rd = road[:n * k, :n * k].reshape(n, k, n, k).any(axis=(1, 3))
        self.sd = sd
        self.free = (sd >= SD_FREE) & rd
        cost = 1.0 + W_NEAR * np.clip((SD_PREF - sd) / SD_PREF, 0, 1)
        if parcel is not None:
            pm = parcel[:n * k, :n * k].reshape(n, k, n, k).any(axis=(1, 3))
            cost = cost * np.where(pm, 1.0, W_OFF_PARCEL)
        self.cost = cost

    # grid <-> ENU (same convention as YardGrid: row 0 = north)
    def node_rc(self, x, y):
        g = self.grid
        cx, cy = getattr(g, "cx", 0.0), getattr(g, "cy", 0.0)
        r = int(np.clip((cy + g.half - y) / self.cell, 0, self.n - 1))
        c = int(np.clip((x - cx + g.half) / self.cell, 0, self.n - 1))
        return r, c

    def rc_xy(self, r, c):
        g = self.grid
        cx, cy = getattr(g, "cx", 0.0), getattr(g, "cy", 0.0)
        return cx - g.half + (c + 0.5) * self.cell, cy + g.half - (r + 0.5) * self.cell

    def _cut(self, free, barriers):
        for (x, y, ux, uy, reach) in barriers:     # segment through (x, y) along (ux, uy), +-reach, stops at non-free
            px, py = -uy, ux                       # thickness direction: a 2-cell wall 8-connected moves cannot cross
            for sgn in (1, -1):
                for d in np.arange(0, reach, self.cell * 0.5):
                    qx, qy = x + sgn * ux * d, y + sgn * uy * d
                    r, c = self.node_rc(qx, qy)
                    if not self.free[r, c] and d > 3.0:
                        break
                    for o in (0.0, 0.7):
                        rr, cc = self.node_rc(qx + px * o, qy + py * o)
                        free[rr, cc] = False
        return free

    def route(self, a_xy, b_xy, barriers=()):
        free = self.free.copy()
        if barriers:
            free = self._cut(free, barriers)
        n = self.n
        ra, ca = self.node_rc(*a_xy)
        rb, cb = self.node_rc(*b_xy)
        # snap to the nearest free cell within 4 m
        def snap(r, c):
            if free[r, c]:
                return r, c
            best = None
            for dr in range(-4, 5):
                for dc in range(-4, 5):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < n and 0 <= cc < n and free[rr, cc]:
                        d = dr * dr + dc * dc
                        if best is None or d < best[0]:
                            best = (d, rr, cc)
            return (best[1], best[2]) if best else None
        sa, sb = snap(ra, ca), snap(rb, cb)
        if sa is None or sb is None:
            return None
        # restrict to a window around both ends to keep Dijkstra small
        pad = 400
        r0, r1 = max(0, min(sa[0], sb[0]) - pad), min(n, max(sa[0], sb[0]) + pad + 1)
        c0, c1 = max(0, min(sa[1], sb[1]) - pad), min(n, max(sa[1], sb[1]) + pad + 1)
        F = free[r0:r1, c0:c1]
        C = self.cost[r0:r1, c0:c1]
        h, w = F.shape
        idx = -np.ones((h, w), int)
        rs, cs = np.nonzero(F)
        idx[rs, cs] = np.arange(len(rs))
        rows, cols, wts = [], [], []
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            r2, c2 = rs + dr, cs + dc
            ok = (r2 >= 0) & (r2 < h) & (c2 >= 0) & (c2 < w)
            a = idx[rs[ok], cs[ok]]
            b = idx[r2[ok], c2[ok]]
            sel = b >= 0
            L = math.hypot(dr, dc) * self.cell
            cw = L * 0.5 * (C[rs[ok], cs[ok]][sel] + C[r2[ok], c2[ok]][sel])
            rows += [a[sel], b[sel]]
            cols += [b[sel], a[sel]]
            wts += [cw, cw]
        N = len(rs)
        if N == 0:
            return None
        G = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
        src = idx[sa[0] - r0, sa[1] - c0]
        dst = idx[sb[0] - r0, sb[1] - c0]
        dist, pred = dijkstra(G, indices=src, return_predecessors=True)
        if not np.isfinite(dist[dst]):
            return None
        chain, node = [], dst
        while node >= 0:
            chain.append(node)
            if node == src:
                break
            node = pred[node]
        chain = chain[::-1]
        pts = [tuple(a_xy)] + [self.rc_xy(rs[i] + r0, cs[i] + c0) for i in chain] + [tuple(b_xy)]
        full = LineString(pts)
        c_full = self._cost_along(full)
        for tol in (6.0, 4.0, 3.0, 2.0, 1.2):          # the simplest polyline that still runs on free cells
            line = full.simplify(tol)                 # and does not cut through high-cost zones
            if self._on_free(line, free) and self._cost_along(line) <= 1.1 * c_full + 2.0:
                break
        return [tuple(map(float, p)) for p in line.coords]

    def _cost_along(self, line):
        tot = 0.0
        for d in np.arange(0.25, line.length, 0.5):
            p = line.interpolate(d)
            tot += 0.5 * float(self.cost[self.node_rc(p.x, p.y)])
        return tot

    def _on_free(self, line, free):
        L = line.length
        for d in np.arange(2.0, max(L - 2.0, 2.0), 0.5):
            p = line.interpolate(d)
            r, c = self.node_rc(p.x, p.y)
            if not free[r, c]:
                return False
        return True
