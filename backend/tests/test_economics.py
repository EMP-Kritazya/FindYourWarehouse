"""True cost per pallet: rent tiers (never invented), normalisation, and cost arithmetic with provenance."""
import json

import pytest
from fastapi.testclient import TestClient

from proinspect import economics as E
from proinspect import rent_comps as RC
from proinspect.api import app


def _fake_data(monkeypatch, by_property=None, by_submarket=None, by_market=None):
    data = {"min_comps": 3, "by_property": by_property or {}, "by_submarket": by_submarket or {}, "by_market": by_market or {}}
    monkeypatch.setattr(RC, "_load", lambda: data)


def test_tier_order_listing_then_own_lease_then_submarket_then_market_then_unknown(monkeypatch):
    sub = {"n": 4, "median": 9.0, "p25": 8.0, "p75": 10.0, "years": [2021, 2025], "service_mix": {}, "properties": 3}
    mk = {"n": 40, "median": 10.5, "p25": 7.5, "p75": 12.0, "years": [2020, 2026], "service_mix": {}, "properties": 20}
    _fake_data(monkeypatch, {"1": [{"rate": 12.0, "year": 2024, "sf": 5000, "service": "nnn"}]}, {"77": sub}, {"Houston": mk})
    listing = {"id": 1, "market": "Houston", "submarket_id": 77, "listings": [{"rent_low_psf_yr": 8.0, "rent_high_psf_yr": 9.0}]}
    assert RC.rent_for(listing)["tier"] == "asking" and RC.rent_for(listing)["value"] == 8.5
    assert RC.rent_for({**listing, "listings": []})["tier"] == "own"
    assert RC.rent_for({**listing, "id": 2, "listings": []})["tier"] == "comps"
    assert RC.rent_for({**listing, "id": 2, "submarket_id": 5, "listings": []})["tier"] == "market"
    u = RC.rent_for({"id": 2, "market": "Phoenix", "submarket_id": 5, "listings": []})
    assert u["tier"] == "unknown" and u["value"] is None                         # never 0, never invented


def test_built_comps_are_normalised_to_dollars_per_sf_per_year():
    d = RC.build()
    for k, s in d["by_market"].items():
        assert 3 <= s["median"] <= 60, (k, s)                                    # monthly rates were x12'd
    assert all(1 <= x["rate"] <= 80 for v in d["by_property"].values() for x in v)


def test_cost_lines_carry_provenance_and_add_up():
    c = E.cost(12896757)
    assert [l["label"] for l in c["lines"]] == ["Rent", "Racking (amortised)", "Forklifts (lease)", "Operators"]
    assert c["lines"][1]["source"] == "assumed" and c["positions"]["source"] == "calculated"
    total = sum(l["value"] for l in c["lines"] if l["value"] is not None)
    assert abs(c["total_year"]["value"] - total) <= 2
    assert c["per_position_month"]["value"] == pytest.approx(total / 12 / c["positions"]["value"], rel=0.01)


def test_overrides_are_marked_yours_and_change_the_result():
    a = E.cost(12896757)
    b = E.cost(12896757, overrides={"rent_psf": 20})
    assert b["rent"]["source"] == "yours" and b["per_position_month"]["value"] > a["per_position_month"]["value"]


def test_api_and_feature_flag(monkeypatch):
    c = TestClient(app)
    assert c.get("/api/economics/12896757").status_code == 200
    assert c.get("/api/economics/999999999").status_code == 404
    monkeypatch.setattr(E, "ENABLED", False)
    assert c.get("/api/economics/12896757").status_code == 404
