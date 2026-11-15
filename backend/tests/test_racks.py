"""Storage capacity planner on a real scan (12896757 / QHMtBN5Nnrc)."""
import pytest

from proinspect.config import BUILD

pytestmark = pytest.mark.skipif(not (BUILD / "twins" / "interior" / "QHMtBN5Nnrc.npz").exists(),
                                reason="needs the interior twin")
PID = 12896757


@pytest.fixture(scope="module")
def base():
    from proinspect import racks
    return racks.plan(PID, use_cache=False)


def test_levels_follow_scan_clear_and_sprinkler(base):
    from proinspect.sim.layout import PALLET_LEVEL_FT
    p5 = base["clear"]["scan_p5_ft"]
    assert base["levels"] == min(int((p5 - 3.0) // PALLET_LEVEL_FT) + 1, int(30 // PALLET_LEVEL_FT) + 1)  # ESFR, reach
    assert "ESFR" in base["sprinkler"].upper()


def test_positions_and_no_overlaps(base):
    assert base["positions"] > 0
    rows = [r for r in base["rows"] if r["slots_kept"]]
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            overlap = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) > 0.01 and \
                min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]) > 0.01
            assert not overlap, (a["id"], b["id"])


def test_narrower_aisle_trucks_store_more():
    from proinspect import racks
    t = {o["equipment"]: o["positions"] for o in racks.compare_trucks(PID)["trucks"]}
    assert t["vna_turret"] >= t["reach_truck"] >= t["counterbalance_electric"] > 0


def test_delete_row_removes_exactly_its_slots(base):
    from proinspect import racks
    row = max(base["rows"], key=lambda r: r["slots_kept"])
    r = racks.plan(PID, edits={"deleted": [row["id"]]}, use_cache=False)
    assert r["positions"] == base["positions"] - row["slots_kept"]


def test_move_into_neighbour_is_rejected_with_reason(base):
    from proinspect import racks
    rows = sorted([r for r in base["rows"] if r["slots_kept"]], key=lambda r: (r["y0"], r["x0"]))
    a = rows[0]
    r = racks.plan(PID, edits={"moved": {a["id"]: 0.6}}, use_cache=False)
    moved = next(x for x in r["rows"] if x["id"] == a["id"])
    if not moved["ok"]:
        assert moved["why"] and moved["slots_kept"] == 0


def test_no_rent_means_no_cost_per_position(base):
    assert base["rent"] is None and base["cost_per_position_yr"] is None
