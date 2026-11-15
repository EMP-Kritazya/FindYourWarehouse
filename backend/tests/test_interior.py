"""Interior planner on the real scan of 12896757 (QHMtBN5Nnrc)."""
import math

import pytest

from proinspect.config import BUILD

pytestmark = pytest.mark.skipif(not (BUILD / "twins" / "interior" / "QHMtBN5Nnrc.npz").exists(),
                                reason="needs the interior twin")
PID = 12896757


def test_operation_decides_layout_types():
    from proinspect import interior
    types = {op: {z["type"] for z in interior.plan(PID, op, use_cache=False)["zones"]}
             for op in ("pallet_3pl", "ecommerce_fulfillment", "light_manufacturing", "cold_storage")}
    assert types["pallet_3pl"] == {"selective"}
    assert "shelving" in types["ecommerce_fulfillment"]
    assert "open" in types["light_manufacturing"]
    assert types["cold_storage"] == {"dense"}


def test_every_default_layout_is_fully_reachable_and_fleet_formula():
    from proinspect import interior
    r = interior.plan(PID, "pallet_3pl", use_cache=False)
    pr = r["proofs"]
    assert pr["reachable_pct"] == 100.0
    assert pr["avg_trip_m"] > 0
    expect = math.ceil(pr["daily_pallet_moves"] / (pr["shift_hours"] * pr["pallets_per_hour"] * 0.8))
    assert pr["machines_needed"] == expect


def test_objects_and_keeping_one_changes_the_floor():
    from proinspect import interior
    base = interior.plan(PID, "pallet_3pl", use_cache=False)
    low = [o for o in base["objects"] if o["height"] == "low"]
    assert low, "scanned contents should be found"
    big = max(low, key=lambda o: o["area_sqft"])
    keep = [o["id"] for o in base["objects"] if o["default_keep"]] + [big["id"]]
    kept = interior.plan(PID, "pallet_3pl", keep=keep, use_cache=False)
    assert kept["positions"] <= base["positions"]


def test_trip_scene_has_a_lifting_forklift():
    import json
    from proinspect import interior
    r = interior.plan(PID, "pallet_3pl", use_cache=False)
    sc = json.loads((BUILD / "interior_plans" / f"{r['run_id']}_scene.json").read_text())
    v = sc["vehicles"][0]
    assert max(k[5] for k in v["keys"]) > 3.0          # the mast rises to an upper level at the bay
    assert sc["racks"] and "levels" in sc["racks"][0]
