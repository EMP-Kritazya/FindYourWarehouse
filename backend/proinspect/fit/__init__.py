"""Equipment test (inside): "do MY machines fit and move through this building?" (docs/CONTRACTS.md, S4).

    run_for_model(property_id, model_id, equipment, route, floor) -> result dict in Showcase coordinates
        equipment  library key | {length, width, height_lowered, lift_height?, turn_radius?, min_aisle?} (ft)
        route      {from_door?: id, from?: {x, z}, to: {x, z} | "auto"}
        floor      "as_scanned" (today's contents are obstacles) | "emptied" (walls, columns, full-height structure)

The maths lives in proinspect.fit.core (sim Site frame, 25 cm grid). Results are cached in build/fit/<run_id>.json,
keyed by the inputs, the door store, the interior grid and the code version.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from proinspect.config import BUILD
from proinspect.fit.core import FT, FitError, Floor, evaluate, simplify, vehicle, verdict  # noqa: F401

FIT_DIR = BUILD / "fit"
HEAT_CELL = 1.0
_PKG = Path(__file__).resolve().parent.parent


def code_version():
    h = hashlib.sha256()
    for p in sorted([*(_PKG / "fit").glob("*.py"), _PKG / "doors.py", _PKG / "sim" / "site.py",
                     _PKG / "sim" / "export.py", _PKG / "library" / "equipment.yaml"]):
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _floor_site(model_id, floor, property_id=None):
    from proinspect import doors as door_store
    from proinspect.sim.site import from_interior
    side = door_store.dock_side(model_id)
    site = from_interior(model_id, doors=[], dock_side=side)
    tf = site.transform
    g = np.load(BUILD / "twins" / "interior" / f"{model_id}.npz")
    if floor == "as_scanned":
        a = np.rot90(g["free"].astype(bool), tf["rot90"])
        c0, c1 = tf["crop"]
        free = a[c0:c0 + site.nx, c1:c1 + site.ny] & site.floor
        note = "floor as scanned: today's racks, pallets and clutter (0.1-2.0 m above the floor) are obstacles"
    elif floor == "emptied":
        free = site.free
        note = ("floor emptied: today's contents removed; walls, columns and full-height structures kept"
                if "structure" in g.files else "no structure grid: emptied = as scanned")
    else:
        raise FitError("floor must be 'as_scanned' or 'emptied'")
    ds, basis = door_store.doors_for_scan(model_id, property_id)
    site.doors = door_store.to_sim(model_id, site, doors=ds)
    site.door_basis = basis
    return site, free, note


def _door_note(site):
    b = getattr(site, "door_basis", {}) or {}
    if not site.doors:
        return "doors: none available in this scan (" + (b.get("why") or "none marked") + ")"
    if b.get("registration") == "marked in the scan":
        return "doors: marked by a person in the scan"
    return (f"doors: {b.get('in_scan')} of {b.get('of')} aerial dock-store doors mapped into the scan "
            f"({b.get('registration')}; registration confidence {b.get('confidence')})")


def _sc(tf, floor_z, u, v, h=0.0):
    from proinspect.sim.export import sim_to_showcase
    x, y, z = sim_to_showcase(u, v, tf, floor_z, h)
    return float(x), float(y), float(z)


def _to_sim(tf, p):
    from proinspect.sim.export import showcase_to_sim
    try:
        u, v = showcase_to_sim(float(p["x"]), 0.0, float(p["z"]), tf)
    except (TypeError, KeyError, ValueError):
        raise FitError("points must be {x, z} in Showcase metres")
    return float(u), float(v)


def _path_keys(res, tf, floor_z, speed):
    from proinspect.sim.export import heading_yaw
    pts = res.get("path_uv") or []
    if not pts:
        return []
    keep = simplify([(u, v) for u, v, _ in pts], 0.3)
    P = [_sc(tf, floor_z, pts[k][0], pts[k][1]) for k in keep]
    keys, t = [], 0.0
    for i, (x, _, z) in enumerate(P):
        if i + 1 < len(P):
            yaw = heading_yaw(P[i + 1][0] - x, P[i + 1][2] - z)
        if i:
            px, _, pz = P[i - 1]
            t += math.hypot(x - px, z - pz) / speed
            prev_yaw = keys[-1][3]
            keys.append([round(t, 2), round(x, 2), round(z, 2), round(prev_yaw, 3)])
            if i + 1 < len(P):
                dy = abs((yaw - prev_yaw + math.pi) % (2 * math.pi) - math.pi)
                if dy > 0.05:
                    t += 0.5 + dy                                 # slow down to turn
                    keys.append([round(t, 2), round(x, 2), round(z, 2), round(yaw, 3)])
        else:
            keys.append([0.0, round(x, 2), round(z, 2), round(yaw if len(P) > 1 else 0.0, 3)])
    return keys


def _heat(F, res, veh, tf, floor_z):
    from proinspect.sim.export import box_yaw
    reach = res.get("reach")
    if reach is None:
        return {"cell": HEAT_CELL, "cells": [], "yaw": 0.0}
    work_need = veh.min_aisle_m or (veh.width_m + 0.6)
    val = np.where(reach & (F.w >= work_need - 0.05), 1.0, np.where(reach, 0.5, 0.0))
    f = int(round(HEAT_CELL / F.cell))
    nx, ny = F.nx // f, F.ny // f
    fl = F.floor[:nx * f, :ny * f].reshape(nx, f, ny, f)
    vv = val[:nx * f, :ny * f].reshape(nx, f, ny, f)
    share = fl.mean(axis=(1, 3))
    s = (vv * fl).sum(axis=(1, 3)) / np.maximum(fl.sum(axis=(1, 3)), 1)
    cells = []
    for a, b in zip(*np.nonzero(share >= 0.5)):
        x, _, z = _sc(tf, floor_z, (a + 0.5) * HEAT_CELL, (b + 0.5) * HEAT_CELL)
        cells.append([round(x, 2), round(z, 2), round(float(s[a, b]), 2)])
    return {"cell": HEAT_CELL, "yaw": round(box_yaw(tf), 4), "cells": cells,
            "legend": {"1": "reachable, with room to work (right-angle stacking aisle)",
                       "0.5": "reachable, drive-through only", "0": "out of reach"},
            "basis": "share of the 25 cm floor cells in each 1 m tile"}


def run_for_model(property_id, model_id, equipment, route=None, floor="as_scanned", use_cache=True):
    from proinspect import doors as door_store
    route = route or {"to": "auto"}
    veh = vehicle(equipment)
    store = door_store.load(model_id) or {}
    _ds, door_basis = door_store.doors_for_scan(model_id, property_id)
    npz = BUILD / "twins" / "interior" / f"{model_id}.npz"
    if not npz.exists():
        raise FitError(f"scan {model_id} has no interior twin")
    blob = json.dumps([int(property_id), model_id, equipment, route, floor, store.get("updated_at"),
                       door_basis.get("updated_at"), door_basis.get("flip"), npz.stat().st_mtime, code_version()], sort_keys=True, default=str)
    run_id = hashlib.sha256(blob.encode()).hexdigest()[:16]
    out_p = FIT_DIR / f"{run_id}.json"
    if use_cache and out_p.exists():
        return json.loads(out_p.read_text())

    site, free, floor_note = _floor_site(model_id, floor, property_id)
    tf = site.transform
    interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
    floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)
    clear_ft = (interior.get("clear") or {}).get("p5_ft")
    F = Floor(free, site.floor, site.cell)

    to = route.get("to", "auto")
    auto = to == "auto" or to is None
    from_door = None
    if route.get("from_door"):
        from_door = next((d for d in site.doors if d.id == route["from_door"]), None)
        if from_door is None:
            raise FitError(f"door {route['from_door']!r} is not marked in scan {model_id}")
    start = _to_sim(tf, route["from"]) if route.get("from") else None
    target = None if auto else _to_sim(tf, to)
    if not auto and start is None and from_door is None:
        truck = [d for d in site.doors if d.kind != "personnel"]
        if not truck:
            raise FitError("no start: mark a door in the scan (or give route.from)")
        from_door = min(truck, key=lambda d: math.hypot(d.u - target[0], d.v - target[1]))
    res = evaluate(F, veh, start=start, target=target, doors=site.doors, from_door=from_door,
                   clear_ft=clear_ft, auto=auto)

    def at(c):
        if c.get("at") is None:
            return None
        x, y, z = _sc(tf, floor_z, c["at"][0], c["at"][1])
        return {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)}
    checks = [{**c, "at": at(c)} for c in res["checks"]]
    pins = []
    for p in res["pinch_points"]:
        x, _, z = _sc(tf, floor_z, p["u"], p["v"])
        what = p.get("what") or "gap"
        lab = (f"{'turn: ' if what == 'turn' else 'stacking aisle: ' if what == 'aisle' else ''}needs "
               f"{p['need_m'] / FT:.1f} ft, has {p['width_m'] / FT:.1f} ft")
        if p.get("area_m2"):
            lab += f" (cuts off {p['area_m2'] * 10.7639:,.0f} SF)"
        pins.append({"x": round(x, 2), "z": round(z, 2), "width_m": p["width_m"], "need_m": p["need_m"],
                     "ok": p["ok"], "what": what, "label": lab})
    ok = verdict(checks)
    worst = next((c for c in checks if c["ok"] is False and c.get("essential", True)), None)
    lim = next((c for c in checks if c["ok"] is False), None)
    if ok is True:
        text = f"{veh.name} fits" + (" and reaches the target" if not auto else
                                     f": reaches {res['reachable_pct']}% of the scanned floor")
        if lim:
            text += f"; limit: {lim['label'].lower()} ({lim['need']} {lim['unit']} needed, {lim['have']} {lim['unit']})"
    elif ok is False:
        text = (f"{veh.name} does not fit: {worst['label'].lower()} "
                f"(needs {worst['need']} {worst['unit']}, has {worst['have'] if worst['have'] is not None else '?'} "
                f"{worst['unit']})")
    else:
        text = f"{veh.name}: fit unknown, some inputs are '?' ({', '.join(c['label'] for c in checks if c['ok'] is None)})"
    unknown = [c for c in checks if c["ok"] is None]
    if ok is not False and unknown:
        dh = sum(c["kind"] == "door" and "height" in c["label"] for c in unknown)
        other = len(unknown) - dh
        bits = ([f"{dh} door height{'s' if dh != 1 else ''} unknown (not marked: '?')"] if dh else []) + \
               ([f"{other} other input{'s' if other != 1 else ''} unknown ('?')"] if other else [])
        text += ", but " + " and ".join(bits)
    counts = {"pass": sum(c["ok"] is True for c in checks), "fail": sum(c["ok"] is False for c in checks),
              "unknown": len(unknown)}
    starts = []
    for u, v in res.get("start") or []:
        x, _, z = _sc(tf, floor_z, u, v)
        starts.append({"x": round(x, 2), "z": round(z, 2)})
    out = {
        "run_id": run_id, "property_id": int(property_id), "model_id": model_id, "floor": floor,
        "floor_y": round(floor_z, 3), "equipment": veh.public(), "route": route, "ok": ok, "verdict": text, "counts": counts,
        "checks": checks, "path": _path_keys(res, tf, floor_z, veh.speed_mps), "pinch_points": pins,
        "reachable_pct": res["reachable_pct"], "heat": _heat(F, res, veh, tf, floor_z), "starts": starts,
        "doors": [{"id": d.id, "kind": d.kind, "width_m": d.width_m, "height_m": d.height_m, "source": d.source}
                  for d in site.doors],
        "door_basis": getattr(site, "door_basis", None),
        "clear_p5_ft": clear_ft,
        "assumptions": [floor_note, *res["assumptions"],
                        f"clear height = P5 of the lowest overhead obstruction from the scan ({clear_ft} ft)"
                        if clear_ft is not None else "clear height unknown",
                        _door_note(site),
                        "floor = the scanned area only (often part of the building)",
                        "vehicle dimensions are typical library values unless custom"],
    }
    FIT_DIR.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(out))
    return out
