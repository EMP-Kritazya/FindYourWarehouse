"""One operating shift of a business in a real warehouse layout (SimPy). docs/research/simulation.md §2.

Flows (pallet operations):
  inbound truck   arrive (appointment + lateness) -> wait for a free door -> back in -> each pallet: trailer -> staging
                  lane (lane capacity blocks unloading) -> paperwork -> depart.  Putaway: lane -> rack slot.
  outbound truck  retrieval tasks (rack -> that door's lane) are released ahead of the appointment; the truck docks at
                  its door, each staged pallet is loaded lane -> trailer, then it departs.
Vehicles take the oldest eligible task nearest to them, drive through `Traffic` (space-time reservations, so aisles
block and queues form), and spend handling time at each end (lift time from the rack level and lift_fpm).
Unload time per trailer is an OUTPUT (validated against equipment.yaml dock_turn_min), never an input.

Not simulated yet (stated in `assumptions`): each-picking / parcels (AMRs, conveyors), tuggers, yard trucks,
breaks and battery charging, vehicle length beyond two lane nodes.
"""
import hashlib
import math
from dataclasses import dataclass, field

import numpy as np
import simpy

from proinspect.library import equipment, profiles
from proinspect.sim.layout import FT, PALLET_LEVEL_FT, Layout
from proinspect.sim.traffic import DEADLOCK_S, PATIENCE_S, TURN_S, LaneGraph, ZoneControl, aisle_ids

MPH = 0.44704
DT = 0.25                      # time rounding for handling (s)
# handling times (s), lognormal with CV 0.3 [U: placeholders until replaced by time studies]
HANDLE = {"trailer_pick": 30, "trailer_drop": 30, "lane_pick": 10, "lane_drop": 10, "fork": 20}
CV = 0.3
BACK_IN_S, PAPERWORK_S = 180, 300
LATENESS_SD_MIN = 10
INITIAL_FILL = 0.8
DOCK_CREW_PER_DOOR = 1          # one truck works inside a trailer at a time
PALLET_MOVERS = {"forklift", "narrow_aisle", "horizontal", "light_lift"}


@dataclass
class Vehicle:
    id: int
    kind: str
    spec: dict
    can_dock: bool
    can_rack: bool
    node: int
    hop_empty: float               # seconds per 1.25 m lane-graph hop
    hop_loaded: float
    min_clear: float
    home: int = None
    req: object = None             # the zone request this vehicle currently holds
    aisle_req: object = None       # the narrow-aisle lock it holds (-1 = none)
    aisle: int = -1
    t: dict = field(default_factory=lambda: {"loaded": 0.0, "empty": 0.0, "handling": 0.0, "delay": 0.0,
                                             "idle": 0.0})
    tasks_done: int = 0
    trace: list = field(default_factory=list)


@dataclass
class Task:
    id: int
    kind: str                      # unload | putaway | retrieve | load
    src: int
    dst: int
    created: float
    lane: int = None
    slot: tuple = None             # (bay id, level)
    truck: int = None
    done: simpy.Event = None
    taken: bool = False


class Shift:
    def __init__(self, layout: Layout, profile_key: str, seed=0, volume_scale=1.0, fleet=None):
        self.lay, self.prof = layout, profiles()[profile_key]
        self.profile_key = profile_key
        self.env = simpy.Environment()
        ss = np.random.SeedSequence(seed)
        self.rng_arr, self.rng_handle, self.rng_slot = (np.random.default_rng(s) for s in ss.spawn(3))
        self.seed = seed
        self.assumptions = list(layout.site.assumptions) + [
            "handling times (trailer 30 s, lane 10 s, fork 20 s + lift) are typical values, lognormal CV 0.3",
            f"trucks arrive by appointment with N(0, {LATENESS_SD_MIN} min) lateness; back-in {BACK_IN_S} s, "
            f"paperwork {PAPERWORK_S} s",
            f"racks start {int(INITIAL_FILL * 100)}% full; slotting A/B/C by distance to the docks",
            "only pallet moves are simulated (no each-picking, AMRs, conveyors, tuggers, breaks or charging yet)",
            "flow-through staging lanes: dropped at the apron end, picked at the rack end (and the reverse outbound)",
            "one vehicle works inside a trailer at a time; narrow rack aisles are single-occupancy",
            "idle vehicles park off the travel lanes (they don't block traffic while waiting for work)"]
        self.log = []
        self.g = LaneGraph(layout.traversable)
        self.traffic = ZoneControl(self.env, self.g, aisle_ids(self.g, layout.rack, layout.aisle_m))
        self._nodes()
        self._fleet(fleet)
        self._stock()
        self.tasks, self.trucks = [], []
        self.task_signal = self.env.event()
        self.lane_count = [0] * len(layout.lanes)
        self.door_crew = {}
        self.lane_max = [0] * len(layout.lanes)
        self.free_doors = simpy.FilterStore(self.env)
        for i, _ in enumerate(self.lane_door):
            self.free_doors.items.append(i)
        # dedicated doors: even lanes receive, odd lanes ship (shared only when there is a single door)
        n = len(self.lane_node)
        self.inbound_doors = [i for i in range(n) if i % 2 == 0] or list(range(n))
        self.outbound_doors = [i for i in range(n) if i % 2 == 1] or list(range(n))
        self.assumptions.append("doors are dedicated: alternating receiving / shipping doors"
                                if n > 1 else "a single door is shared by receiving and shipping (deadlock risk)")
        self.counts = {"inbound_planned": 0, "inbound_received": 0, "putaway": 0, "outbound_planned": 0,
                       "outbound_shipped": 0, "unreachable_tasks": 0}
        self.door_busy = 0.0

    # ---------------------------------------------------------------- setup
    def _nodes(self):
        lay, g = self.lay, self.g
        self.lane_door, self.lane_node, self.door_node, self.lane_spots = [], [], [], []
        for ln in lay.lanes:
            d = lay.site.doors[ln["door"]]
            dn = g.nearest(d.x, lay.site.wall_y + 1.0)
            lnode = g.nearest((ln["x0"] + ln["x1"]) / 2, (ln["y0"] + ln["y1"]) / 2)
            self.lane_door.append(ln["door"])
            self.door_node.append(dn)
            self.lane_node.append(lnode)
            # flow-through lane: drop / pick spots at the apron ("front") end and the rack ("back") end
            inside = [i for i, (x, y) in enumerate(g.xy) if ln["x0"] <= x <= ln["x1"] and ln["y0"] <= y <= ln["y1"]]
            front = sorted((i for i in inside if g.xy[i, 1] < ln["y0"] + 1.5), key=lambda i: g.xy[i, 0])
            back = sorted((i for i in inside if g.xy[i, 1] > ln["y1"] - 1.5), key=lambda i: g.xy[i, 0])
            self.lane_spots.append({"front": front or [g.nearest((ln["x0"] + ln["x1"]) / 2, ln["y0"])],
                                    "back": back or [g.nearest((ln["x0"] + ln["x1"]) / 2, ln["y1"])]})

    def _fleet(self, fleet):
        eq = equipment()
        scale = self.lay.site.area_sqft / 100_000
        fleet = fleet or {k: max(1, round(n * scale)) for k, n in self.prof["fleet"].items()
                          if k in eq and eq[k]["category"] in PALLET_MOVERS}
        if not any(eq[k].get("dock_ok") for k in fleet):
            fleet["pallet_jack_electric"] = max(1, round(scale * 2))
            self.assumptions.append("added electric pallet jacks: the profile fleet has no truck that can enter a trailer")
        self.fleet_spec = fleet
        self.vehicles = []
        used = set(self.door_node) | set(self.lane_node)
        # park idle vehicles at the sides of the dock zone, away from the doors and lanes
        cx = float(np.mean([self.g.xy[n, 0] for n in self.door_node])) if self.door_node else 0.0
        zone = self.g.xy[:, 1] < self.lay.dock_zone_end
        order = np.argsort(-np.abs(self.g.xy[:, 0] - cx) + 0.2 * self.g.xy[:, 1] + np.where(zone, 0, 1e6))
        homes = [int(n) for n in order if int(n) not in used]
        spaced = set()
        vid = 0
        for kind in sorted(fleet):
            spec = eq[kind]
            for _ in range(fleet[kind]):
                width = spec["dims_ft"]["width"] * FT
                min_clear = width / 2 + 0.1
                home = next(n for n in homes if self.g.clear[n] >= max(min_clear, 2.0) and n not in used
                            and n not in spaced and self.traffic.aisle_of[n] < 0)
                used.add(home)
                spaced.update({home, *self.g.adj[home]})            # keep parked vehicles 2 cells apart
                spaced.update(w for u in self.g.adj[home] for w in self.g.adj[u])
                hop = [1.25 / (0.8 * s * MPH) for s in spec["speed_mph"]]    # cruise at 80% of top speed
                rack_ok = (spec.get("lift_height_ft", 0) >= 10 and bool(spec.get("min_aisle_ft"))
                           and spec["min_aisle_ft"] <= self.lay.params["aisle_ft"] + 0.1)   # must fit the aisle
                self.vehicles.append(Vehicle(vid, kind, spec, bool(spec.get("dock_ok")), rack_ok,
                                             home, hop[0], hop[1], min_clear, home=home))
                vid += 1

    def _stock(self):
        """Rack slots with their access node, ordered A (near docks) -> C; INITIAL_FILL of them full."""
        lay, g = self.lay, self.g
        clear = max([v.min_clear for v in self.vehicles if v.can_rack] or [0.5])
        dock_xy = np.mean([[lay.site.doors[l["door"]].x, lay.site.wall_y] for l in lay.lanes] or [[0, 0]], axis=0)
        slots = []
        for b in lay.bays:
            node = g.nearest(b["aisle_x"], (b["y0"] + b["y1"]) / 2, clear)
            if node is None:
                continue
            for lvl in range(lay.levels):
                for pos in range(2):
                    slots.append({"bay": b["id"], "level": lvl, "pos": pos, "node": node,
                                  "dist": abs(b["face_x"] - dock_xy[0]) + abs(b["y0"] - dock_xy[1]) + lvl * 1.5})
        # keep only slots a rack truck can actually reach from the staging lanes
        if self.lane_spots:
            h = g.hops_from(self.lane_spots[0]["back"][0], clear)
            lost = sum(1 for s in slots if not np.isfinite(h[s["node"]]))
            if lost:
                slots = [s for s in slots if np.isfinite(h[s["node"]])]
                self.assumptions.append(f"{lost} rack positions excluded: unreachable (obstacle or column in the aisle)")
        slots.sort(key=lambda s: s["dist"])
        n = len(slots)
        for i, s in enumerate(slots):
            s["zone"] = "A" if i < 0.2 * n else "B" if i < 0.5 * n else "C"
            s["full"] = bool(self.rng_slot.random() < INITIAL_FILL)
            s["reserved"] = False
        self.slots = slots

    # ---------------------------------------------------------------- helpers
    def _handle_time(self, base):
        sigma = math.sqrt(math.log(1 + CV ** 2))
        t = base * float(self.rng_handle.lognormal(-sigma ** 2 / 2, sigma))
        return max(DT, round(t / DT) * DT)

    def _lift_time(self, v, level):
        h = level * PALLET_LEVEL_FT
        fpm = v.spec.get("lift_fpm") or 60
        return 2 * h / fpm * 60

    def _event(self, *a):
        self.log.append((round(self.env.now, 2),) + a)

    def _signal(self):
        ev, self.task_signal = self.task_signal, self.env.event()
        ev.succeed()

    def add_task(self, **kw):
        t = Task(id=len(self.tasks), created=self.env.now, done=self.env.event(), **kw)
        self.tasks.append(t)
        self._signal()
        return t

    def _eligible(self, v, t):
        if t.taken:
            return False
        if t.kind in ("unload", "load") and not v.can_dock:
            return False
        if t.kind in ("putaway", "retrieve") and not v.can_rack:
            return False
        if t.kind == "unload" and self.lane_count[t.lane] >= self.lay.lanes[t.lane]["capacity"]:
            return False                                    # lane full: unloading blocks
        if t.kind in ("unload", "load") and self.door_crew.get(t.lane, 0) >= DOCK_CREW_PER_DOOR:
            return False                                    # someone is already working this trailer
        return True

    def _spot(self, lane, k, end):
        spots = self.lane_spots[lane][end]
        return spots[k % len(spots)]

    # ---------------------------------------------------------------- movement
    def move(self, v, goal, loaded):
        """Zone-controlled travel: request the next zone, enter when granted, then release the one behind."""
        if goal is None:
            return False
        hop = v.hop_loaded if loaded else v.hop_empty
        free_flow = self.g.path_time(v.node, goal, hop, v.min_clear)
        if free_flow is None:
            return False
        zc, start, avoid, d_prev = self.traffic, self.env.now, set(), None
        state = "loaded" if loaded else "empty"
        static = self.g.hops_from(goal, v.min_clear)
        best, t_best, force, ghost = static[v.node], self.env.now, False, False
        while v.node != goal:
            dist = self.g.hops_from(goal, v.min_clear, frozenset(avoid))
            if not np.isfinite(dist[v.node]):
                if not avoid:
                    return False
                avoid.clear()
                continue
            nxt = self.g.next_hop(v.node, dist)
            # narrow aisles are single-occupancy: take the aisle lock before entering it
            a_next = int(zc.aisle_of[nxt])
            if a_next >= 0 and a_next != v.aisle and not force:
                if v.aisle_req is not None:                 # never hold one aisle while waiting for another
                    zc.aisle(v.aisle).release(v.aisle_req)  # (breaks hold-and-wait; zone locks still prevent overlap)
                    v.aisle, v.aisle_req = -1, None
                areq = zc.aisle(a_next).request()
                t_req = self.env.now
                got = yield areq | self.env.timeout(PATIENCE_S)
                zc.waited(nxt, self.env.now - t_req, areq not in got)
                if areq not in got:
                    areq.cancel()
                    zc.reroutes += 1
                    avoid.add(nxt)
                    force = self.env.now - t_best >= DEADLOCK_S
                    continue
                v.aisle, v.aisle_req = a_next, areq
            req = None
            force = force or ghost
            if not force:
                req = zc.zone(nxt).request()
                t_req = self.env.now
                got = yield req | self.env.timeout(PATIENCE_S)
                zc.waited(nxt, self.env.now - t_req, req not in got)
                if req not in got:
                    req.cancel()
                    zc.reroutes += 1
                    avoid.add(nxt)
                    force = self.env.now - t_best >= DEADLOCK_S   # no progress toward the goal for too long
                    continue
            else:                                           # circular wait or livelock: pass, and count it
                if not ghost:                                # once per trip: passage through to the goal
                    zc.deadlock_breaks += 1
                    self._event("deadlock_break", v.id, int(nxt))
                    ghost = True
                    if v.aisle_req is not None:
                        zc.aisle(v.aisle).release(v.aisle_req)
                        v.aisle, v.aisle_req = -1, None
            d = (round(self.g.xy[nxt, 0] - self.g.xy[v.node, 0], 2), round(self.g.xy[nxt, 1] - self.g.xy[v.node, 1], 2))
            dt = hop + (TURN_S if d_prev is not None and d != d_prev else 0.0)
            d_prev = d
            v.trace.append((round(self.env.now, 2), int(v.node), state))
            yield self.env.timeout(dt)
            if v.req is not None:
                zc.zone(v.node).release(v.req)
            v.req, v.node = req, nxt
            if a_next < 0 and v.aisle_req is not None:         # left the aisle: free it
                zc.aisle(v.aisle).release(v.aisle_req)
                v.aisle, v.aisle_req = -1, None
            if static[v.node] < best:
                best, t_best = static[v.node], self.env.now
            avoid.clear()
        v.trace.append((round(self.env.now, 2), int(v.node), state))
        took = self.env.now - start
        v.t[state] += min(took, free_flow)
        v.t["delay"] += max(0.0, took - free_flow)
        return True

    def handle(self, v, seconds, what):
        """Stationary work (forks in a rack, inside a trailer): the vehicle keeps its zone."""
        v.trace.append((round(self.env.now, 2), int(v.node), what))
        yield self.env.timeout(seconds)
        v.t["handling"] += seconds

    # ---------------------------------------------------------------- processes
    def vehicle(self, v):
        v.req = self.traffic.zone(v.node).request()
        yield v.req
        while True:
            cands = [t for t in self.tasks if self._eligible(v, t)]
            if not cands:
                if v.node != v.home:
                    yield from self.move(v, v.home, False)
                if v.node == v.home and v.req is not None:     # parked off the travel lanes: frees its zone
                    self.traffic.zone(v.node).release(v.req)
                    v.req = None
                v.trace.append((round(self.env.now, 2), int(v.node), "idle"))
                t_idle = self.env.now
                yield self.task_signal | self.env.timeout(60)
                v.t["idle"] += self.env.now - t_idle
                continue
            hops = self.g.hops_from(v.node, v.min_clear)
            # nearest task first, older tasks get a head start (0.5 s of travel per second waited)
            task = min(cands, key=lambda t: (hops[t.src] * v.hop_empty - 0.5 * (self.env.now - t.created), t.id))
            task.taken = True
            if task.kind in ("unload", "load"):
                self.door_crew[task.lane] = self.door_crew.get(task.lane, 0) + 1
            if task.kind == "unload":
                self.lane_count[task.lane] += 1                # claim the lane space now
            ok = yield from self.move(v, task.src, False)
            if not ok:
                self._fail(task, v)
                continue
            if task.kind == "unload":
                yield from self.handle(v, self._handle_time(HANDLE["trailer_pick"]), "pick")
            elif task.kind == "putaway":
                self.lane_count[task.lane] -= 1
                yield from self.handle(v, self._handle_time(HANDLE["lane_pick"]), "pick")
            elif task.kind == "retrieve":
                yield from self.handle(v, self._handle_time(HANDLE["fork"]) + self._lift_time(v, task.slot[1]), "pick")
            elif task.kind == "load":
                self.lane_count[task.lane] -= 1
                yield from self.handle(v, self._handle_time(HANDLE["lane_pick"]), "pick")
            ok = yield from self.move(v, task.dst, True)
            if not ok:
                self._fail(task, v)
                continue
            if task.kind == "unload":
                yield from self.handle(v, self._handle_time(HANDLE["lane_drop"]), "drop")
                self.counts["inbound_received"] += 1
                slot = self._free_slot()
                if slot is not None:
                    self.add_task(kind="putaway", src=self._spot(task.lane, task.id, "back"), dst=slot["node"],
                                  lane=task.lane,
                                  slot=(slot["bay"], slot["level"]))
            elif task.kind == "putaway":
                yield from self.handle(v, self._handle_time(HANDLE["fork"]) + self._lift_time(v, task.slot[1]), "drop")
                self.counts["putaway"] += 1
            elif task.kind == "retrieve":
                self.lane_count[task.lane] += 1
                self.lane_max[task.lane] = max(self.lane_max[task.lane], self.lane_count[task.lane])
                yield from self.handle(v, self._handle_time(HANDLE["lane_drop"]), "drop")
            elif task.kind == "load":
                yield from self.handle(v, self._handle_time(HANDLE["trailer_drop"]), "drop")
                self.counts["outbound_shipped"] += 1
            if task.kind == "unload":
                self.lane_max[task.lane] = max(self.lane_max[task.lane], self.lane_count[task.lane])
            if task.kind in ("unload", "load"):
                self.door_crew[task.lane] -= 1
            v.tasks_done += 1
            self._event("task", task.kind, task.id, v.id)
            task.done.succeed()
            self._signal()

    def _fail(self, task, v):
        self.counts["unreachable_tasks"] += 1
        self._event("unreachable", task.kind, task.id, v.id)
        if task.kind == "unload":
            self.lane_count[task.lane] -= 1
        if task.kind in ("unload", "load"):
            self.door_crew[task.lane] -= 1
        task.done.succeed()

    def _free_slot(self):
        cls = self.rng_slot.choice(["A", "B", "C"], p=[self.prof["velocity"][c] for c in "ABC"])
        for want in [cls, "A", "B", "C"]:
            for s in self.slots:
                if s["zone"] == want and not s["full"] and not s["reserved"]:
                    s["reserved"] = True
                    s["full"] = True
                    return s
        return None

    def _full_slot(self):
        cls = self.rng_slot.choice(["A", "B", "C"], p=[self.prof["velocity"][c] for c in "ABC"])
        for want in [cls, "A", "B", "C"]:
            cand = [s for s in self.slots if s["zone"] == want and s["full"] and not s["reserved"]]
            if cand:
                s = cand[int(self.rng_slot.integers(len(cand)))]
                s["full"] = False
                return s
        return None

    def truck(self, tid, kind, inbound, appt, pallets):
        late = float(self.rng_arr.normal(0, LATENESS_SD_MIN * 60))
        arrive = max(0.0, appt + late)
        rec = {"id": tid, "kind": kind, "inbound": inbound, "appt": appt, "arrive": arrive, "pallets": pallets}
        self.trucks.append(rec)
        door_lane = None
        if not inbound:                                     # stage ahead of the appointment at a chosen door
            door_lane = self.outbound_doors[tid % len(self.outbound_doors)]
            release = max(0.0, appt - 3600)
            yield self.env.timeout(release)
            staged = []
            for _ in range(pallets):
                s = self._full_slot()
                if s is None:
                    break
                s["reserved"] = True
                staged.append(self.add_task(kind="retrieve", src=s["node"], dst=self._spot(door_lane, len(staged), "back"),
                                            lane=door_lane, slot=(s["bay"], s["level"]), truck=tid))
            yield self.env.timeout(max(0.0, arrive - self.env.now))
        else:
            yield self.env.timeout(arrive)
        rec["arrived_at"] = self.env.now
        self._event("arrive", tid)
        if inbound:
            door = yield self.free_doors.get(lambda d: d in self.inbound_doors)
        else:
            door = yield self.free_doors.get(lambda d: d == door_lane)
        rec["door"] = int(door)
        rec["docked"] = self.env.now + BACK_IN_S
        yield self.env.timeout(BACK_IN_S)
        t_dock = self.env.now
        if inbound:
            jobs = [self.add_task(kind="unload", src=self.door_node[door], dst=self._spot(door, i, "front"), lane=door,
                                  truck=tid) for i in range(pallets)]
        else:
            yield simpy.AllOf(self.env, [t.done for t in staged])
            jobs = [self.add_task(kind="load", src=self._spot(door, i, "front"), dst=self.door_node[door], lane=door,
                                  truck=tid) for i, _ in enumerate(staged)]
        if jobs:
            yield simpy.AllOf(self.env, [t.done for t in jobs])
        rec["work_done"] = self.env.now
        rec["turn_min"] = round((self.env.now - t_dock) / 60, 1)
        yield self.env.timeout(PAPERWORK_S)
        rec["depart"] = self.env.now
        rec["dwell_min"] = round((rec["depart"] - rec["arrived_at"]) / 60, 1)
        self.door_busy += rec["depart"] - rec["arrived_at"] - (rec["docked"] - BACK_IN_S - rec["arrived_at"])
        self._event("depart", tid)
        yield self.free_doors.put(door)

    # ---------------------------------------------------------------- run
    def schedule(self):
        eq = equipment()
        scale = self.lay.site.area_sqft / self.prof["ref_sqft"] * self.volume_scale
        H = self.prof["shift_hours"] * 3600
        tid = 0
        for inbound, plan in ((True, self.prof.get("inbound", {})), (False, self.prof.get("outbound", {}))):
            for kind, n in sorted(plan.items()):
                if "pallets_per_load" not in eq.get(kind, {}):
                    continue                                 # parcel vans: not a pallet flow
                count = max(1, round(n * scale)) if n else 0
                for i in range(count):
                    appt = (i + 0.5) / count * max(H - 2 * 3600, 3600)
                    pallets = eq[kind]["pallets_per_load"]
                    key = "inbound_planned" if inbound else "outbound_planned"
                    self.counts[key] += pallets
                    self.env.process(self.truck(tid, kind, inbound, appt, pallets))
                    tid += 1

    def run(self, volume_scale=1.0, max_hours=None):
        self.volume_scale = volume_scale
        if not self.lay.lanes:
            return self.results(note="no usable door with a staging lane: nothing can be received or shipped")
        for v in self.vehicles:
            self.env.process(self.vehicle(v))
        self.schedule()
        cap = (max_hours or self.prof["shift_hours"] + 4) * 3600
        self.env.run(until=cap)
        return self.results()

    def results(self, note=None):
        trucks = self.trucks
        done = [t for t in trucks if "depart" in t]
        H = self.prof["shift_hours"] * 3600
        span = max([t["depart"] for t in done] or [H])
        doors = max(len(self.lane_node), 1)
        veh = []
        for v in self.vehicles:
            busy = v.t["loaded"] + v.t["empty"] + v.t["handling"] + v.t["delay"]
            veh.append({"id": v.id, "kind": v.kind, "tasks": v.tasks_done,
                        **{k: round(x / 60, 1) for k, x in v.t.items()},
                        "utilization": round(busy / span, 3) if span else 0,
                        "cruise_speed_mph": round(1.25 / v.hop_loaded / MPH, 1)})
        delay_h = sum(v.t["delay"] for v in self.vehicles) / 3600
        moving_h = sum(v.t["loaded"] + v.t["empty"] + v.t["delay"] for v in self.vehicles) / 3600
        inb = [t for t in done if t["inbound"] and t["kind"] == "semi_53"]
        h = hashlib.sha256(repr(self.log).encode()).hexdigest()[:16]
        return {
            "note": note, "seed": self.seed, "profile": self.profile_key, "site": self.lay.site.name,
            "kpi": {
                "trucks_planned": len(trucks), "trucks_done": len(done),
                "pallets_received": self.counts["inbound_received"], "pallets_received_plan": self.counts["inbound_planned"],
                "pallets_putaway": self.counts["putaway"],
                "pallets_shipped": self.counts["outbound_shipped"], "pallets_shipped_plan": self.counts["outbound_planned"],
                "last_truck_out_h": round(span / 3600, 2) if done else None,
                "mean_dwell_min": round(float(np.mean([t["dwell_min"] for t in done])), 1) if done else None,
                "max_dwell_min": round(max([t["dwell_min"] for t in done]), 1) if done else None,
                "mean_wait_for_door_min": round(float(np.mean([(t["docked"] - BACK_IN_S - t["arrived_at"]) / 60
                                                               for t in done])), 1) if done else None,
                "unload_53ft_min": round(float(np.median([t["turn_min"] for t in inb])), 1) if inb else None,
                "door_utilization": round(self.door_busy / (doors * span), 3) if span else None,
                "congestion_delay_share": round(delay_h / moving_h, 3) if moving_h else 0.0,
                "staging_lane_peak": max(self.lane_max or [0]),
                "reroutes": self.traffic.reroutes,
                "deadlock_breaks": self.traffic.deadlock_breaks,
                "unreachable_tasks": self.counts["unreachable_tasks"],
            },
            "vehicles": veh, "trucks": trucks, "log_hash": h, "assumptions": self.assumptions,
            "layout": {**self.lay.params, **self.lay.checks},
        }
