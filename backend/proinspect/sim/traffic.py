"""Floor traffic: a lane graph with ZONE CONTROL, the way industrial AGV/forklift traffic systems work.

LaneGraph    nodes every 1.25 m on traversable floor (4-connected), with clearance per node so wide vehicles stay
             out of places they don't fit. Routing = shortest path (BFS hops, deterministic tie-break).
ZoneControl  every node is a zone that admits ONE vehicle (a capacity-1 SimPy resource). A vehicle holds the zone
             it is in, requests the next zone on its path, enters when granted, and only then releases the zone
             behind it (so a moving vehicle covers two zones). Handling and parking keep the zone held, so a
             forklift working at a rack blocks that aisle spot, and queues form where they would in reality.
             If a grant doesn't come within PATIENCE_S the vehicle reroutes around that zone (counted). If it
             is still stuck after DEADLOCK_S (a true circular wait, e.g. head-on in a one-lane aisle) the
             deadlock is broken by letting it pass and the event is COUNTED and reported, never hidden.

Why not space-time A* (memo §3)? Tried it: in pure Python, windowed cooperative A* spent its whole search budget
whenever a goal zone was occupied (420 s for one shift, and gridlocked). Zone control is how deployed systems do
it, is event-driven, and runs a shift in seconds.
"""
from collections import deque

import numpy as np
import simpy
from scipy import ndimage

from proinspect.sim.site import CELL

STEP = 1.25                  # lane graph spacing (m)
F = int(round(STEP / CELL))  # fine cells per node (5)
PATIENCE_S = 20.0
DEADLOCK_S = 120.0
TURN_S = 2.0                 # per change of direction [U: memo says 2-4 s per 90 deg]


class LaneGraph:
    def __init__(self, traversable: np.ndarray):
        # pad with a solid border: the building's edge is a wall, not open space
        clear = ndimage.distance_transform_edt(np.pad(traversable, 1))[1:-1, 1:-1] * CELL
        nx, ny = traversable.shape[0] // F, traversable.shape[1] // F
        idx = -np.ones((nx, ny), int)
        xy, cl = [], []
        for a in range(nx):
            for b in range(ny):
                i, j = a * F + F // 2, b * F + F // 2
                if traversable[i, j]:
                    idx[a, b] = len(xy)
                    xy.append(((i + 0.5) * CELL, (j + 0.5) * CELL))
                    cl.append(clear[i, j])
        self.idx, self.xy, self.clear = idx, np.array(xy), np.array(cl)
        self.adj = [[] for _ in xy]
        for a in range(nx):
            for b in range(ny):
                u = idx[a, b]
                if u < 0:
                    continue
                for da, db in ((1, 0), (0, 1)):
                    if a + da < nx and b + db < ny and idx[a + da, b + db] >= 0:
                        v = idx[a + da, b + db]
                        i0, j0 = a * F + F // 2, b * F + F // 2
                        i1, j1 = (a + da) * F + F // 2, (b + db) * F + F // 2
                        if traversable[min(i0, i1):max(i0, i1) + 1, min(j0, j1):max(j0, j1) + 1].all():
                            self.adj[u].append(v)
                            self.adj[v].append(u)
        for a in self.adj:
            a.sort()
        # keep only the largest connected floor: islands (offices behind doors, scan fragments) get clearance -1
        # so nothing is ever snapped onto them
        comp = -np.ones(len(xy), int)
        k = best = 0
        sizes = []
        for s in range(len(xy)):
            if comp[s] >= 0:
                continue
            stack, comp[s], n = [s], k, 0
            while stack:
                u = stack.pop()
                n += 1
                for v in self.adj[u]:
                    if comp[v] < 0:
                        comp[v] = k
                        stack.append(v)
            sizes.append(n)
            k += 1
        if sizes:
            best = int(np.argmax(sizes))
            self.clear[comp != best] = -1.0
        self.islands = max(len(sizes) - 1, 0)
        self._dist = {}

    def __len__(self):
        return len(self.xy)

    def nearest(self, x, y, min_clear=0.0):
        d = np.hypot(self.xy[:, 0] - x, self.xy[:, 1] - y)
        d[self.clear < min_clear] = np.inf
        i = int(np.argmin(d))
        return i if np.isfinite(d[i]) else None

    def hops_from(self, goal, min_clear=0.0, avoid=frozenset()):
        """BFS hop distance to `goal`. Nodes narrower than `min_clear` get a distance (so a vehicle standing at one,
        e.g. a lane end, can still leave and arrive) but are never used as through-roads."""
        key = (goal, round(min_clear, 2))
        if not avoid and key in self._dist:
            return self._dist[key]
        dist = np.full(len(self), np.inf)
        dist[goal] = 0
        q = deque([goal])
        while q:
            u = q.popleft()
            for v in self.adj[u]:
                if dist[v] == np.inf and v not in avoid:
                    dist[v] = dist[u] + 1
                    if self.clear[v] >= min_clear:
                        q.append(v)
        if not avoid:
            self._dist[key] = dist
        return dist

    def next_hop(self, u, dist):
        best = None
        for v in self.adj[u]:
            if dist[v] < dist[u] and (best is None or dist[v] < dist[best]):
                best = v
        return best

    def path_time(self, start, goal, hop_s, min_clear=0.0):
        """Free-flow travel time along the static shortest path (hops + turns)."""
        dist = self.hops_from(goal, min_clear)
        if not np.isfinite(dist[start]):
            return None
        t, u, d_prev = 0.0, start, None
        while u != goal:
            v = self.next_hop(u, dist)
            d = (round(self.xy[v, 0] - self.xy[u, 0], 2), round(self.xy[v, 1] - self.xy[u, 1], 2))
            t += hop_s + (TURN_S if d_prev is not None and d != d_prev else 0.0)
            d_prev, u = d, v
        return t


def aisle_ids(graph: LaneGraph, rack: np.ndarray, aisle_m: float):
    """Label lane-graph nodes that lie in a rack aisle (rack on both sides within aisle/2 + 0.6 m). Connected runs
    of such nodes form one aisle segment; in real operations a narrow aisle is single-occupancy."""
    reach = int(round((aisle_m / 2 + 0.6) / CELL))
    ids = -np.ones(len(graph), int)
    mark = np.zeros(len(graph), bool)
    for n, (x, y) in enumerate(graph.xy):
        i, j = int(x / CELL), int(y / CELL)
        left = rack[max(i - reach, 0):i, j].any()
        right = rack[i + 1:i + 1 + reach, j].any()
        mark[n] = left and right
    k = 0
    for n in range(len(graph)):
        if mark[n] and ids[n] < 0:
            stack = [n]
            ids[n] = k
            while stack:
                u = stack.pop()
                for v in graph.adj[u]:
                    if mark[v] and ids[v] < 0:
                        ids[v] = k
                        stack.append(v)
            k += 1
    # the mouth of each aisle (the cell just outside it) belongs to the aisle too, so a vehicle waiting for the
    # lock waits clear of the exit instead of blocking the vehicle that is coming out
    mouths = {}
    for n in range(len(graph)):
        if ids[n] < 0:
            for v in graph.adj[n]:
                if ids[v] >= 0:
                    mouths.setdefault(n, ids[v])
    for n, a in mouths.items():
        ids[n] = a
    return ids


class ZoneControl:
    def __init__(self, env: simpy.Environment, graph: LaneGraph, aisle_of=None):
        self.env, self.g = env, graph
        self.aisle_of = aisle_of if aisle_of is not None else -np.ones(len(graph), int)
        self.aisles = {}
        self.zones = {}
        self.reroutes = 0
        self.deadlock_breaks = 0
        self.wait_s = {}             # node -> seconds vehicles spent waiting to enter it (congestion heat)
        self.reroute_at = {}         # node -> reroutes caused by it

    def waited(self, n, seconds, rerouted=False):
        n = int(n)
        if seconds > 0:
            self.wait_s[n] = self.wait_s.get(n, 0.0) + seconds
        if rerouted:
            self.reroute_at[n] = self.reroute_at.get(n, 0) + 1

    def aisle(self, a):
        z = self.aisles.get(a)
        if z is None:
            z = self.aisles[a] = simpy.Resource(self.env, capacity=1)
        return z

    def zone(self, n):
        z = self.zones.get(n)
        if z is None:
            z = self.zones[n] = simpy.Resource(self.env, capacity=1)
        return z
