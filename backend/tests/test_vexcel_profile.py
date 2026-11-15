"""Vexcel profile + rank: units, mask area, likelihood thresholds, rank ordering, superlatives, no key leak."""
import base64
import io
import json
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from shapely.geometry import box

from proinspect import vexcel_rank as vr
from proinspect.api import app
from proinspect.tools import vexcel as vx
from proinspect.twin import vexcel_profile as vp

client = TestClient(app)
DEMO = 12896757


def _mask_b64(w, h, fill_box=None):
    im = Image.new("L", (w, h), 0)
    if fill_box:
        im.paste(255, fill_box)
    b = io.BytesIO()
    im.save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


def _square_wkt(lat, lon, half_m):
    return vx.square_around(lat, lon, half_m)


def test_full_mask_area_matches_bbox():
    """A mask covering the whole 100 m x 100 m bbox -> ~10,000 m² (~107,639 SF) in the local metric frame."""
    bbox = _square_wkt(29.6, -95.37, 50)
    g = vx.mask_to_wkt(_mask_b64(200, 200, (0, 0, 200, 200)), bbox, "ortho")
    from shapely import wkt
    a = vp.area_m2(wkt.loads(g))
    assert abs(a - 10_000) / 10_000 < 0.03


def test_half_mask_area_is_half():
    bbox = _square_wkt(34.0, -118.2, 50)                    # LA latitude: cos(lat) handled
    g = vx.mask_to_wkt(_mask_b64(200, 200, (0, 0, 100, 200)), bbox, "ortho")
    from shapely import wkt
    a = vp.area_m2(wkt.loads(g))
    assert abs(a - 5_000) / 5_000 < 0.05


def test_area_units_sqft():
    g = box(-95.37, 29.6, -95.37 + 0.001, 29.6 + 0.001)
    m2 = vp.area_m2(g)
    # 0.001° lat ≈ 110.6 m, 0.001° lon ≈ 96.8 m at 29.6° N
    assert 10_400 < m2 < 10_900
    assert vp.M2_TO_SF == pytest.approx(10.7639, rel=1e-5)          # 1 m² = 10.7639 SF


def test_likelihood_thresholds_are_per_term_and_never_certain():
    assert vp.likelihood(0.20, "rail_yard") == "likely"
    assert vp.likelihood(0.16, "rail_yard") == "possible"
    assert vp.likelihood(0.065, "rail_yard") is None       # no rail near 12896757
    assert vp.likelihood(0.20, "highway_ramp") == "possible"
    for k, v in vp.VOCAB.items():
        assert v["likely"] > v["possible"]
        assert vp.likelihood(0.99, k) == "likely"            # top label is "likely", nothing above it


def test_resolve_place_aliases():
    assert vp.resolve_place("grocery store") == "grocery"
    assert vp.resolve_place("the highway") == "highway_ramp"
    assert vp.resolve_place("port") == "port_container"
    assert vp.resolve_place("water tower") is None


def test_metric_for_superlatives():
    assert vr.metric_for("closest to a grocery store")[0] == "distance_to:grocery"
    m, alts = vr.metric_for("biggest parking lot")
    assert m == "parking_area_sqft" and "car_stalls_est" in alts
    assert vr.metric_for("most trailers")[0] == "trailers_seen"
    assert vr.metric_for("deepest truck court")[0] == "truck_court_ft"


def _prof(pid, lot, dist=None, lk="likely", market="Houston"):
    return {"id": pid, "market": market, "address": f"{pid} Test St",
            "parking": {"parking_lot_area_sqft": {"value": lot, "unit": "sqft", "source": "s", "date": "2026-01-01",
                                                  "confidence": 0.5}},
            "nearby": {"grocery": {"label": "big-box", "source": "emb",
                                   "nearest": None if dist is None else {"distance_m": dist, "likelihood": lk,
                                                                         "lat": 0, "lon": 0, "score": 0.22}}}}


def test_rank_ordering_and_unknowns():
    profs = {1: _prof(1, 5000, 900), 2: _prof(2, 20000, 300), 3: _prof(3, None, 2000, "possible"),
             4: _prof(4, 12000, None)}
    r = vr.rank("parking_area_sqft", profiles=profs, top=10)
    assert [x["id"] for x in r["results"]] == [2, 4, 1]
    assert [x["id"] for x in r["unknown"]] == [3]
    assert r["metric"]["label"] and r["metric"]["definition"] and r["metric"]["unit"] == "sqft"
    d = vr.rank("distance_to:grocery store", profiles=profs, top=10)
    assert [x["id"] for x in d["results"]] == [2, 1]                # ascending, likely only
    assert {x["id"] for x in d["unknown"]} == {3, 4}
    assert "possible" in next(x for x in d["unknown"] if x["id"] == 3)["note"]


def test_rank_bad_metric_is_400():
    assert client.get("/api/vexcel/rank?metric=bogus").status_code == 400
    assert client.get("/api/vexcel/rank?metric=distance_to:moon").status_code == 400


def test_metrics_are_documented():
    ms = client.get("/api/vexcel/metrics").json()
    names = {m["metric"] for m in ms}
    for need in ("parking_area_sqft", "paved_area_sqft", "car_stalls_est", "trucks_seen", "trailers_seen",
                 "truck_court_ft", "roof_condition", "solar", "tree_cover_pct", "building_height_ft",
                 "distance_to:<place>"):
        assert need in names
    assert all(m["label"] and m["definition"] and m["source"] for m in ms)


@pytest.mark.skipif(not (vp.OUT / f"{DEMO}.json").exists(), reason="demo profile not precomputed")
def test_demo_profile_shape_and_sources():
    prof = client.get(f"/api/vexcel/{DEMO}?compute=false").json()
    for sec in ("captured", "parking", "building", "site", "history", "nearby", "sources", "confidence"):
        assert sec in prof
    for sec in ("parking", "building", "site"):
        for k, f in prof[sec].items():
            if isinstance(f, dict) and "value" in f:
                assert "source" in f and "confidence" in f, (sec, k)
                if f["value"] is not None:
                    assert f["source"] and f["method"], (sec, k)
                else:
                    assert f.get("note"), (sec, k)
    assert prof["parking"]["car_stalls_est"]["estimate"] is True
    for n in prof["nearby"].values():
        for h in (n.get("nearest"), n.get("best")):
            if h:
                assert h["likelihood"] in ("likely", "possible")
    # hardscapes come back in m²: 302 m² -> ~3,251 SF, far below the paved area
    hs = prof["parking"]["vexcel_hardscape_sqft"]["value"]
    assert hs is None or hs < prof["parking"]["paved_area_sqft"]["value"]
    key = os.environ.get("VEXCEL_API_KEY")
    if key:
        assert key not in json.dumps(prof)


def test_no_key_in_any_profile_file():
    key = os.environ.get("VEXCEL_API_KEY")
    if not key or not vp.OUT.exists():
        pytest.skip("no key or no profiles")
    for p in list(vp.OUT.glob("*.json"))[:200]:
        assert key not in p.read_text()


def test_parcel_check_rules():
    # 11175666: 6,740 SF of Vexcel buildings on 20 ac vs CoStar 1,230,097 SF on 50 ac -> wrong parcel
    bad = vp.parcel_check(6740, 20.0, 1_230_097, 50.0)
    assert not bad["ok"] and "doesn't match" in bad["note"]
    assert vp.parcel_check(89789, 6.65, 88670, 7.49)["ok"]               # 12896757 agrees
    big = vp.parcel_check(181142, 4.93, 180925, 22.8)                  # acres > 2x apart: caveat, not mismatch
    assert big["ok"] and "Vexcel parcel only" in big["acres_note"]
    assert vp.parcel_check(89789, 2.0, 88670, 7.49)["acres_note"]
    assert vp.parcel_check(89789, 6.65, 88670, 7.49)["acres_note"] is None
    assert vp.parcel_check(5000, 1.0, None, None)["ok"]                 # nothing to check against


def test_rank_excludes_mismatched_parcels_except_distance():
    profs = {1: _prof(1, 5000, 900), 2: _prof(2, 900_000, 300)}
    profs[2]["parcel_mismatch"] = True
    profs[2]["parcel_check"] = {"ok": False, "note": vp.MISMATCH_NOTE}
    r = vr.rank("parking_area_sqft", profiles=profs)
    assert [x["id"] for x in r["results"]] == [1]
    bad = next(x for x in r["unknown"] if x["id"] == 2)
    assert bad["excluded"] == "parcel_mismatch" and "doesn't match" in bad["note"]
    d = vr.rank("distance_to:grocery", profiles=profs)
    assert [x["id"] for x in d["results"]] == [2, 1]                    # point-based: kept


def test_rank_keeps_acres_caveat_rows():
    profs = {1: _prof(1, 5000, 900), 2: _prof(2, 9000, 300)}
    profs[2]["parcel_check"] = {"ok": True, "acres_note": "Vexcel parcel 4.93 ac vs CoStar lot 22.8 ac: x"}
    r = vr.rank("parking_area_sqft", profiles=profs)
    assert [x["id"] for x in r["results"]] == [2, 1]
    assert "Vexcel parcel 4.93 ac" in r["results"][0]["note"] and r["results"][0]["confidence_label"] == "medium"
    assert "note" not in r["results"][1]
