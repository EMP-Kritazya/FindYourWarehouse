"""The floor the simulation runs on: a 25 cm grid of free / blocked cells plus dock doors.

Convention: arrays are indexed [ix, iy] (x east-ish, y north-ish in the scan's rotated frame), cell = 0.25 m.
The site is always normalised so the DOCK WALL is the low-y side (y = 0 side); `transform` records how to map
back to the scan frame. Every assumption is recorded in `assumptions` so the UI can show it.

    synthetic(width_m, depth_m, ...)    a clean rectangular test building (golden tests)
    from_interior(model_id, ...)        a real scanned warehouse floor (build/twins/interior/<model>.npz)
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from proinspect.config import BUILD

CELL = 0.25
M_TO_FT = 3.28084


@dataclass
class Door:
    x: float                      # metres along the dock wall
    kind: str = "dock_high"       # dock_high | drive_in
    source: str = "assumed"       # vexcel | assumed | synthetic
    width_m: float = None         # measured door width (Vexcel), None = unknown


@dataclass
class Site:
    name: str
    floor: np.ndarray             # bool [nx, ny]: inside the building
    free: np.ndarray              # bool [nx, ny]: floor with nothing standing on it (0.1-2.0 m)
    doors: list
    clear_height_ft: float
    cell: float = CELL
    wall_y: float = 0.0           # y (m) of the dock wall's inner face
    transform: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)

    @property
    def nx(self):
        return self.floor.shape[0]

    @property
    def ny(self):
        return self.floor.shape[1]

    @property
    def area_sqft(self):
        return float(self.floor.sum()) * self.cell ** 2 * 10.7639


def synthetic(width_m=60.0, depth_m=90.0, doors=4, door_kind="dock_high", columns=None, clear_ft=32.0):
    """A rectangular building with the dock wall at y = 0. `columns`: (spacing_x_m, spacing_y_m) grid of
    0.5 m square columns, or None for clear span."""
    nx, ny = int(round(width_m / CELL)), int(round(depth_m / CELL))
    floor = np.ones((nx, ny), bool)
    free = floor.copy()
    if columns:
        sx, sy = columns
        for x in np.arange(sx, width_m - 1, sx):
            for y in np.arange(sy, depth_m - 1, sy):
                i, j = int(x / CELL), int(y / CELL)
                free[i - 1:i + 1, j - 1:j + 1] = False
    pitch = 4.0                                                     # ~13 ft door centres
    start = (width_m - pitch * (doors - 1)) / 2
    ds = [Door(start + k * pitch, door_kind, "synthetic") for k in range(doors)]
    return Site(f"synthetic {width_m:.0f}x{depth_m:.0f} m", floor, free, ds, clear_ft,
                assumptions=["synthetic building"])


def _rotate_dock_wall_down(floor, free, side):
    """Rotate arrays so `side` ('S','N','W','E' in array terms: low-y, high-y, low-x, high-x) becomes low-y."""
    k = {"S": 0, "E": 1, "N": 2, "W": 3}[side]
    return np.rot90(floor, k), np.rot90(free, k), k


def _longest_straight_side(floor):
    """The bounding-box side of the floor with the longest straight run of wall (a dock wall is long and straight)."""
    best = None
    for side in "SNWE":
        f, _, _ = _rotate_dock_wall_down(floor, floor, side)
        cols = np.where(f.any(axis=1))[0]
        if not len(cols):
            continue
        first = np.array([np.argmax(f[i]) if f[i].any() else 10 ** 6 for i in range(f.shape[0])])
        base = np.median(first[cols])
        straight = np.abs(first - base) <= 2                          # within 0.5 m of one line
        lab, n = ndimage.label(straight)
        run = max((lab == k).sum() for k in range(1, n + 1)) if n else 0
        if best is None or run > best[1]:
            best = (side, run, base)
    return best


def from_interior(model_id, doors=None, clear_ft=None, dock_side=None, n_doors=None):
    """A real scanned floor. `doors`: list of Door positions along the dock wall (from the Vexcel/Matterport
    fusion). Without them, doors are PLACED (assumption, shown in the UI) on the longest straight wall."""
    g = np.load(BUILD / "twins" / "interior" / f"{model_id}.npz")
    floor = g["floor"].astype(bool)
    if "structure" in g.files:          # plan for a new occupant: keep the structure, clear today's contents
        free = floor & ~g["structure"].astype(bool)
        contents_note = ("today's contents (pallets, low racking, clutter) are assumed removed; walls, columns and "
                         "full-height structures are kept")
    else:
        free = g["free"].astype(bool)
        contents_note = "floor as scanned, including today's contents"
    lab, n = ndimage.label(floor)                                     # keep the main connected floor
    if n > 1:
        sizes = ndimage.sum(floor, lab, range(1, n + 1))
        floor = lab == (1 + int(np.argmax(sizes)))
    free &= floor
    assumptions = [f"floor = scanned area of {model_id} (often part of the building)", contents_note]
    picked = "the longest straight wall" if dock_side is None else "the given dock wall"
    side, run, _ = _longest_straight_side(floor) if dock_side is None else (dock_side, None, None)
    shape0 = list(floor.shape)
    floor, free, k = _rotate_dock_wall_down(floor, free, side)
    xs, ys = np.where(floor)
    crop = [int(xs.min()), int(ys.min())]
    floor, free = floor[xs.min():xs.max() + 1, ys.min():ys.max() + 1], free[xs.min():xs.max() + 1, ys.min():ys.max() + 1]
    if doors is None:
        # place doors along the straight part of the low-y wall, 4 m apart, 3 m clear of corners
        first = np.array([np.argmax(floor[i]) if floor[i].any() else 10 ** 6 for i in range(floor.shape[0])])
        base = int(np.median(first[first < 10 ** 6]))
        ok = np.where(np.abs(first - base) <= 2)[0] * CELL
        span = (ok.min() + 3, ok.max() - 3) if len(ok) else (3, floor.shape[0] * CELL - 3)
        count = min(n_doors or 4, max(1, int((span[1] - span[0]) // 4.0) + 1))
        mid = (span[0] + span[1]) / 2
        xs_d = [mid + (k - (count - 1) / 2) * 4.0 for k in range(count)]
        doors = [Door(float(x), "dock_high", "assumed") for x in xs_d]
        assumptions.append(f"{count} dock doors PLACED 4 m apart on {picked} ('{side}' side): positions along the "
                           "wall are not matched to aerial door detections")
    if clear_ft is None:
        import json
        r = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
        clear_ft = (r.get("clear") or {}).get("p5_ft") or (r["deck_ft"] - 1.1 if r.get("deck_ft") else 20.0)
        assumptions.append(f"usable clear height {clear_ft} ft = lowest-obstruction P5 from the scan")
    first = np.array([np.argmax(floor[i]) if floor[i].any() else 0 for i in range(floor.shape[0])])
    wall_y = float(np.median(first)) * CELL
    return Site(f"scan {model_id}", floor, free, doors, float(clear_ft), wall_y=wall_y,
                transform={"model_id": model_id, "dock_side": side, "rot90": k,
                           # to map the sim frame back to the scan (proinspect.sim.export): sim cell (a, b) is
                           # cell (a + crop[0], b + crop[1]) of np.rot90(npz grid of shape `shape0`, k)
                           "crop": crop, "shape0": shape0, "origin": [float(v) for v in g["origin"]],
                           "theta": float(g["theta"][0]), "grid": float(g["grid"][0]) if "grid" in g.files else CELL},
                assumptions=assumptions)
