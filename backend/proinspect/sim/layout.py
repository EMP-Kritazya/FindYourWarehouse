"""Rule-based warehouse layout in the real floor (docs/research/simulation.md §5). Deterministic.

Dock wall at low y. From the wall: a dock apron aisle, one staging lane per door (8 ft wide x 13 pallets deep =
one 26-pallet trailer), then selective rack modules running perpendicular to the dock wall:
    [aisle A][rack D][flue F][rack D] [aisle A][rack D][flue F][rack D] ...
Aisle width = the put-away truck's min_aisle_ft. Cross-aisles every ~36 m (120 ft) of rack run. The lateral offset is
searched in 25 cm steps; a bay is kept only if its footprint (+ 15 cm) and the aisle it faces are clear.
Levels = floor((clear - 3 ft) / 5.5 ft) + 1, capped by the truck's lift height. All parameters are listed in
`params` so the UI can show them as assumptions.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from proinspect.library import equipment
from proinspect.sim.site import CELL, M_TO_FT, Site

FT = 0.3048
RACK_D = 42 * 0.0254          # rack frame depth for 48" pallets
FLUE = 0.15
BAY = 8.25 * FT               # two 40" pallets per bay
PALLET_LEVEL_FT = 5.5         # 54" load + lift-off + beam
SPRINKLER_FT = 3.0            # deflector-to-storage clearance assumption [U: NFPA 13]
PERIMETER = 1.2               # perimeter aisle (44-48 in fire-code aisle)
APRON = 4.5                   # dock apron / main aisle depth along the dock wall
LANE_W, LANE_D, LANE_CAP = 8 * FT, 13 * 48 * 0.0254, 26
CROSS_EVERY = 36.0            # cross-aisle every ~120 ft of rack run
MAIN_AISLE = 14 * 0.3048      # two-way main cross aisle between staging and racks (12-14 ft is typical)


@dataclass
class Layout:
    site: Site
    aisle_m: float
    levels: int
    bays: list                       # dicts: id, x0, x1, y0, y1, face_x, aisle_side(-1 left / +1 right), slots
    lanes: list                      # dicts: door, x0, x1, y0, y1, capacity
    rack: np.ndarray                 # bool [nx, ny]
    traversable: np.ndarray          # bool [nx, ny]
    dock_zone_end: float
    params: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)

    @property
    def positions(self):
        return sum(b["slots"] for b in self.bays)


def _rect(mask, x0, x1, y0, y1):
    i0, i1 = max(int(np.floor(x0 / CELL)), 0), int(np.ceil(x1 / CELL))
    j0, j1 = max(int(np.floor(y0 / CELL)), 0), int(np.ceil(y1 / CELL))
    if i1 > mask.shape[0] or j1 > mask.shape[1] or i0 >= i1 or j0 >= j1:
        return None
    return mask[i0:i1, j0:j1]


def _all(mask, x0, x1, y0, y1):
    r = _rect(mask, x0, x1, y0, y1)
    return r is not None and r.size > 0 and bool(r.all())


def putaway_vehicle(fleet):
    """The fleet vehicle that stores pallets in racks: highest lift among lift trucks."""
    eq = equipment()
    cands = [k for k in fleet if k in eq and eq[k].get("lift_height_ft", 0) >= 10 and eq[k].get("min_aisle_ft")]
    return max(cands, key=lambda k: (eq[k]["lift_height_ft"], -eq[k]["min_aisle_ft"])) if cands else None


def generate(site: Site, fleet: dict):
    eq = equipment()
    pv = putaway_vehicle(fleet)
    aisle = (eq[pv]["min_aisle_ft"] if pv else 12.0) * FT
    lift = eq[pv]["lift_height_ft"] if pv else 15
    levels = max(1, min(int((site.clear_height_ft - SPRINKLER_FT) // PALLET_LEVEL_FT) + 1,
                        int(lift // PALLET_LEVEL_FT) + 1))

    interior = ndimage.binary_fill_holes(site.floor)
    perim = ndimage.binary_erosion(interior, iterations=int(PERIMETER / CELL))
    obstacles = interior & ~site.free
    obst_buf = ndimage.binary_dilation(obstacles, iterations=1)       # 25 cm (>= 15 cm) clearance
    usable = site.free & perim & ~obst_buf
    open_floor = site.free & interior                                 # aisles may run along walls

    # staging lanes, one per door: a full trailer (13 deep) when the building is deep enough, shorter
    # (min 3 deep = 6 pallets) in shallow buildings, never more than 35% of the building's depth
    depth = float(np.where(interior.any(axis=0))[0].max() + 1) * CELL - site.wall_y
    lane_d = max(min(LANE_D, 0.35 * depth - APRON), 3 * 48 * 0.0254)
    lane_cap = 2 * int(lane_d / (48 * 0.0254))
    lanes = []
    y0 = site.wall_y + APRON
    for k, d in enumerate(site.doors):
        x0, x1 = d.x - LANE_W / 2, d.x + LANE_W / 2
        if _all(open_floor, x0, x1, y0, y0 + lane_d):
            lanes.append({"door": k, "x0": x0, "x1": x1, "y0": y0, "y1": y0 + lane_d, "capacity": lane_cap})
    dock_zone_end = site.wall_y + APRON + lane_d + MAIN_AISLE

    W = site.nx * CELL
    H = site.ny * CELL
    pitch = aisle + 2 * RACK_D + FLUE
    best = None
    for off in np.arange(0, pitch, CELL):
        bays, blocked_aisles = [], 0
        x_a = PERIMETER + off - pitch                               # first aisle may start before the floor
        while x_a < W:
            ra = (x_a + aisle, x_a + aisle + RACK_D)
            rb = (ra[1] + FLUE, ra[1] + FLUE + RACK_D)
            left_aisle = (x_a, x_a + aisle)
            right_aisle = (rb[1], rb[1] + aisle)
            strip = _rect(interior & ~site.free, max(x_a, 0), min(x_a + aisle, W), dock_zone_end, H)
            if strip is not None and strip.size and strip.any():
                blocked_aisles += 1                         # a column / obstacle stands in this aisle
            y = dock_zone_end
            run = 0.0
            while y + BAY <= H:
                if run >= CROSS_EVERY:
                    y += max(aisle, 3.6)
                    run = 0.0
                    continue
                for (rx0, rx1), (ax0, ax1), side in ((ra, left_aisle, -1), (rb, right_aisle, +1)):
                    if _all(usable, rx0, rx1, y, y + BAY) and _all(open_floor, ax0, ax1, y, y + BAY):
                        bays.append({"x0": rx0, "x1": rx1, "y0": y, "y1": y + BAY,
                                     "face_x": rx0 if side < 0 else rx1, "aisle_side": side,
                                     "aisle_x": (ax0 + ax1) / 2})
                y += BAY
                run += BAY
            x_a += pitch
        score = len(bays) - 25 * blocked_aisles               # columns belong in flues, not aisles
        if best is None or score > best[2]:
            best = (off, bays, score, blocked_aisles)
    off, bays, _, blocked_aisles = best
    rack = np.zeros_like(site.floor)
    for i, b in enumerate(bays):
        b["id"] = i
        b["slots"] = 2 * levels
        r = _rect(rack, b["x0"], b["x1"], b["y0"], b["y1"])
        r[...] = True
    # staging lanes are full of pallets: only their two ends are drivable (flow-through staging: the dock crew works
    # the apron end, the put-away crew works the rack end)
    lane_core = np.zeros_like(site.floor)
    for ln in lanes:
        r = _rect(lane_core, ln["x0"], ln["x1"], ln["y0"] + 1.5, ln["y1"] - 1.5)
        if r is not None:
            r[...] = True
    traversable = open_floor & ~rack & ~lane_core
    # keep only bays a rack truck can reach from the dock zone; report how many were dropped
    from proinspect.sim.traffic import LaneGraph
    g = LaneGraph(traversable)
    width = (equipment()[pv]["dims_ft"]["width"] * FT / 2 + 0.1) if pv else 0.7
    seeds = [g.nearest(d.x, site.wall_y + APRON / 2, width) for d in site.doors]
    seeds = [s for s in seeds if s is not None]
    dropped = 0
    if seeds:
        reach = np.min([g.hops_from(s, width) for s in seeds], axis=0)
        keep = []
        for b in bays:
            n = g.nearest(b["aisle_x"], (b["y0"] + b["y1"]) / 2, width)
            if n is not None and np.isfinite(reach[n]):
                keep.append(b)
            else:
                r = _rect(rack, b["x0"], b["x1"], b["y0"], b["y1"])
                r[...] = False
                dropped += 1
        bays = keep
        for i, b in enumerate(bays):
            b["id"] = i
        traversable = open_floor & ~rack & ~lane_core
    lay = Layout(site, aisle, levels, bays, lanes, rack, traversable, dock_zone_end,
                 params={"putaway_vehicle": pv, "aisle_ft": round(aisle / FT, 1), "levels": levels,
                         "rack_depth_in": 42, "bay_ft": 8.25, "pallet_level_ft": PALLET_LEVEL_FT,
                         "sprinkler_clearance_ft": SPRINKLER_FT, "perimeter_aisle_m": PERIMETER,
                         "apron_m": APRON, "main_aisle_m": round(MAIN_AISLE, 2), "cross_aisle_every_m": CROSS_EVERY, "offset_m": float(off),
                         "orientation": "racks perpendicular to the dock wall",
                         "aisles_with_columns": blocked_aisles, "bays_dropped_unreachable": dropped})
    # density sanity: selective rack typically stores one pallet per ~8-30 SF of building (more levels = denser)
    sf_per_pos = site.area_sqft / lay.positions if lay.positions else None
    lay.checks = {"positions": lay.positions, "sqft_per_position": round(sf_per_pos, 1) if sf_per_pos else None,
                  "density_ok": bool(sf_per_pos and 6 <= sf_per_pos <= 40),
                  "staging_lanes": len(lanes), "lane_capacity": lane_cap, "doors": len(site.doors),
                  "rack_rows_reachable": all("aisle_x" in b for b in bays)}
    lay.params["lane_depth_m"] = round(lane_d, 2)
    return lay
