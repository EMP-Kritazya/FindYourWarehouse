"""Door store: validation, round trip, outward normal, conversion into a sim Site frame, API."""
import math

import numpy as np
import pytest

from proinspect import doors as D
from proinspect.sim import export as X
from proinspect.sim import site as site_mod

THETA = 0.37
ORIGIN = (-12.3, 4.5)


def _rot_to_showcase(x, y):
    """Scan rot-frame metres -> Showcase (x, z) at floor height."""
    Xm, Ym = X.rot_to_mesh(x, y, THETA)
    sx, _, sz = X.model_to_showcase(float(Xm), float(Ym), 0.0)
    return sx, sz


@pytest.fixture
def scan(tmp_path, monkeypatch):
    for mod in (D, site_mod):
        monkeypatch.setattr(mod, "BUILD", tmp_path)
    d = tmp_path / "twins" / "interior"
    d.mkdir(parents=True)
    f = np.zeros((168, 226), bool)                 # floor: rot x in [-11.05, 28.95], y in [5.25, 59.25]
    f[3:163, 3:219] = True
    np.savez_compressed(d / "TEST.npz", floor=f, free=f, structure=np.zeros_like(f), origin=np.array(ORIGIN),
                        theta=np.array([THETA]), grid=np.array([0.25]))
    (d / "TEST.json").write_text('{"clear": {"p5_ft": 30.0}, "deck_ft": 32, "floor": {"z0": 0.0}}')
    return f


def _door_on_low_y_wall(x_mid, width=3.0, kind="dock_high"):
    y = ORIGIN[1] + 3 * 0.25                        # the low-y wall line of the floor
    ax, az = _rot_to_showcase(x_mid - width / 2, y)
    bx, bz = _rot_to_showcase(x_mid + width / 2, y)
    return {"kind": kind, "a": {"x": ax, "y": 1.5, "z": az}, "b": {"x": bx, "y": 1.6, "z": bz}}


def test_round_trip_and_derived_fields(scan):
    rec = D.save("TEST", 123, [_door_on_low_y_wall(0.0), _door_on_low_y_wall(6.0, 3.6, "drive_in")])
    back = D.load("TEST")
    assert back == rec
    assert [d["id"] for d in back["doors"]] == ["d1", "d2"]
    d1, d2 = back["doors"]
    assert d1["width_m"] == pytest.approx(3.0, abs=0.01) and d2["width_m"] == pytest.approx(3.6, abs=0.01)
    assert d1["height_m"] is None and d1["source"] == "marked_in_scan"
    # outward normal = towards -y in the rot frame (away from the floor)
    x0, y0 = D.showcase_to_rot(d1["center"]["x"], d1["center"]["z"], THETA)
    x1, y1 = D.showcase_to_rot(d1["center"]["x"] + d1["normal"]["x"], d1["center"]["z"] + d1["normal"]["z"], THETA)
    assert y1 - y0 == pytest.approx(-1.0, abs=1e-3) and abs(x1 - x0) < 1e-3
    assert d1["normal_basis"] == "away from the scanned floor"
    # ids and marked_at survive a re-save
    rec2 = D.save("TEST", 123, back["doors"])
    assert [d["marked_at"] for d in rec2["doors"]] == [d["marked_at"] for d in back["doors"]]
    assert D.dock_side("TEST") == "S"


@pytest.mark.parametrize("bad, msg", [
    (dict(width=0.6), "outside"),
    (dict(width=7.5), "outside"),
])
def test_rejects_bad_widths(scan, bad, msg):
    with pytest.raises(D.DoorError, match=msg):
        D.save("TEST", 1, [_door_on_low_y_wall(0.0, **bad)])


def test_rejects_points_off_the_wall_or_diagonal(scan):
    ax, az = _rot_to_showcase(0.0, 30.0)                # middle of the floor
    bx, bz = _rot_to_showcase(3.0, 30.0)
    with pytest.raises(D.DoorError, match="from any wall"):
        D.save("TEST", 1, [{"kind": "dock_high", "a": {"x": ax, "y": 1, "z": az}, "b": {"x": bx, "y": 1, "z": bz}}])
    ax, az = _rot_to_showcase(0.0, 5.25)
    bx, bz = _rot_to_showcase(2.0, 7.25)                # 45 deg
    with pytest.raises(D.DoorError, match="off the building's wall"):
        D.save("TEST", 1, [{"kind": "dock_high", "a": {"x": ax, "y": 1, "z": az}, "b": {"x": bx, "y": 1, "z": bz}}])
    with pytest.raises(D.DoorError, match="kind"):
        D.save("TEST", 1, [{**_door_on_low_y_wall(0.0), "kind": "garage"}])


@pytest.mark.parametrize("side", ["S", "E", "N", "W"])
def test_to_sim_against_site_transform(scan, side):
    D.save("TEST", 1, [_door_on_low_y_wall(0.0, 3.0), _door_on_low_y_wall(8.0, 4.0, "drive_in")])
    s = site_mod.from_interior("TEST", doors=[], dock_side=side, clear_ft=30.0)
    sd = D.to_sim("TEST", s)
    assert len(sd) == 2 and sd[0].id == "d1" and sd[1].kind == "drive_in"
    assert sd[0].width_m == pytest.approx(3.0, abs=0.01)
    # the centre maps back to the same Showcase point
    for d, rec in zip(sd, D.load("TEST")["doors"]):
        x, _, z = X.sim_to_showcase(d.u, d.v, s.transform, 0.0)
        assert float(x) == pytest.approx(rec["center"]["x"], abs=1e-6)
        assert float(z) == pytest.approx(rec["center"]["z"], abs=1e-6)
        assert math.hypot(d.nu, d.nv) == pytest.approx(1.0)
    # with the marked wall as dock wall, doors sit on the low-y wall and face out of it
    if side == "S":
        assert all(d.on_dock_wall for d in sd)
        assert sd[0].v == pytest.approx(s.wall_y, abs=0.3)
        assert sd[1].x - sd[0].x == pytest.approx(8.0, abs=0.01)
    else:
        assert not any(d.on_dock_wall for d in sd)


def test_api_put_get_delete(scan, monkeypatch):
    from fastapi.testclient import TestClient
    import proinspect.api_sim as api_sim
    from proinspect.api import app
    monkeypatch.setattr(api_sim, "pick_model", lambda pid, m=None: "TEST")
    c = TestClient(app)
    assert c.get("/api/warehouses/5/doors").json()["doors"] == []
    r = c.put("/api/warehouses/5/doors", json={"doors": [{**_door_on_low_y_wall(0.0), "source": "dev_fixture"}]})
    assert r.status_code == 200, r.text
    assert c.get("/api/warehouses/5/doors").json()["doors"][0]["source"] == "dev_fixture"
    r = c.put("/api/warehouses/5/doors", json={"doors": [_door_on_low_y_wall(0.0, 9.0)]})
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    assert c.delete("/api/warehouses/5/doors").json()["doors"] == []
    assert D.load("TEST") is None


def test_to_enu_resolves_flip_with_marked_dock_doors_real_scan():
    """QHMtBN5Nnrc / 12896757: the registration is a 180-deg tie; doors marked on the scan's high-z wall (the real
    dock doors, verified in the walk view) must land on the footprint's SOUTH facade, where the truck court is."""
    from proinspect.config import BUILD
    if not ((BUILD / "twins" / "exterior" / "12896757.npz").exists()
            and (BUILD / "twins" / "interior" / "QHMtBN5Nnrc.npz").exists()):
        pytest.skip("demo twins not built")
    raw = [{"kind": "dock_high", "a": {"x": -13.307, "y": 1.43, "z": 64.3}, "b": {"x": -16.061, "y": 1.43, "z": 64.313}},
           {"kind": "dock_high", "a": {"x": -17.54, "y": 1.43, "z": 64.321}, "b": {"x": -20.295, "y": 1.43, "z": 64.334}},
           {"kind": "dock_high", "a": {"x": -21.874, "y": 1.43, "z": 64.308}, "b": {"x": -24.628, "y": 1.43, "z": 64.322}}]
    ds = [{**D.normalise("QHMtBN5Nnrc", d), "id": f"d{i + 1}"} for i, d in enumerate(raw)]
    out, why = D.to_enu_report("QHMtBN5Nnrc", 12896757, doors=ds)
    assert out is not None, why
    assert all(d["facing"] == "S" and d["normal"]["y"] < -0.9 for d in out)
    reg = out[0]["registration"]
    assert reg["court_depth_m"] > reg["runner_up_court_depth_m"] * 1.2
    # the doors sit on the footprint outline (within a few metres)
    import json
    from shapely.geometry import Point, Polygon
    fp = Polygon(json.loads((BUILD / "twins" / "exterior" / "12896757.json").read_text())["buildings"][0]["footprint_local"])
    assert all(fp.exterior.distance(Point(d["x"], d["y"])) < 4.0 for d in out)
