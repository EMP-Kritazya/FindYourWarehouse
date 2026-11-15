"""Equipment test golden cases on synthetic floors (sim frame, 25 cm cells) + the Showcase wrapper and API."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from proinspect.fit import core as C

CELL = 0.25
FT = 0.3048


def _door(u=20.0, v=0.0, width=3.0, height=None, did="d1"):
    return SimpleNamespace(id=did, u=u, v=v, nu=0.0, nv=-1.0, width_m=width, height_m=height, kind="dock_high")


def _rack_floor():
    """40 x 30 m building; open dock zone y < 12 m; racking y >= 12 m with one 9 ft (11-cell) aisle at x = 18.5-21.25."""
    floor = np.ones((160, 120), bool)
    free = floor.copy()
    free[:, 48:] = False
    free[74:85, 48:] = True                     # 11 cells = 2.75 m = 9.02 ft aisle
    return C.Floor(free, floor, CELL)


def _get(res, kind, ok=None):
    cs = [c for c in res["checks"] if c["kind"] == kind and (ok is None or c["ok"] is ok)]
    assert cs, f"no {kind} check in {[c['kind'] for c in res['checks']]}"
    return cs[0]


def test_nine_foot_aisle_passes_reach_truck():
    res = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(19.9, 27.0), doors=[_door()],
                     from_door=_door(), clear_ft=36.0)
    assert res["strict"] and C.verdict(res["checks"]) is True
    a = _get(res, "aisle")
    assert a["ok"] and a["need"] == 9.0 and a["have"] == pytest.approx(9.0, abs=0.1)
    assert res["path_uv"][-1][1] > 25 and res["path_uv"][0][1] < 2


def test_nine_foot_aisle_fails_counterbalance_needing_12_5_ft():
    res = C.evaluate(_rack_floor(), C.vehicle("counterbalance_lpg"), target=(19.9, 27.0), doors=[_door()],
                     from_door=_door(), clear_ft=36.0)
    assert C.verdict(res["checks"]) is False
    a = _get(res, "aisle")
    assert a["ok"] is False and a["need"] == 12.5 and a["have"] == pytest.approx(9.0, abs=0.1)
    assert _get(res, "corridor")["ok"] is True                    # it can drive in, it can't work there
    pin = [p for p in res["pinch_points"] if p.get("what") == "aisle"][0]
    assert pin["need_m"] == pytest.approx(12.5 * FT, abs=0.01) and pin["width_m"] == pytest.approx(2.75)


def test_three_metre_door_fails_a_3_5_m_wide_vehicle():
    wide = {"length": 20, "width": 3.5 / FT, "height_lowered": 10, "name": "wide"}
    res = C.evaluate(_rack_floor(), C.vehicle(wide), target=(20.0, 6.0), doors=[_door()], from_door=_door(),
                     clear_ft=36.0)
    d = [c for c in res["checks"] if c["kind"] == "door" and "width" in c["label"]][0]
    assert d["ok"] is False and d["have"] == pytest.approx(9.8, abs=0.1) and d["need"] > 11.4
    h = [c for c in res["checks"] if c["kind"] == "door" and "height" in c["label"]][0]
    assert h["ok"] is None and h["have"] is None                 # height not marked -> '?'
    assert C.verdict(res["checks"]) is False
    narrow = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(20.0, 6.0), doors=[_door(height=4.0)],
                        from_door=_door(height=4.0), clear_ft=36.0)
    assert all(c["ok"] for c in narrow["checks"] if c["kind"] == "door")


def test_low_clear_height_fails_lift_and_very_low_fails_travel():
    res = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(19.9, 27.0), from_door=_door(), clear_ft=20.0)
    lift = _get(res, "lift")
    assert lift["ok"] is False and lift["need"] == 32.0 and lift["have"] == 20.0
    assert _get(res, "clear_height")["ok"] is True
    assert C.verdict(res["checks"]) is True                       # fits and moves; the lift is a limit, not a block
    low = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(19.9, 27.0), from_door=_door(), clear_ft=8.0)
    assert _get(low, "clear_height")["ok"] is False and C.verdict(low["checks"]) is False
    unk = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(19.9, 27.0), from_door=_door(), clear_ft=None)
    assert _get(unk, "clear_height")["ok"] is None


def _l_corridor(width_cells=9):
    floor = np.ones((160, 100), bool)
    free = np.zeros_like(floor)
    free[40:40 + width_cells, 0:80 + width_cells] = True         # vertical leg
    free[40:140, 80:80 + width_cells] = True                     # horizontal leg
    return C.Floor(free, floor, CELL)


def test_tight_corner_fails_turn_for_counterbalance_passes_pallet_jack():
    F = _l_corridor(9)                                           # 2.25 m = 7.4 ft legs
    cb = C.evaluate(F, C.vehicle("counterbalance_lpg"), start=(11.1, 1.0), target=(33.0, 21.1), clear_ft=30)
    t = _get(cb, "turn", ok=False)
    assert t["need"] == pytest.approx((7.5 * FT + 0.3) / FT, abs=0.1)
    u, v = t["at"]
    assert math.hypot(u - 11.1, v - 21.1) < 2.5                   # at the corner
    assert not cb["strict"] and C.verdict(cb["checks"]) is False
    pj = C.evaluate(F, C.vehicle("pallet_jack_electric"), start=(11.1, 1.0), target=(33.0, 21.1), clear_ft=30)
    assert pj["strict"] and all(c["ok"] for c in pj["checks"] if c["kind"] == "turn")


def test_narrow_gap_blocks_route_and_cuts_off_floor():
    floor = np.ones((160, 120), bool)
    free = floor.copy()
    free[:, 60:64] = False                                        # a wall across the building at y = 15-16 m
    free[78:84, 60:64] = True                                     # with a 1.5 m gap
    F = C.Floor(free, floor, CELL)
    cb = C.vehicle("counterbalance_lpg")                          # needs 4 ft + 2 x 0.3 m = 1.82 m
    res = C.evaluate(F, cb, start=(20.0, 5.0), target=(20.0, 25.0), clear_ft=30)
    corr = _get(res, "corridor", ok=False)
    assert corr["have"] == pytest.approx(1.5 / FT, abs=0.1)
    assert corr["at"][1] == pytest.approx(15.5, abs=1.0)
    auto = C.evaluate(F, cb, start=(20.0, 5.0), auto=True, clear_ft=30)
    assert 40 < auto["reachable_pct"] < 55
    assert auto["pinch_points"] and auto["pinch_points"][0]["width_m"] == pytest.approx(1.5, abs=0.3)
    amr = C.evaluate(F, C.vehicle("amr_goods_to_person"), start=(20.0, 5.0), auto=True, clear_ft=30)
    assert amr["reachable_pct"] > 90                              # a small robot gets through


def test_custom_dims_validation():
    with pytest.raises(C.FitError):
        C.vehicle({"length": 8})
    with pytest.raises(C.FitError):
        C.vehicle("hovercraft")
    v = C.vehicle({"length": 8, "width": 4, "height_lowered": 7, "turn_radius": 6, "min_aisle": 10})
    assert v.width_m == pytest.approx(4 * FT) and v.min_aisle_m == pytest.approx(10 * FT)


def test_wrapper_and_api_on_a_scan(tmp_path, monkeypatch):
    """End to end on a synthetic scan: door store -> sim frame -> fit -> Showcase coordinates."""
    from fastapi.testclient import TestClient

    import proinspect.api_sim as api_sim
    import proinspect.fit as fit
    from proinspect import doors as D
    from proinspect.sim import export as X
    from proinspect.sim import site as site_mod
    for mod in (D, site_mod, fit):
        monkeypatch.setattr(mod, "BUILD", tmp_path)
    monkeypatch.setattr(fit, "FIT_DIR", tmp_path / "fit")
    monkeypatch.setattr(api_sim, "pick_model", lambda pid, m=None: "TEST")
    theta, origin = 0.37, (-12.3, 4.5)
    d = tmp_path / "twins" / "interior"
    d.mkdir(parents=True)
    f = np.zeros((168, 130), bool)
    f[4:164, 4:124] = True
    free = f.copy()
    free[4:164, 52:124] = False
    free[78:89, 52:124] = True                                   # 9 ft aisle, rot x ~ [7.2, 9.95]
    np.savez_compressed(d / "TEST.npz", floor=f, free=free, structure=np.zeros_like(f), origin=np.array(origin),
                        theta=np.array([theta]), grid=np.array([0.25]))
    (d / "TEST.json").write_text('{"clear": {"p5_ft": 30.0}, "deck_ft": 33, "floor": {"z0": -0.03}}')

    def sc(x, y):
        Xm, Ym = X.rot_to_mesh(x, y, theta)
        return float(Xm), float(-Ym)
    yw = origin[1] + 4 * 0.25
    ax, az = sc(7.0, yw)
    bx, bz = sc(10.0, yw)
    c = TestClient(__import__("proinspect.api", fromlist=["app"]).app)
    r = c.put("/api/warehouses/1/doors", json={"doors": [{"kind": "dock_high", "a": {"x": ax, "y": 1, "z": az},
                                                          "b": {"x": bx, "y": 1, "z": bz}, "source": "dev_fixture"}]})
    assert r.status_code == 200, r.text
    tx, tz = sc(8.6, origin[1] + 29.0)
    for eq, want in (("reach_truck", True), ("counterbalance_lpg", False)):
        r = c.post("/api/fit/test", json={"property_id": 1, "equipment": eq, "floor": "as_scanned",
                                          "route": {"from_door": "d1", "to": {"x": tx, "z": tz}}})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is want, j["verdict"]
        assert len(j["path"]) >= 2 and all(len(k) == 4 for k in j["path"])
        assert math.hypot(j["path"][-1][1] - tx, j["path"][-1][2] - tz) < 1.0
        assert math.hypot(j["path"][0][1] - (ax + bx) / 2, j["path"][0][2] - (az + bz) / 2) < 2.0
    assert "stacking aisle" in j["pinch_points"][-1]["label"] and "12.5 ft" in j["pinch_points"][-1]["label"]
    # cached + retrievable
    assert c.get(f"/api/fit/{j['run_id']}").json()["run_id"] == j["run_id"]
    # emptied floor removes today's contents: the counterbalance gets room to work
    r = c.post("/api/fit/test", json={"property_id": 1, "equipment": "counterbalance_lpg", "floor": "emptied",
                                      "route": {"from_door": "d1", "to": {"x": tx, "z": tz}}})
    assert r.json()["ok"] is True
    auto = c.post("/api/fit/test", json={"property_id": 1, "equipment": "reach_truck", "route": {"to": "auto"}}).json()
    assert auto["heat"]["cells"] and 0 < auto["reachable_pct"] <= 100
    assert c.post("/api/fit/test", json={"property_id": 1, "equipment": "nope"}).status_code == 422


def test_unknown_door_height_is_never_a_fail(tmp_path):
    res = C.evaluate(_rack_floor(), C.vehicle("reach_truck"), target=(19.9, 27.0), doors=[_door()],
                     from_door=_door(), clear_ft=36.0)
    h = [c for c in res["checks"] if c["kind"] == "door" and "height" in c["label"]][0]
    assert h["ok"] is None and h["have"] is None and "not marked" in h["note"]
    assert not any(c["ok"] is False for c in res["checks"])
    assert C.verdict(res["checks"]) is True
