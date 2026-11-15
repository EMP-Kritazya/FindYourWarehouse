"""Yard test orchestration: the buyer's trucks on a real site.

    yard_test(req) -> result dict per docs/CONTRACTS.md (POST /api/yard/test), positions in lon/lat.

Steps: doors (marked > detected > assumed) -> per door x truck type back-in fit (kinematics.fit) -> street entrance and
drivable routes (site) -> staging area left on the court (estimate) -> SimPy queue over 20 seeds (queue) -> key-frames
of the representative run for the animation.
Headings in the output are compass bearings (degrees clockwise from north). The animated reference point is the
kingpin for a tractor-semitrailer and the rear axle for a rigid truck; `vehicle_geometry` gives the box extents.
"""
import json
import math

import numpy as np
from scipy import ndimage
from shapely.geometry import mapping
from shapely.ops import transform, unary_union

from proinspect.config import BUILD
from proinspect.tools import elevation as el
from proinspect.yard import kinematics as kin
from proinspect.yard import queue as yq
from proinspect.yard import site

TYPES = ("semi_53", "box_truck_26", "last_mile_van")
DRIVE_SPEED = 5 * 0.44704          # m/s, equipment.yaml approach_speed_mph
SETUP_S = 30.0                     # stop, check mirrors, shift into reverse [U]
STAGING_USABLE = 0.5               # share of the leftover court usable for waiting trucks [U]
MAX_ANIMATED = 80


def _margin_text(margin_m):
    """'clears by 2.1 ft' / 'fails by 0.4 ft': words, not signs; one decimal under 10 ft (same as web/src/yard/format.ts)."""
    ft = abs(margin_m) / kin.FT
    return f"{'clears' if margin_m >= 0 else 'fails'} by {ft:.1f} ft" if ft < 10 else \
        f"{'clears' if margin_m >= 0 else 'fails'} by {round(ft)} ft"


def _compass(th):
    return (90.0 - np.degrees(th)) % 360.0


def _model_ids(property_id):
    try:
        cat = {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}
        return [s["model_id"] for s in cat.get(property_id, {}).get("scans", []) if s.get("model_id")]
    except Exception:  # noqa: BLE001
        return []


def _dock_minutes(types, override):
    eq = kin._yaml()
    return {t: float((override or {}).get(t) or eq[t].get("dock_turn_min") or 30) for t in types}


class _Geo:
    def __init__(self, yard):
        self.yard = yard

    def ll(self, x, y):
        lat, lon = el.to_latlon(x, y, self.yard.lat0, self.yard.lon0)
        return round(float(lon), 7), round(float(lat), 7)

    def poly(self, g):
        return mapping(transform(lambda x, y, z=None: el.to_latlon(x, y, self.yard.lat0, self.yard.lon0)[::-1], g))


def _ref_point(m, pose):
    """[t, x, y, th0, th1] rear-axle pose -> (x, y) of the animated reference point."""
    if m.articulated:
        return pose[1] + m.hitch_offset * math.cos(pose[3]), pose[2] + m.hitch_offset * math.sin(pose[3])
    return pose[1], pose[2]


def _trail(points, th1, m):
    """Headings along a polyline for the reference point: tractor heading from the segment, trailer by the tractrix
    th1' = sin(th0 - th1) / L1 per metre."""
    out = []
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        th0 = math.atan2(y1 - y0, x1 - x0) if L > 1e-6 else (out[-1][2] if out else th1)
        if m.articulated:
            steps = max(1, int(L / 0.5))
            for _ in range(steps):
                th1 += (L / steps) * math.sin(th0 - th1) / m.trailer_wheelbase
        else:
            th1 = th0
        out.append((x1, y1, th0, th1, L))
    return out


def _stalls(yard, router, swept_all, entrance_xy, spacing=20.0, max_n=40):
    """Estimated staging room: paved, clear, on the parcel, reachable from the gate, outside every swept path and
    >= 2 m from obstacles. Returns (area_m2, stall points [(x, y)] ordered from the gate)."""
    g = yard.grid
    m = (g.kind == 0) & yard.paved & (g.sd >= 2.0)
    if yard.parcel_mask is not None:
        m &= yard.parcel_mask
    if swept_all is not None and not swept_all.is_empty:
        from proinspect.twin.exterior import Frame
        m &= ~Frame(yard.lat0, yard.lon0, yard.half).raster([swept_all])
    k = int(round(router.cell / g.res))
    reach = np.zeros_like(m)
    ok = np.isfinite(router.dist)
    rr, cc = router.rs[ok], router.cs[ok]
    for a in range(k):
        for b in range(k):
            r2, c2 = rr * k + a, cc * k + b
            sel = (r2 < g.n) & (c2 < g.n)
            reach[r2[sel], c2[sel]] = True
    m &= reach
    m = ndimage.binary_opening(m, iterations=2)
    area = float(m.sum()) * g.res ** 2
    rs, cs = np.nonzero(m[::8, ::8])
    pts = [g.xy(r * 8, c * 8) for r, c in zip(rs, cs)]
    pts.sort(key=lambda p: math.hypot(p[0] - entrance_xy[0], p[1] - entrance_xy[1]))
    chosen = []
    for p in pts:
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= spacing for q in chosen):
            chosen.append((float(p[0]), float(p[1])))
        if len(chosen) >= max_n:
            break
    return area, chosen


def yard_test(req):
    pid = int(req["property_id"])
    trucks = [t for t in req.get("trucks") or [] if t.get("type") in TYPES]
    if not trucks:
        raise ValueError(f"trucks: give at least one of {TYPES} with per_day or per_hour")
    hours = list(req.get("hours") or [6, 16])
    types = sorted({t["type"] for t in trucks}, key=TYPES.index)
    yard = site.load(pid)
    geo = _Geo(yard)
    assumptions = list(yard.notes)

    all_doors, door_src, notes = site.doors(yard, _model_ids(pid))
    assumptions += notes
    want = req.get("door_ids")
    doors = [d for d in all_doors if not want or d["id"] in set(map(str, want))] or all_doors
    longest = max(types, key=kin.parked_length)

    fits, fit_by = [], {}
    for d in doors:
        nb = site.neighbor_offsets(d, doors)
        for t in types:
            f = kin.fit(t, yard.grid, (d["x"], d["y"]), d["normal"], d["width_m"], neighbors=nb, neighbor_key=longest)
            f["door_id"], f["neighbors"] = d["id"], len(nb)
            fit_by[(d["id"], t)] = f
            fits.append(f)

    dock_c = (float(np.mean([d["x"] for d in doors])), float(np.mean([d["y"] for d in doors])))
    (ent_xy, ent_n), ent_method = site.entrance(yard, dock_c)
    assumptions.append(ent_method)
    inside = (ent_xy[0] - ent_n[0] * 4, ent_xy[1] - ent_n[1] * 4)
    router = site.Router(yard, inside)
    gate = router.node_xy(router.src)

    swept_all = unary_union([f["swept"] for f in fits if f["ok"]]) if any(f["ok"] for f in fits) else None
    area, stall_pts = _stalls(yard, router, swept_all, gate)
    usable = area * STAGING_USABLE
    semi_eq = usable / yq.STALL_M2["semi_53"]

    dock_min = _dock_minutes(types, req.get("dock_minutes"))
    service, fits_ok, start_pose = {"dock_min": dock_min}, {}, {}
    for (did, t), f in fit_by.items():
        st = f["poses"][0]
        rl = router.length((st[1], st[2]))
        unreachable = rl is None
        fits_ok[(did, t)] = bool(f["ok"]) and not unreachable
        f["reachable_from_gate"] = not unreachable
        tin = (rl or 0.0) / DRIVE_SPEED
        service[(did, t)] = (tin, f["maneuver_s"] + SETUP_S, f["maneuver_s"])
        start_pose[(did, t)] = st
    q = yq.simulate(trucks, hours, [d["id"] for d in doors], fits_ok, service, usable, runs=int(req.get("runs", 20)))
    rep = q.pop("_rep")

    # -------------------------------------------------------------- key-frames of the representative run
    road_t = np.array([ent_n[1], -ent_n[0]])
    street0 = np.array(ent_xy) + np.array(ent_n) * 6.0

    def street_slot(k):
        return street0 + road_t * (12.0 + 22.0 * k)

    vehicles = []
    stall_busy = [[] for _ in stall_pts]
    street_busy = []
    for rec in rep["log"][:MAX_ANIMATED]:
        if rec.get("unserved") or "dock_start" not in rec:
            continue
        typ, m = rec["type"], kin.model(rec["type"])
        keys = []
        th_in = math.atan2(-road_t[1], -road_t[0])

        def add(t, x, y, th0, th1, state):
            lon, lat = geo.ll(x, y)
            keys.append([round(float(t), 1), lon, lat, round(float(_compass(th0)), 1), state,
                         round(float(_compass(th1)), 1)])

        t = rec["arrive"]
        far = street0 + road_t * 160.0
        add(t - 60, *far, th_in, th_in, "approach")
        if rec["street_s"] > 1:
            k = sum(1 for (a, b) in street_busy if a <= t < b)
            street_busy.append((t, t + rec["street_s"]))
            p = street_slot(k)
            add(t, *p, th_in, th_in, "street_queue")
            t += rec["street_s"]
            add(t, *p, th_in, th_in, "street_queue")
        add(t, *ent_xy, th_in, th_in, "enter")
        pos, th1 = tuple(ent_xy), th_in
        if rec["yard_s"] > 1 and stall_pts:
            j = next((i for i, b in enumerate(stall_busy) if all(not (a <= t < e) for a, e in b)), len(stall_pts) - 1)
            stall_busy[j].append((t, t + rec["yard_s"]))
            path = router.route(pos, stall_pts[j])
            for x, y, h0, h1, L in _trail(path, th1, m):
                t += L / DRIVE_SPEED
                add(t, x, y, h0, h1, "to_yard")
                th1 = h1
            pos = stall_pts[j]
            h_stall = math.radians(90 - keys[-1][3])
            add(t, *pos, h_stall, th1, "yard_queue")
            add(rec["dock_start"], *pos, h_stall, th1, "yard_queue")
        t = rec["dock_start"]
        f = fit_by[(rec["door"], typ)]
        st = f["poses"]
        sx, sy = _ref_point(m, st[0])
        pre = (sx + 15 * math.cos(st[0][3]), sy + 15 * math.sin(st[0][3]))      # pull past, then reverse
        path = router.route(pos, pre) + [(sx, sy)]
        tr = _trail(path, th1, m)
        total = sum(L for *_, L in tr) or 1.0
        for x, y, h0, h1, L in tr[:-1]:
            t += rec["drive_in_s"] * L / total if rec["drive_in_s"] else L / DRIVE_SPEED
            add(t, x, y, h0, h1, "to_door")
        t = max(t, rec["dock_start"] + rec["drive_in_s"])
        add(t, sx, sy, st[0][3], st[0][4], "to_door")
        t += SETUP_S
        for pose in st[::10]:
            add(t + pose[0], *_ref_point(m, pose), pose[3], pose[4], "back_in")
        t += st[-1][0]
        add(t, *_ref_point(m, st[-1]), st[-1][3], st[-1][4], "docked")
        t += rec["dwell_s"]
        add(t, *_ref_point(m, st[-1]), st[-1][3], st[-1][4], "docked")
        for pose in st[::-1][::10]:
            add(t + (st[-1][0] - pose[0]), *_ref_point(m, pose), pose[3], pose[4], "pull_out")
        t += st[-1][0]
        h_end = st[0][3]
        path = router.route((sx, sy), gate) + [tuple(ent_xy)]
        for x, y, h0, h1, L in _trail(path, st[0][4], m):
            t += L / DRIVE_SPEED
            add(t, x, y, h0, h1, "exit")
            h_end = h0
        out = street0 - road_t * 160.0
        add(t + 70, *out, math.atan2(-road_t[1], -road_t[0]), math.atan2(-road_t[1], -road_t[0]), "gone")
        vehicles.append({"id": rec["id"], "type": typ, "door_id": rec["door"],
                         "wait_min": round((rec["dock_start"] - rec["arrive"]) / 60, 1), "keys": keys})

    # -------------------------------------------------------------- output
    eq = kin._yaml()
    summary = []
    for t in types:
        ok = [f for f in fits if f["vehicle"] == t and fits_ok[(f["door_id"], t)]]
        best = max((f for f in fits if f["vehicle"] == t), key=lambda f: f["margin_m"])
        summary.append({"type": t, "name": eq[t]["name"], "doors_ok": len(ok), "doors_tested": len(doors),
                        "best_margin_m": best["margin_m"],
                        "depth_needed_ft": round(min(f["depth_needed_m"] for f in (ok or [best])) / kin.FT),
                        "margin_text": _margin_text(best["margin_m"]),
                        "verdict": (f"fits at {len(ok)} of {len(doors)} doors (best door {_margin_text(best['margin_m'])})" if ok else
                                    f"does not fit at any tested door (best door {best['door_id']} "
                                    f"{_margin_text(best['margin_m'])}, blocked by "
                                    f"{best['conflicts'][0]['what'] if best['conflicts'] else 'the route'})")})
    out_fits = []
    for f in fits:
        m = kin.model(f["vehicle"])
        out_fits.append({
            "door_id": f["door_id"], "vehicle": f["vehicle"], "ok": fits_ok[(f["door_id"], f["vehicle"])],
            "geometric_ok": f["ok"], "reachable_from_gate": f["reachable_from_gate"],
            "margin_m": f["margin_m"], "maneuver_s": f["maneuver_s"], "depth_needed_ft": round(f["depth_needed_m"] / kin.FT),
            "steering": f["params"], "neighbors_parked": f["neighbors"], "crosses_parcel_line": f["crosses_parcel_line"],
            "conflicts": [{**dict(zip(("lon", "lat"), geo.ll(c["x"], c["y"]))), "what": c["what"],
                           "intrusion_m": c["intrusion_m"]} for c in f["conflicts"]],
            "path": [[round(float(p[0]), 1), *geo.ll(*_ref_point(m, p)), round(float(_compass(p[3])), 1),
                      round(float(_compass(p[4])), 1)] for p in f["poses"][::5]],
            "swept": geo.poly(f["swept"]),
        })
    out_doors = [{"id": d["id"], "source": d["source"], "kind": d["kind"], "enu": {"x": round(d["x"], 2), "y": round(d["y"], 2)},
                  "lonlat": list(geo.ll(d["x"], d["y"])), "normal": [round(float(v), 3) for v in d["normal"]],
                  "width_m": round(d["width_m"], 2)} for d in doors]
    tl = [[r[0], r[1], r[2]] for r in rep["timeline"]]
    queue = {k: v for k, v in q.items()}
    queue.update(timeline=tl, at_doors=[[r[0], r[3]] for r in rep["timeline"]],
                 staging_area_m2=round(area), staging_usable_m2=round(usable),
                 staging_semi_equivalents=round(semi_eq, 1),
                 staging_note=(f"ESTIMATE: {area:,.0f} m2 of clear paved court is left outside the swept paths; "
                               f"{int(STAGING_USABLE * 100)}% is taken as usable for waiting trucks (car parking and "
                               f"aisles are not separated) = room for about {semi_eq:.0f} waiting 53' rigs"),
                 dock_minutes=dock_min, hours=hours)
    frame = yard.twin["frame"]
    assumptions += [
        f"door source: {door_src}",
        "back-in paths: a forward pull-out from the docked pose played backwards (kinematic model, no dynamics); the "
        f"steering law is searched per door; clearance buffer {kin.CLEARANCE_M} m around the body",
        f"neighbouring tested doors within 8 m are occupied by a parked {kin.model(longest).name} (design case)",
        "obstacles: buildings, aerial-DSM objects > 1 m that are not vehicles (canopies within 5 m of a wall "
        "ignored), unpaved ground (twin paved mask + bare-soil colour test); parked vehicles count as pavement",
        "the parcel line is a soft limit: crossing it is flagged, not failed (access drives often cross it)",
        f"arrivals: per-day counts become appointments evenly spaced over {hours[0]}:00-{hours[1]}:00, each truck "
        f"+-{yq.LATENESS_SD_MIN:.0f} min (1 sd) late/early [U]; dock time lognormal (CV {yq.DOCK_CV}) around "
        + ", ".join(f"{t} {v:.0f} min" for t, v in dock_min.items()),
        f"drive speed in the yard {DRIVE_SPEED:.1f} m/s (5 mph), backing {kin.REVERSE_SPEED} m/s, {SETUP_S:.0f} s set-up "
        "before each back-in [U]",
        "street queue positions in the animation are illustrative (along the parcel line at the entrance)",
        f"{q['runs']} seeded runs; waits pooled over all trucks; max in yard / on street = median of the per-run maxima",
    ]
    for t in types:
        assumptions += list(kin.model(t).assumptions)
    return {
        "property_id": pid,
        "doors": out_doors, "door_source": door_src,
        "fits": out_fits, "summary": summary,
        "queue": queue,
        "vehicles": vehicles,
        "vehicle_geometry": {t: kin.model(t).geometry() for t in types},
        "entrance": {"lonlat": list(geo.ll(*ent_xy)), "method": ent_method,
                     "street_dir": [round(float(road_t[0]), 3), round(float(road_t[1]), 3)]},
        "stalls": [list(geo.ll(*p)) for p in stall_pts],
        "frame": {"lat0": frame["lat0"], "lon0": frame["lon0"], "half_m": frame["half_m"],
                  "bounds": [*geo.ll(-frame["half_m"], -frame["half_m"]), *geo.ll(frame["half_m"], frame["half_m"])],
                  "ortho_url": f"/media/twins/exterior/{pid}_ortho.jpg"},
        "parcel": geo.poly(yard.parcel) if yard.parcel is not None else None,
        "assumptions": assumptions,
    }
