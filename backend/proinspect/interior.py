"""Interior planner: what goes where on the REAL scanned floor, decided by the buyer's operation, and what the indoor
equipment (forklifts, reach trucks, order pickers) can do in it.

    plan(pid, operation="pallet_3pl", machine=None, keep=None, orientation="perpendicular", zone_types=None, edits=None)
    summary(pid, operation=None)

1. Scanned contents become objects (ids, boxes, kind guess). By default today's low contents (pallets, clutter) are
   cleared and full-height interior structures (rack runs, room walls) are kept; `keep` lists the ids to keep.
2. The operation picks a recipe (library/layouts.yaml): zones across the floor (depth bands from the dock wall) of
   type selective | dense | shelving | open, and the machine. Every choice carries a plain "why".
3. Each storage zone is filled with sim.layout.generate restricted to the zone (place_mask); aisles may run anywhere.
4. Forklift proofs on the resulting floor: reachable share of positions (machine width + clearance from the dock
   apron), trip distance dock -> bay (grid Dijkstra), minutes per pallet, pallets/hour, machines needed for the
   operation's daily volume; one example trip exported as an animated vehicle for the Matterport view.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import yaml
from scipy import ndimage

from proinspect.config import BUILD
from proinspect.library import equipment as equipment_lib
from proinspect.library import profiles as profiles_lib

OUT = BUILD / "interior_plans"
FT = 0.3048
SQFT = 10.7639
_PKG = Path(__file__).resolve().parent
RECIPES = yaml.safe_load((_PKG / "library" / "layouts.yaml").read_text())["operations"]
ZONE_TYPES = ("selective", "dense", "shelving", "cantilever", "open")
LOAD_TYPES = ("selective", "dense", "cantilever")    # zones a forklift loads (reachability / trips)
HANDLING_S = 20.0                 # pick-up or set-down at a rack face / door [U: typical]
UTILISATION = 0.8
COVERAGE_OK = 30.0                # below this share of the building scanned, whole-building estimates are flagged


def _code():
    h = hashlib.sha256()
    for p in (_PKG / "interior.py", _PKG / "racks.py", _PKG / "sim" / "layout.py", _PKG / "library" / "layouts.yaml",
              _PKG / "doors.py"):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


# ------------------------------------------------------------------ scanned contents as objects
def _contents(site, transposed, model_id):
    """Masks in the generator frame: low contents (removed from site.free by the structure grid) and full-height
    interior structures that are neither walls (touching the floor edge) nor columns."""
    base = site._base if transposed else site
    tf = base.transform
    g = np.load(BUILD / "twins" / "interior" / f"{model_id}.npz")
    a = np.rot90(g["free"].astype(bool), tf["rot90"])
    c0, c1 = tf["crop"]
    scanned_free = a[c0:c0 + base.nx, c1:c1 + base.ny] & base.floor
    low = base.free & ~scanned_free                                 # today's contents (0.1-2 m), cleared by default
    interior = ndimage.binary_fill_holes(base.floor)
    struct = interior & ~base.free
    edge_zone = ndimage.binary_dilation(~interior, iterations=6)    # within 1.5 m of the outer wall: walls
    if transposed:
        low, struct, edge_zone = low.T, struct.T, edge_zone.T
    return low, struct, edge_zone


def objects(site, transposed, model_id):
    from proinspect.sim.site import CELL
    low, struct, edge_zone = _contents(site, transposed, model_id)
    out, masks = [], {}
    for kind_src, m in (("low", low), ("full", struct)):
        lab, n = ndimage.label(m)
        for i, sl in enumerate(ndimage.find_objects(lab) or [], 1):
            if sl is None:
                continue
            mk = lab[sl] == i
            area = float(mk.sum()) * CELL ** 2
            if area < (1.0 if kind_src == "low" else 2.0):
                continue
            comp = lab == i
            if kind_src == "full" and (comp & edge_zone).any():
                continue                                            # an outer wall / pilaster line
            w, h = (sl[0].stop - sl[0].start) * CELL, (sl[1].stop - sl[1].start) * CELL
            long_, short = max(w, h), min(w, h)
            kind = ("rack_run" if long_ >= 6 and long_ / max(short, 0.1) >= 4 else
                    "room" if kind_src == "full" and area >= 25 else "stack" if kind_src == "low" else "structure")
            oid = f"o{len(out) + 1}"
            masks[oid] = comp
            out.append({"id": oid, "kind": kind, "height": kind_src, "area_sqft": round(area * SQFT),
                        "x0": round(sl[0].start * CELL, 2), "x1": round(sl[0].stop * CELL, 2),
                        "y0": round(sl[1].start * CELL, 2), "y1": round(sl[1].stop * CELL, 2),
                        "default_keep": kind_src == "full"})
    return out, masks


# ------------------------------------------------------------------ pallet -> rack spec
PALLET_PRESETS = {                     # width x depth (in): depth is the side that goes INTO the rack
    "gma_48x40": {"name": "GMA 48 x 40 in (US standard)", "width_in": 40, "depth_in": 48},
    "42x42": {"name": "42 x 42 in (paint, drums)", "width_in": 42, "depth_in": 42},
    "48x48": {"name": "48 x 48 in (drums, bulk)", "width_in": 48, "depth_in": 48},
    "euro_1200x800": {"name": "Euro 1200 x 800 mm", "width_in": 31.5, "depth_in": 47.2},
}
DEFAULT_PALLET = {"preset": "gma_48x40", "width_in": 40, "depth_in": 48, "load_in": 54}
OVERHANG_IN = 3.0          # a pallet overhangs the frame 3 in front and back
BAY_CLEAR_IN = 19.0 / 3    # clearance between pallets / uprights in a bay (99 in bay for two 40 in pallets)
LIFT_BEAM_IN = 12.0        # lift-off + beam depth added to the load height per level (54 in load -> 5.5 ft)


def pallet_spec(pallet):
    """The buyer's pallet (or the US default) -> rack geometry. Everything here is a standard-practice ASSUMPTION
    (or the buyer's own input), never a measurement."""
    p = {**DEFAULT_PALLET, **{k: v for k, v in (pallet or {}).items() if v not in (None, "")}}
    if p.get("preset") in PALLET_PRESETS and not any(k in (pallet or {}) for k in ("width_in", "depth_in")):
        p.update({k: PALLET_PRESETS[p["preset"]][k] for k in ("width_in", "depth_in")})
    w, d, h = (float(p[k]) for k in ("width_in", "depth_in", "load_in"))
    if not (24 <= w <= 60 and 24 <= d <= 60 and 12 <= h <= 96):
        raise ValueError("pallet must be 24-60 in wide and deep, with a load 12-96 in tall")
    per_bay = 3 if w <= 32 else 2
    bay_in = per_bay * w + (per_bay + 1) * BAY_CLEAR_IN
    custom = pallet is not None and (p.get("preset") not in PALLET_PRESETS or
                                     float(pallet.get("width_in", w)) != PALLET_PRESETS.get(p.get("preset"), {}).get("width_in", w))
    return {"preset": p.get("preset") if p.get("preset") in PALLET_PRESETS else "custom",
            "name": PALLET_PRESETS.get(p.get("preset"), {}).get("name", f"Custom {w:g} x {d:g} in"),
            "width_in": w, "depth_in": d, "load_in": h, "per_bay": per_bay,
            "frame_depth_in": round(d - 2 * OVERHANG_IN, 1), "bay_in": round(bay_in, 1),
            "level_in": round(h + LIFT_BEAM_IN, 1),
            "source": "yours" if pallet else "assumed",
            "is_default": pallet is None or (p.get("preset") == "gma_48x40" and w == 40 and d == 48 and h == 54),
            "custom": bool(custom)}


# ------------------------------------------------------------------ zones
def _zone_params(ztype, machine, eq):
    if ztype == "dense":
        return {"rack_d": 2 * 42 * 0.0254, "deep": 2}, "double-deep pallet rack (2 pallets deep each side)"
    if ztype == "cantilever":
        return ({"rack_d": 4 * FT, "bay": 12 * FT, "level_ft": 3.0, "aisle_m": 13 * FT, "lanes_on": False},
                "cantilever rack for lumber, pipe and sheet goods: 4 ft arms, no front beams, 12 ft bays, a 3 ft "
                "bundle per arm level")
    if ztype == "shelving":
        return ({"rack_d": 24 * 0.0254, "bay": 4 * FT, "level_ft": 1.75, "max_levels": 4, "aisle_m": 4 * FT,
                 "lanes_on": False}, "7 ft hand-pick shelving, 4 ft aisles (pick faces, served by carts)")
    return {}, "selective pallet rack: every pallet directly reachable"


def _zones(recipe, zone_types):
    zs = []
    for i, z in enumerate(recipe["zones"]):
        t = (zone_types or {}).get(f"z{i + 1}", z["type"])
        zs.append({"id": f"z{i + 1}", "type": t if t in ZONE_TYPES else z["type"], "from": z["from"], "to": z["to"],
                   "why": z.get("why", "")})
    return zs


# ------------------------------------------------------------------ forklift proofs
def _grid_graph(free2):
    from scipy.sparse import coo_matrix
    n0, n1 = free2.shape
    idx = -np.ones(free2.shape, int)
    rs, cs = np.nonzero(free2)
    idx[rs, cs] = np.arange(len(rs))
    rows, cols, w = [], [], []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        r2, c2 = rs + dr, cs + dc
        ok = (r2 >= 0) & (r2 < n0) & (c2 >= 0) & (c2 < n1)
        a, b = idx[rs[ok], cs[ok]], idx[r2[ok], c2[ok]]
        sel = b >= 0
        L = math.hypot(dr, dc)
        rows += [a[sel], b[sel]]
        cols += [b[sel], a[sel]]
        w += [np.full(sel.sum() * 2, L)]
    N = len(rs)
    G = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
    return G, idx, rs, cs


def _proofs(site, rows, racks_mask, kept_mask, machine, eq, levels, level_ft, op, rba, transposed):
    """Reachability, trip distances, cycle time, pallets/hour and fleet size for `machine` on this floor."""
    levels = max(levels, 1)
    from scipy.sparse.csgraph import dijkstra

    from proinspect.sim.layout import APRON
    from proinspect.sim.site import CELL
    spec = eq[machine]
    half = (spec["dims_ft"]["width"] * FT) / 2 + 0.15
    floor = ndimage.binary_fill_holes(site.floor)
    trav = site.free & floor & ~racks_mask & ~kept_mask
    trav = ndimage.binary_erosion(trav, iterations=max(1, int(math.ceil(half / CELL))))   # machine body fits
    k = 2                                                                                  # 0.5 m graph
    n0, n1 = trav.shape[0] // k, trav.shape[1] // k
    free2 = trav[:n0 * k, :n1 * k].reshape(n0, k, n1, k).any(axis=(1, 3))
    G, idx, rs, cs = _grid_graph(free2)
    step = CELL * k
    if transposed:
        seeds_xy = [(APRON / 2 + 0.5, site.ny * CELL / 2)]
    else:
        seeds_xy = [(d.x, site.wall_y + APRON / 2) for d in site.doors] or [(site.nx * CELL / 2, site.wall_y + APRON / 2)]

    def node(x, y, r=4.0):
        i0, j0 = int(x / step), int(y / step)
        best, bd = None, 1e9
        R = int(r / step)
        for i in range(max(0, i0 - R), min(n0, i0 + R + 1)):
            for j in range(max(0, j0 - R), min(n1, j0 + R + 1)):
                if idx[i, j] >= 0:
                    dd = (i - i0) ** 2 + (j - j0) ** 2
                    if dd < bd:
                        best, bd = idx[i, j], dd
        return best
    seeds = [s for s in (node(x, y) for x, y in seeds_xy) if s is not None]
    if not seeds and len(rs) and not site.doors:
        # no dock door in the scanned area: machines start from the largest open floor area (where they'd come in)
        lab, nlab = ndimage.label(free2)
        if nlab:
            big = 1 + int(np.argmax(ndimage.sum(free2, lab, range(1, nlab + 1))))
            ci, cj = ndimage.center_of_mass(lab == big)
            seeds = [s for s in [node((ci + 0.5) * step, (cj + 0.5) * step, r=15.0)] if s is not None]
    if not seeds or not len(rs):
        return {"reachable_pct": 0.0, "why": "no drivable floor from the dock apron for this machine"}, None
    dist, pred, src = dijkstra(G, indices=seeds, min_only=True, return_predecessors=True)
    dist = dist * step
    reach_slots = total = 0
    trip = []
    for r in rows:
        if not r.get("slots_kept"):
            continue
        total += r["slots_kept"]
        n = node(r["aisle_x"], (r["y0"] + r["y1"]) / 2, r=2.5)
        d = float(dist[n]) if n is not None and np.isfinite(dist[n]) else None
        r["reachable"] = d is not None
        r["trip_m"] = round(d, 1) if d is not None else None
        if d is not None:
            reach_slots += r["slots_kept"]
            trip.append((d, r["slots_kept"], r, n))
    lift_ok = spec.get("lift_height_ft", 0) >= (levels - 1) * level_ft
    v_empty, v_loaded = [s * 0.44704 * 0.8 for s in spec.get("speed_mph", [5, 5])]
    lift_fpm = spec.get("lift_fpm") or 60
    if trip:
        w = np.array([t[1] for t in trip], float)
        dd = np.array([t[0] for t in trip])
        d_avg = float((dd * w).sum() / w.sum())
        h_avg_ft = (levels - 1) / 2 * level_ft
        lift_s = 2 * h_avg_ft / lift_fpm * 60
        cycle = d_avg / v_loaded + d_avg / v_empty + lift_s + 2 * HANDLING_S
        pph = 3600 / cycle
    else:
        d_avg = cycle = pph = None
    prof = profiles_lib().get(op) or {}
    units = prof.get("units") or {}
    scale = (float(rba) / prof["ref_sqft"]) if rba and prof.get("ref_sqft") else 1.0
    moves = (units.get("inbound_pallets") or 0) + (units.get("outbound_pallets") or units.get("inbound_pallets") or 0)
    moves = int(round(moves * scale))
    shift_h = prof.get("shift_hours") or 8
    fleet = math.ceil(moves / (shift_h * pph * UTILISATION)) if pph and moves else None
    # example trip: the bay at the median distance, there loaded and back empty, mast up and down at the bay
    example = None
    if trip:
        trip.sort(key=lambda t: t[0])
        d, _, r, n = trip[len(trip) // 2]
        path = [n]
        while pred[path[-1]] >= 0:
            path.append(pred[path[-1]])
        path = path[::-1]
        pts = [((rs[p] + 0.5) * step, (cs[p] + 0.5) * step) for p in path]
        example = {"row": r["id"], "trip_m": round(d, 1), "points": pts[::2] + [pts[-1]],
                   "lift_ft": round((levels - 1) * level_ft, 1)}
    proofs = {
        "machine": machine, "machine_name": spec.get("name"),
        "reachable_pct": round(100 * reach_slots / total, 1) if total else 0.0,
        "unreachable_rows": [r["id"] for r in rows if r.get("slots_kept") and not r.get("reachable")],
        "lift_ok": lift_ok,
        "lift_note": (f"{spec.get('name')} lifts {spec.get('lift_height_ft')} ft; top beam at "
                      f"{(levels - 1) * level_ft:.1f} ft" + ("" if lift_ok else " (too high: levels capped)")),
        "avg_trip_m": round(d_avg, 1) if d_avg else None,
        "minutes_per_pallet": round(cycle / 60, 2) if cycle else None,
        "pallets_per_hour": round(pph, 1) if pph else None,
        "daily_pallet_moves": moves, "shift_hours": shift_h,
        "machines_needed": fleet,
        "basis": (f"cycle = average dock-to-bay trip {d_avg:.0f} m loaded at {v_loaded:.1f} m/s + back empty at "
                  f"{v_empty:.1f} m/s + lift to the average level and down at {lift_fpm} ft/min + 2 x {HANDLING_S:.0f} s "
                  f"handling; machines = {moves} pallet moves per {shift_h} h shift (profile volume scaled to the "
                  f"building) / (pallets per hour x {UTILISATION:.0%} utilisation)") if d_avg else None,
    }
    return proofs, example


# ------------------------------------------------------------------ dimensions + feasibility
def _v(value, unit, source, basis=None):
    """One dimension with its provenance: measured (scan / CoStar record), assumed (standard spec or library), yours
    (the buyer's input) or calculated (derived from the others)."""
    return {"value": value, "unit": unit, "source": source, **({"basis": basis} if basis else {})}


def _dims(site, w, pal, levels, level_ft, spr_ft, spr_note, eq, machine, aisle_m, interior_js, proofs, positions,
          zones):
    psrc = "yours" if pal["source"] == "yours" else "assumed"
    clear = round(float(site.clear_height_ft), 1)
    usable = round(clear - spr_ft, 1)
    load_ft = pal["load_in"] / 12
    beams = [round(i * level_ft, 1) for i in range(max(levels, 0))]
    top_beam = beams[-1] if beams else None
    top_load = round(top_beam + load_ft, 1) if top_beam is not None else None
    spec = eq[machine]
    md = spec.get("dims_ft") or {}
    dims = {
        "pallet": {"name": pal["name"], "preset": pal["preset"],
                   "width": _v(pal["width_in"], "in", psrc, "the pallet's face along the rack beam"),
                   "depth": _v(pal["depth_in"], "in", psrc, "the side that goes into the rack"),
                   "load_height": _v(pal["load_in"], "in", psrc, "pallet + goods"),
                   "note": "US GMA standard pallet: an assumption until you set yours" if psrc == "assumed" else
                           "your pallet"},
        "rack": {"frame_depth": _v(pal["frame_depth_in"], "in", "calculated", f"pallet depth - {2 * OVERHANG_IN:g} in overhang"),
                 "bay_width": _v(round(pal["bay_in"] / 12, 2), "ft", "calculated",
                                 f"{pal['per_bay']} pallets + clearances"),
                 "pallets_per_bay": _v(pal["per_bay"], "", "calculated"),
                 "level_pitch": _v(round(level_ft, 2), "ft", "calculated",
                                   f"load {pal['load_in']:g} in + {LIFT_BEAM_IN:g} in lift-off and beam"),
                 "levels": _v(levels, "", "calculated"),
                 "beam_heights_ft": beams,
                 "top_beam": _v(top_beam, "ft", "calculated"),
                 "top_load": _v(top_load, "ft", "calculated", "top beam + load height")},
        "building": {"clear_height": _v(clear, "ft", "measured", "lowest overhead obstruction (P5) in the Matterport scan"),
                     "roof_deck": _v(interior_js.get("deck_ft"), "ft", "measured", "roof deck height in the Matterport scan"),
                     "sprinkler_clearance": _v(spr_ft, "ft", "assumed", spr_note),
                     "usable_height": _v(usable, "ft", "calculated", "clear height - sprinkler clearance"),
                     "column_bay": _v((interior_js.get("columns") or {}).get("bays", {}).get("x", {}) and
                                      (interior_js["columns"]["bays"]["x"] or {}).get("spacing_ft"), "ft", "measured",
                                      "column spacing fitted in the scan") if isinstance(interior_js.get("columns"), dict) else None},
        "aisle": _v(round(aisle_m / FT, 1), "ft", "assumed", f"minimum aisle for the {spec.get('name')} (library spec)"),
        "machine": {"name": spec.get("name"), "basis": spec.get("basis"),
                    "length": _v(md.get("length"), "ft", "assumed"), "width": _v(md.get("width"), "ft", "assumed"),
                    "height": _v(md.get("height_mast_lowered") or md.get("height"), "ft", "assumed", "mast lowered"),
                    "lift_height": _v(spec.get("lift_height_ft"), "ft", "assumed"),
                    "capacity": _v(spec.get("capacity_lb"), "lb", "assumed")},
        "lane": {"width": _v(8.0, "ft", "assumed", "one pallet-wide staging lane per dock door"),
                 "pallet_depth": _v(pal["depth_in"], "in", psrc)},
    }
    lift = spec.get("lift_height_ft") or 0
    feas = [
        {"id": "levels", "label": "At least one storage level fits",
         "ok": levels >= 1,
         "detail": (f"{levels} levels: the top load reaches {top_load} ft under a {usable} ft usable height"
                    if levels >= 1 else f"a {pal['load_in']:g} in load does not fit under the {usable} ft usable height")},
        {"id": "sprinkler", "label": "Top load stays below the sprinkler clearance",
         "ok": top_load is not None and top_load <= usable + 1e-6,
         "detail": f"top load {top_load} ft vs {usable} ft ({clear} ft clear - {spr_ft:g} ft clearance)" if top_load else "no levels"},
        {"id": "lift", "label": f"{spec.get('name')} reaches the top beam",
         "ok": top_beam is not None and lift >= top_beam,
         "detail": f"lifts {lift} ft; top beam {top_beam} ft" if top_beam is not None else "no levels"},
        {"id": "frame", "label": "Pallet depth suits a standard rack frame",
         "ok": 36 <= pal["depth_in"] <= 54,
         "detail": f"{pal['depth_in']:g} in pallet on a {pal['frame_depth_in']:g} in frame "
                   f"({OVERHANG_IN:g} in overhang each side)" + ("" if 36 <= pal["depth_in"] <= 54 else
                                                                  ": outside the usual 36-54 in, needs special frames")},
        {"id": "reach", "label": "Every rack face is reachable",
         "ok": (proofs.get("reachable_pct") or 0) >= 99.5,
         "detail": f"{proofs.get('reachable_pct', 0):g}% of positions reachable by the {spec.get('name')}"},
        {"id": "capacity", "label": "The layout stores pallets", "ok": positions > 0,
         "detail": f"{positions:,} pallet positions on the scanned floor"},
    ]
    return dims, feas


# ------------------------------------------------------------------ main
def plan(pid, operation="pallet_3pl", machine=None, keep=None, orientation="perpendicular", zone_types=None,
         edits=None, use_cache=True, pallet=None):
    from proinspect import racks
    from proinspect.sim.layout import PALLET_LEVEL_FT, generate
    from proinspect.sim.site import CELL
    if operation not in RECIPES:
        raise ValueError(f"operation must be one of {sorted(RECIPES)}")
    recipe = RECIPES[operation]
    eq = equipment_lib()
    machine = machine or recipe["machine"]
    if machine not in eq or not eq[machine].get("min_aisle_ft"):
        raise ValueError(f"unknown machine {machine}")
    w = racks._catalog(pid)
    model_id = racks._scan(w)
    pal = pallet_spec(pallet)
    key = hashlib.sha256(json.dumps([pid, model_id, operation, machine, sorted(keep) if keep is not None else None,
                                     orientation, zone_types or {}, edits or {}, _code(),
                                     {k: pal[k] for k in ("width_in", "depth_in", "load_in", "source")}], sort_keys=True)
                         .encode()).hexdigest()[:16]
    p = OUT / f"{key}.json"
    if use_cache and p.exists():
        return json.loads(p.read_text())

    site, transposed = racks._site(model_id, pid, orientation)
    objs, masks = objects(site, transposed, model_id)
    keep_ids = set(keep) if keep is not None else {o["id"] for o in objs if o["default_keep"]}
    kept_mask = np.zeros_like(site.floor)
    for o in objs:
        o["kept"] = o["id"] in keep_ids
        m = masks[o["id"]]
        if o["kept"] and o["height"] == "low":
            kept_mask |= m                                          # stays: an obstacle for racks and machines
        if not o["kept"] and o["height"] == "full":
            site.free = site.free | (m & site.floor)                # cleared: free floor
    site.free = site.free & ~kept_mask

    spr_ft, spr_note = racks._sprinkler(w)
    zones = _zones(recipe, zone_types)
    W, H = site.nx * CELL, site.ny * CELL
    rows, lanes, lay0 = [], [], None
    levels_by_zone = {}
    yy = (np.arange(site.ny) + 0.5) * CELL
    xx = (np.arange(site.nx) + 0.5) * CELL
    # zones are depth bands of the floor BEHIND the dock zone (apron + staging lanes + main aisle)
    probe = generate(site, {machine: 1}, sprinkler_ft=spr_ft, dock_zone=(not transposed and bool(site.doors)))
    if transposed:
        from proinspect.sim.layout import APRON, LANE_D, MAIN_AISLE
        d0 = APRON + min(LANE_D, 0.3 * W) + MAIN_AISLE
        span = (d0, W)
    else:
        span = (probe.dock_zone_end, H)
    no_dock_zone = not site.doors   # the scanned floor has no dock doors: nothing to keep clear in front of them
    lanes = probe.lanes

    from proinspect.sim.layout import MAIN_AISLE
    storage_after = {z["id"]: any(o["type"] in LOAD_TYPES for o in zones[i + 1:]) for i, z in enumerate(zones)}

    def band_of(i, z):
        a = span[0] + z["from"] * (span[1] - span[0])
        b = span[0] + z["to"] * (span[1] - span[0]) + (1.0 if z["to"] >= 1 else 0.0)
        if i > 0:
            a += MAIN_AISLE                     # a cross aisle between zones, so machines can turn into every aisle
        if transposed:
            m = (xx[:, None] >= a) & (xx[:, None] < b) & np.ones((1, site.ny), bool)
            mid = site.ny * CELL / 2
            main = (yy[None, :] >= mid - MAIN_AISLE / 2) & (yy[None, :] < mid + MAIN_AISLE / 2) & np.ones((site.nx, 1), bool)
        else:
            m = (yy[None, :] >= a) & (yy[None, :] < b) & np.ones((site.nx, 1), bool)
            mid = site.nx * CELL / 2
            main = (xx[:, None] >= mid - MAIN_AISLE / 2) & (xx[:, None] < mid + MAIN_AISLE / 2) & np.ones((1, site.ny), bool)
        z["rect"] = ([round(a, 2), round(b, 2), 0.0, round(site.ny * CELL, 2)] if transposed
                     else [0.0, round(site.nx * CELL, 2), round(a, 2), round(b, 2)])     # x0, x1, y0, y1
        if z["type"] in ("shelving", "open") and storage_after[z["id"]]:
            m = m & ~main                       # a main forklift aisle through the pick / open area to the racks behind
            z["main_aisle"] = True
        return m
    for i, z in enumerate(zones):
        band = band_of(i, z)
        z["area_sqft"] = round(float((band & site.floor).sum()) * CELL ** 2 * SQFT)
        if z["type"] == "open":
            z["positions"] = 0
            continue
        prm, z["what"] = _zone_params(z["type"], machine, eq)
        per_bay = 2
        if z["type"] in ("selective", "dense"):              # pallet racks follow the buyer's pallet
            per_bay = pal["per_bay"]
            prm = {**prm, "rack_d": pal["frame_depth_in"] * 0.0254 * prm.get("deep", 1),
                   "bay": pal["bay_in"] * 0.0254, "level_ft": pal["level_in"] / 12, "load_ft": pal["load_in"] / 12}
        fleet = {} if z["type"] == "shelving" else {machine: 1}       # shelving is worked by people with carts
        lay = generate(site, fleet, sprinkler_ft=spr_ft, dock_zone=(not transposed and bool(site.doors)), place_mask=band, **prm)
        lay0 = lay0 or lay
        lv = lay.levels
        levels_by_zone[z["id"]] = (lv, prm.get("level_ft", PALLET_LEVEL_FT))
        z["levels"] = lv
        zr = racks._rows(lay.bays, lv)
        for r in zr:
            r["id"] = f"{z['id']}-{r['id']}"
            r["zone"] = z["id"]
            r["type"] = z["type"]
            r["levels"] = lv
            r["deep"] = prm.get("deep", 1)
            r["slots"] = r["bays"] * per_bay * lv * prm.get("deep", 1)
            r["per_bay"] = per_bay
            r["level_ft"] = round(prm.get("level_ft", PALLET_LEVEL_FT), 3)
            b0 = next((b for b in lay.bays if abs(b["x0"] - r["x0"]) < 0.02 and r["y0"] - 0.05 <= b["y0"] <= r["y1"]), None)
            r["aisle_x"] = b0["aisle_x"] if b0 else (r["x0"] + r["x1"]) / 2
        z["unreachable_bays_dropped"] = lay.params.get("bays_dropped_unreachable", 0)
        rows += zr
    aisle_m = lay0.aisle_m if lay0 else eq[machine]["min_aisle_ft"] * FT
    rows = racks._apply_edits(rows, edits, site, aisle_m)
    for z in zones:
        z["positions"] = sum(r["slots_kept"] for r in rows if r.get("zone") == z["id"])
    racks_mask = np.zeros_like(site.floor)
    for r in rows:
        if r["slots_kept"]:
            i0, i1 = int(r["x0"] / CELL), int(math.ceil(r["x1"] / CELL))
            j0, j1 = int(r["y0"] / CELL), int(math.ceil(r["y1"] / CELL))
            racks_mask[max(0, i0):i1, max(0, j0):j1] = True
    pallet_rows = [r for r in rows if r.get("type") in LOAD_TYPES]
    load_z = [zid for zid, z_ in ((z["id"], z) for z in zones) if z_["type"] in ("selective", "dense")] or \
        [z["id"] for z in zones if z["type"] in LOAD_TYPES]
    lv_main, lvl_ft = next((levels_by_zone[zid] for zid in load_z if zid in levels_by_zone),
                           next(iter(levels_by_zone.values()), (1, PALLET_LEVEL_FT)))
    rba = w.get("rba_sqft") or w.get("gba_sqft")
    proofs, example = _proofs(site, pallet_rows or rows, racks_mask, kept_mask, machine, eq, lv_main, lvl_ft,
                              operation, rba, transposed)
    positions = sum(r["slots_kept"] for r in rows if r.get("type") in ("selective", "dense"))
    pick_faces = sum(r["slots_kept"] for r in rows if r.get("type") == "shelving")
    long_goods = sum(r["slots_kept"] for r in rows if r.get("type") == "cantilever")
    scanned_sqft = float(site.floor.sum()) * CELL ** 2 * SQFT
    interior_js = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    office = interior_js.get("office_sqft") or 0
    est = None
    coverage = round(100 * scanned_sqft / float(rba), 1) if rba else None
    if rba and scanned_sqft and positions:
        ratio = max(1.0, (float(rba) - office) / scanned_sqft)
        reliable = coverage is not None and coverage >= COVERAGE_OK
        est = {"value": int(round(positions * ratio, -1)), "ratio": round(ratio, 2), "reliable": reliable,
               "note": (f"the scan covers {coverage:.0f}% of the building; scaling it up assumes the rest looks the same"
                        if reliable else
                        f"the scan covers only {coverage:.0f}% of the building, so a whole-building number would be a "
                        "guess: treat it as unreliable")}

    dims, feas = _dims(site, w, pal, lv_main, lvl_ft, spr_ft, spr_note, eq, machine, aisle_m, interior_js, proofs,
                       positions, zones)
    mpos = None
    if example and len(example["points"]) >= 2:
        (x0_, y0_), (x1_, y1_) = example["points"][0], example["points"][min(2, len(example["points"]) - 1)]
        mpos = {"x": round(x0_, 2), "y": round(y0_, 2), "heading_deg": round(math.degrees(math.atan2(y1_ - y0_, x1_ - x0_)), 1)}
    scene = racks._scene(site, [r for r in rows], lanes, lv_main, model_id, pid, transposed)
    _decorate_scene(scene, site, rows, transposed, model_id, eq, machine, example, proofs)
    scene["removed"] = _removed_outlines(site, transposed, model_id, [o for o in objs if not o["kept"]])
    scene["zones"] = _zone_patches(site, transposed, model_id, zones)
    res = {
        "property_id": pid, "model_id": model_id, "operation": operation,
        "operation_name": (profiles_lib().get(operation) or {}).get("name", operation),
        "why": recipe["why"], "machine": machine, "machine_name": eq[machine].get("name"),
        "orientation": orientation, "zones": zones, "objects": objs,
        "positions": positions, "pick_faces": pick_faces, "long_goods_positions": long_goods,
        "scan_coverage_pct": coverage, "building_estimate": est,
        "levels": lv_main, "levels_basis": f"{site.clear_height_ft:.1f} ft lowest obstruction (scan) - {spr_ft:g} ft sprinkler clearance; {spr_note}",
        "scanned_sqft": round(scanned_sqft), "proofs": proofs, "rows": rows, "transposed": transposed,
        "pallet": pal, "dims": dims, "feasibility": feas, "machine_pos": mpos,
        "lanes": [{k_: round(float(L[k_]), 2) for k_ in ("x0", "x1", "y0", "y1")} for L in lanes],
        "plan": racks._plan_geometry(site, transposed),
        "assumptions": [
            "today's low contents (pallets, clutter) are removed unless kept; full-height structures stay unless removed",
            "the Matterport scan itself cannot be erased: removed items are outlined red on the floor in 3D",
            f"rack geometry from the pallet ({pal['name']}, {pal['load_in']:g} in load): {pal['frame_depth_in']:g} in "
            f"frames, {pal['per_bay']} pallets per {pal['bay_in'] / 12:.2f} ft bay, {pal['level_in'] / 12:.2f} ft per level; "
            "dense = double-deep; shelving = 7 ft, 4 levels, 4 ft aisles",
            f"machine speeds, lift rate and aisle widths are library values ({eq[machine].get('basis', 'typical')})",
            "daily pallet moves come from the operation profile, scaled to the building size: replace with your own",
            *(["no dock doors fall inside the scanned area, so no dock apron or staging lanes are reserved on it"]
              if not site.doors else []),
        ],
    }
    res = json.loads(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else float(o)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}_scene.json").write_text(json.dumps(scene, default=float))
    res["scene_url"] = f"/media/interior_plans/{key}_scene.json"
    res["run_id"] = key
    p.write_text(json.dumps(res))
    return res


def _decorate_scene(scene, site, rows, transposed, model_id, eq, machine, example, proofs):
    """Real-looking racks (per-row bays / levels / depth), removed-object outlines and the example forklift trip."""
    from proinspect import racks
    from proinspect.sim.export import heading_yaw, sim_to_showcase
    from proinspect.sim.layout import PALLET_LEVEL_FT
    base = site._base if transposed else site
    tf = base.transform
    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)

    def pt(u, v):
        bu, bv = racks._sim_xy(site, transposed, u, v)
        x, _, z = sim_to_showcase(bu, bv, tf, floor_z, 0.0)
        return float(x), float(z)
    by_id = {r["id"]: r for r in rows}
    for rk in scene["racks"]:
        r = by_id.get(rk.get("id"))
        if not r:
            continue
        lv_ft = 1.75 if r.get("type") == "shelving" else PALLET_LEVEL_FT
        rk.update(bays=r["bays"], levels=r.get("levels", 1), level_h=round(lv_ft * 0.3048, 3), deep=r.get("deep", 1),
                  type=r.get("type", "selective"), along="d" if not transposed else "w",
                  ok=bool(r.get("reachable", True)), h=round(r.get("levels", 1) * lv_ft * 0.3048, 2))
    if example:
        spec = eq[machine]
        dft = spec["dims_ft"]
        v_loaded, v_empty = [s * 0.44704 * 0.8 for s in spec.get("speed_mph", [5, 5])][::-1]
        P = [pt(u, v) for u, v in example["points"]]
        keys, t = [], 0.0
        for i, (x, z) in enumerate(P):
            if i:
                t += math.hypot(x - P[i - 1][0], z - P[i - 1][1]) / v_loaded
            nxt = P[min(i + 1, len(P) - 1)]
            prv = P[max(i - 1, 0)]
            dx, dz = (nxt[0] - x, nxt[1] - z) if i + 1 < len(P) else (x - prv[0], z - prv[1])
            keys.append([round(t, 2), round(x, 2), round(z, 2), round(heading_yaw(dx, dz), 3), "loaded", 0.0])
        lift_m = example["lift_ft"] * 0.3048
        lift_s = example["lift_ft"] / (spec.get("lift_fpm") or 60) * 60
        x, z, yaw = keys[-1][1], keys[-1][2], keys[-1][3]
        keys += [[round(t + 1, 2), x, z, yaw, "loaded", 0.0], [round(t + 1 + lift_s, 2), x, z, yaw, "loaded", round(lift_m, 2)],
                 [round(t + 3 + lift_s, 2), x, z, yaw, "drop", round(lift_m, 2)],
                 [round(t + 3 + 2 * lift_s, 2), x, z, yaw, "empty", 0.0]]
        t2 = keys[-1][0]
        for i, (x, z) in enumerate(P[::-1][1:], 1):
            px, pz = P[::-1][i - 1]
            t2 += math.hypot(x - px, z - pz) / v_empty
            keys.append([round(t2, 2), round(x, 2), round(z, 2), round(heading_yaw(x - px, z - pz), 3), "empty", 0.0])
        scene["vehicles"] = [{"id": "trip", "kind": machine, "glb": machine,
                              "dims": {"l": round(dft["length"] * FT, 2), "w": round(dft["width"] * FT, 2),
                                       "h": round((dft.get("height_mast_lowered") or 7) * FT, 2)},
                              "label": f"{spec.get('name')}: dock -> {example['row']} ({example['trip_m']} m)",
                              "keys": keys}]
        scene["duration_s"] = keys[-1][0]
    scene["kind"] = "interior_plan"


def _removed_outlines(site, transposed, model_id, objs):
    """Floor outlines (Showcase x, z) of scanned objects the buyer removed: the scan can't be erased, so we mark them."""
    from proinspect import racks
    from proinspect.sim.export import sim_to_showcase
    base = site._base if transposed else site
    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)
    out = []
    for o in objs:
        ring = []
        for u, v in ((o["x0"], o["y0"]), (o["x1"], o["y0"]), (o["x1"], o["y1"]), (o["x0"], o["y1"])):
            bu, bv = racks._sim_xy(site, transposed, u, v)
            x, _, z = sim_to_showcase(bu, bv, base.transform, floor_z, 0.0)
            ring.append([round(float(x), 2), round(float(z), 2)])
        out.append({"id": o["id"], "ring": ring})
    return out


ZONE_NAME = {"open": "Open floor", "shelving": "Pick shelving", "selective": "Selective pallet racks",
             "dense": "Double-deep racks", "cantilever": "Cantilever racks (lumber, pipe)"}


def _zone_patches(site, transposed, model_id, zones):
    """Floor patches (Showcase x, z) for each zone, so open zones are visible in 3D too."""
    from proinspect import racks
    from proinspect.sim.export import sim_to_showcase
    base = site._base if transposed else site
    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)
    out = []
    for z in zones:
        if "rect" not in z:
            continue
        x0, x1, y0, y1 = z["rect"]
        ring = []
        for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            bu, bv = racks._sim_xy(site, transposed, u, v)
            x, _, zz = sim_to_showcase(bu, bv, base.transform, floor_z, 0.0)
            ring.append([round(float(x), 2), round(float(zz), 2)])
        label = ZONE_NAME.get(z["type"], z["type"]) + (f" · {z['area_sqft']:,} SF" if z["type"] == "open" else "")
        out.append({"id": z["id"], "type": z["type"], "ring": ring, "label": label})
    return out


def summary(pid, operation=None):
    try:
        from proinspect import racks
        w = racks._catalog(pid)
        op = operation or "pallet_3pl"
        r = plan(pid, op)
    except FileNotFoundError as e:
        return {"status": "no_scan", "why": str(e)}
    pr = r["proofs"]
    est = r["building_estimate"] or {}
    line = (f"{r['positions']:,} pallet positions in the scanned {r['scanned_sqft']:,} SF ({r['scan_coverage_pct']:.0f}% of the building)"
            f" · {pr['reachable_pct']:.0f}% reachable with a {r['machine_name'].lower()}"
            + (f" · {pr['minutes_per_pallet']} min/pallet" if pr.get("minutes_per_pallet") else "")
            + (f" · {pr['machines_needed']} machine{'s' if pr['machines_needed'] != 1 else ''} for "
               f"{pr['daily_pallet_moves']} pallet moves/shift" if pr.get("machines_needed") else ""))
    return {"status": "computed", "line": line, "operation": op, "positions": r["positions"],
            "building_estimate": est.get("value") if est.get("reliable") else None,
            "building_estimate_unreliable": est.get("value") if est and not est.get("reliable") else None,
            "scan_coverage_pct": r["scan_coverage_pct"], "scanned_sqft": r["scanned_sqft"], "levels": r["levels"],
            "reachable_pct": pr["reachable_pct"], "minutes_per_pallet": pr.get("minutes_per_pallet"),
            "pallets_per_hour": pr.get("pallets_per_hour"), "machines_needed": pr.get("machines_needed"),
            "machine": r["machine_name"], "sqft_per_position": round(r["scanned_sqft"] / r["positions"], 1) if r["positions"] else None}
