"""Yard test (S3): kinematics golden tests, WB-67 sanity, queue sanity, API on the demo property."""
import math

import numpy as np
import pytest

from proinspect.yard import kinematics as k
from proinspect.yard import queue as yq

PITCH = 13 * k.FT          # neighbouring doors at 13 ft centres, occupied (design case)


def _fit(key, depth_ft):
    return k.fit(key, k.synthetic_court(depth_ft), (0.0, 0.0), (0.0, 1.0), 9 * k.FT, neighbors=(-PITCH, PITCH))


# ------------------------------------------------------------------ golden: court depth
def test_semi_fits_130ft_court():
    r = _fit("semi_53", 130)
    assert r["ok"] and r["margin_m"] >= 0 and not r["conflicts"]


def test_semi_fails_90ft_court():
    r = _fit("semi_53", 90)
    assert not r["ok"] and r["conflicts"]


def test_semi_needs_about_120ft_with_neighbours():
    """Industry guidance: 120-135 ft for a 53' trailer; our model needs ~120 ft with both next doors occupied."""
    assert not _fit("semi_53", 110)["ok"]
    assert _fit("semi_53", 125)["ok"]


def test_box_truck_fits_70ft_court():
    r = _fit("box_truck_26", 70)
    assert r["ok"], r["margin_m"]


def test_van_fits_50ft_court():
    assert _fit("last_mile_van", 50)["ok"]


# ------------------------------------------------------------------ WB-67 sanity
def test_wb67_dimensions():
    m = k.model("semi_53")
    assert m.overall_length / k.FT == pytest.approx(73.5, abs=0.05)
    assert m.width / k.FT == pytest.approx(8.5)
    assert (m.trailer_front + m.trailer_wheelbase + m.trailer_rear) / k.FT == pytest.approx(53.0)


def _full_lock_turn(m, angle=math.pi):
    s = [0.0, 0.0, math.pi / 2, math.pi / 2]
    poses = [[0.0, *s]]
    t = 0.0
    while s[2] - math.pi / 2 < angle:
        for _ in range(10):
            s = k.step(m, s, 1.0, m.delta_max, 0.01)
        t += 0.1
        poses.append([t, *s])
    return np.array(poses)


def test_wb67_outside_turning_radius_is_45ft():
    m = k.model("semi_53")
    P = _full_lock_turn(m)
    R = m.wheelbase / math.tan(m.delta_max)
    loc = k._body_samples(*m.bodies()["tractor"])
    th = P[:, 3]
    px = P[:, 1, None] + np.cos(th)[:, None] * loc[:, 0] - np.sin(th)[:, None] * loc[:, 1]
    py = P[:, 2, None] + np.sin(th)[:, None] * loc[:, 0] + np.cos(th)[:, None] * loc[:, 1]
    r = np.hypot(px + R, py)                                   # turn centre at (-R, 0)
    assert r.max() / k.FT == pytest.approx(45.0, abs=0.3)


def test_wb67_inside_radius_close_to_green_book():
    """AASHTO minimum inside radius for WB-67 is 1.9 ft (180 deg turn); the kinematic model gives < 3 ft."""
    m = k.model("semi_53")
    P = _full_lock_turn(m)
    R = m.wheelbase / math.tan(m.delta_max)
    pts = k.sample_points(m, P)
    assert np.hypot(pts[:, 0] + R, pts[:, 1]).min() / k.FT < 3.0


def test_trailer_axle_never_slides_sideways():
    m = k.model("semi_53")
    P = k.pullout(m, 3.0, 1, 1.0)
    kx, ky = P[:, 1] + m.hitch_offset * np.cos(P[:, 3]), P[:, 2] + m.hitch_offset * np.sin(P[:, 3])
    ax, ay = kx - m.trailer_wheelbase * np.cos(P[:, 4]), ky - m.trailer_wheelbase * np.sin(P[:, 4])
    vx, vy, th = np.diff(ax), np.diff(ay), P[:-1, 4]
    lateral = -vx * np.sin(th) + vy * np.cos(th)
    assert np.abs(lateral).max() < 1e-3


def test_backin_is_time_reversed_pullout_ending_at_door():
    r = _fit("semi_53", 130)
    P = r["poses"]
    assert P[0, 0] == 0 and np.all(np.diff(P[:, 0]) > 0)
    end = P[-1]                                                  # docked: trailer square to the wall
    assert abs(math.sin(end[4] - math.pi / 2)) < 1e-6 or abs(end[4] - math.pi / 2) < 1e-6


# ------------------------------------------------------------------ queue
def _q(n, doors, dock=60, fits=True, yard=1000.0):
    ds = [f"d{i}" for i in range(doors)]
    ok = {(d, "semi_53"): fits for d in ds}
    svc = {(d, "semi_53"): (60.0, 90.0, 60.0) for d in ds} | {"dock_min": {"semi_53": dock}}
    return yq.simulate([{"type": "semi_53", "per_day": n}], [6, 8], ds, ok, svc, yard, runs=5)


def test_queue_light_load_no_wait():
    q = _q(2, 4)
    assert q["wait_p90_min"] == 0 and q["max_on_street"] == 0


def test_queue_overload_spills_to_street():
    q = _q(30, 2, yard=250.0)            # room for 2 waiting rigs
    assert q["wait_p50_min"] > 30 and q["max_on_street"] > 0 and q["max_in_yard"] <= 2


def test_queue_no_fitting_door_is_unserved():
    q = _q(5, 2, fits=False)
    assert q["unserved_per_day"] == 5 and q["wait_p50_min"] is None


def test_queue_is_deterministic():
    assert _q(12, 2)["wait_p90_min"] == _q(12, 2)["wait_p90_min"]


# ------------------------------------------------------------------ API on the demo property (cached twin)
def test_api_yard_demo():
    from proinspect.config import BUILD
    if not (BUILD / "twins" / "exterior" / "12896757.npz").exists():
        pytest.skip("no exterior twin for the demo property")
    from fastapi.testclient import TestClient
    from proinspect.api import app
    c = TestClient(app)
    body = {"property_id": 12896757, "trucks": [{"type": "semi_53", "per_day": 12},
                                                {"type": "box_truck_26", "per_day": 8}], "hours": [6, 16]}
    r = c.post("/api/yard/test", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    for key in ("run_id", "doors", "fits", "queue", "vehicles", "frame", "assumptions"):
        assert key in j
    assert j["doors"] and all("source" in d and len(d["lonlat"]) == 2 for d in j["doors"])
    f = j["fits"][0]
    assert f["swept"]["type"] in ("Polygon", "MultiPolygon") and len(f["path"][0]) >= 4
    q = j["queue"]
    for key in ("max_in_yard", "max_on_street", "wait_p50_min", "wait_p90_min", "door_utilization", "timeline"):
        assert key in q
    lon, lat = j["doors"][0]["lonlat"]
    assert abs(lat - 29.597) < 0.01 and abs(lon + 95.373) < 0.01
    if j["door_source"] == "assumed":
        assert any("door positions assumed (no marked doors)" in a for a in j["assumptions"])
    assert c.get(f"/api/yard/{j['run_id']}").json()["run_id"] == j["run_id"]
    assert c.post("/api/yard/test", json={"property_id": 12896757, "trucks": [{"type": "jet", "per_day": 1}]}).status_code == 422
