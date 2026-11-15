"""Concierge + /ws/concierge with Claude, ElevenLabs and the simulator mocked (no network)."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from proinspect import api, api_concierge
from proinspect.agents import concierge as cmod
from proinspect.agents.concierge import Concierge, unsupported_numbers
from proinspect.voice.tts import SentenceChunker, speakable

HERO = 12896757          # inspectable hero (scan QHMtBN5Nnrc)


# ------------------------------------------------------------------ fake Anthropic client
def text(t):
    return NS(type="text", text=t, to_dict=lambda: {"type": "text", "text": t})


def tool(name, inp, tid=None):
    tid = tid or f"tu_{name}"
    return NS(type="tool_use", id=tid, name=name, input=inp,
              to_dict=lambda: {"type": "tool_use", "id": tid, "name": name, "input": inp})


class FakeStream:
    def __init__(self, blocks, stop, delay=0.0):
        self.blocks, self.stop, self.delay = blocks, stop, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for b in self.blocks:
            if b.type == "text":
                for w in b.text.split(" "):
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    yield NS(type="text", text=w + " ")
            else:
                yield NS(type="content_block_start", content_block=b)
                yield NS(type="content_block_stop", content_block=b)

    async def get_final_message(self):
        return NS(content=self.blocks, stop_reason=self.stop)


class FakeClient:
    """Plays back a script: one list of blocks per model call. Records every request."""

    def __init__(self, script, delay=0.0):
        self.script, self.calls, self.delay = list(script), [], delay
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(json.loads(json.dumps(kw["messages"], default=str)))
        assert kw["model"] == "claude-opus-5-5"
        assert kw["output_config"] == {"effort": "low"}
        assert "thinking" not in kw and "tool_choice" not in kw
        assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
        blocks = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return FakeStream(blocks, stop, self.delay)


def run_turn(c, msg):
    events = []

    async def emit(ev):
        events.append(ev)
    reply = asyncio.run(c.turn(msg, emit))
    return reply, events


def tool_results(call):
    """tool_result payloads (parsed) in the last user message of a recorded request."""
    return [json.loads(b["content"]) for b in call[-1]["content"] if b.get("type") == "tool_result"]


# ------------------------------------------------------------------ tool routing
def test_plain_search_runs_immediately_and_drives_map():
    fc = FakeClient([[text("Pulling those up."), tool("search_warehouses", {"query": "houston 30 ft clear"})],
                     [text("Here they are.")]])
    c = Concierge(client=fc)
    reply, ev = run_turn(c, "show me houston warehouses with 30 ft clear")
    ui = [e for e in ev if e["type"] == "ui"]
    assert ui[0]["cmd"] == "search"
    assert {ch["field"] for ch in ui[0]["args"]["chips"]} == {"clear_height_min_ft", "market"}
    res = tool_results(fc.calls[1])[0]
    assert res["counts"]["shown"] > 0 and res["top"][0]["id"]
    assert all(r["market"] == "Houston" for r in res["top"])
    assert reply.startswith("Pulling those up.")
    assert [e["type"] for e in ev].count("assistant_delta") > 0


def test_hyphenated_feet_parse():
    out, _ = Concierge(client=FakeClient([])).t_search_warehouses("houston with 32-foot clear height")
    assert any("Min clear height" in u for u in out["understood"])


def test_get_warehouse_has_sources_and_explain_rank():
    c = Concierge(client=FakeClient([]))
    out, _ = c.t_get_warehouse(HERO)
    assert out["facts"]["clear_height_ft"]["source"]
    out, _ = c.t_search_warehouses("houston 30 ft clear")
    top = out["top"][0]["id"]
    why, _ = c.t_explain_rank(top)
    assert why["rank"] == 1 and why["checks"]
    assert "error" in c.t_get_warehouse(1)[0]


def test_ui_tool_validates_ids_and_inspectable():
    c = Concierge(client=FakeClient([]))
    res, ui = c.t_ui("flyTo", [HERO])
    assert ui == [("flyTo", {"id": HERO})]
    res, ui = c.t_ui("compare", [HERO, 999999999])
    assert ui == [("compare", {"ids": [HERO]})]
    flat = next(w["id"] for w in api.catalog() if not w.get("inspectable"))
    res, ui = c.t_ui("showInside", [flat])
    assert "error" in res and ui == []
    assert "error" in c.t_ui("flyTo", [123])[0]


def test_ui_tool_emitted_while_streaming():
    fc = FakeClient([[text("Flying there."), tool("ui", {"cmd": "openCard", "ids": [HERO]})], [text("Done.")]])
    c = Concierge(client=fc)
    _, ev = run_turn(c, "open that one")
    assert {"type": "ui", "cmd": "openCard", "args": {"id": HERO}} in ev
    assert c.context["selected_id"] == HERO
    assert len(tool_results(fc.calls[1])) == 1          # executed once, not twice


def test_context_is_given_to_claude():
    fc = FakeClient([[text("It is selected.")]])
    c = Concierge(client=fc)
    c.set_context(selected_id=HERO)
    run_turn(c, "tell me about this one")
    first = fc.calls[0][0]["content"][0]["text"]
    assert "UI context" in first and str(HERO) in first


# ------------------------------------------------------------------ simulation: ask only what's needed, confirm
def test_simulation_asks_only_missing_then_requires_confirmation(monkeypatch):
    c = Concierge(client=FakeClient([]))
    c.turn_no = 1
    out, _ = c.t_check_simulation_requirements(id=HERO)
    assert out["missing"] == ["profile", "truck"] and "profile_options" in out
    out, _ = c.t_check_simulation_requirements(id=HERO, profile="pallet_3pl")
    assert out["missing"] == [] and "read_back" in out           # profile implies a default truck
    # same turn: not confirmed yet
    res = asyncio.run(c.t_run_simulation(HERO, "pallet_3pl"))[0]
    assert "not confirmed" in res["error"]
    c.turn_no = 2
    posted = {}

    async def fake_post(payload):
        posted.update(payload)
        return 200, {"run_id": "r1", "summary": {"kpi": {"trucks_per_hour": {"p10": 1.0, "p50": 2.0, "p90": 3.0}},
                                                 "assumptions": ["a"], "runs": 10}}
    monkeypatch.setattr(c, "_post_sim", fake_post)
    res, ui = asyncio.run(c.t_run_simulation(HERO, "pallet_3pl"))
    assert res["run_id"] == "r1" and posted["property_id"] == HERO and posted["runs"] == 10
    assert ui[0][0] == "simulate" and ui[0][1]["run_id"] == "r1"
    assert c.req.confirmed


def test_simulation_service_not_ready(monkeypatch):
    c = Concierge(client=FakeClient([]))
    c.sim_proposal = {"id": HERO, "profile": "pallet_3pl", "truck": "semi_53", "volume_scale": 1.0, "turn": 0}
    c.turn_no = 1

    async def no_route(payload):
        return 404, "no route"
    monkeypatch.setattr(c, "_post_sim", no_route)
    assert asyncio.run(c.t_run_simulation(HERO, "pallet_3pl"))[0] == {"error": "simulation service not ready"}


def test_simulation_needs_a_warehouse():
    c = Concierge(client=FakeClient([]))
    out, _ = c.t_check_simulation_requirements(profile="cold_storage")
    assert out["missing"][0] == "warehouse"


# ------------------------------------------------------------------ no numbers without tool results
def test_number_guard():
    ev = json.dumps({"clear_height_ft": 32.0, "size_sqft": 238011, "shown": 19})
    assert unsupported_numbers("It has 32 feet clear and 19 matches.", ev) == []
    assert unsupported_numbers("About 238,000 square feet, or 238k.", ev) == []
    assert unsupported_numbers("It has 45 dock doors.", ev) == ["45"]


def test_reply_numbers_are_checked_against_tool_results():
    fc = FakeClient([[text("Checking."), tool("get_warehouse", {"id": HERO})], [text("It has 9999 docks.")]])
    c = Concierge(client=fc)
    reply, _ = run_turn(c, "how many docks?")
    assert "9999" in c.check_numbers(reply)


def test_refusal_is_handled():
    class Refuse(FakeClient):
        def _stream(self, **kw):
            s = super()._stream(**kw)
            s.stop = "refusal"
            return s
    c = Concierge(client=Refuse([[text("")]]))
    reply, _ = run_turn(c, "something")
    assert "can't help" in reply


def test_history_stays_valid_after_barge_in():
    fc = FakeClient([[text("one two three four five six seven eight nine ten")]], delay=0.05)
    c = Concierge(client=fc)

    async def go():
        async def emit(ev):
            pass
        t = asyncio.create_task(c.turn("hi", emit))
        await asyncio.sleep(0.12)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert [m["role"] for m in c.messages] == ["user", "assistant"]
    assert "[interrupted by the user]" in c.messages[-1]["content"][0]["text"]


# ------------------------------------------------------------------ TTS chunking
def test_sentence_chunker():
    ch = SentenceChunker()
    out = []
    for d in ["Pulling", " those up. ", "CoStar says 32 ft; ", "the scan measured 30. Want", " to see it?"]:
        out += ch.feed(d)
    out += ch.flush()
    assert out == ["Pulling those up. CoStar says 32 ft; the scan measured 30.", "Want to see it?"]
    assert speakable("**Bold** `x` [link](http://a)") == "Bold x link"


# ------------------------------------------------------------------ websocket protocol
class FakeSpeaker:
    def __init__(self, sj, sb):
        self.sj, self.sb, self.buf, self.first_audio_at = sj, sb, "", None

    def feed(self, d):
        self.buf += d

    def flush(self):
        pass

    async def end(self):
        await self.sj({"type": "audio_begin"})
        await self.sb(b"ID3fake-mp3")
        await self.sj({"type": "audio_end"})

    async def cancel(self):
        pass


class FakeSTT:
    def __init__(self, on_partial):
        self.on_partial, self.n = on_partial, 0

    async def start(self):
        pass

    async def feed(self, pcm):
        self.n += len(pcm)
        await self.on_partial("show me")

    async def finish(self):
        return "show me warehouses in houston" if self.n else ""

    async def close(self):
        pass


@pytest.fixture
def ws_client(monkeypatch):
    scripts = []
    monkeypatch.setattr(api_concierge, "make_concierge", lambda: Concierge(client=FakeClient(scripts.pop(0))))
    monkeypatch.setattr(api_concierge, "make_speaker", lambda sj, sb: FakeSpeaker(sj, sb))
    monkeypatch.setattr(api_concierge, "make_stt", lambda p: FakeSTT(p))
    return TestClient(api.app), scripts


def recv_until(ws, kind):
    out = []
    while True:
        m = ws.receive()
        if m.get("bytes") is not None:
            out.append({"type": "<bytes>", "n": len(m["bytes"])})
            continue
        d = json.loads(m["text"])
        out.append(d)
        if d["type"] == kind:
            return out


def test_ws_text_turn_protocol(ws_client):
    client, scripts = ws_client
    scripts.append([[text("Pulling those up."), tool("search_warehouses", {"query": "houston"})],
                    [text("Found them.")]])
    with client.websocket_connect("/ws/concierge") as ws:
        ws.send_json({"type": "context", "selected_id": HERO})
        ws.send_json({"type": "text", "text": "houston warehouses"})
        msgs = recv_until(ws, "metrics")
    types = [m["type"] for m in msgs]
    assert "assistant_delta" in types and "ui" in types and "assistant_done" in types
    assert types.index("audio_begin") < types.index("<bytes>") < types.index("audio_end")
    done = next(m for m in msgs if m["type"] == "assistant_done")
    assert done["text"].startswith("Pulling those up.") and done["unverified_numbers"] == []
    assert next(m for m in msgs if m["type"] == "ui")["cmd"] == "search"


def test_ws_audio_turn(ws_client):
    client, scripts = ws_client
    scripts.append([[text("Sure.")]])
    with client.websocket_connect("/ws/concierge") as ws:
        ws.send_json({"type": "audio_start"})
        ws.send_bytes(b"\x00\x01" * 1600)
        ws.send_json({"type": "audio_end"})
        msgs = recv_until(ws, "metrics")
    tr = [m for m in msgs if m["type"] == "transcript"]
    assert tr[0] == {"type": "transcript", "text": "show me", "final": False}
    assert tr[-1] == {"type": "transcript", "text": "show me warehouses in houston", "final": True}
    assert any(m["type"] == "assistant_done" and m["text"] == "Sure." for m in msgs)


def test_ws_unknown_and_bad_messages(ws_client):
    client, scripts = ws_client
    scripts.append([])
    with client.websocket_connect("/ws/concierge") as ws:
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["type"] == "error"
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"
