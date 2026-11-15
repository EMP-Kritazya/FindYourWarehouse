"""Pallet -> rack spec, load-aware levels and dimension provenance in the interior planner."""
import pytest

from proinspect import interior as I


def test_default_pallet_reproduces_the_standard_rack():
    s = I.pallet_spec(None)
    assert (s["frame_depth_in"], s["per_bay"], s["level_in"]) == (42.0, 2, 66.0)
    assert abs(s["bay_in"] / 12 - 8.25) < 0.01 and s["source"] == "assumed"


def test_euro_pallets_go_three_per_bay_and_user_input_is_marked_yours():
    s = I.pallet_spec({"preset": "euro_1200x800"})
    assert s["per_bay"] == 3 and s["width_in"] == 31.5 and s["source"] == "yours"
    with pytest.raises(ValueError):
        I.pallet_spec({"width_in": 10, "depth_in": 48, "load_in": 54})


@pytest.mark.parametrize("load_in", [54, 72])
def test_top_load_never_enters_the_sprinkler_zone(load_in):
    r = I.plan(12896757, "pallet_3pl", pallet={"preset": "gma_48x40", "width_in": 40, "depth_in": 48, "load_in": load_in})
    rack, b = r["dims"]["rack"], r["dims"]["building"]
    assert rack["top_load"]["value"] <= b["usable_height"]["value"] + 1e-6
    assert b["clear_height"]["source"] == "measured" and b["sprinkler_clearance"]["source"] == "assumed"
    assert rack["levels"]["source"] == "calculated" and r["dims"]["pallet"]["load_height"]["source"] == "yours"
    assert all(f["ok"] for f in r["feasibility"] if f["id"] in ("sprinkler", "lift", "levels"))


def test_taller_loads_mean_fewer_levels_and_positions():
    a = I.plan(12896757, "pallet_3pl")
    b = I.plan(12896757, "pallet_3pl", pallet={"preset": "gma_48x40", "width_in": 40, "depth_in": 48, "load_in": 72})
    assert b["levels"] < a["levels"] and b["positions"] < a["positions"]
    assert a["dims"]["pallet"]["width"]["source"] == "assumed"           # the default is never shown as measured
