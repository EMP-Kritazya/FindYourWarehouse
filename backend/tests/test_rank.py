from proinspect.rank import Fact, rank
from proinspect.requirements import Criterion, Requirements


def wh(i, **kw):
    base = {"id": i, "address": f"{i} Test St", "market": "Houston", "lat": 29.8, "lon": -95.4,
            "rba_sqft": 50000, "clear_height_ft": 30, "docks_external": 8, "listings": [], "inspectable": True}
    base.update(kw)
    return base


def req(**kw):
    r = Requirements(profile="pallet_3pl", market="Houston", size_min_sqft=40000, deal="lease", truck="semi_53",
                     confirmed=True, **kw)
    return r


def test_search_needs_nothing_simulation_needs_operation():
    r = Requirements()
    assert r.missing_for("search") == [] and r.ready_for("search")
    assert r.missing_for("rank") == ["criteria"]
    assert r.missing_for("simulate") == ["profile", "truck"]
    r.apply_profile("pallet_3pl")              # profile also implies the truck
    assert r.truck == "semi_53" and r.missing_for("simulate") == []
    assert not r.ready_for("simulate")          # a simulation still needs the user's confirmation
    r.confirmed = True
    assert r.ready_for("simulate") and r.ready_for("rank")


def test_browse_mode_without_any_requirements():
    lease = [{"kind": "lease", "status": "Active"}]
    rows = rank(Requirements(), [wh(1, rba_sqft=90000), wh(2, listings=lease, rba_sqft=20000),
                                 wh(3, inspectable=False, listings=lease, rba_sqft=50000)])
    assert all(r["mode"] == "browse" and not r["excluded"] for r in rows)
    assert [r["id"] for r in rows] == [2, 3, 1]   # available first, then has-3D, then size


def test_profile_does_not_overwrite_user_words():
    r = Requirements(criteria={"clear_height_ft": Criterion(min=36, level="D", weight=5, said="36 feet")})
    r.apply_profile("pallet_3pl")
    assert r.criteria["clear_height_ft"].min == 36
    assert r.criteria["docks_per_10ksf"].level == "D"


def test_dealbreaker_excludes_with_reason():
    r = req(criteria={"clear_height_ft": Criterion(min=28, level="D", weight=5)})
    rows = rank(r, [wh(1, clear_height_ft=32), wh(2, clear_height_ft=20)])
    keep = {x["id"]: x for x in rows}
    assert not keep[1]["excluded"] and keep[1]["rank"] == 1
    assert keep[2]["excluded"] and "Clear height 20 ft" in keep[2]["excluded_because"][0]


def test_near_miss_is_partial_not_fail():
    r = req(criteria={"clear_height_ft": Criterion(min=28, level="N", weight=5)})
    near = next(c for c in rank(r, [wh(1, clear_height_ft=26)])[0]["checks"] if c["fact"] == "clear_height_ft")
    assert near["status"] == "near" and near["score"] == 0.5


def test_unknown_widens_band_and_flags_dealbreaker():
    r = req(criteria={"clear_height_ft": Criterion(min=28, level="D", weight=5)})
    row = rank(r, [wh(1, clear_height_ft=None)])[0]
    assert not row["excluded"]
    assert "Clear height" in row["unverified_dealbreakers"]
    assert row["band"][0] < row["score"] < row["band"][1]


def test_weights_order_results():
    r = req(criteria={"clear_height_ft": Criterion(min=30, level="N", weight=5),
                      "docks_total": Criterion(min=10, level="N", weight=1)})
    rows = rank(r, [wh(1, clear_height_ft=32, docks_external=4), wh(2, clear_height_ft=24, docks_external=12)])
    assert [x["id"] for x in rows] == [1, 2]


def test_measured_fact_overrides_costar_and_keeps_alt():
    r = req(criteria={"clear_height_ft": Criterion(min=28, level="D", weight=5)})
    measured = {1: {"clear_height_ft": Fact(29.5, "matterport", 0.9)}}
    row = rank(r, [wh(1, clear_height_ft=24)], measured)[0]
    c = next(c for c in row["checks"] if c["fact"] == "clear_height_ft")
    assert not row["excluded"] and c["source"] == "matterport" and c["alt"][0]["value"] == 24


def test_market_filter_and_availability():
    r = req()
    lease = [{"kind": "lease", "status": "Active", "rent_low_psf_yr": 9.0}]
    rows = rank(r, [wh(1, listings=lease), wh(2), wh(3, market="Los Angeles")])
    assert {x["id"] for x in rows} == {1, 2}
    a = {x["id"]: next(c for c in x["checks"] if c["fact"] == "availability") for x in rows}
    assert a[1]["score"] == 1.0 and a[2]["score"] < 0.5


def test_lower_bound_fact_below_min_is_unknown_not_fail():
    r = req(criteria={"docks_total": Criterion(min=10, level="D", weight=4)})
    w = wh(1, docks_external=None)
    row = rank(r, [w], {1: {"docks_total": Fact(3, "vexcel", 0.2, bound="lower")}})[0]
    c = next(c for c in row["checks"] if c["fact"] == "docks_total")
    assert c["status"] == "unknown" and not row["excluded"]
    row = rank(r, [w], {1: {"docks_total": Fact(12, "vexcel", 0.2, bound="lower")}})[0]
    assert next(c for c in row["checks"] if c["fact"] == "docks_total")["status"] == "pass"


def test_exclusion_reason_names_the_bound_that_failed():
    r = Requirements(size_min_sqft=32000, size_max_sqft=50000)
    row = rank(r, [wh(1, rba_sqft=88670)])[0]
    assert row["excluded"] and "needs ≤ 50,000" in row["excluded_because"][0] and "88,670" in row["excluded_because"][0]
