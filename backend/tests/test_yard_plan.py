"""Multi-truck yard maneuver test (S2, v3): kinematic legs + prioritized space-time reservations."""
import math
from functools import lru_cache

import numpy as np
import pytest
from shapely.geometry import Polygon

from proinspect.yard import kinematics as kin
from proinspect.yard import plan
from proinspect.yard import pursuit as pp

V_MAX = pp.V_MAX


@lru_cache(maxsize=None)
def _synthetic(depth_ft, n, block=None, n_doors=6):
    sc = plan.synthetic_scene(depth_ft, n_doors=n_doors, block=block)
    return plan.maneuver({"trucks": [{"type": "semi_53", "count": n}], "dwell_s": 60}, scene=sc, internal=True)


def _polys(m, row):
    t, x, y, th0, th1 = row[:5]
    P = np.array([[t, x, y, th0, th1]])
    return kin.footprints(m, P)


def _check_run(res, grid_check=True):
    tl = res["_internal"]["timelines"]
    plans = {p["id"]: p for p in res["_internal"]["plans"]}
    scene = res["_internal"]["scene"]
    by_t = {}
    for vid, rows in tl.items():
        m = kin.model(plans[vid]["key"])
        prev = None
        for r in rows:
            k = int(round(r[0] / 0.2))
            by_t.setdefault(k, []).append((vid, m, r))
            g = abs(kin._wrap(r[3] - r[4])) if m.articulated else 0.0
            assert g <= kin.HITCH_MAX + 1e-6, (vid, r)
            if r[5] == -1:
                assert r[6] == "reverse"
            if r[6] == "reverse":
                assert r[5] == -1
            if prev is not None:
                dt = r[0] - prev[0]
                assert dt > 0
                step = math.hypot(r[1] - prev[1], r[2] - prev[2])
                assert step <= V_MAX * dt + 0.05, (vid, prev, r)
                dth = abs(kin._wrap(r[3] - prev[3]))
                lim = (V_MAX * math.tan(m.delta_max) / m.wheelbase) * dt + 0.02
                assert dth <= lim, (vid, prev[:5], r[:5], dth, lim)
                if m.articulated:
                    dth1 = abs(kin._wrap(r[4] - prev[4]))
                    lim1 = (V_MAX + m.hitch_offset * V_MAX * math.tan(m.delta_max) / m.wheelbase) / m.trailer_wheelbase * dt
                    assert dth1 <= lim1 + math.radians(1.65), (vid, prev[:5], r[:5])    # gear-change alignment
            prev = r
    # no two trucks overlap at any 0.2 s key
    for k, items in by_t.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a = _polys(items[i][1], items[i][2])
                b = _polys(items[j][1], items[j][2])
                assert not any(p.intersects(q) for p in a for q in b), (k * 0.2, items[i][0], items[j][0])
    if grid_check:      # no body sample inside a building / obstacle cell (dock opening carved at used doors)
        for vid, rows in tl.items():
            p = plans[vid]
            m = kin.model(p["key"])
            d = p["opt"]["door"]
            g = scene.grid.carve_slot((d["x"], d["y"]), d["normal"], max(d["width_m"], m.width))
            P = np.array([r[:5] for r in rows], float)
            sd, where, _ = pp.clearance(m, g, P, every=1)
            assert sd >= 0, (vid, sd, where)
    return by_t


def test_synthetic_five_trucks_concurrent_and_clear():
    res = _synthetic(125, 5)
    ids = [v["id"] for v in res["vehicles"]]
    assert len(set(ids)) == 5
    assert all(v["status"] == "done" for v in res["vehicles"]), [v.get("fail") for v in res["vehicles"]]
    by_t = _check_run(res)
    moving = max(sum(1 for _, _, r in items if r[6] not in ("docked",)) for items in by_t.values())
    assert moving >= 2
    assert res["summary"]["completed"] == 5 and res["summary"]["headline"]
    keys = res["vehicles"][0]["keys"]
    assert len(keys[0]) == 7 and keys[0][6] in plan.PHASES
    assert all(abs(b[0] - a[0] - 0.2) < 1e-6 for a, b in zip(keys, keys[1:]))


def test_synthetic_court_125_passes_90_fails_geometric():
    ok = _synthetic(125, 1)
    assert ok["vehicles"][0]["status"] == "done"
    assert not [i for i in ok["issues"] if i["class"] == "geometric"]
    bad = _synthetic(90, 1)
    assert bad["vehicles"][0]["status"] == "failed"
    assert bad["vehicles"][0]["fail"]["class"] == "geometric"
    assert any(i["class"] == "geometric" for i in bad["issues"])


def test_blocked_corridor_is_never_geometric():
    # a wall across the court between the gate and the doors: no way in
    res = _synthetic(125, 2, block=(-60.0, 0.0, -58.0, 200.0))
    v = res["vehicles"][0]
    assert v["status"] != "done"
    assert v["fail"]["class"] in ("planner", "congestion")
    assert not any(i["class"] == "geometric" for i in res["issues"] if i.get("trucks"))


def test_narrowed_corridor_yields_congestion_or_planner():
    # a 1-door court: trucks must take turns -> congestion, never geometric
    res = _synthetic(125, 3, n_doors=1)
    classes = {i["class"] for i in res["issues"]}
    assert "geometric" not in classes
    assert "congestion" in classes or any(v["status"] != "done" for v in res["vehicles"])
    _check_run(res)


def test_real_site_12896757_five_semis():
    from proinspect.config import BUILD
    if not (BUILD / "twins" / "exterior" / "12896757.json").exists():
        pytest.skip("no exterior twin")
    res = plan.maneuver({"property_id": 12896757, "trucks": [{"type": "semi_53", "count": 5}]}, internal=True)
    assert len({v["id"] for v in res["vehicles"]}) == 5
    assert res["summary"]["completed"] == 5, res["summary"]["headline"]
    assert "timing" in res["summary"]
    waited = [v for v in res["vehicles"] if any(k[6] == "wait" for k in v["keys"])]
    assert waited, "queued trucks are drawn waiting on the street stub"
    done = [v for v in res["vehicles"] if v["status"] == "done"]
    for v in res["vehicles"]:
        if v["status"] != "done":
            assert v["fail"]["class"] in ("planner", "congestion", "assumption", "geometric")
    if done:
        _check_run(res)


# ------------------------------------------------------------------ notches (multi-building flex parks, e.g. 242089)
def _notch_scene(width_m=25.0, depth_ft=200):
    sc = plan.synthetic_scene(depth_ft, n_doors=1)
    g = sc.grid
    k = g.kind.copy()
    R, C = np.mgrid[0:g.n, 0:g.n]
    X, Y = g.xy(R, C)
    h = width_m / 2
    for x0 in (-h - 3.0, h):                                   # two wings 3 m thick, 30 m deep
        k[(X >= x0) & (X <= x0 + 3.0) & (Y >= 0) & (Y <= 30.0)] = 1
    g = kin.YardGrid(k, g.half, g.res)
    door = {"id": "n1", "x": -h, "y": 12.0, "normal": [1.0, 0.0], "width_m": 9 * kin.FT, "kind": "dock_high",
            "status": "confirmed", "source": "confirmed"}
    sc2 = plan.Scene(g, g.kind == 0, None, [door], sc.gate, sc.lat0, sc.lon0, "confirmed", [], None)
    sc2._cg = plan.rt.CorridorGrid(g, g.kind == 0, None)
    return sc2


def test_van_in_narrow_notch():
    sc = _notch_scene()
    res = plan.maneuver({"trucks": [{"type": "last_mile_van", "count": 1}], "dwell_s": 30}, scene=sc, internal=True)
    v = res["vehicles"][0]
    assert v["status"] == "done", (v.get("fail"), res["_internal"]["door_res"])
    _check_run(res)


def test_gate_inside_notch_is_detected():
    from shapely.geometry import box

    class Y:
        footprints = [(0, box(-40, 0, -15, 30)), (1, box(15, 0, 40, 30)), (2, box(-40, -20, 40, 0))]
    assert plan._in_notch(Y, 0.0, 15.0)                       # between the two wings
    assert not plan._in_notch(Y, 0.0, 80.0)                   # out on the street
    assert not plan._in_notch(Y, 60.0, 15.0)                  # beside one wing only


def test_real_242089_gate_not_in_notch_and_vans_complete():
    from proinspect.config import BUILD
    if not (BUILD / "twins" / "exterior" / "242089.json").exists():
        pytest.skip("no exterior twin")
    sc = plan.scene_for(242089)
    from proinspect.yard import site
    yard = site.load(242089)
    assert not plan._in_notch(yard, sc._gates[0]["x"], sc._gates[0]["y"])
    res = plan.maneuver({"property_id": 242089, "trucks": [{"type": "last_mile_van", "count": 2}]}, internal=True)
    assert all(v["status"] == "done" for v in res["vehicles"])
    _check_run(res)


def test_real_9235035_mixed_fleet():
    from proinspect.config import BUILD
    if not (BUILD / "twins" / "exterior" / "9235035.json").exists():
        pytest.skip("no exterior twin")
    res = plan.maneuver({"property_id": 9235035, "trucks": [{"type": "semi_53", "count": 2},
                                                             {"type": "box_truck_26", "count": 1},
                                                             {"type": "last_mile_van", "count": 1}]}, internal=True)
    assert res["summary"]["completed"] >= 3, res["summary"]["headline"]
    for v in res["vehicles"]:
        if v["status"] != "done":
            assert v["fail"]["where"] and "planner" not in v["fail"]["text"] or "not proof" in v["fail"]["text"]
    _check_run(res)
