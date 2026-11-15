import numpy as np
from shapely.geometry import box

from proinspect.twin.exterior import RES, Frame, _classify, _court, _edges


def test_edges_have_outward_normals_and_facing():
    e = {x["facing"]: x for x in _edges(box(0, 0, 40, 20))}
    assert set(e) == {"N", "E", "S", "W"}
    assert e["N"]["n"] == (0.0, 1.0) or np.allclose(e["N"]["n"], (0, 1))
    assert np.allclose(e["W"]["n"], (-1, 0))


def test_door_classification_cues():
    assert _classify(2.7, 3.0, None)[0] == "dock_high"          # 9 x 10 ft
    assert _classify(4.3, 4.6, None)[0] == "drive_in"           # 14 x 15 ft
    assert _classify(2.7, 3.0, 1.1)[0] == "dock_high"           # plus a truck well
    assert _classify(4.3, 4.6, 1.1)[0] == "uncertain"           # cues disagree -> say so
    assert _classify(3.3, 2.0, None)[0] == "uncertain"          # no decisive cue


def synthetic_site(court_m, curb="landscape"):
    """Building 40 x 20 m at the origin facing south onto a paved court `court_m` deep, then grass or a wall."""
    fr = Frame(29.8, -95.4, 100)
    n = fr.n
    paved = np.ones((n, n), bool)
    nd = np.zeros((n, n), np.float32)
    ys = 100 - (np.arange(n) + 0.5) * RES
    edge_y = -10 - court_m                                       # building's south face is at y = -10
    rows = ys < edge_y
    if curb == "landscape":
        paved[rows, :] = False
    else:
        nd[(ys < edge_y) & (ys > edge_y - 1), :] = 2.0           # a 2 m wall
    vehicle = np.zeros((n, n), bool)
    others = np.zeros((n, n), bool)
    e = next(x for x in _edges(box(-20, -10, 20, 10)) if x["facing"] == "S")
    doors = [{"edge": 0, "s_m": 10.0}, {"edge": 0, "s_m": 30.0}]
    return fr, e, doors, nd, paved, vehicle, others


def test_court_depth_to_landscaping():
    fr, e, doors, nd, paved, veh, oth = synthetic_site(41.1)      # 135 ft
    c = _court(fr, e, doors, nd, paved, veh, oth, None)
    assert abs(c["depth_ft"] - 135) <= 3 and c["stops"] == {"landscape": c["rays"]}
    assert c["verdict_53ft"].startswith("full")


def test_court_depth_to_wall_and_trailers_are_transparent():
    fr, e, doors, nd, paved, veh, oth = synthetic_site(33.5, curb="wall")   # 110 ft
    r, c0 = fr.rc(0, -20)
    nd[r - 4:r + 4, :] = 4.0                                     # a parked trailer 10 m out...
    veh[r - 4:r + 4, :] = True                                   # ...is a vehicle, not a wall
    c = _court(fr, e, doors, nd, paved, veh, oth, None)
    assert abs(c["depth_ft"] - 110) <= 3 and set(c["stops"]) == {"obstacle"}


def test_dock_canopy_near_wall_does_not_stop_the_court():
    fr, e, doors, nd, paved, veh, oth = synthetic_site(39.6)
    r, _ = fr.rc(0, -12)
    nd[r - 2:r + 2, :] = 4.5                                     # canopy 2 m out from the wall
    c = _court(fr, e, doors, nd, paved, veh, oth, None)
    assert abs(c["depth_ft"] - 130) <= 3
