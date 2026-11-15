"""S4 analytics (CONTRACTS_v3 §2): tiers never turn unknown into 0, criteria differ by operation, winner logic."""
import pytest
from fastapi.testclient import TestClient

from proinspect import analytics as A
from proinspect.api import app

client = TestClient(app)


def _v(i, value, kind="verified", lo=None, hi=None, status=None):
    if kind == "verified":
        lo = hi = value
    return {"id": i, "value": value, "lo": lo, "hi": hi, "kind": kind, "status": status or kind}


# ------------------------------------------------------------------ rank_attribute
def test_null_docks_never_verified_nor_zero():
    r = A.rank_attribute("docks_total", top=200)
    ver = {x["id"]: x for x in r["verified"]}
    for w in A.catalog():
        known = w.get("docks_external") is not None or w.get("docks_internal") is not None
        if w["id"] in ver and not known:
            # only allowed: CoStar's explicit "no loading docks" (a statement, not a missing field) or a person
            assert w.get("has_docks") is False or ver[w["id"]]["source"] == "confirmed by a person", w["id"]
        if w.get("has_docks") is None and not known:
            assert w["id"] not in ver, w["id"]
    for x in r["estimated"]:
        assert x["value"] is None or x["value"] > 0 or x["lo"] > 0       # a lower bound is never "0 docks"
    for x in r["unknown"]:
        assert "value" not in x
    n = r["coverage"]
    assert n["verified"] + n["estimated"] + n["unknown"] == len(A.catalog())


def test_docks_ranked_desc_in_tiers_and_unknown_largest_first():
    r = A.rank_attribute("highest number of loading docks", market="Houston", top=200)
    assert r["attr"] == "docks_total" and r["order"] == "desc"
    vals = [x["value"] for x in r["verified"]]
    assert vals == sorted(vals, reverse=True)
    assert all(x["market"] == "Houston" for x in r["verified"] + r["estimated"] + r["unknown"])
    sizes = [x["size_sqft"] or 0 for x in r["unknown"]]
    assert sizes == sorted(sizes, reverse=True)


@pytest.mark.parametrize("attr", sorted(A.ATTRS))
def test_every_attr_has_tiers(attr):
    r = A.rank_attribute(attr, top=5)
    assert set(r["coverage"]) == {"verified", "estimated", "unknown"}
    for x in r["verified"]:
        assert x["value"] is not None


def test_asking_rent_lowest_first_with_service_type():
    r = A.rank_attribute("cheapest rent")
    assert r["attr"] == "asking_rent" and r["order"] == "asc"
    vals = [x["value"] for x in r["verified"]]
    assert vals == sorted(vals) and all(v > 0 for v in vals)
    assert all("service type" in x["basis"] or "triple net" in x["basis"] for x in r["verified"])


def test_rank_route():
    r = client.get("/api/rank/attribute", params={"attr": "clear_height_ft", "market": "Los Angeles", "top": 3})
    assert r.status_code == 200
    j = r.json()
    assert len(j["verified"]) <= 3 and j["market"] == "Los Angeles"
    assert client.get("/api/rank/attribute", params={"attr": "zzz"}).status_code == 400


# ------------------------------------------------------------------ criteria
def test_cross_dock_and_pallet_storage_criteria_differ():
    a = A.select_criteria("cross_dock")
    b = A.select_criteria("pallet_storage")
    ka, kb = [c["key"] for c in a["criteria"]], [c["key"] for c in b["criteria"]]
    assert ka != kb
    assert "cross_dock_config" in ka and "dock_turn_capacity_est" in ka and "cross_dock_config" not in kb
    assert ka[0] == "docks_per_10ksf" and kb[0] == "clear_height_ft"


def test_every_operation_has_5_to_7_criteria_with_why():
    from proinspect.library import profiles
    ops = A.library()["operations"]
    for k in list(profiles()) + ["cross_dock", "pallet_storage", "ecommerce", "cold_storage", "last_mile",
                                  "manufacturing"]:
        assert k in ops, k
        c = A.select_criteria(k)["criteria"]
        assert 5 <= len(c) <= 7, k
        assert all(x["why"] and 1 <= x["weight"] <= 5 for x in c)


def test_ops_text_rules():
    assert A.select_criteria(ops_text="we run a cross-dock for a 3PL", use_claude=False)["operation"] == "cross_dock"
    assert A.select_criteria(ops_text="frozen food cold storage", use_claude=False)["operation"] == "cold_storage"
    assert A.select_criteria(ops_text="last-mile vans", use_claude=False)["operation"] in ("last_mile",
                                                                                          "last_mile_station")
    d = A.select_criteria(ops_text="something vague", use_claude=False)
    assert d["matched_by"] == "default"


# ------------------------------------------------------------------ winner logic
def test_winner_needs_all_known():
    w = A.winner("clear_height_ft", [_v(1, 32), _v(2, None, "unknown")], "higher")
    assert w["verdict"] == "unknown" and w["winner"] is None


def test_winner_tolerance_tie():
    w = A.winner("clear_height_ft", [_v(1, 32), _v(2, 31)], "higher")     # tol 2 ft
    assert w["verdict"] == "tie" and w["winner"] is None
    w = A.winner("clear_height_ft", [_v(1, 36), _v(2, 30)], "higher")
    assert w["verdict"] == "winner" and w["winner"] == 1 and not w["estimated"]


def test_lower_is_better_rent():
    w = A.winner("asking_rent", [_v(1, 9.0), _v(2, 12.0)], "lower")
    assert w["winner"] == 1


def test_lower_bound_cannot_lose_or_win_without_margin():
    # verified 20 docks vs "at least 3": the lower bound could be more -> undecided
    w = A.winner("docks_total", [_v(1, 20), _v(2, 3, "estimated", 3, None, "lower_bound")], "higher")
    assert w["verdict"] == "unknown" and w["winner"] is None
    # at least 25 vs verified 20: the estimate wins, and is marked
    w = A.winner("docks_total", [_v(1, 20), _v(2, 25, "estimated", 25, None, "lower_bound")], "higher")
    assert w["winner"] == 2 and w["estimated"]


def test_overlapping_estimates_undecided():
    w = A.winner("truck_court_ft", [_v(1, 100, "estimated", 80, 120), _v(2, 130, "estimated", 110, 140)], "higher")
    assert w["verdict"] == "unknown"
    w = A.winner("truck_court_ft", [_v(1, 60, "estimated", 50, 70), _v(2, 140, "estimated", 130, 150)], "higher")
    assert w["winner"] == 2


# ------------------------------------------------------------------ compare + comparables
IDS = [12896757, 238885, 237474]


def test_compare_shape_and_rules():
    c = A.compare(IDS, "pallet_3pl", use_claude=False)
    assert 5 <= len(c["rows"]) <= 7 and len(c["criteria"]) == len(c["rows"])
    for r in c["rows"]:
        assert r["verdict"] in ("winner", "tie", "unknown")
        if r["verdict"] == "winner":
            assert all(v["kind"] != "unknown" for v in r["values"])
    assert set(c["fit"]) == set(IDS) and set(c["fit"].values()) <= {"pass", "flag", "fail"}
    assert c["recommendation"]["id"] in IDS
    assert c["recommendation"]["text"].count(".") <= 4
    assert 1 <= len(c["tradeoffs"]) <= 4
    # deterministic
    assert A.compare(IDS, "pallet_3pl", use_claude=False) == c


def test_compare_only_verified_values_fail():
    c = A.compare(IDS, "pallet_3pl", use_claude=False)
    for pid, st in c["fit"].items():
        if st == "fail":
            failing = [r for r in c["rows"] for v in r["values"] if v["id"] == pid and v["kind"] == "verified"]
            assert failing


def test_compare_route_and_profiles_differ():
    a = client.post("/api/compare", json={"ids": IDS[:2], "profile": "pallet_3pl"}).json()
    b = client.post("/api/compare", json={"ids": IDS[:2], "profile": "cross_dock"}).json()
    assert [c["key"] for c in a["criteria"]] != [c["key"] for c in b["criteria"]]
    assert client.post("/api/compare", json={"ids": [IDS[0]]}).status_code == 400


def test_comparables():
    r = A.find_comparables(12896757, "pallet_3pl", use_claude=False)
    w0 = A.wh(12896757)
    assert 12896757 not in r["ids"] and len(r["ids"]) <= 3
    for i in r["ids"]:
        w = A.wh(i)
        assert w["market"] == w0["market"]
        assert 0.6 * w0["rba_sqft"] <= (w["rba_sqft"] or w["gba_sqft"]) <= 1.4 * w0["rba_sqft"]
    j = client.get("/api/comparables/12896757", params={"profile": "pallet_3pl"}).json()
    assert j["ids"] == r["ids"] and j["why"]


def test_dock_turn_capacity_is_labelled_estimate():
    w = A.wh(12896757)
    x = A.criterion_value("dock_turn_capacity_est", w, {"shift_hours": 16, "truck": "semi_53"})
    assert x["kind"] == "estimated" and "estimate" in x["basis"]
