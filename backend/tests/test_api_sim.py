"""Sim -> Matterport scene: frame round trips, scene contents, congestion instrumentation, API endpoints."""
import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from proinspect.sim import export as X
from proinspect.sim import site as site_mod
from proinspect.sim.run import simulate


def _write_scan(tmp_path, model_id, floor, origin=(-12.3, 4.5), theta=0.37):
    d = tmp_path / "twins" / "interior"
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(d / f"{model_id}.npz", floor=floor, free=floor, structure=np.zeros_like(floor),
                        origin=np.array(origin), theta=np.array([theta]), grid=np.array([0.25]))


def _l_shape(nx=160, ny=220):
    f = np.zeros((nx + 8, ny + 6), bool)            # padding so the crop offset is non-zero
    f[5:5 + nx, 3:3 + ny] = True
    f[5 + nx // 2:5 + nx, 3 + ny * 2 // 3:3 + ny] = False      # cut a corner: asymmetric
    return f


@pytest.fixture
def scan(tmp_path, monkeypatch):
    monkeypatch.setattr(site_mod, "BUILD", tmp_path)
    f = _l_shape()
    _write_scan(tmp_path, "TEST", f)
    return f


@pytest.mark.parametrize("side", ["S", "E", "N", "W"])
def test_sim_cells_map_back_to_the_same_scan_cells(scan, side):
    s = site_mod.from_interior("TEST", dock_side=side, clear_ft=30.0)
    tf = s.transform
    assert tf["rot90"] == {"S": 0, "E": 1, "N": 2, "W": 3}[side]
    rng = np.random.default_rng(0)
    a = rng.integers(0, s.nx, 400)
    b = rng.integers(0, s.ny, 400)
    # cell centres in the sim frame -> grid index -> must be the same floor value
    p, q = X.array_to_grid(a + 0.5 + tf["crop"][0], b + 0.5 + tf["crop"][1], tf["rot90"], tf["shape0"])
    i, j = np.floor(p).astype(int), np.floor(q).astype(int)
    assert (scan[i, j] == s.floor[a, b]).all()


@pytest.mark.parametrize("side", ["S", "E", "N", "W"])
def test_round_trip_sim_rot_mesh_showcase(scan, side):
    s = site_mod.from_interior("TEST", dock_side=side, clear_ft=30.0)
    tf = s.transform
    u, v = np.array([0.0, 3.3, 17.25, s.nx * 0.25]), np.array([0.0, 41.0, 2.5, s.ny * 0.25])
    x, y, z = X.sim_to_showcase(u, v, tf, floor_z=-0.03, h=1.0)
    assert np.allclose(y, 0.97)
    u2, v2 = X.showcase_to_sim(x, y, z, tf)
    assert np.allclose(u, u2) and np.allclose(v, v2)
    # rot frame: sim origin corner is a corner of the floor bbox in the npz grid
    xr, yr = X.sim_to_rot(u, v, tf)
    u3, v3 = X.rot_to_sim(xr, yr, tf)
    assert np.allclose(u, u3) and np.allclose(v, v3)
    # the mapping is a rigid motion: distances are preserved all the way to Showcase
    d_sim = math.hypot(u[1] - u[2], v[1] - v[2])
    d_sc = math.hypot(x[1] - x[2], z[1] - z[2])
    assert d_sim == pytest.approx(d_sc, abs=1e-9)


def test_model_to_showcase_is_proper_rotation():
    M = np.array([X.model_to_showcase(*e) for e in np.eye(3)]).T
    assert np.linalg.det(M) == pytest.approx(1.0)
    assert X.showcase_to_model(*X.model_to_showcase(1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)


def test_yaw_conventions():
    tf = {"crop": [0, 0], "shape0": [10, 10], "origin": [0.0, 0.0], "theta": 0.0, "grid": 0.25, "rot90": 0}
    # identity chain: sim +x = mesh +X = showcase +x; sim +y = mesh +Y = showcase -z
    assert X.sim_dir_to_showcase(1, 0, tf) == pytest.approx((1.0, 0.0))
    assert X.sim_dir_to_showcase(0, 1, tf) == pytest.approx((0.0, -1.0))
    assert X.box_yaw(tf) == pytest.approx(0.0)
    assert X.heading_yaw(1.0, 0.0) == pytest.approx(0.0)       # local +x already along +x
    # Stream A: rotation.y == atan2(dY, dX) in the Model API frame
    for dX, dY in ((1, 0), (0, 1), (-1, 1), (0.3, -2)):
        dx, _, dz = X.model_to_showcase(dX, dY, 0)
        assert X.heading_yaw(dx, dz) == pytest.approx(math.atan2(dY, dX))


@pytest.fixture(scope="module")
def sim_scene(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scan")
    mp = pytest.MonkeyPatch()
    mp.setattr(site_mod, "BUILD", tmp)
    f = np.zeros((200, 250), bool)
    f[4:196, 4:246] = True                                      # 48 x 60.5 m
    _write_scan(tmp, "RECT", f, origin=(5.0, -20.0), theta=1.2)
    s = site_mod.from_interior("RECT", dock_side="E", clear_ft=30.0)
    summ = simulate(s, "pallet_3pl", runs=1)
    sc = X.scene(s, summ, run_id="t", property_id=1, model_id="RECT", floor_z=-0.05)
    mp.undo()
    return s, summ, sc


def test_scene_contract_and_geometry(sim_scene):
    s, summ, sc = sim_scene
    json.dumps(sc)                                              # serialisable
    for k in ("run_id", "property_id", "model_id", "profile", "floor_y", "scanned_area", "racks", "lanes", "doors",
              "heat", "vehicles", "trucks", "duration_s", "kpi", "assumptions"):
        assert k in sc
    assert sc["floor_y"] == pytest.approx(-0.05)
    assert len(sc["doors"]) == len(s.doors) and sc["racks"] and sc["lanes"]
    # doors sit on the outline of the simulated floor
    from shapely.geometry import Point, Polygon
    outline = Polygon(sc["scanned_area"])
    for d in sc["doors"]:
        assert outline.exterior.distance(Point(d["x"], d["z"])) < 1.0
        # the door heading points out of the building
        ox, oz = d["x"] + 2 * math.cos(d["yaw"]), d["z"] - 2 * math.sin(d["yaw"])
        assert not outline.contains(Point(ox, oz))
    for r in sc["racks"]:
        assert outline.buffer(0.5).contains(Point(r["x"], r["z"]))
    for v in sc["vehicles"]:
        ts = [k[0] for k in v["keys"]]
        assert ts == sorted(ts)
        assert {k[4] for k in v["keys"]} <= {"empty", "loaded", "pick", "drop", "idle"}
        assert all(outline.buffer(0.5).contains(Point(k[1], k[2])) for k in v["keys"])
    assert all(0 <= c[2] <= 1 for c in sc["heat"]["cells"])
    assert all(t["depart_s"] is not None and t["depart_s"] <= sc["duration_s"] for t in sc["trucks"])


def test_congestion_instrumentation_matches_kpis(sim_scene):
    _, summ, _ = sim_scene
    tr = summ["example"]["trace"]
    assert sum(tr["node_reroutes"].values()) == summ["example"]["kpi"]["reroutes"]
    assert all(w >= 0 for w in tr["node_wait_s"].values())


def test_synthetic_site_cannot_be_exported():
    s = site_mod.synthetic(30, 40, doors=2)
    with pytest.raises(ValueError):
        X.scene(s, {"example": {"trace": {"nodes": []}}})


# ------------------------------------------------------------------ API
c = TestClient(__import__("proinspect.api", fromlist=["app"]).app)


def test_config_has_markets_and_never_other_secrets():
    r = c.get("/api/config").json()
    assert r["markets"] == ["Houston", "Los Angeles"]
    assert set(r) == {"matterport_sdk_key", "markets"}


def test_twin_endpoint():
    r = c.get("/api/warehouses/238885/twin").json()
    assert r["interior"]["model_id"] == "suXvWyjFWvZ"
    assert all("ray_detail" not in ct for ct in r["exterior"]["courts"])
    assert "positions" not in r["interior"]["columns"]
    assert r["media"]["ortho"].startswith("/media/twins/exterior/")
    assert c.get("/api/warehouses/1/twin").status_code == 404


def test_sim_api_errors_and_heroes():
    assert c.get("/api/sim/heroes").status_code == 200
    assert c.get("/api/sim/nope0000/scene").status_code == 404
    assert c.post("/api/sim/run", json={"property_id": 238885, "profile": "nope"}).status_code == 400
    assert c.post("/api/sim/run", json={"property_id": 1, "profile": "pallet_3pl"}).status_code == 404


def test_model_api_panos_fall_inside_the_scan_grid():
    """Mesh frame == pano frame in X/Y (Stream A only verified Z): QHMtBN5Nnrc panos lie inside the interior grid."""
    from proinspect.cache import cache_only
    from proinspect.config import BUILD
    from proinspect.tools.matterport import model_overview
    from proinspect.twin.interior import rotate_xy
    p = BUILD / "twins" / "interior" / "QHMtBN5Nnrc.npz"
    if not p.exists():
        pytest.skip("interior twin not built")
    try:
        with cache_only():
            ov = model_overview("QHMtBN5Nnrc")
    except Exception:
        pytest.skip("model overview not cached")
    g = np.load(p)
    P = np.array([[q["x"], q["y"], q["z"]] for q in ov["pano_positions"].values() if q])
    Q = rotate_xy(P, float(g["theta"][0]))[:, :2]
    lo = g["origin"] - 1.0
    hi = g["origin"] + np.array(g["floor"].shape) * float(g["grid"][0]) + 1.0
    assert ((Q >= lo) & (Q <= hi)).all()
