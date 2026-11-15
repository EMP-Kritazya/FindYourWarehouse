"""The deterministic layer of the search parser on 20 scripted queries (no network)."""
import pytest

from proinspect.query import parse, resolve_place, to_requirements
from proinspect.rank import rank


def chips(q):
    return {c["field"]: c["value"] for c in parse(q, use_claude=False)["chips"]}


CASES = [
    ("warehouses over 30k in Houston", {"size_min_sqft": 30000, "market": "Houston"}),
    ("at least 50,000 sf with docks", {"size_min_sqft": 50000, "docks_min": 1}),
    ("between 20k and 40k sq ft", {"size_min_sqft": 20000, "size_max_sqft": 40000}),
    ("20,000-40,000 SF for lease", {"size_min_sqft": 20000, "size_max_sqft": 40000, "deal": "lease"}),
    ("under 25k square feet", {"size_max_sqft": 25000}),
    ("32 ft clear height", {"clear_height_min_ft": 32}),
    ("clear height of 28'", {"clear_height_min_ft": 28}),
    ("8 dock doors and 2 drive-in doors", {"docks_min": 8, "drive_ins_min": 2}),
    ("need a grade-level door", {"drive_ins_min": 1}),
    ("heavy power, 3-phase, 1200 amps", {"heavy_power": True, "three_phase": True, "power_amps_min": 1200}),
    ("rail served with overhead crane", {"rail": True, "cranes": True}),
    ("ESFR sprinklers", {"esfr": True}),
    ("for sale in Los Angeles under $5m", {"deal": "buy", "market": "Los Angeles", "price_max": 5_000_000}),
    ("available now with a 3D tour", {"available_only": True, "inspectable_only": True}),
    ("cold storage facility", {"profile": "cold_storage"}),
    ("3PL space in LA", {"profile": "pallet_3pl", "market": "Los Angeles"}),
    ("e-commerce fulfillment 40k sf", {"profile": "ecommerce_fulfillment", "size_min_sqft": 32000,
                                      "size_max_sqft": 50000}),
    ("rent under $12 per sf per year", {"rent_max_psf_yr": 12}),
    ("under $1 per sq ft per month", {"rent_max_psf_yr": 12}),
    ("warehouse near 77092", {"near": "77092"}),
]


@pytest.mark.parametrize("q,want", CASES)
def test_rules(q, want):
    got = chips(q)
    for k, v in want.items():
        assert got.get(k) == v, f"{q!r}: {k} = {got.get(k)!r}, wanted {v!r} (all: {got})"


def test_empty_query_is_browse():
    out = parse("", use_claude=False)
    assert out["chips"] == [] and out["requirements"].missing_for("search") == []


def test_every_chip_keeps_the_words_it_came_from():
    for c in parse("over 30k sf in Houston with 6 docks", use_claude=False)["chips"]:
        assert c["said"] and c["source"] == "rules"


def test_unknown_fact_is_kept_and_flagged_not_dropped():
    cat = [{"id": 1, "market": "Houston", "rba_sqft": 50000, "clear_height_ft": None, "listings": [],
            "lat": 29.8, "lon": -95.4},
           {"id": 2, "market": "Houston", "rba_sqft": 50000, "clear_height_ft": 20, "listings": [],
            "lat": 29.8, "lon": -95.4}]
    req = parse("32 ft clear", use_claude=False)["requirements"]
    rows = {r["id"]: r for r in rank(req, cat)}
    assert not rows[1]["excluded"] and rows[1]["unverified_dealbreakers"] == ["Clear height"]
    assert rows[2]["excluded"]


def test_place_resolution_uses_only_our_data():
    cat = [{"zip": "77092", "address": "6445 Burlington", "city": "Houston", "lat": 29.86, "lon": -95.48}]
    assert resolve_place("77092", cat)["matched"] == 1
    assert resolve_place("katy", cat) is None          # not in our data -> say so, don't guess


def test_profile_alone_does_not_become_hard_filters():
    req = to_requirements([{"field": "profile", "value": "pallet_3pl", "said": "3pl"}])
    assert all(c.level == "N" for c in req.criteria.values())


def test_measurements_are_not_places():
    got = chips("tall ceilings around thirty feet near 77092")
    assert got.get("near") == "77092"
    assert "near" not in chips("around 30 ft clear")


def test_hyphenated_units():
    assert chips("32-foot clear")["clear_height_min_ft"] == 32
    assert chips("at least 40,000-square-foot")["size_min_sqft"] == 40000
