"""Storage capacity planner: selective pallet racks laid out on the REAL scanned floor.

    plan(pid, equipment="reach_truck", orientation="perpendicular", edits=None) -> dict
    compare_trucks(pid, orientation=...)                                      -> trade-off strip
    summary(pid)                                                              -> one line for card / compare

Inputs and where they come from:
    floor, columns, walls      Matterport scan (interior twin structure grid: today's contents removed)
    lowest overhead obstruction Matterport scan (clear P5), CoStar clear height shown alongside
    sprinkler type             CoStar (ESFR -> 3 ft deflector clearance, wet/standard -> 1.5 ft, unknown -> 3 ft)
    dock doors                 dock store (Vexcel obliques) mapped into the scan; staging lanes kept clear
    building size / rent       CoStar RBA and listings; office area from the scan
Layout engine: proinspect.sim.layout.generate (column-aware selective racks, aisle = truck's min aisle,
levels = clear and lift limited). Edits: delete / move rack rows, validated against the floor and the aisle width.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy import ndimage

from proinspect.config import BUILD
from proinspect.library import equipment as equipment_lib

OUT = BUILD / "racks"
FT = 0.3048
SQFT = 10.7639
TRUCKS = ("counterbalance_electric", "reach_truck", "vna_turret")
SPRINKLER_CLEARANCE_FT = {"esfr": 3.0, "wet": 1.5, "dry": 1.5, "true": 1.5}
_PKG = Path(__file__).resolve().parent


def _code():
    h = hashlib.sha256()
    for p in (_PKG / "racks.py", _PKG / "sim" / "layout.py", _PKG / "sim" / "site.py", _PKG / "doors.py"):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _catalog(pid):
    from proinspect.api_twin import catalog_record
    w = catalog_record(pid)
    if w is None:
        raise FileNotFoundError(f"warehouse {pid} not in the catalog")
    return w


def _scan(w):
    from proinspect.api_twin import best_scan
    s = best_scan(w)
    if not s or not (BUILD / "twins" / "interior" / f"{s['model_id']}.npz").exists():
        scans = w.get("scans") or []
        rba = w.get("rba_sqft") or w.get("gba_sqft")
        if not scans:
            raise FileNotFoundError("this property has no Matterport scan")
        parts = [f"{x.get('floor_sqft') or 0:,.0f} SF" + (f" ({100 * (x.get('floor_sqft') or 0) / rba:.0f}% of the building)" if rba else "")
                 for x in sorted(scans, key=lambda x: -(x.get("floor_sqft") or 0))[:3]]
        raise FileNotFoundError(
            f"the {len(scans)} Matterport scan{'s' if len(scans) > 1 else ''} of this property cover only "
            f"{', '.join(parts)}: too little warehouse floor to plan on (a scan needs at least 5,000 SF of floor and "
            "16 ft of height). You can still walk the scan in 3D.")
    return s["model_id"]


def _sprinkler(w):
    v = str(w.get("sprinklers") or "").lower()
    if v in SPRINKLER_CLEARANCE_FT:
        ft = SPRINKLER_CLEARANCE_FT[v]
        return ft, f"CoStar sprinklers: {w['sprinklers']} -> {ft:g} ft clearance below the sprinkler deflectors"
    return 3.0, "sprinkler type not in CoStar: assumed 3 ft clearance (the conservative ESFR rule)"


def _rent(w):
    vals = []
    for ln in w.get("listings") or []:
        for k in ("rent_low_psf_yr", "rent_high_psf_yr"):
            if ln.get(k):
                vals.append(float(ln[k]))
    return (round(sum(vals) / len(vals), 2), "CoStar asking rent ($/SF/yr, listing average)") if vals else (None, None)


def _dock_side(model_id, ds):
    """The side label sim.site.from_interior needs to bring the dock wall down, from the doors' outward normals.
    from_interior's labels (verified numerically, see doors.SIDE_NORMAL): 'S' = low-y, 'N' = high-y, 'E' = LOW-x,
    'W' = HIGH-x of the scan's rot frame."""
    from proinspect import doors as door_store
    G = door_store._grid(model_id)
    if not ds or G is None:
        return None
    votes = {}
    for d in ds:
        if d.get("kind") == "personnel":
            continue
        x0, y0 = door_store.showcase_to_rot(d["center"]["x"], d["center"]["z"], G["theta"])
        x1, y1 = door_store.showcase_to_rot(d["center"]["x"] + d["normal"]["x"], d["center"]["z"] + d["normal"]["z"],
                                            G["theta"])
        nx, ny = x1 - x0, y1 - y0
        side = ("W" if nx > 0 else "E") if abs(nx) > abs(ny) else ("N" if ny > 0 else "S")
        votes[side] = votes.get(side, 0) + 1
    return max(votes, key=votes.get) if votes else None


def _site(model_id, pid, orientation):
    """The scan floor as a sim Site (dock wall at low y). For 'parallel', the site is transposed so the generator's
    rack rows run parallel to the dock wall; the dock zone is then blocked along the (transposed) dock wall."""
    from proinspect import doors as door_store
    from proinspect.sim.site import Site, from_interior
    ds, _basis = door_store.doors_for_scan(model_id, pid)
    side = _dock_side(model_id, ds) or door_store.dock_side(model_id)
    base = from_interior(model_id, doors=[], dock_side=side)
    base.doors = [d for d in door_store.to_sim(model_id, base, doors=ds) if getattr(d, "on_dock_wall", True)]
    if orientation == "perpendicular":
        return base, False
    from proinspect.sim.layout import APRON, LANE_D, MAIN_AISLE
    from proinspect.sim.site import CELL
    free = base.free.copy()
    strip = int(round((base.wall_y + APRON + min(LANE_D, 0.3 * base.ny * CELL) + MAIN_AISLE) / CELL))
    free[:, :strip] = False                                        # keep the dock zone clear
    t = Site(base.name + " (rows parallel to the dock wall)", base.floor.T.copy(), free.T.copy(), [],
             base.clear_height_ft, wall_y=0.0, transform=base.transform, assumptions=list(base.assumptions))
    t._base = base
    return t, True


def _columns_mask(site):
    """Small isolated obstacles inside the floor = columns (and pilasters): connected parts of the blocked interior
    under 1.5 m2."""
    from proinspect.sim.site import CELL
    interior = ndimage.binary_fill_holes(site.floor)
    blocked = interior & ~site.free
    lab, n = ndimage.label(blocked)
    if not n:
        return np.zeros_like(blocked)
    sizes = ndimage.sum(blocked, lab, range(1, n + 1)) * CELL ** 2
    small = np.isin(lab, np.nonzero(sizes <= 1.5)[0] + 1)
    return ndimage.binary_dilation(small, iterations=1)


def _rows(bays, levels):
    """Merge bays into rack rows (same x span, contiguous in y). Each row: id, x0, x1, y0, y1, bays, slots."""
    by = {}
    for b in bays:
        by.setdefault((round(b["x0"], 2), round(b["x1"], 2)), []).append((b["y0"], b["y1"]))
    rows = []
    for (x0, x1), spans in sorted(by.items()):
        spans.sort()
        cur = [spans[0][0], spans[0][1], 1]
        for y0, y1 in spans[1:]:
            if y0 - cur[1] <= 0.05:
                cur[1], cur[2] = y1, cur[2] + 1
            else:
                rows.append([x0, x1, *cur])
                cur = [y0, y1, 1]
        rows.append([x0, x1, *cur])
    return [{"id": f"r{i + 1}", "x0": r[0], "x1": r[1], "y0": round(r[2], 2), "y1": round(r[3], 2), "bays": r[4],
             "slots": r[4] * 2 * levels} for i, r in enumerate(rows)]


def _apply_edits(rows, edits, site, aisle_m):
    """Delete / move (dx metres across the row) rack rows. A moved row must stay on clear floor and keep either a
    back-to-back flue (<= 0.3 m) or at least one aisle (truck's min aisle) to every row beside it."""
    from proinspect.sim.layout import _all
    edits = edits or {}
    deleted = set(edits.get("deleted") or [])
    moved = {k: float(v) for k, v in (edits.get("moved") or {}).items()}
    interior = ndimage.binary_fill_holes(site.floor)
    usable = site.free & interior
    out = []
    for r in rows:
        r = dict(r)
        if r["id"] in deleted:
            r.update(deleted=True, ok=True, slots_kept=0)
            out.append(r)
            continue
        if r["id"] in moved:
            dx = moved[r["id"]]
            r["x0"], r["x1"] = round(r["x0"] + dx, 2), round(r["x1"] + dx, 2)
            r["moved_m"] = dx
        out.append(r)
    live = [r for r in out if not r.get("deleted")]
    for r in live:
        why = None
        if "moved_m" in r:
            if not _all(usable, r["x0"], r["x1"], r["y0"], r["y1"]):
                why = "sits on a column, wall or other obstruction"
            else:
                for o in live:
                    if o is r or o["y1"] <= r["y0"] or o["y0"] >= r["y1"]:
                        continue
                    gap = max(o["x0"] - r["x1"], r["x0"] - o["x1"])
                    if gap < -0.01:
                        why = f"overlaps rack row {o['id']}"
                        break
                    if 0.3 < gap < aisle_m - 0.05:
                        why = (f"leaves a {gap / FT:.1f} ft aisle next to {o['id']}; the chosen truck needs "
                               f"{aisle_m / FT:.1f} ft")
                        break
        r["ok"] = why is None
        r["why"] = why
        r["slots_kept"] = r["slots"] if why is None else 0
    return out


def plan(pid, equipment="reach_truck", orientation="perpendicular", edits=None, use_cache=True):
    from proinspect.sim.layout import PALLET_LEVEL_FT, generate
    from proinspect.sim.site import CELL
    eq = equipment_lib()
    if equipment not in eq or not eq[equipment].get("min_aisle_ft") or eq[equipment].get("lift_height_ft", 0) < 10:
        raise ValueError(f"equipment must be a rack truck: {[k for k in TRUCKS]}")
    if orientation not in ("perpendicular", "parallel"):
        raise ValueError("orientation must be 'perpendicular' or 'parallel'")
    w = _catalog(pid)
    model_id = _scan(w)
    key = hashlib.sha256(json.dumps([pid, model_id, equipment, orientation, edits or {}, _code()],
                                    sort_keys=True).encode()).hexdigest()[:16]
    p = OUT / f"{key}.json"
    if use_cache and p.exists():
        return json.loads(p.read_text())

    site, transposed = _site(model_id, pid, orientation)
    spr_ft, spr_note = _sprinkler(w)
    lay = generate(site, {equipment: 1}, sprinkler_ft=spr_ft, dock_zone=(not transposed and bool(site.doors)))
    aisle_m, levels = lay.aisle_m, lay.levels
    rows = _rows(lay.bays, levels)
    # positions lost to columns: the same layout on the same floor with the columns removed
    cols = _columns_mask(site)
    lost_cols = None
    if cols.any():
        from copy import copy
        s2 = copy(site)
        s2.free = site.free | (cols & site.floor)
        lost_cols = max(0, generate(s2, {equipment: 1}, sprinkler_ft=spr_ft, dock_zone=(not transposed and bool(site.doors))).positions
                        - lay.positions)
    rows = _apply_edits(rows, edits, site, aisle_m)
    positions = sum(r["slots_kept"] for r in rows)

    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    clear = interior.get("clear") or {}
    p5 = site.clear_height_ft
    lift = eq[equipment]["lift_height_ft"]
    by_clear = int((p5 - spr_ft) // PALLET_LEVEL_FT) + 1
    by_lift = int(lift // PALLET_LEVEL_FT) + 1
    scanned_sqft = float(site.floor.sum()) * CELL ** 2 * SQFT
    rba = w.get("rba_sqft") or w.get("gba_sqft")
    office = interior.get("office_sqft") or 0
    est = None
    if rba and scanned_sqft:
        ratio = max(1.0, (float(rba) - office) / scanned_sqft)
        est = {"value": int(round(positions * ratio, -1)), "ratio": round(ratio, 2),
               "basis": (f"{positions:,} positions on the {scanned_sqft:,.0f} SF the scan covers, scaled to CoStar's "
                         f"{float(rba):,.0f} SF building (minus {office:,.0f} SF office seen in the scan). An estimate: "
                         "the unscanned part may have offices, columns or docks we did not see.")}
    rent, rent_src = _rent(w)
    per_pos = None
    if rent and rba and est and est["value"]:
        per_pos = {"value": round(rent * float(rba) / est["value"], 0),
                   "basis": f"${rent}/SF/yr x {float(rba):,.0f} SF / {est['value']:,} estimated positions"}
    rack_area = sum((r["x1"] - r["x0"]) * (r["y1"] - r["y0"]) for r in rows if r["slots_kept"]) * SQFT
    cube = (rack_area * levels * PALLET_LEVEL_FT) / (scanned_sqft * p5) if scanned_sqft and p5 else None

    # Matterport scene: rack boxes + staging lanes + the scanned outline (no vehicles)
    scene = _scene(site, rows, lay.lanes, levels, model_id, pid, transposed)
    res = {
        "property_id": pid, "model_id": model_id, "equipment": equipment, "equipment_name": eq[equipment].get("name"),
        "orientation": orientation, "run_id": key,
        "positions": positions, "levels": levels, "aisle_ft": round(aisle_m / FT, 1),
        "levels_basis": (f"{p5:.1f} ft lowest overhead obstruction (scan P5) - {spr_ft:g} ft sprinkler clearance -> "
                         f"{by_clear} levels at {PALLET_LEVEL_FT} ft"
                         + (f"; the {eq[equipment].get('name')} lifts {lift} ft -> max {by_lift} levels"
                            if by_lift < by_clear else "")),
        "clear": {"scan_p5_ft": p5, "scan_deck_ft": interior.get("deck_ft"), "costar_ft": w.get("clear_height_ft")},
        "sprinkler": spr_note,
        "scanned_sqft": round(scanned_sqft), "sqft_per_position": round(scanned_sqft / positions, 1) if positions else None,
        "building_estimate": est, "cost_per_position_yr": per_pos,
        "rent": {"psf_yr": rent, "source": rent_src} if rent else None,
        "positions_lost_to_columns": lost_cols, "cube_used_pct": round(100 * cube, 1) if cube else None,
        "staging_lanes": len(lay.lanes), "dock_doors": len(site.doors) if not transposed else len(site._base.doors),
        "rows": rows, "transposed": transposed,
        "lanes": [{k: round(float(L[k]), 2) for k in ("x0", "x1", "y0", "y1")} for L in lay.lanes],
        "plan": _plan_geometry(site, transposed),
        "scene": scene,
        "assumptions": [
            "racks placed on the scanned floor with today's contents removed (walls, columns, full-height structures kept)",
            "selective pallet rack: 42 in deep frames, 2 pallets per 8.25 ft bay, 5.5 ft per level (48 in pallets)",
            f"aisle = the {eq[equipment].get('name')}'s minimum aisle ({eq[equipment]['min_aisle_ft']} ft, library value)",
            spr_note,
            "cross-aisle every ~120 ft of rack run; perimeter aisle 4 ft; staging lanes kept in front of the dock doors",
            "no floor-load or seismic check (CoStar has no floor load for these buildings)"],
        "code": _code(),
    }
    res = json.loads(json.dumps(res, default=float))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}_scene.json").write_text(json.dumps(res.pop("scene")))
    res["scene_url"] = f"/media/racks/{key}_scene.json"
    p.write_text(json.dumps(res))
    return res


def _sim_xy(site, transposed, u, v):
    """Generator frame -> the base sim frame (undo the transpose)."""
    return (v, u) if transposed else (u, v)


def _plan_geometry(site, transposed):
    """2D plan for the editor, in the generator frame: floor outline polygon(s), column dots, staging lanes."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    from proinspect.sim.site import CELL
    f = ndimage.binary_fill_holes(site.floor)
    k = 4                                                           # 1 m blocks
    nx, ny = f.shape[0] // k, f.shape[1] // k
    coarse = f[:nx * k, :ny * k].reshape(nx, k, ny, k).mean(axis=(1, 3)) >= 0.5
    rects = []
    for i in range(nx):
        j = 0
        while j < ny:
            if coarse[i, j]:
                e = j
                while e < ny and coarse[i, e]:
                    e += 1
                rects.append(box(i * k * CELL, j * k * CELL, (i + 1) * k * CELL, e * k * CELL))
                j = e
            else:
                j += 1
    poly = unary_union(rects).simplify(0.4) if rects else None
    polys = [] if poly is None else ([poly] if poly.geom_type == "Polygon" else list(poly.geoms))
    cols = _columns_mask(site)
    lab, n = ndimage.label(cols)
    centers = ndimage.center_of_mass(cols, lab, range(1, n + 1)) if n else []
    blocked = ndimage.binary_fill_holes(site.floor) & ~site.free & ~cols
    blab, bn = ndimage.label(blocked)
    big = []
    for sl in ndimage.find_objects(blab) or []:
        if sl is None:
            continue
        i0, i1, j0, j1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if (i1 - i0) * (j1 - j0) * CELL ** 2 >= 2.0:
            big.append([round(i0 * CELL, 2), round(i1 * CELL, 2), round(j0 * CELL, 2), round(j1 * CELL, 2)])
    return {"width_m": round(site.floor.shape[0] * CELL, 2), "depth_m": round(site.floor.shape[1] * CELL, 2),
            "outline": [[[round(x, 2), round(y, 2)] for x, y in p.exterior.coords] for p in polys],
            "columns": [[round(c[0] * CELL, 2), round(c[1] * CELL, 2)] for c in centers],
            "obstructions": big[:400],
            "dock_wall": "left" if transposed else "bottom"}


def _scene(site, rows, lanes, levels, model_id, pid, transposed):
    from proinspect.sim.export import box_yaw, model_to_showcase, sim_to_showcase
    from proinspect.sim.layout import PALLET_LEVEL_FT
    base = site._base if transposed else site
    tf = base.transform
    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)
    _, floor_y, _ = model_to_showcase(0.0, 0.0, floor_z)
    byaw = round(box_yaw(tf), 4)
    h = round(levels * PALLET_LEVEL_FT * FT, 2)

    def pt(u, v):
        bu, bv = _sim_xy(site, transposed, u, v)
        x, _, z = sim_to_showcase(bu, bv, tf, floor_z, 0.0)
        return round(float(x), 2), round(float(z), 2)
    racks = []
    for r in rows:
        if not r["slots_kept"]:
            continue
        x, z = pt((r["x0"] + r["x1"]) / 2, (r["y0"] + r["y1"]) / 2)
        wdt, dpt = r["x1"] - r["x0"], r["y1"] - r["y0"]
        if transposed:
            wdt, dpt = dpt, wdt
        racks.append({"id": r["id"], "x": x, "z": z, "w": round(wdt, 2), "d": round(dpt, 2), "h": h, "yaw": byaw})
    ln = []
    for L in lanes:
        x, z = pt((L["x0"] + L["x1"]) / 2, (L["y0"] + L["y1"]) / 2)
        ln.append({"x": x, "z": z, "w": round(L["x1"] - L["x0"], 2), "d": round(L["y1"] - L["y0"], 2), "yaw": byaw,
                   "door": L["door"], "inbound": None, "capacity": L.get("capacity")})
    return {"run_id": None, "property_id": pid, "model_id": model_id, "floor_y": round(float(floor_y), 3),
            "racks": racks, "lanes": ln, "doors": [], "vehicles": [], "trucks": [], "heat": {"cells": []},
            "scanned_area": [], "duration_s": 0, "kind": "rack_plan",
            "assumptions": [f"rack boxes drawn {h} m tall ({levels} pallet levels x {PALLET_LEVEL_FT} ft)"]}


def compare_trucks(pid, orientation="perpendicular"):
    eq = equipment_lib()
    out = []
    for k in TRUCKS:
        if k not in eq:
            continue
        try:
            r = plan(pid, k, orientation)
        except Exception as e:                                     # noqa: BLE001
            out.append({"equipment": k, "error": str(e)[:160]})
            continue
        out.append({"equipment": k, "name": eq[k].get("name"), "positions": r["positions"], "levels": r["levels"],
                    "aisle_ft": r["aisle_ft"], "sqft_per_position": r["sqft_per_position"],
                    "building_estimate": (r["building_estimate"] or {}).get("value"),
                    "cost_per_position_yr": (r["cost_per_position_yr"] or {}).get("value")})
    base = next((o for o in out if o.get("equipment") == "counterbalance_electric" and o.get("positions")), None)
    for o in out:
        if base and o.get("positions"):
            o["vs_counterbalance_pct"] = round(100 * (o["positions"] / base["positions"] - 1))
    return {"property_id": pid, "orientation": orientation, "trucks": out}


def summary(pid):
    """The card / compare line: reach-truck plan (the common default)."""
    try:
        r = plan(pid)
    except FileNotFoundError as e:
        return {"status": "no_scan", "why": str(e)}
    est = (r["building_estimate"] or {}).get("value")
    line = (f"{r['positions']:,} pallet positions in the scanned {r['scanned_sqft']:,} SF ({r['levels']} levels, "
            f"reach truck)" + (f" · ~{est:,} for the whole building (est.)" if est and est != r["positions"] else "")
            + (f" · ${r['cost_per_position_yr']['value']:,.0f}/position/yr" if r["cost_per_position_yr"] else ""))
    return {"status": "computed", "line": line, "positions": r["positions"], "building_estimate": est,
            "levels": r["levels"], "sqft_per_position": r["sqft_per_position"],
            "cost_per_position_yr": (r["cost_per_position_yr"] or {}).get("value"),
            "positions_lost_to_columns": r["positions_lost_to_columns"], "equipment": r["equipment"]}
