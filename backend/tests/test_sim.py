"""Golden tests for the shift simulation (docs/research/simulation.md §6 checklist)."""
import numpy as np
import pytest

from proinspect.library import profiles
from proinspect.sim.layout import generate
from proinspect.sim.shift import Shift
from proinspect.sim.site import synthetic
from proinspect.sim.traffic import TURN_S, LaneGraph

FLEET = profiles()["pallet_3pl"]["fleet"]


@pytest.fixture(scope="module")
def lay():
    return generate(synthetic(60, 90, doors=4), FLEET)


def run(lay, seed=1, fleet=None, scale=1.0):
    sh = Shift(lay, "pallet_3pl", seed=seed, fleet=fleet)
    return sh, sh.run(volume_scale=scale)


def test_same_seed_same_event_log(lay):
    a, b, c = run(lay, 7)[1], run(lay, 7)[1], run(lay, 8)[1]
    assert a["log_hash"] == b["log_hash"] and a["kpi"] == b["kpi"]
    assert a["log_hash"] != c["log_hash"]


def test_pallets_are_conserved_and_the_shift_completes(lay):
    sh, r = run(lay)
    k = r["kpi"]
    assert k["trucks_done"] == k["trucks_planned"]
    assert k["pallets_received"] == k["pallets_received_plan"]
    in_lanes = sum(sh.lane_count)
    assert k["pallets_putaway"] + in_lanes >= k["pallets_received"] - 1
    assert k["pallets_shipped"] == k["pallets_shipped_plan"]
    assert k["unreachable_tasks"] == 0


def test_free_flow_travel_matches_geometry():
    g = LaneGraph(np.ones((80, 80), bool))                 # 20 x 20 m open floor
    a, b = g.nearest(1, 1), g.nearest(16, 11)
    hops = abs(round((g.xy[b, 0] - g.xy[a, 0]) / 1.25)) + abs(round((g.xy[b, 1] - g.xy[a, 1]) / 1.25))
    t = g.path_time(a, b, 0.5)
    assert hops * 0.5 <= t <= hops * 0.5 + 2 * TURN_S        # Manhattan hops plus at most a couple of turns


def test_a_lone_vehicle_has_no_congestion(lay):
    fleet = {"counterbalance_electric": 1, "reach_truck": 1}
    r = run(lay, fleet=fleet, scale=0.3)[1]
    assert r["kpi"]["deadlock_breaks"] == 0
    rt = next(v for v in r["vehicles"] if v["kind"] == "reach_truck")
    assert rt["delay"] <= 0.05 * (rt["loaded"] + rt["empty"]) + 0.5


def test_congestion_rises_with_fleet_size(lay):
    small = run(lay, fleet={"counterbalance_electric": 2, "reach_truck": 1})[1]["kpi"]["congestion_delay_share"]
    big = run(lay, fleet={"counterbalance_electric": 2, "reach_truck": 6})[1]["kpi"]["congestion_delay_share"]
    assert big > small


def test_no_doors_means_nothing_ships():
    lay0 = generate(synthetic(60, 90, doors=0), FLEET)
    r = Shift(lay0, "pallet_3pl", seed=1).run()
    assert r["note"] and r["kpi"]["pallets_shipped"] == 0


def test_layout_racks_avoid_columns_and_are_reachable():
    site = synthetic(60, 90, doors=4, columns=(15.24, 15.85))
    lay = generate(site, FLEET)
    assert not (lay.rack & ~site.free).any()               # no rack on a column
    assert lay.checks["density_ok"] and lay.positions > 0
    sh = Shift(lay, "pallet_3pl", seed=1)
    rt = next(v for v in sh.vehicles if v.can_rack)
    h = sh.g.hops_from(sh.lane_spots[0]["back"][0], rt.min_clear)
    assert all(np.isfinite(h[s["node"]]) for s in sh.slots)


def test_emerging_unload_time_is_plausible(lay):
    r = run(lay)[1]
    assert 10 <= r["kpi"]["unload_53ft_min"] <= 60           # reference: ~35 min (equipment.yaml dock_turn_min)
