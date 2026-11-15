"""The voice Concierge: Claude (claude-opus-5-5, low effort, streaming) with deterministic tools over our own data.

Rules it is built around (CLAUDE.md):
  * Plain searches run immediately; the Concierge never interviews the user before searching.
  * Every number it speaks comes from a tool result (search/rank/facts/simulation); unknown is "?".
  * A simulation asks ONLY for what `Requirements.missing_for('simulate')` lists, then reads the plan back and
    runs only after the user confirms in a later turn (enforced in code, not just in the prompt).
  * The map is driven through UI commands `{type:'ui', cmd, args}` (CONTRACTS.md), emitted as soon as a tool runs.

    c = Concierge(client=AsyncAnthropic())
    reply = await c.turn("big warehouses in houston", emit)     # emit(event: dict) is awaited for each event
Events: {"type":"assistant_delta","text"} {"type":"ui","cmd","args"} {"type":"tool","name","input"}
"""
import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Optional

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.library import equipment, profiles
from proinspect.requirements import Requirements

MODEL = "claude-opus-5-5"
MAX_STEPS = 6
TRUCKS = ("semi_53", "box_truck_26", "last_mile_van")
UI_CMDS = ("flyTo", "openCard", "highlight", "compare", "showInside")

SYSTEM = """You are ProInspect's voice concierge: a sharp, friendly industrial real-estate broker helping someone \
find, inspect and test-drive warehouses in Houston and Los Angeles on a map. Your words are spoken aloud.

Style: at most 3 short spoken sentences (about 40 words; aim for 2). No lists, no markdown, no headings. Put detail on screen (cards, map), not in speech. Before calling any tool, first say a 2-6 word lead-in ending with a period, e.g. "Pulling those up." or "Let me check." (it is spoken while the tool runs); it must contain no facts or numbers.

Truth rules (strict):
- Never state a number, fact or ranking you did not get from a tool result in this conversation. If a value is \
unknown or missing, say it's unknown ("?" on screen). Never estimate or round into new numbers beyond simple rounding.
- When sources disagree or confidence is low, say so briefly, e.g. "CoStar says 32 feet; the scan measured 30."
- Our data is a sample of about 110 warehouses on CoStar's scan list, not the whole market; say so if it matters.

Behaviour:
- A plain search request ("show me warehouses in Houston over 50k square feet") -> call search_warehouses right \
away with the user's words. Never ask questions before searching. Then summarise in a sentence (how many matched, \
the top one and why) and offer one next step.
- Use the ui tool to drive the map: flyTo/openCard one warehouse, highlight or compare several, showInside for a \
3D interior (only when the warehouse is inspectable). Refer to warehouses by street address, not id.
- "Why is this ranked first?" -> explain_rank. Facts about one building -> get_warehouse.
- Simulation ("can my operation run here?", "simulate"): call check_simulation_requirements with whatever the user \
told you (business type, truck, volume). Ask ONLY for the items it lists as missing, one short question at a time, \
offering the options it returns. When nothing is missing, read the plan back in one sentence and ask the user to \
confirm. Only after the user says yes in their next message call run_simulation. Then report the P50 and the \
P10-P90 range of the headline KPIs from the tool result, plus the main assumption.
- If the UI context says a warehouse is selected, "this one"/"here" means that warehouse.
- If a tool errors or a service isn't ready, say so plainly in one sentence."""

TOOLS = [
    {"name": "search_warehouses",
     "description": "Search and rank our warehouse catalog from a plain-language query (sizes in SF, clear height in "
                    "ft, docks, market Houston/Los Angeles, lease/buy, available, 3D scan, business type, near a "
                    "place). Also updates the map. Empty query = browse all. Returns counts and the top results "
                    "with their pass/near/fail/unknown checks and sources.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "The user's search in their own words"},
         "top": {"type": "integer", "description": "How many top results to return (default 5, max 10)"}},
         "required": ["query"]}},
    {"name": "get_warehouse",
     "description": "All known facts for one warehouse, each with its source (costar, vexcel, scan, derived), "
                    "confidence and alternative values from other sources; plus availability/listings.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "explain_rank",
     "description": "Why a warehouse ranks where it does in the current search: its score, band, and every check "
                    "(need vs have, source, status), or why it was excluded.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "get_library",
     "description": "The business profiles (operation types) and vehicles/equipment we can simulate.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_simulation_requirements",
     "description": "Record what the user said about their operation and return what a simulation still needs "
                    "(missing), the options to offer, and a read-back of the plan for confirmation.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "integer", "description": "Warehouse to simulate in (default: the selected one)"},
         "profile": {"type": "string", "description": "Business profile key from get_library", "enum": []},
         "truck": {"type": "string", "enum": list(TRUCKS)},
         "volume_scale": {"type": "number", "description": "Multiplier on the profile's typical volume, only if "
                                                           "the user said e.g. 'double the volume'"}}}},
    {"name": "run_simulation",
     "description": "Run the shift simulation (10 seeded runs) after the user confirmed the read-back. Also opens "
                    "the 3D replay on screen. Returns KPI bands (P10/P50/P90) and assumptions.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "integer"}, "profile": {"type": "string"},
         "volume_scale": {"type": "number"}}, "required": ["id", "profile"]}},
    {"name": "ui",
     "description": "Drive the map/app: flyTo or openCard (one id), highlight or compare (several ids), "
                    "showInside (one inspectable id: opens the 3D interior).",
     "input_schema": {"type": "object", "properties": {
         "cmd": {"type": "string", "enum": list(UI_CMDS)},
         "ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["cmd", "ids"]}},
]


def _tools():
    tools = json.loads(json.dumps(TOOLS))
    for t in tools:
        if t["name"] == "check_simulation_requirements":
            t["input_schema"]["properties"]["profile"]["enum"] = sorted(profiles())
    tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


def _api():
    from proinspect import api            # lazy: api.py imports this package's router at startup
    return api


# ------------------------------------------------------------------ number guard (used in tests + assistant_done)
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _norm(n: str) -> str:
    n = n.replace(",", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


def numbers_in(text: str) -> set[str]:
    return {_norm(n) for n in _NUM.findall(text or "")}


def unsupported_numbers(reply: str, evidence: str) -> list[str]:
    """Numbers in the reply that appear nowhere in the evidence (tool results + user words). Integers that are
    roundings of an evidence number (238,011 -> 238k / 238,000) and tiny counting words (1-10) are allowed."""
    have = numbers_in(evidence)
    have_f = []
    for h in have:
        try:
            have_f.append(float(h))
        except ValueError:
            pass
    out = []
    for n in numbers_in(reply):
        if n in have:
            continue
        v = float(n)
        if v <= 10 and v == int(v):
            continue
        if any(abs(v - h) <= max(0.5, 0.05 * abs(h)) or abs(v * 1000 - h) <= 0.05 * abs(h) for h in have_f):
            continue
        out.append(n)
    return sorted(out)


# ------------------------------------------------------------------ session
class Concierge:
    """One conversation (one websocket). Holds history, the operation the user described, and UI context."""

    def __init__(self, client=None, model: str = MODEL):
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic()
        self.client = client
        self.model = model
        self.messages: list[dict] = []
        self.req = Requirements()
        self.context: dict = {}
        self.last_search: Optional[dict] = None
        self.sim_proposal: Optional[dict] = None
        self.turn_no = 0
        self.evidence: list[str] = []           # tool results + user text, for the number guard

    # ---------------------------------------------------------- UI context from the web app
    def set_context(self, selected_id=None, chips=None, visible=None, market=None):
        if selected_id is not None:
            self.context["selected_id"] = selected_id
        if chips is not None:
            self.context["chips"] = chips

    def _context_note(self) -> Optional[str]:
        parts = []
        sid = self.context.get("selected_id")
        if sid:
            w = self._wh(sid)
            parts.append(f"selected warehouse id {sid}" + (f" ({w.get('address')})" if w else ""))
        if self.context.get("chips"):
            parts.append("active filter chips: " + json.dumps(self.context["chips"], default=str)[:600])
        return "[UI context: " + "; ".join(parts) + "]" if parts else None

    # ---------------------------------------------------------- tools (deterministic; never invent numbers)
    def _wh(self, pid) -> Optional[dict]:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        return next((w for w in _api().catalog() if w["id"] == pid), None)

    def t_search_warehouses(self, query: str = "", top: int = 5):
        from proinspect.query import parse
        api = _api()
        top = max(1, min(int(top or 5), 10))
        query = re.sub(r"(\d)\s*-\s*(foot|feet|ft)\b", r"\1 \2", query or "")      # "32-foot" -> "32 foot"
        chips = parse(query or "", api.catalog(), use_claude=False)["chips"] if query else []
        res = api.search(api.SearchBody(chips=chips, limit=200))
        res["query"] = query
        self.last_search = res
        by_id = {w["id"]: w for w in api.catalog()}
        rows = []
        for r in res["results"][:top]:
            w = by_id.get(r["id"], {})
            rows.append({
                "id": r["id"], "rank": r.get("rank"), "address": r["address"], "market": r["market"],
                "score": r.get("score"), "band": r.get("band"), "inspectable": r.get("inspectable"),
                "size_sqft": w.get("rba_sqft"), "clear_height_ft": w.get("clear_height_ft"),
                "availability": w.get("availability"),
                "checks": [{k: c.get(k) for k in ("label", "need", "have", "unit", "source", "status")}
                           for c in r.get("checks", [])][:6],
                "unverified_dealbreakers": r.get("unverified_dealbreakers"),
            })
        out = {"understood": [f"{c['label']} {c['op']} {c['value']} (from '{c['said']}')" for c in chips],
               "not_understood": None, "mode": res["mode"], "counts": res["counts"], "top": rows,
               "note": "null = unknown"}
        from proinspect.query import parse_rules, unparsed_words
        if query:
            _, used = parse_rules(query)
            out["not_understood"] = unparsed_words(query, used) or None
        ui = [("search", {"query": query, "chips": chips})]
        return out, ui

    def t_get_warehouse(self, id: int):
        w = self._wh(id)
        if not w:
            return {"error": f"warehouse {id} is not in the catalog"}, []
        full = _api().warehouse(int(id))
        facts = {k: {kk: v.get(kk) for kk in ("value", "source", "confidence", "detail", "alt") if v.get(kk)
                     not in (None, [], "")} for k, v in full["facts"].items()}
        keep = ("id", "name", "address", "city", "market", "subtype", "status", "building_class", "year_built",
                "inspectable", "availability", "owner_occupied", "tenancy", "zoning")
        out = {k: full.get(k) for k in keep}
        out["facts"] = facts
        lst = json.dumps(full.get("listings"), default=str)
        out["listings"] = full.get("listings") if len(lst) <= 1500 else lst[:1500] + "...(truncated)"
        out["scans"] = full.get("scans")
        return out, []

    def t_explain_rank(self, id: int):
        if not self._wh(id):
            return {"error": f"warehouse {id} is not in the catalog"}, []
        if self.last_search is None:
            self.t_search_warehouses("")
        res = self.last_search
        for r in res["results"]:
            if r["id"] == int(id):
                return {k: r.get(k) for k in ("rank", "address", "mode", "score", "band", "counts", "checks",
                                              "unverified_dealbreakers")} | {"of": res["counts"]["shown"],
                                                                              "query": res.get("query")}, []
        for e in res["excluded"]:
            if e["id"] == int(id):
                return {"excluded": True, "address": e["address"], "why": e["why"], "query": res.get("query")}, []
        return {"error": "not in the current search results"}, []

    def t_get_library(self):
        return {"profiles": {k: {"name": p.get("name"), "description": p.get("description")}
                             for k, p in profiles().items()},
                "trucks": {k: equipment()[k].get("name") for k in TRUCKS if k in equipment()}}, []

    def t_check_simulation_requirements(self, id: Optional[int] = None, profile: Optional[str] = None,
                                        truck: Optional[str] = None, volume_scale: Optional[float] = None):
        pid = id or self.context.get("selected_id")
        if profile:
            if profile not in profiles():
                return {"error": f"unknown profile {profile}", "options": sorted(profiles())}, []
            had_truck = self.req.truck
            self.req.truck = None
            self.req.apply_profile(profile)         # fills a default truck from the profile's fleet
            if had_truck and not truck:
                self.req.truck = had_truck
        if truck:
            if truck not in TRUCKS:
                return {"error": f"unknown truck {truck}", "options": list(TRUCKS)}, []
            self.req.truck = truck
        if volume_scale:
            self.req.volume_scale = float(volume_scale)
        self.req.confirmed = False
        missing = self.req.missing_for("simulate")
        if not pid:
            missing = ["warehouse"] + missing
        w = self._wh(pid) if pid else None
        if pid and not w:
            return {"error": f"warehouse {pid} is not in the catalog"}, []
        out = {"missing": missing, "ready_after_confirmation": not missing,
               "have": {"warehouse": w and w.get("address"), "profile": self.req.profile, "truck": self.req.truck,
                        "volume_scale": self.req.volume_scale}}
        if "profile" in missing:
            out["profile_options"] = {k: p.get("name") for k, p in profiles().items()}
        if "truck" in missing:
            out["truck_options"] = {k: equipment()[k].get("name") for k in TRUCKS if k in equipment()}
        if "warehouse" in missing:
            out["ask"] = "which warehouse (or select one on the map)"
        if w and w.get("inspectable") is False:
            out["note"] = "this warehouse has no interior scan; the simulation may not be available for it"
        if not missing:
            self.sim_proposal = {"id": int(pid), "profile": self.req.profile, "truck": self.req.truck,
                                 "volume_scale": self.req.volume_scale, "turn": self.turn_no}
            out["read_back"] = (f"Simulate a {profiles()[self.req.profile].get('name')} operation at "
                                f"{w.get('address')} with {equipment()[self.req.truck].get('name')} trucks at "
                                f"{self.req.volume_scale:g}x typical volume. Ask the user to confirm.")
        return out, []

    async def t_run_simulation(self, id: int, profile: str, volume_scale: Optional[float] = None):
        p = self.sim_proposal
        if not p or p["turn"] >= self.turn_no:
            return {"error": "not confirmed: call check_simulation_requirements, read the plan back, and wait for "
                             "the user's yes in their next message"}, []
        if int(id) != p["id"] or profile != p["profile"]:
            return {"error": "this differs from the plan the user confirmed; read the new plan back first",
                    "confirmed_plan": p}, []
        scale = float(volume_scale or p["volume_scale"] or 1.0)
        try:
            status, body = await self._post_sim({"property_id": int(id), "profile": profile, "runs": 10,
                                                 "volume_scale": scale})
        except Exception as e:
            return {"error": f"simulation failed: {type(e).__name__}: {str(e)[:200]}"}, []
        if status == 404 and body == "no route":
            return {"error": "simulation service not ready"}, []
        if status != 200:
            return {"error": f"simulation failed ({status}): {str(body)[:300]}"}, []
        self.req.confirmed = True
        self.sim_proposal = None
        summary = body.get("summary", body)
        kpi = summary.get("kpi") or {}
        out = {"run_id": body.get("run_id"), "kpi": kpi, "assumptions": (summary.get("assumptions") or [])[:6],
               "runs": summary.get("runs"), "fleet": summary.get("fleet")}
        txt = json.dumps(out, default=str)
        if len(txt) > 6000:
            out = {"run_id": body.get("run_id"), "kpi": kpi, "assumptions": (summary.get("assumptions") or [])[:4]}
        ui = [("simulate", {"id": int(id), "profile": profile, "volume_scale": scale, "run_id": body.get("run_id")})]
        return out, ui

    async def _post_sim(self, payload: dict):
        """POST /api/sim/run in-process (Stream B's endpoint) -> (status, json | text)."""
        api = _api()
        from proinspect.agents.guide_tools import route_ready
        if not route_ready("/api/sim/run"):
            return 404, "no route"
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://local",
                                     timeout=300) as c:
            r = await c.post("/api/sim/run", json=payload)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    def t_ui(self, cmd: str, ids: list):
        if cmd not in UI_CMDS:
            return {"error": f"unknown cmd {cmd}"}, []
        ids = [int(i) for i in (ids or []) if self._wh(i)]
        if not ids:
            return {"error": "no valid warehouse ids"}, []
        if cmd in ("flyTo", "openCard", "showInside"):
            w = self._wh(ids[0])
            if cmd == "showInside" and not w.get("inspectable"):
                return {"error": f"{w['address']} has no interior scan; offer the exterior instead"}, []
            if cmd in ("openCard", "showInside"):
                self.context["selected_id"] = ids[0]
            return {"ok": True, "address": w["address"]}, [(cmd, {"id": ids[0]})]
        return {"ok": True, "addresses": [self._wh(i)["address"] for i in ids]}, [(cmd, {"ids": ids})]

    async def run_tool(self, name: str, args: dict):
        """-> (result dict, [(ui_cmd, ui_args)]). Errors become tool errors the model can speak about."""
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}, []
        try:
            if asyncio.iscoroutinefunction(fn):
                return await fn(**(args or {}))
            return await asyncio.to_thread(fn, **(args or {}))
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}, []
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:200]}"}, []

    # ---------------------------------------------------------- one user turn
    def _params(self):
        return dict(model=self.model, max_tokens=4000, system=[{"type": "text", "text": SYSTEM}],
                    tools=_tools(), output_config={"effort": "low"}, cache_control={"type": "ephemeral"},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default")

    async def turn(self, text: str, emit: Callable[[dict], Awaitable[None]]) -> str:
        """Run one user turn to completion; returns the full assistant text. Cancel-safe (barge-in)."""
        self.turn_no += 1
        base = len(self.messages)
        content = []
        note = self._context_note()
        if note:
            content.append({"type": "text", "text": note})
        content.append({"type": "text", "text": text})
        self.messages.append({"role": "user", "content": content})
        self.evidence.append(text)
        said = []
        try:
            for _ in range(MAX_STEPS):
                done_early: dict[str, tuple] = {}
                step_text = []
                async with self.client.beta.messages.stream(messages=self.messages, **self._params()) as stream:
                    async for ev in stream:
                        et = getattr(ev, "type", None)
                        if et == "text":
                            step_text.append(ev.text)
                            said.append(ev.text)
                            await emit({"type": "assistant_delta", "text": ev.text})
                        elif et == "content_block_start" and \
                                getattr(getattr(ev, "content_block", None), "type", None) == "tool_use":
                            await emit({"type": "tool_pending"})      # internal: lets TTS speak the lead-in now
                        elif et == "content_block_stop":
                            b = getattr(ev, "content_block", None)
                            if b is not None and getattr(b, "type", None) == "tool_use" and b.name == "ui":
                                done_early[b.id] = await self._exec(b, emit)   # move the map right away
                    final = await stream.get_final_message()
                blocks = [b.to_dict() if hasattr(b, "to_dict") else b for b in final.content]
                self.messages.append({"role": "assistant", "content": blocks})
                if final.stop_reason == "refusal":
                    msg = "Sorry, I can't help with that one."
                    await emit({"type": "assistant_delta", "text": msg})
                    said.append(msg)
                    self.messages[base:] = [self.messages[base],
                                            {"role": "assistant", "content": [{"type": "text", "text": msg}]}]
                    break
                uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
                if not uses or final.stop_reason == "max_tokens":
                    break
                results = []
                for b in uses:
                    res = done_early.get(b.id) or await self._exec(b, emit)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": res[0],
                                    **({"is_error": True} if res[1] else {})})
                self.messages.append({"role": "user", "content": results})
                if step_text and not "".join(step_text).endswith((" ", "\n")):
                    said.append(" ")
                    await emit({"type": "assistant_delta", "text": " "})
            return "".join(said).strip()
        except asyncio.CancelledError:
            # barge-in: keep history valid (drop dangling tool calls) and record what was actually said
            self.messages[base + 1:] = []
            partial = "".join(said).strip()
            self.messages.append({"role": "assistant", "content": [
                {"type": "text", "text": (partial + " " if partial else "") + "[interrupted by the user]"}]})
            raise

    async def _exec(self, block, emit):
        args = block.input if isinstance(block.input, dict) else {}
        await emit({"type": "tool", "name": block.name, "input": args})
        result, ui = await self.run_tool(block.name, args)
        for cmd, a in ui:
            await emit({"type": "ui", "cmd": cmd, "args": a})
        txt = json.dumps(result, default=str)
        self.evidence.append(txt)
        return txt, "error" in result

    def check_numbers(self, reply: str) -> list[str]:
        return unsupported_numbers(reply, "\n".join(self.evidence))
