from fastapi.testclient import TestClient

from proinspect.api import app

c = TestClient(app)


def test_browse_with_nothing():
    r = c.post("/api/search", json={}).json()
    assert r["mode"] == "browse" and r["counts"]["shown"] == r["counts"]["total"] == 110


def test_plain_text_search_no_questions():
    r = c.post("/api/search", json={"query": "over 30k sf in Houston with docks"}).json()
    assert r["mode"] == "ranked" and r["counts"]["shown"] > 0 and not r["used_claude"]
    assert all(x["market"] == "Houston" for x in r["results"])
    assert {ch["field"] for ch in r["chips"]} >= {"size_min_sqft", "market", "docks_min"}


def test_edited_chips_take_precedence():
    r = c.post("/api/search", json={"query": "in Houston", "chips": []}).json()
    assert r["counts"]["shown"] == 110


def test_simulate_asks_only_for_operation():
    r = c.post("/api/requirements/missing", json={"feature": "simulate"}).json()
    assert r["missing"] == ["profile", "truck"] and not r["ready"]


def test_unknown_warehouse_404():
    assert c.get("/api/warehouses/1").status_code == 404


def test_measured_facts_fill_gaps_but_do_not_override_costar():
    from proinspect.facts import measured_facts
    mf = measured_facts()
    if 238885 not in mf:            # twins not built in this checkout
        return
    f = c.get("/api/warehouses/238885").json()["facts"]
    assert f["clear_height_ft"]["source"] == "costar"                     # CoStar has 24 ft: kept
    assert any(a["source"] == "matterport" for a in f["clear_height_ft"]["alt"])
    assert f["truck_court_ft"]["source"] == "vexcel"                      # CoStar has none: filled
