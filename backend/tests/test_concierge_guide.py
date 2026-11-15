"""The deepagents guide (agents/guide.py) + v2 ws protocol with the LLM mocked (no Anthropic calls, no TTS/STT).

The fake chat model plays back a script of AIMessages (text and/or tool calls); the real tools, the real deepagents
graph and the real catalog / S1 Vexcel profiles on disk run underneath. Network-touching Vexcel calls are mocked.
"""
import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from proinspect import api, api_concierge
from proinspect.agents import guide as gmod
from proinspect.agents import guide_tools as G
from proinspect.agents import vexcel_bridge as VX
from proinspect.agents.guide import Guide

HERO = 12896757            # 3200 Sam Houston Pky (inspectable)


class FakeChat(BaseChatModel):
    """Scripted chat model: each call pops the next AIMessage. Records the messages it was given."""
    script: list = []
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "fake-guide"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.append([getattr(m, "content", "") for m in messages])
        msg = self.script.pop(0) if self.script else AIMessage(content="Done. Anything else?")
        return ChatResult(generations=[ChatGeneration(message=msg)])


_n = [0]


def say(text: str, *calls: tuple[str, dict]) -> AIMessage:
    tcs = []
    for name, args in calls:
        _n[0] += 1
        tcs.append({"name": name, "args": args, "id": f"call_{_n[0]}", "type": "tool_call"})
    return AIMessage(content=text, tool_calls=tcs)


def make(script) -> tuple[Guide, FakeChat]:
    m = FakeChat(script=list(script), seen=[])
    return Guide(model=m, subagent_model=m), m


def run(g: Guide, coro_fn):
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)
    reply = asyncio.run(coro_fn(emit))
    return reply, events


def turn(g, text):
    return run(g, lambda emit: g.turn(text, emit))


def event(g, name, data):
    return run(g, lambda emit: g.event(name, data, emit))


def uis(events):
    return [(e["cmd"], e["args"]) for e in events if e["type"] == "ui"]


def tool_results(m: FakeChat) -> str:
    return json.dumps(m.seen, default=str)


# ------------------------------------------------------------------ metric interpretation
def test_superlatives_map_to_defined_metrics():
    m, alts = VX.interpret("biggest parking lot")
    assert m == "parking_area_sqft" and "car_stalls_est" in alts
    assert VX.interpret("most car stalls")[0] == "car_stalls_est"
    assert VX.interpret("closest to a grocery store")[0] == "distance_to:grocery"
    assert VX.interpret("most trucks")[0] == "trucks_seen"
    assert VX.interpret("parking_lot_area_sqft")[0] == "parking_area_sqft"     # contract alias
    assert VX.interpret("the nicest vibes")[0]                                  # never refuses


def test_rank_falls_back_and_says_so(monkeypatch):
    monkeypatch.setattr(VX, "_vr", lambda: None)                                 # S1 missing entirely
    r = asyncio.run(VX.rank("parking_area_sqft", "Houston", 3))
    assert r["rows"] and r["metric"] in ("paved_area_sqft", "costar_parking_spaces")
    assert r["notes"]                                                            # the fallback is reported
    assert 11175666 not in [x["id"] for x in r["rows"]]                          # wrong-parcel site suppressed


# ------------------------------------------------------------------ acceptance a: biggest parking lot
def test_biggest_parking_lot_routes_to_rank_by_with_alternative():
    g, m = make([say("Checking the aerials.", ("rank_by", {"metric": "biggest parking lot", "market": "Houston"})),
                 say("The biggest parking lot is at the top one. Want most car stalls instead?")])
    reply, ev = turn(g, "Which warehouse has the biggest parking lot in Houston?")
    cmds = uis(ev)
    assert ("rankBy", {"metric": "parking_area_sqft", "market": "Houston"}) in cmds
    assert [c for c, _ in cmds][-2:] == ["highlight", "flyTo"]
    res = json.loads(g.evidence[-1])
    assert res["metric"] == "parking_area_sqft" and res["definition"]
    assert any(a["metric"] == "car_stalls_est" for a in res["alternatives"])
    assert res["top"] and all(G.wh(r["id"])["market"] == "Houston" for r in res["top"])
    assert any(e["type"] == "suggestions" for e in ev)


# ------------------------------------------------------------------ acceptance b: closest to a grocery store
def test_closest_to_grocery_shows_place_and_flies(monkeypatch):
    async def fake_closest(place, ids=None, market=None):
        return {"place": "grocery", "source": "S1 test", "caveat": "can't tell a supermarket from big-box retail",
                "rows": [{"id": HERO, "address": "3200 Sam Houston Pky", "distance_m": 569, "likelihood": "likely",
                          "lat": 29.6, "lon": -95.37, "label": "big-box store", "thumb_url": "/media/x.jpg"}]}
    monkeypatch.setattr(VX, "closest_to", fake_closest)
    g, m = make([say("Scanning the neighborhoods.", ("closest_to", {"place": "grocery store"})),
                 say("3200 Sam Houston Pky is about 569 meters from a likely big-box store. Want the brief?")])
    reply, ev = turn(g, "Find me a warehouse closest to a grocery store.")
    cmds = uis(ev)
    assert cmds[0][0] == "showPlace" and cmds[0][1]["lat"] == 29.6 and "likely" in cmds[0][1]["label"]
    assert ("flyTo", {"id": HERO}) in cmds
    assert g.check_numbers(reply) == []                    # 569 came from the tool


# ------------------------------------------------------------------ acceptance c: buyer profile + openCard event
def test_buyer_profile_then_search_then_open_card_event():
    g, m = make([
        say("Pulling those up.",
            ("update_buyer_profile", {"business": "3PL", "size_sqft": 40000, "trucks": ["53-foot trailers"],
                                      "equipment_used": ["reach trucks"]}),
            ("search_warehouses", {"query": "3PL about 40k square feet"})),
        say("Here are the best fits. Want me to open the top one?"),
        say("It has the docks you need, but the truck court is unknown. Want a yard test?"),
    ])
    reply, ev = turn(g, "I'm a 3PL running 53-foot trailers and reach trucks, about 40k square feet")
    p = g.profile
    assert p["business"] == "pallet_3pl" and p["size_sqft"] == 40000
    assert p["trucks"] == ["semi_53"] and p["equipment"] == ["reach_truck"]
    assert uis(ev)[0][0] == "profile" and uis(ev)[0][1]["profile"]["business"] == "pallet_3pl" and uis(ev)[1][0] == "search"
    top = g.last_search["ids"][0]

    reply2, ev2 = event(g, "openCard", {"id": top})
    assert reply2 and "yard test" in reply2.lower()
    last_in = json.dumps(m.seen[-1])
    assert "[EVENT openCard" in last_in and "fit_for_buyer" in last_in and "53 ft trailer" in last_in
    sug = [e for e in ev2 if e["type"] == "suggestions"][-1]["items"]
    assert any("yard" in s["label"].lower() or "shortlist" in s["label"].lower() for s in sug)

    calls = len(m.seen)
    again, ev3 = event(g, "openCard", {"id": top})           # at most once per event
    assert again is None and len(m.seen) == calls


def test_event_skipped_when_the_guide_opened_the_card_itself():
    g, m = make([say("Opening it.", ("ui", {"cmd": "openCard", "id": HERO})), say("Here it is. Shortlist it?")])
    turn(g, "open 3200 Sam Houston")
    calls = len(m.seen)
    r, _ = event(g, "openCard", {"id": HERO})
    assert r is None and len(m.seen) == calls


def test_search_results_stay_quiet_and_test_result_events_comment():
    """Unprompted remarks only where they add something: search results are already on screen (no comment); a test
    result is explained."""
    g, m = make([say("It passes, limited by the tightest turn.")])
    r, _ = event(g, "searchResults", {"query": "warehouses in Houston"})
    assert r is None
    r2, _ = event(g, "testResult", {"kind": "yard", "result": {"run_id": "r1", "fits": [
        {"door_id": "d1", "vehicle": "semi_53", "ok": True, "margin_m": 1.2, "conflicts": []}], "queue": {}}})
    assert r2 and g.stage == "tested"
    assert '"fits_ok": 1' in g.evidence[-1] and "[EVENT testResult" in json.dumps(m.seen[-1])


# ------------------------------------------------------------------ acceptance d: yard test + confirmation gate
def test_yard_test_not_ready_is_graceful(monkeypatch):
    monkeypatch.setattr(G, "route_ready", lambda path: False)
    g, m = make([say("Setting that up.", ("prepare_test", {"kind": "yard", "id": HERO, "trucks": ["semi_53"]})),
                 say("The yard test isn't available yet; I can check the truck court instead. Want that?")])
    reply, _ = turn(g, "Can a 53-foot trailer back into the docks at 3200 Sam Houston?")
    res = json.loads(g.evidence[-1])
    assert res.get("not_ready") and g.proposal is None


def test_yard_test_needs_confirmation_in_a_later_turn(monkeypatch):
    monkeypatch.setattr(G, "route_ready", lambda path: True)
    posted = []

    async def fake_post(path, payload, timeout=300):
        posted.append((path, payload))
        return 200, {"run_id": "yard1", "doors": [{"id": "d1", "source": "marked_in_scan"}],
                     "fits": [{"door_id": "d1", "vehicle": "semi_53", "ok": True, "margin_m": 0.8, "conflicts": []}],
                     "queue": {"max_in_yard": 2, "max_on_street": 0, "wait_p50_min": 3, "wait_p90_min": 9},
                     "assumptions": ["doors marked in the scan"]}
    monkeypatch.setattr(G, "post", fake_post)
    g, m = make([
        say("Setting that up.", ("prepare_test", {"kind": "yard", "id": HERO, "trucks": ["semi_53"]}),
            ("run_test", {"kind": "yard"})),                      # model tries to run in the same turn: refused
        say("Yard test with 53-foot trailers at 3200 Sam Houston Pky. Shall I run it?"),
        say("Okay.", ("run_test", {"kind": "yard"})),             # user said no: refused
        say("No problem."),
        say("Running it.", ("run_test", {"kind": "yard"})),       # user said yes in a later turn: runs
        say("All doors pass; the tightest margin is 0.8 meters. Shortlist it?"),
    ])
    turn(g, "Can a 53-foot trailer back into the docks at 3200 Sam Houston?")
    assert not posted and g.proposal and "not confirmed" in tool_results(m)
    turn(g, "no, wait")
    assert not posted
    # re-propose happened in this flow? the plan is still pending; a later explicit yes runs it
    reply, ev = turn(g, "yes, run it")
    assert posted and posted[0][0] == "/api/yard/test" and posted[0][1]["trucks"][0]["type"] == "semi_53"
    assert ("yardTest", {"id": HERO, "trucks": [{"type": "semi_53"}], "run_id": "yard1"}) in uis(ev)
    assert g.check_numbers(reply) == []
    assert g.proposal is None and g.last_test["summary"]["fits_ok"] == 1


def test_run_test_without_plan_is_refused():
    g, m = make([say("Running.", ("run_test", {"kind": "equipment"})), say("I need to set it up first.")])
    turn(g, "run the equipment test")
    assert "no equipment test planned" in tool_results(m)


# ------------------------------------------------------------------ suggestions, numbers, selling help
def test_model_suggestions_win_over_derived():
    g, m = make([say("Here.", ("suggest", {"items": [{"label": "Yard test", "say": "Run the yard test"}]})),
                 say("Anything else?")])
    _, ev = turn(g, "hi")
    sug = [e for e in ev if e["type"] == "suggestions"]
    assert len(sug) == 1 and sug[0]["items"] == [{"label": "Yard test", "say": "Run the yard test"}]


def test_derived_suggestions_when_model_gives_none():
    g, m = make([say("Hello! Want to see Houston warehouses?")])
    _, ev = turn(g, "hi")
    sug = [e for e in ev if e["type"] == "suggestions"][-1]["items"]
    assert 1 <= len(sug) <= 3 and all(s["label"] and s["say"] for s in sug)


def test_numbers_without_tool_evidence_are_flagged():
    g, m = make([say("It has 412 docks and 37,500 square feet. Want a tour?")])
    reply, _ = turn(g, "tell me something")
    assert set(g.check_numbers(reply)) == {"412", "37500"}


def test_listing_contact_and_tour_are_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "BUILD", tmp_path)
    g, m = make([say("Checking.", ("listing_contact", {"id": HERO}), ("request_tour", {"id": HERO})),
                 say("The listing contact is the broker on the listing; I logged your tour request.")])
    turn(g, "who do I call, and book me a tour")
    txt = "\n".join(g.evidence)
    assert "listing_contact" in txt and "fake" in txt and '"sent": false' in txt
    assert (tmp_path / "guide" / "tour_requests.jsonl").exists()


def test_shortlist_and_ui_validation():
    g, m = make([say("Adding.", ("shortlist", {"action": "add", "id": HERO}),
                     ("ui", {"cmd": "showInside", "id": 1}), ("ui", {"cmd": "teleport", "id": HERO})),
                 say("Added to your shortlist.")])
    _, ev = turn(g, "shortlist it")
    assert g.shortlist == [HERO] and ("shortlist", {"action": "add", "id": HERO}) in uis(ev)
    txt = tool_results(m)
    assert "no valid warehouse id" in txt and "unknown cmd teleport" in txt


def test_lead_in_spoken_when_model_goes_straight_to_a_tool():
    msg = AIMessage(content=[{"type": "tool_use", "id": "tu1", "name": "search_warehouses",
                              "input": {"query": "houston"}}],
                    tool_calls=[{"name": "search_warehouses", "args": {"query": "houston"}, "id": "tu1",
                                 "type": "tool_call"}])
    g, m = make([msg, say("Found them. Open the first?")])
    reply, ev = turn(g, "houston warehouses")
    deltas = [e["text"] for e in ev if e["type"] == "assistant_delta"]
    assert deltas[0].startswith("Pulling those up.")


# ------------------------------------------------------------------ websocket: events + suggestions
@pytest.fixture
def ws_guide(monkeypatch):
    scripts = []

    def factory():
        g, _ = make(scripts.pop(0) if scripts else [])
        return g
    monkeypatch.setattr(api_concierge, "make_concierge", factory)
    monkeypatch.setattr(api_concierge, "make_speaker", lambda sj, sb: None)
    return TestClient(api.app), scripts


def _until(ws, kind):
    out = []
    while True:
        m = ws.receive_json()
        out.append(m)
        if m["type"] == kind:
            return out


def test_ws_event_gets_a_proactive_comment(ws_guide):
    client, scripts = ws_guide
    scripts.append([say("It passes the yard test. Add it to your shortlist?")])
    with client.websocket_connect("/ws/concierge") as ws:
        # an unknown buyer opening a card stays quiet now; a test result always earns a comment
        ws.send_json({"type": "event", "name": "testResult", "data": {"id": HERO, "kind": "yard", "result": {
            "run_id": "r1", "fits": [{"door_id": "d1", "vehicle": "semi_53", "ok": True, "margin_m": 1.0, "conflicts": []}],
            "queue": {}}}})
        msgs = _until(ws, "metrics")
    done = next(m for m in msgs if m["type"] == "assistant_done")
    assert done["kind"] == "event" and "shortlist" in done["text"]
    assert any(m["type"] == "suggestions" and m["items"] for m in msgs)


def test_ws_text_turn_still_speaks_v1(ws_guide):
    client, scripts = ws_guide
    scripts.append([say("Pulling those up.", ("search_warehouses", {"query": "houston"})), say("Found some.")])
    with client.websocket_connect("/ws/concierge") as ws:
        ws.send_json({"type": "text", "text": "warehouses in houston", "speak": False})
        msgs = _until(ws, "metrics")
    types = [m["type"] for m in msgs]
    assert "assistant_delta" in types and "ui" in types and "assistant_done" in types and "suggestions" in types
    assert msgs[-1]["first_text_s"] is not None and msgs[-1]["kind"] == "turn"


def test_small_counts_need_evidence_too():
    from proinspect.agents.guide import strict_unsupported
    assert strict_unsupported("Vexcel shows 8 trucks.", '{"trucks_seen": 0}') == ["8"]
    assert strict_unsupported("about 509,000 square feet", '{"value": 508553}') == []
    assert strict_unsupported("53-foot trailers", "I run 53-foot trailers") == []
