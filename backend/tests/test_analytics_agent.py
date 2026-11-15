"""S4 agent tools (rank_attribute, dock_evidence, compare_warehouses, find_comparables, yard_maneuver_test) through
the real deepagents graph with a scripted chat model: no LLM calls. Checks UI commands, step messages and evidence."""
import json

from proinspect import analytics as A
from proinspect.agents import analyst_tools as AT

from test_concierge_guide import HERO, make, say, turn, uis


def steps(ev):
    return [e for e in ev if e["type"] == "step"]


def test_most_loading_docks_ranks_tiers_and_investigates_unknowns():
    r = A.rank_attribute("docks_total", market="Houston", top=200)
    big_unknown = [x["id"] for x in r["unknown"][:2]]
    g, m = make([say("Ranking the catalog.", ("rank_attribute", {"attr": "most loading docks", "market": "Houston"})),
                 say("", ("dock_evidence", {"ids": big_unknown})),
                 say("Ranked by CoStar dock count. Want the cross-dock comparison?")])
    reply, ev = turn(g, "Find me the warehouse with the highest number of loading docks in Houston")
    cmds = uis(ev)
    assert ("rankAttribute", {"attr": "docks_total", "market": "Houston", "order": "desc"}) in cmds
    assert any(c == "highlight" for c, _ in cmds) and any(c == "flyTo" for c, _ in cmds)
    res = json.loads(g.evidence[-2])
    assert res["attr"] == "docks_total" and res["coverage"]["unknown"] == len(r["unknown"])
    assert res["verified_top"][0]["value"] == r["verified"][0]["value"]
    assert all(x["value"] is not None for x in res["verified_top"])
    st = steps(ev)
    assert [s["tool"] for s in st] == ["rank_attribute", "dock_evidence"]
    assert "verified" in st[0]["summary"] and st[0]["ids"]
    ev2 = json.loads(g.evidence[-1])
    assert [b["id"] for b in ev2["buildings"]] == big_unknown


def test_compare_for_operation_emits_compare_with_profile():
    g, m = make([say("", ("find_comparables", {"id": HERO, "ops_text": "cross-dock"})),
                 say("Done. Want details?")])
    _, ev = turn(g, "Compare this warehouse with others for a cross-dock")
    comp = json.loads(g.evidence[-1])
    assert comp["operation_key"] == "cross_dock" and HERO not in comp["ids"]
    ids = [HERO] + comp["ids"][:2]
    if len(ids) < 2:
        ids = [HERO, 238885]
    g2, _ = make([say("", ("compare_warehouses", {"ids": ids, "profile": "cross_dock"})), say("Here it is.")])
    _, ev = turn(g2, "compare them for cross-dock")
    assert ("compare", {"ids": ids, "profile": "cross_dock"}) in uis(ev)
    out = json.loads(g2.evidence[-1])
    assert out["operation_key"] == "cross_dock" and out["recommendation"]["text"]
    assert any("Cross-dock layout" in c for c in out["criteria"])
    assert steps(ev)[0]["tool"] == "compare_warehouses" and "recommends" in steps(ev)[0]["summary"]


def test_compare_needs_two_ids():
    g, _ = make([say("", ("compare_warehouses", {"ids": [HERO]})), say("I need two.")])
    _, ev = turn(g, "compare")
    assert "error" in json.loads(g.evidence[-1])
    assert steps(ev)[0]["ok"] is False


def test_five_trucks_opens_the_maneuver_test_with_counts():
    g, _ = make([say("", ("yard_maneuver_test", {"id": HERO, "trucks": [{"type": "semi_53", "count": 5}]})),
                 say("Opening the maneuver test.")])
    _, ev = turn(g, "can 5 trucks use this yard at once?")
    cmd = [a for c, a in uis(ev) if c == "yardTest"]
    assert cmd and cmd[0]["id"] == HERO and cmd[0]["trucks"] == [{"type": "semi_53", "count": 5}]
    out = json.loads(g.evidence[-1])
    assert out["trucks_total"] == 5 and out["required_court_ft"] == 130 and out["precheck"]


def test_agent_cannot_start_a_yard_test_the_user_did_not_ask_for():
    g, _ = make([say("", ("yard_maneuver_test", {"id": HERO, "trucks": [{"type": "semi_53", "count": 5}]})),
                 say("Here it is.")])
    _, ev = turn(g, "take me to 3200 Sam Houston Pkwy")
    assert not [c for c, _ in uis(ev) if c == "yardTest"]
    assert "not run" in json.loads(g.evidence[-1])["error"]


def test_parse_trucks_words():
    assert AT.parse_trucks(["5 semis"]) == [{"type": "semi_53", "count": 5}]
    assert AT.parse_trucks(["3 box trucks", "2 vans"]) == [{"type": "box_truck_26", "count": 3},
                                                          {"type": "last_mile_van", "count": 2}]
    assert AT.parse_trucks([{"type": "53 ft", "count": 40}]) == [{"type": "semi_53", "count": 12}]


def test_step_summary_never_raises():
    assert AT.step_summary("anything", {}, "text")["summary"] == "done"
    assert AT.step_summary("rank_attribute", {}, {"weird": 1})["summary"].startswith("returned")
    assert AT.step_summary("x", {}, {"error": "boom"})["ok"] is False


def test_smallest_ranks_ascending_from_the_words():
    from proinspect import analytics as A
    r = A.rank_attribute("smallest warehouses", None, "Houston", 5)
    assert r["attr"] == "rba_sqft" and r["order"] == "asc"
    v = [x["value"] for x in r["verified"]]
    assert v == sorted(v)
    assert A.rank_attribute("biggest", None, "Houston", 5)["order"] == "desc"
