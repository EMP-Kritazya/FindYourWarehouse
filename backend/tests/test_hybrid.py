"""Hybrid A* (yard/hybrid.py) on synthetic yards: every pose is re-integrated with the kinematic model and checked."""
import math

import numpy as np
import pytest

from proinspect.yard import hybrid as hy
from proinspect.yard import kinematics as kin
from proinspect.yard import pursuit as pp


def _grid(free_fn, half=80.0, res=0.5):
    n = int(2 * half / res)
    k = np.ones((n, n), np.uint8) * 2
    g0 = kin.YardGrid(np.zeros((n, n), np.uint8), half, res)
    R, C = np.mgrid[0:n, 0:n]
    X, Y = g0.xy(R, C)
    k[free_fn(X, Y)] = 0
    return kin.YardGrid(k, half, res)


def _solve(key, grid, start, goal):
    m = kin.model(key)
    H = hy.Heuristic(grid, m, goal.seeds(), buffer=hy.prims(key)["rad"] - m.width / 2 + hy.BUFFER_M)
    r = hy.search(m, grid, [start], goal, H)
    assert r["ok"], r
    P = hy.integrate(m, start, r["seq"])
    T = hy.timed(P)
    sd, where, _ = pp.clearance(m, grid, T[:, :5], every=1)
    assert sd >= 0, (sd, where)
    if m.articulated:
        g = np.abs(np.arctan2(np.sin(T[:, 3] - T[:, 4]), np.cos(T[:, 3] - T[:, 4])))
        assert g.max() <= kin.HITCH_MAX + 1e-6
    step = np.hypot(*np.diff(T[:, 1:3], axis=0).T)
    assert step.max() <= pp.V_MAX * kin.DT + 0.05
    return r, T


def test_semi_L_turn():
    # a 16 m wide corridor going north then east
    grid = _grid(lambda X, Y: ((np.abs(X + 40) <= 8) & (Y <= 48) & (Y >= -70))
                 | ((np.abs(Y - 40) <= 8) & (X >= -48) & (X <= 70)))
    r, T = _solve("semi_53", grid, [-40, -40, math.pi / 2, math.pi / 2], hy.PointGoal(55, 40, 0.0, tol=3.0, dth=20))
    assert abs(kin._wrap(T[-1, 3])) < math.radians(25)


def test_semi_U_turn_around_building():
    # a ring road 16 m wide around a 60 x 40 m building
    def free(X, Y):
        outer = (np.abs(X) <= 46) & (np.abs(Y) <= 36)
        inner = (np.abs(X) <= 30) & (np.abs(Y) <= 20)
        return outer & ~inner
    grid = _grid(free)
    _solve("semi_53", grid, [-15, -28, 0.0, 0.0], hy.PointGoal(-15, 28, math.pi, tol=3.0, dth=25))


def test_van_into_narrow_notch_and_reverse_allowed():
    # a 25 m street with a 12 m deep, 9 m wide dead-end notch: the van may reverse (rigid trucks only)
    grid = _grid(lambda X, Y: ((np.abs(Y) <= 12.5) & (np.abs(X) <= 70)) | ((np.abs(X) <= 4.5) & (Y <= 24) & (Y >= 0)))
    r, T = _solve("last_mile_van", grid, [-50, 0, 0.0, 0.0], hy.PointGoal(0, 18, None, tol=2.0))
    assert (T[:, 1] > -60).all()


def test_semi_is_forward_only_and_exhaustion_is_reported():
    # a dead end the semi cannot turn in: the search must report exhaustion with a closest pose, not invent motion
    grid = _grid(lambda X, Y: (np.abs(Y) <= 5) & (X <= 60) & (X >= -60))
    m = kin.model("semi_53")
    goal = hy.PointGoal(-50, 0, math.pi, tol=2.0, dth=20)
    H = hy.Heuristic(grid, m, goal.seeds(), buffer=hy.prims("semi_53")["rad"] - m.width / 2 + hy.BUFFER_M)
    r = hy.search(m, grid, [[0, 0, 0.0, 0.0]], goal, H, budget=3000)
    assert not r["ok"] and r["explored"] > 0 and r["closest"] is not None
    assert (hy.prims("semi_53")["dirs"] > 0).all()


def test_primitives_respect_articulation_limit():
    T = hy.prims("semi_53")
    assert np.all(np.abs(T["end"][..., 3][T["ok"]]) <= math.radians(70) + 1e-6)


def test_property_bounds_are_hard_and_driveway_is_open():
    """Free ground outside the property becomes kind 5 (an obstacle); the gate sits on drivable ground."""
    import numpy as np
    import pytest
    from proinspect.config import BUILD
    if not (BUILD / "twins" / "exterior" / "9235035.npz").exists():
        pytest.skip("needs the exterior twin")
    from proinspect.yard import plan
    sc = plan.scene_for(9235035)
    g = sc.grid
    assert (g.kind == 5).any()
    r, c = g.rc(np.array([sc.gate["x"]]), np.array([sc.gate["y"]]))
    assert g.kind[r[0], c[0]] == 0
    assert any(n.startswith("trucks stay inside the property") for n in sc.notes)
