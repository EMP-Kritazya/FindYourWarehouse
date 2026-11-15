"""The yard of a real property, from its exterior twin (build/twins/exterior/<pid>.{json,npz}).

    load(property_id) -> Yard: obstacle grid (YardGrid), paved mask, parcel, buildings, frame
    doors(yard, model_ids) -> (doors, source, notes)   marked doors > oblique detections > assumed placeholders
    entrance(yard, dock_center) -> (point, method)
    Router: shortest drivable routes from the street entrance (1 m grid)

Frame: the twin's local metres east (x) / north (y) of (lat0, lon0); grid row 0 = north, 0.5 m cells.
"""
import json
import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, Polygon

from proinspect.config import BUILD
from proinspect.tools import elevation as el
from proinspect.yard.kinematics import FT, YardGrid

EXT = BUILD / "twins" / "exterior"
DOOR_PITCH_M = 13 * FT          # assumed placeholder spacing, 13 ft centres [U: typical 12-14 ft]
WALL_BLUR_M = 3.0               # cells this close to a wall that are "unpaved" only because the DSM is high: wall blur
CANOPY_ZONE_M = 5.0             # objects > 1 m within this distance of a wall are canopies / overhangs (as in the twin)


@dataclass
class Yard:
    property_id: int
    twin: dict
    lat0: float
    lon0: float
    half: float
    res: float
    building: np.ndarray
    paved: np.ndarray
    vehicle: np.ndarray
    grid: YardGrid
    parcel: Polygon | None
    parcel_mask: np.ndarray | None
    footprints: list = field(default_factory=list)      # [(index, Polygon)] warehouse-sized buildings
    notes: list = field(default_factory=list)

    def lonlat(self, x, y):
        lat, lon = el.to_latlon(np.asarray(x, float), np.asarray(y, float), self.lat0, self.lon0)
        return lon, lat


def _parcel(property_id, lat0, lon0):
    """The Vexcel parcel polygon (local metres), from the disk cache only (no paid call at test time)."""
    try:
        from proinspect import cache
        from proinspect.twin import exterior as ex
        with cache.cache_only():
            _, _, parcel_ll, _ = ex._site(property_id)
        from shapely.ops import transform
        g = transform(lambda lon, lat, z=None: el.to_local(lat, lon, lat0, lon0), parcel_ll)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda p: p.area)
        return g
    except Exception:       # noqa: BLE001  (cache miss / offline: the parcel is only a soft limit)
        return None


def _bare_soil(property_id, shape):
    """Dirt / gravel fields read as 'paved' in the twin (low and not green). Mark large brownish, darker-than-concrete
    areas of the ortho as unpaved: (R - B) / brightness >= 0.11 and brightness < 150, smoothed 2.5 m, blobs >= 50 m2."""
    from PIL import Image
    p = EXT / f"{property_id}_ortho.jpg"
    if not p.exists():
        return np.zeros(shape, bool)
    a = np.asarray(Image.open(p).convert("RGB").resize(shape[::-1]), dtype=np.float32)
    a = ndimage.uniform_filter(a, size=(5, 5, 1))
    br = a.mean(axis=2)
    m = ((a[..., 0] - a[..., 2]) / np.maximum(br, 1) >= 0.11) & (br < 150)
    m = ndimage.binary_opening(m, iterations=2)
    lab, n = ndimage.label(m)
    if not n:
        return m
    sizes = ndimage.sum(m, lab, np.arange(1, n + 1))
    return np.isin(lab, 1 + np.flatnonzero(sizes >= 200))


def load(property_id):
    p = EXT / f"{property_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no exterior twin for property {property_id}")
    twin = json.loads(p.read_text())
    z = np.load(EXT / f"{property_id}.npz")
    fr = twin["frame"]
    from proinspect.twin.exterior import Frame
    frame = Frame(fr["lat0"], fr["lon0"], fr["half_m"])
    paved, vehicle, others = z["paved"], z["vehicle"], z["others"]
    nd = z["ndsm"].astype(np.float32)
    main = Polygon(twin["building"]["footprint_local"])
    fps = [(b["index"], Polygon(b["footprint_local"])) for b in twin.get("buildings", [])] or [(0, main)]
    building = frame.raster([main]) | others
    near_wall = ndimage.distance_transform_edt(~building) * fr["res_m"] < CANOPY_ZONE_M
    obj = (nd > 1.0) & ~vehicle & ~building & ~near_wall
    lab, nlab = ndimage.label(obj)
    if nlab:                                                            # single-cell DSM specks are noise
        sizes = ndimage.sum(obj, lab, np.arange(1, nlab + 1))
        obj = np.isin(lab, 1 + np.flatnonzero(sizes >= 2))
    soil = _bare_soil(property_id, paved.shape)
    land = (~paved | soil) & ~vehicle & ~building & ~obj
    dwall = ndimage.distance_transform_edt(~building) * fr["res_m"]
    land &= ~((dwall < WALL_BLUR_M) & (nd > 0.5) & ~soil)      # DSM wall blur, not landscaping
    lab, nlab = ndimage.label(land)
    if nlab:
        sizes = ndimage.sum(land, lab, np.arange(1, nlab + 1))
        land = np.isin(lab, 1 + np.flatnonzero(sizes >= 16))           # drop specks < 4 m2 (paint, shadows)
    kind = np.zeros(paved.shape, np.uint8)
    kind[land] = 3
    kind[obj] = 2
    kind[building] = 1
    parcel = _parcel(property_id, fr["lat0"], fr["lon0"])
    pm = frame.raster([parcel]) if parcel is not None else None
    notes = []
    if parcel is None:
        notes.append("parcel polygon not in the cache: the parcel line is not checked")
    if twin.get("parcel_mismatch"):
        notes.append("WARNING: " + (twin.get("parcel_note") or "the Vexcel parcel may be the wrong one"))
    g = YardGrid(kind, fr["half_m"], fr["res_m"], pm)
    return Yard(property_id, twin, fr["lat0"], fr["lon0"], fr["half_m"], fr["res_m"], building, paved, vehicle, g,
                parcel, pm, fps, notes)


# ------------------------------------------------------------------ doors
def _edges(yard):
    from proinspect.twin.exterior import _edges
    return [e for i, fp in yard.footprints for e in _edges(fp, i)]


def _apron_depth(yard, e, step=2.0, max_m=80.0):
    """Median paved depth in front of a facade (rays every 2 m; stop at buildings, objects, 3 m of unpaved, and the
    parcel line)."""
    deps = []
    g = yard.grid
    for s in np.arange(1.0, e["len"] - 1.0, step):
        bx, by = e["a"][0] + e["t"][0] * s, e["a"][1] + e["t"][1] * s
        run, d = 0.0, 0.0
        while d < max_m:
            d += 0.5
            r, c = g.rc(np.array([bx + e["n"][0] * d]), np.array([by + e["n"][1] * d]))
            k = g.kind[r[0], c[0]]
            if (k in (1, 2) and d > 1.0) or (yard.parcel_mask is not None and not yard.parcel_mask[r[0], c[0]]):
                break
            run = run + 0.5 if k == 3 else 0.0
            if run >= 3.0:
                d -= run
                break
        deps.append(d)
    return float(np.median(deps)) if deps else 0.0


def doors(yard, model_ids=()):
    """Doors to test, best source first. Returns (doors, source, notes). Each door:
    {id, x, y, normal:[nx,ny], width_m, kind, source, edge?}."""
    notes = []
    try:
        from proinspect import doors as door_store                   # S4, built in parallel
    except Exception:  # noqa: BLE001
        door_store = None
        notes.append("door store (proinspect.doors) not available yet")
    if door_store is not None and hasattr(door_store, "to_enu"):
        for mid in model_ids:
            try:
                got = door_store.to_enu(mid, yard.property_id)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"marked doors for scan {mid} could not be placed on the aerial frame: {exc}")
                continue
            if got:
                out = []
                for i, d in enumerate(got):
                    n = d.get("normal") or [0, 1]
                    n = [n["x"], n["y"]] if isinstance(n, dict) else list(n)
                    out.append({"id": str(d.get("id") or f"m{i + 1}"), "x": float(d["x"]), "y": float(d["y"]),
                                "normal": n, "width_m": float(d.get("width_m") or 2.75),
                                "kind": d.get("kind", "dock_high"),
                                "source": d.get("source") or "marked_in_scan"})
                return out, "marked_in_scan", notes
            notes.append(f"no marked doors placeable for scan {mid} (none marked, or the scan-to-aerial "
                         "registration is not trustworthy)")
    edges = _edges(yard)
    items = yard.twin.get("doors", {}).get("items", [])
    courts = yard.twin.get("courts", [])
    if items and courts:
        main = sorted(courts, key=lambda c: (c.get("kind") != "dock court", -c["doors"], -c["depth_ft"]))[0]
        e = edges[main["edge"]] if main["edge"] < len(edges) else None
        if e is not None:
            on = sorted((d for d in items if d["edge"] == main["edge"]), key=lambda d: d["s_m"])
            out = [{"id": f"v{i + 1}", "x": d["x"], "y": d["y"], "normal": list(e["n"]),
                    "width_m": d["width_ft"] * FT, "kind": d["class"], "edge": main["edge"],
                    "source": "detected_oblique (Vexcel obliques; a lower bound, doors hidden by trailers are missed)"}
                   for i, d in enumerate(on)]
            other = len(items) - len(on)
            notes.append(f"doors: {len(out)} detected on the main court facade ({main['facing']}-facing) in Vexcel "
                         f"obliques; this is a LOWER BOUND" + (f"; {other} more on other facades not tested" if other else ""))
            return out, "detected_oblique", notes
    # placeholder: the facade with the deepest paved apron
    main_i = next((i for i, fp in yard.footprints if fp.equals(Polygon(yard.twin["building"]["footprint_local"]))),
                  yard.footprints[0][0])
    cand = [e for e in edges if e["bldg"] == main_i and e["len"] >= 20]
    best = max(cand, key=lambda e: _apron_depth(yard, e) * min(e["len"], 120))
    try:
        cat = {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}
        costar = cat.get(yard.property_id, {}).get("docks_external")
    except Exception:  # noqa: BLE001
        costar = None
    fit_n = int((best["len"] - 6) // DOOR_PITCH_M)
    n = max(1, min(costar or max(4, fit_n // 2), fit_n, 30))
    mid = best["len"] / 2
    out = []
    for i in range(n):
        s = mid + (i - (n - 1) / 2) * DOOR_PITCH_M
        out.append({"id": f"a{i + 1}", "x": round(best["a"][0] + best["t"][0] * s, 2),
                    "y": round(best["a"][1] + best["t"][1] * s, 2), "normal": list(best["n"]), "width_m": 9 * FT,
                    "kind": "dock_high", "edge": None,
                    "source": "assumed: door positions assumed (no marked doors)"})
    notes.append(f"door positions assumed (no marked doors): {n} doors at 13 ft centres, centred on the "
                 f"{best['facing']}-facing facade with the deepest paved apron "
                 f"({_apron_depth(yard, best) / FT:.0f} ft median)" + (f"; count from CoStar ({costar} docks)" if costar
                                                                        else "; count assumed"))
    return out, "assumed", notes


def neighbor_offsets(door, all_doors, within=8.0):
    """Along-facade offsets of other tested doors on the same facade line within `within` metres."""
    n = np.asarray(door["normal"], float)
    t = np.array([n[1], -n[0]])
    out = []
    for o in all_doors:
        if o is door:
            continue
        dx, dy = o["x"] - door["x"], o["y"] - door["y"]
        along, across = dx * t[0] + dy * t[1], dx * n[0] + dy * n[1]
        if abs(along) <= within and abs(across) < 2.0 and np.dot(o["normal"], n) > 0.9:
            out.append(float(along))
    return out


# ------------------------------------------------------------------ street entrance
def entrance(yard, dock_xy):
    """Where trucks come in from the street. Method: sample the parcel line every 1 m; a crossing is a run where the
    pavement is continuous from 6 m inside to 6 m outside; prefer long runs whose outside is wide pavement (a road),
    farthest from the docks on a tie. Fallback: the parcel point farthest from the dock facade."""
    if yard.parcel is None:
        return (0.0, yard.half * 0.9), "no parcel: entrance assumed at the north edge of the frame"
    g = yard.grid
    ring = yard.parcel.exterior
    L = ring.length
    clear = g.kind == 0
    road = yard.paved | yard.vehicle

    def ok(x, y, need):
        r, c = g.rc(np.array([x]), np.array([y]))
        return bool(need[r[0], c[0]])
    hits = []
    for s in np.arange(0, L, 1.0):
        p = ring.interpolate(s)
        q = ring.interpolate((s + 1.0) % L)
        tx, ty = q.x - p.x, q.y - p.y
        nrm = math.hypot(tx, ty) or 1
        nx, ny = ty / nrm, -tx / nrm
        if yard.parcel.contains(Point(p.x + nx, p.y + ny)):
            nx, ny = -nx, -ny                                          # outward
        inside = np.mean([ok(p.x - nx * d, p.y - ny * d, clear) for d in range(1, 9)])
        outside = np.mean([ok(p.x + nx * d, p.y + ny * d, road) for d in range(1, 13)])
        if inside >= 0.75 and outside >= 0.75:
            wide = sum(ok(p.x + nx * d, p.y + ny * d, road) for d in range(8, 31, 2))
            hits.append((s, p.x, p.y, nx, ny, wide))
    runs, cur = [], []
    for h in hits:
        if cur and h[0] - cur[-1][0] > 1.5:
            runs.append(cur)
            cur = []
        cur.append(h)
    if cur:
        runs.append(cur)
    if runs and len(runs) > 1 and runs[0][0][0] < 1 and runs[-1][-1][0] > L - 2:
        runs[0] = runs.pop() + runs[0]
    runs = [r for r in runs if len(r) >= 5]
    yard.entrance_candidates = [(len(r), round(float(np.mean([h[5] for h in r])), 1),
                                 round(r[len(r) // 2][1], 1), round(r[len(r) // 2][2], 1)) for r in runs]
    runs = [r for r in runs if len(r) <= 45]     # a longer paved run is a shared lot line, not a driveway
    if runs:
        far = max(math.hypot(r[len(r) // 2][1] - dock_xy[0], r[len(r) // 2][2] - dock_xy[1]) for r in runs) or 1

        def score(r):          # pavement continuing outside (a road), then distance from the docks
            m = r[len(r) // 2]
            return np.mean([h[5] for h in r]) + 8 * math.hypot(m[1] - dock_xy[0], m[2] - dock_xy[1]) / far
        r = max(runs, key=score)
        m = r[len(r) // 2]
        return ((m[1], m[2]), (m[3], m[4])), (
            f"street entrance: a {len(r)} m paved crossing of the parcel line (driveway-sized, <= 45 m) scored by "
            "pavement continuing outside (a road) and distance from the docks; found in the aerial paved mask, not "
            "from a road map")
    # fallback: the middle of the parcel side farthest from the docks
    pts = list(ring.coords)
    sides = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a, b) for a, b in zip(pts[:-1], pts[1:]) if math.dist(a, b) >= 10]
    mx, my, a, b = max(sides, key=lambda q: math.hypot(q[0] - dock_xy[0], q[1] - dock_xy[1]))
    tx, ty = (b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b)
    nx, ny = ty, -tx
    if yard.parcel.contains(Point(mx + nx, my + ny)):
        nx, ny = -nx, -ny
    return ((mx, my), (nx, ny)), (
        "street entrance APPROXIMATED: no driveway-sized paved crossing of the parcel line was found in the aerial "
        "paved mask; used the middle of the parcel side farthest from the docks")


# ------------------------------------------------------------------ routing
class Router:
    """Drivable 1 m grid (clear of obstacles by >= 1.4 m, i.e. half a truck plus margin; pavement outside the parcel
    is allowed), shortest paths from the entrance."""

    def __init__(self, yard, src_xy, cell=1.0):
        g = yard.grid
        k = int(round(cell / g.res))
        n = g.n // k
        sd = g.sd[:n * k, :n * k].reshape(n, k, n, k).min(axis=(1, 3))
        road = (yard.paved | yard.vehicle)[:n * k, :n * k].reshape(n, k, n, k).any(axis=(1, 3))
        self.free = (sd >= 1.4) & road
        self.n, self.cell, self.half = n, cell, g.half
        idx = -np.ones((n, n), int)
        rs, cs = np.nonzero(self.free)
        idx[rs, cs] = np.arange(len(rs))
        self.idx, self.rs, self.cs = idx, rs, cs
        rows, cols, w = [], [], []
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            r2, c2 = rs + dr, cs + dc
            okm = (r2 >= 0) & (r2 < n) & (c2 >= 0) & (c2 < n)
            a = idx[rs[okm], cs[okm]]
            b = idx[r2[okm], c2[okm]]
            sel = b >= 0
            cost = math.hypot(dr, dc) * cell
            rows += [a[sel], b[sel]]
            cols += [b[sel], a[sel]]
            w += [np.full(sel.sum() * 2, cost)]
        N = len(rs)
        self.graph = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
        self.src = self.nearest(*src_xy)
        self.dist, self.pred = dijkstra(self.graph, indices=self.src, return_predecessors=True)

    def node_xy(self, i):
        return -self.half + (self.cs[i] + 0.5) * self.cell, self.half - (self.rs[i] + 0.5) * self.cell

    def nearest(self, x, y):
        X, Y = self.node_xy(np.arange(len(self.rs)))
        d = (X - x) ** 2 + (Y - y) ** 2
        reach = np.isfinite(getattr(self, "dist", np.zeros(len(d))))
        d = np.where(reach, d, np.inf)
        return int(np.argmin(d))

    def chain(self, node):
        out = []
        while node >= 0:
            out.append(node)
            if node == self.src:
                break
            node = self.pred[node]
        return out[::-1]

    def route(self, a_xy, b_xy):
        """Polyline (ENU) from a to b through the entrance tree (common prefix removed), simplified."""
        a, b = self.nearest(*a_xy), self.nearest(*b_xy)
        ca, cb = self.chain(a), self.chain(b)
        k = 0
        while k < min(len(ca), len(cb)) and ca[k] == cb[k]:
            k += 1
        nodes = ca[k - 1:][::-1] + cb[k:] if k else ca[::-1] + cb
        pts = [a_xy] + [self.node_xy(i) for i in nodes] + [b_xy]
        line = LineString(pts).simplify(1.0)
        return [tuple(map(float, p)) for p in line.coords]

    def length(self, xy):
        i = self.nearest(*xy)
        return float(self.dist[i]) if np.isfinite(self.dist[i]) else None
