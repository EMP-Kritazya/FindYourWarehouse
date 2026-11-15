"""Simulation -> Matterport scene JSON (docs/CONTRACTS.md "Matterport scene contract").

Frames, in the order a sim point travels:
  sim      metres, dock wall at low y. Cell (a, b) of the site grid (0.25 m).
  array    cell (a + crop0, b + crop1) of np.rot90(npz_grid, k) (site.from_interior crops and rotates).
  grid     cell (i, j) of the interior npz grid (build/twins/interior/<model>.npz), shape `shape0`.
  rot      metres in the scan's wall-aligned frame: x = origin0 + i * cell (cell i spans [i, i+1)).
  mesh     Matterport Model API frame, metres, +Z up: P_mesh = R(+theta) P_rot (interior.rotate_xy used -theta).
  showcase Matterport Showcase world, metres, y up: model_to_showcase(X, Y, Z).   [UNVERIFIED axis swap]

Every frame change is a function here so the round trip is tested (tests/test_api_sim.py).
"""
import math

import numpy as np

from proinspect.library import equipment

FT = 0.3048
DOOR_W_DEFAULT = round(9 * 0.3048, 2)   # 9 ft dock door when no measured width
PALLET_LEVEL_M = 5.5 * FT
SHOWCASE_AXES = ("showcase (x, y, z) = (X, Z, -Y) of the Model API mesh frame (verified by Stream A on 51 panos of QHMtBN5Nnrc, 0.0 m error)")
YAW_NOTE = ("yaw = radians about +y (three.js rotation.y) = atan2(dY, dX) in the Model API frame. Local +x is rotated "
            "by yaw: a box's width w and a vehicle's length l run along local +x; a heading / door outward direction "
            "is (x, z) = (cos yaw, -sin yaw)")


# ------------------------------------------------------------------ the one place the axis swap lives
def model_to_showcase(X, Y, Z):
    """Model API / mesh frame (+Z up) -> Showcase world (y up). Verified against SDK sweeps (Stream A)."""
    return X, Z, -Y


def showcase_to_model(x, y, z):
    return x, -z, y


# ------------------------------------------------------------------ 2-D frame changes (vectorised, continuous)
def _shape_before(t, shape0):
    """Shape of the array that the t-th rot90 (1-based) is applied to."""
    return tuple(shape0) if (t - 1) % 2 == 0 else tuple(shape0)[::-1]


def array_to_grid(p, q, k, shape0):
    """Continuous index in np.rot90(grid, k) -> continuous index in grid. One rot90: out[i, j] = in[j, n1-1-i],
    i.e. continuous (p, q) in out -> (q, n1 - p) in in."""
    p, q = np.asarray(p, float), np.asarray(q, float)
    for t in range(k % 4, 0, -1):
        n1 = _shape_before(t, shape0)[1]
        p, q = q, n1 - p
    return p, q


def grid_to_array(i, j, k, shape0):
    i, j = np.asarray(i, float), np.asarray(j, float)
    for t in range(1, k % 4 + 1):
        n1 = _shape_before(t, shape0)[1]
        i, j = n1 - j, i
    return i, j


def sim_to_rot(u, v, tf):
    c = tf["grid"]
    p = np.asarray(u, float) / c + tf["crop"][0]
    q = np.asarray(v, float) / c + tf["crop"][1]
    i, j = array_to_grid(p, q, tf["rot90"], tf["shape0"])
    return tf["origin"][0] + i * c, tf["origin"][1] + j * c


def rot_to_sim(x, y, tf):
    c = tf["grid"]
    i = (np.asarray(x, float) - tf["origin"][0]) / c
    j = (np.asarray(y, float) - tf["origin"][1]) / c
    p, q = grid_to_array(i, j, tf["rot90"], tf["shape0"])
    return (p - tf["crop"][0]) * c, (q - tf["crop"][1]) * c


def rot_to_mesh(x, y, theta):
    c, s = math.cos(theta), math.sin(theta)
    x, y = np.asarray(x, float), np.asarray(y, float)
    return c * x - s * y, s * x + c * y


def mesh_to_rot(X, Y, theta):
    return rot_to_mesh(X, Y, -theta)


def sim_to_showcase(u, v, tf, floor_z, h=0.0):
    """Sim (u, v) metres at height h above the floor -> Showcase (x, y, z)."""
    X, Y = rot_to_mesh(*sim_to_rot(u, v, tf), tf["theta"])
    return model_to_showcase(X, Y, np.full(np.shape(X), floor_z + h))


def showcase_to_sim(x, y, z, tf):
    X, Y, Z = showcase_to_model(x, y, z)
    return rot_to_sim(*mesh_to_rot(X, Y, tf["theta"]), tf)


def sim_dir_to_showcase(du, dv, tf):
    """A horizontal direction in the sim frame -> (dx, dz) in Showcase (linear part of the chain)."""
    x0, _, z0 = sim_to_showcase(0.0, 0.0, tf, 0.0)
    x1, _, z1 = sim_to_showcase(du, dv, tf, 0.0)
    return float(x1 - x0), float(z1 - z0)


def heading_yaw(dx, dz):
    """three.js rotation.y that points local +x along the Showcase direction (dx, dz)."""
    return math.atan2(-dz, dx)


def box_yaw(tf):
    """rotation.y that turns a box's local +x onto the sim +x axis."""
    return heading_yaw(*sim_dir_to_showcase(1.0, 0.0, tf))


# ------------------------------------------------------------------ scene pieces
def _outline(site, step=1.0):
    """Outline of the simulated floor (largest polygon), sim metres, simplified to ~0.5 m."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    f = int(round(step / site.cell))
    nx, ny = site.nx // f, site.ny // f
    coarse = site.floor[:nx * f, :ny * f].reshape(nx, f, ny, f).mean(axis=(1, 3)) >= 0.5
    rects = []
    for a in range(nx):
        col = coarse[a]
        b = 0
        while b < ny:
            if col[b]:
                e = b
                while e < ny and col[e]:
                    e += 1
                rects.append(box(a * step, b * step, (a + 1) * step, e * step))
                b = e
            else:
                b += 1
    if not rects:
        return []
    poly = unary_union(rects).simplify(0.5)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return list(poly.exterior.coords)


def _merge_bays(bays):
    """Adjacent bays of one rack row -> one box (fewer scene objects)."""
    rows = {}
    for x0, x1, y0, y1 in bays:
        rows.setdefault((round(x0, 2), round(x1, 2)), []).append([y0, y1])
    out = []
    for (x0, x1), spans in rows.items():
        spans.sort()
        cur = list(spans[0])
        for y0, y1 in spans[1:]:
            if y0 - cur[1] <= 0.05:
                cur[1] = max(cur[1], y1)
            else:
                out.append((x0, x1, cur[0], cur[1]))
                cur = [y0, y1]
        out.append((x0, x1, cur[0], cur[1]))
    return out


def _keys(veh, nodes, tf, floor_z):
    """Trace (t, node, state) -> Showcase key-frames [t, x, z, yaw, state]. Adds an arrival key when a vehicle waited
    at a node (the trace only stamps departures), and drops redundant mid-points of straight constant-speed runs."""
    from proinspect.sim.traffic import TURN_S
    tr = veh["keys"]
    if not tr:
        return []
    hop_e, hop_l = veh.get("hop_s") or [1.0, 1.0]
    raw = [list(tr[0])]
    for prev, cur in zip(tr[:-1], tr[1:]):
        t0, n0, s0 = prev
        t1, n1, s1 = cur
        if n1 != n0:
            arrive = t0 + (hop_l if s0 == "loaded" else hop_e)
            if t1 - arrive > TURN_S + 0.5:                     # waited at n1 before departing: arrive first
                raw.append([round(arrive, 2), n1, s0])
        raw.append(list(cur))
    xy = np.array([nodes[n] for _, n, _ in raw])
    X, _, Z = sim_to_showcase(xy[:, 0], xy[:, 1], tf, floor_z)
    yaw, out = 0.0, []
    for i, (t, n, s) in enumerate(raw):
        nxt = next((j for j in range(i + 1, min(i + 4, len(raw))) if raw[j][1] != n), None)
        if nxt is not None:
            yaw = heading_yaw(X[nxt] - X[i], Z[nxt] - Z[i])
        elif i and raw[i - 1][1] != n:
            yaw = heading_yaw(X[i] - X[i - 1], Z[i] - Z[i - 1])
        out.append([round(float(t), 2), round(float(X[i]), 2), round(float(Z[i]), 2), round(yaw, 3), s])
    keep = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = keep[-1], out[i], out[i + 1]
        if b[4] == a[4] == c[4] and b[3] == c[3] and c[0] > a[0]:
            f = (b[0] - a[0]) / (c[0] - a[0])
            if abs(a[1] + f * (c[1] - a[1]) - b[1]) < 0.02 and abs(a[2] + f * (c[2] - a[2]) - b[2]) < 0.02:
                continue
        keep.append(b)
    if len(out) > 1:
        keep.append(out[-1])
    return keep


def scene(site, summary, *, run_id=None, property_id=None, model_id=None, floor_z=0.0, extra_assumptions=()):
    """Build the Matterport scene for one representative replication (summary['example']['trace'])."""
    tf = site.transform
    if not {"crop", "shape0", "origin", "theta", "grid"} <= set(tf):
        raise ValueError("site has no scan transform (synthetic site?): cannot place it in a Matterport model")
    ex = summary["example"]
    tr = ex["trace"]
    nodes = tr["nodes"]
    _, floor_y, _ = model_to_showcase(0.0, 0.0, floor_z)
    byaw = round(box_yaw(tf), 4)

    def pt(u, v, h=0.0):
        x, _, z = sim_to_showcase(u, v, tf, floor_z, h)
        return round(float(x), 2), round(float(z), 2)

    outline = [list(pt(u, v)) for u, v in _outline(site)]
    rack_h = round(tr.get("levels", 1) * PALLET_LEVEL_M, 2)
    racks = []
    for x0, x1, y0, y1 in _merge_bays(tr.get("bays", [])):
        x, z = pt((x0 + x1) / 2, (y0 + y1) / 2)
        racks.append({"x": x, "z": z, "w": round(x1 - x0, 2), "d": round(y1 - y0, 2), "h": rack_h, "yaw": byaw})
    n_lanes = len(tr.get("lanes", []))
    lanes = []
    for i, ln in enumerate(tr.get("lanes", [])):
        x, z = pt((ln["x0"] + ln["x1"]) / 2, (ln["y0"] + ln["y1"]) / 2)
        lanes.append({"x": x, "z": z, "w": round(ln["x1"] - ln["x0"], 2), "d": round(ln["y1"] - ln["y0"], 2),
                      "yaw": byaw, "door": ln["door"], "inbound": (i % 2 == 0) if n_lanes > 1 else None,
                      "capacity": ln.get("capacity")})
    out_dx, out_dz = sim_dir_to_showcase(0.0, -1.0, tf)            # out of the building through the dock wall
    door_yaw = round(heading_yaw(out_dx, out_dz), 3)
    doors = []
    for d in site.doors:
        x, z = pt(d.x, site.wall_y)
        w = getattr(d, "width_m", None)
        doors.append({"x": x, "z": z, "yaw": door_yaw, "w": round(w, 2) if w else DOOR_W_DEFAULT,
                      "w_source": "vexcel" if w else "assumed", "kind": d.kind, "source": d.source})

    waits = {int(k): v for k, v in (tr.get("node_wait_s") or {}).items()}
    cells = []
    if waits:
        scale = float(np.percentile(list(waits.values()), 95)) or max(waits.values())
        for n, w in sorted(waits.items()):
            x, z = pt(*nodes[n])
            cells.append([x, z, round(min(1.0, w / scale), 3)])
    heat = {"cell": 1.25, "basis": "seconds vehicles waited to enter each 1.25 m lane-graph zone in this replication, "
                                   "divided by the P95 node (clipped to 1); nodes with no waiting are omitted",
            "cells": cells, "max_wait_s": round(max(waits.values()), 1) if waits else 0.0}

    eq = equipment()
    vehicles, t_end = [], 0.0
    for v in tr["vehicles"]:
        spec = eq.get(v["kind"], {})
        dft = spec.get("dims_ft", {})
        keys = _keys(v, nodes, tf, floor_z)
        busy = [k[0] for k in keys if k[4] != "idle"]
        if busy:
            t_end = max(t_end, busy[-1])
        vehicles.append({"id": v["id"], "kind": v["kind"], "glb": v["kind"],
                         "dims": {"l": round(dft.get("length", 8) * FT, 2), "w": round(dft.get("width", 4) * FT, 2),
                                  "h": round((dft.get("height_mast_lowered") or dft.get("height") or 7) * FT, 2)},
                         "keys": keys})
    lane_door = tr.get("door_of_lane") or list(range(n_lanes))
    trucks = []
    for t in ex.get("trucks", []):
        trucks.append({"id": t["id"], "kind": t["kind"], "inbound": t["inbound"], "pallets": t.get("pallets"),
                       "door": lane_door[t["door"]] if t.get("door") is not None and t["door"] < len(lane_door) else None,
                       "arrive_s": round(t["arrived_at"], 1) if "arrived_at" in t else None,
                       "dock_s": round(t["docked"], 1) if "docked" in t else None,
                       "depart_s": round(t["depart"], 1) if "depart" in t else None})
        if "depart" in t:
            t_end = max(t_end, t["depart"])
    return {
        "run_id": run_id, "property_id": property_id, "model_id": model_id or tf.get("model_id"),
        "profile": summary.get("profile"), "floor_y": round(float(floor_y), 3),
        "frame": {"axes": SHOWCASE_AXES, "yaw": YAW_NOTE, "units": "metres", "seed": ex.get("seed"),
                  "replication": "first of the replications (the KPI bands cover all of them)"},
        "scanned_area": outline, "racks": racks, "lanes": lanes, "doors": doors, "heat": heat,
        "vehicles": vehicles, "trucks": trucks, "duration_s": round(t_end + 60.0, 1), "kpi": summary.get("kpi"),
        "assumptions": list(summary.get("assumptions", [])) + list(extra_assumptions) + [
            SHOWCASE_AXES,
            f"door widths without a Vexcel measurement drawn at {DOOR_W_DEFAULT} m (9 ft dock door)",
            f"rack boxes drawn {rack_h} m tall ({tr.get('levels')} pallet levels x 5.5 ft)",
            "floor height = the scan's main floor plane (interior twin floor.z0)",
            "vehicle motion is linear between lane-graph zones (1.25 m); turns take 2 s"],
    }
