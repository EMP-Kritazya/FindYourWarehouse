"""The ProInspect guide: a LangChain deepagents orchestrator that walks a buyer from find -> shortlist -> inspect ->
test -> decide, talks in 1-3 spoken sentences, and drives the app with UI commands (CONTRACTS.md v2).

    orchestrator (claude-opus-5-5, effort low)  fast tools for simple asks: search, rank_by (Vexcel metrics),
        closest_to, building_brief, buyer profile, shortlist, selling help, test plan/run (gated), ui, suggest
      task -> costar_analyst    search / rank / facts / listings / tenants / sales      (multi-step CoStar work)
      task -> vexcel_analyst    S1's Vexcel profile, rank, search, detect, imagery, metrics
      task -> interior_analyst  interior twin facts (Matterport scan measurements)
      task -> test_engineer     runs the Yard test (S3) and the Equipment test (S4), explains the result

Rules enforced in code, not just in prompts:
  * every tool result is kept as evidence; `check_numbers` flags any spoken number no tool returned;
  * a test only runs after the plan was read back and the user confirmed in a LATER turn;
  * UI events comment at most once per (event, building/run);
  * suggestion chips are always sent (the model's own via `suggest`, else derived from the funnel stage).

    g = Guide()
    reply = await g.turn("big warehouses in houston", emit)       # emit(event: dict) is awaited per event
    reply = await g.event("openCard", {"id": 12896757}, emit)      # None when deduplicated
"""
import asyncio
import functools
import json
import os
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.agents import analyst_tools as AT
from proinspect.agents import guide_tools as G
from proinspect.agents import vexcel_bridge as VX
from proinspect.agents.concierge import numbers_in
from proinspect.library import equipment, profiles

MODEL = "claude-opus-5-5"
EFFORT = os.environ.get("PROINSPECT_GUIDE_EFFORT", "low")
EVENT_GAP_S = 8.0                # at most one unprompted comment per this many seconds
UI_CMDS = ("search", "flyTo", "openCard", "highlight", "compare", "showInside", "yardTest", "equipmentTest",
           "showPlace", "rankBy", "showAerial", "shortlist", "rankAttribute", "showDocks", "storageTest",
           "closeCompare", "results", "showFlood")
ONE_ID = ("flyTo", "openCard", "showInside", "showAerial", "yardTest", "equipmentTest", "showDocks", "storageTest")
# a test runs only when the user's own words in THIS turn ask for one (never from an event or the agent's initiative)
ASK_YARD = re.compile(r"\b(test|simulate|run it|go ahead|yes|yep|sure|do it|maneuver|back(ing)?[- ]in|turn(ing)? around|"
                      r"\d+\s*(trucks?|semis?|trailers?|vans?|box trucks?)|(trucks?|semis?|trailers?|vans?) (fit|get in|use))\b", re.I)
ASK_INSIDE = re.compile(r"\b(interior|inside|racks?|racking|pallets?|storage|forklifts?|reach trucks?|planner|layout|"
                        r"capacity|fit|test|run it|go ahead|yes|yep|sure|do it)\b", re.I)
NO = re.compile(r"^\s*(no|nope|nah|don'?t|do not|cancel|stop|wait|hold on|not yet|change)\b", re.I)
LEAD_INS = {"search_warehouses": "Pulling those up.", "rank_by": "Checking the aerials.",
            "closest_to": "Scanning the neighborhoods.", "building_brief": "Let me look.",
            "task": "Let me dig into that.", "run_test": "Running the test now.",
            "prepare_test": "Setting that up.", "listing_contact": "Checking the listing.",
            "rank_attribute": "Ranking the catalog.", "dock_evidence": "Checking the dock evidence.",
            "compare_warehouses": "Comparing them.", "find_comparables": "Finding comparables.",
            "yard_maneuver_test": "Setting up the yard test.", "storage_capacity": "Laying out racks on the scan."}

RULES = """Truth rules (non-negotiable):
- Agents decide, tools compute. Never say a number, count, fact or ranking that a tool did not return in this \
conversation (not even small counts like trucks seen or doors). Unknown is unknown ("?"), say why. Label estimates as estimates and Vexcel search hits as \
"likely"/"possible", never certain.
- Say where a number comes from when it matters (CoStar, the scan, Vexcel imagery with its date).
- Our data is CoStar's scan list (about 110 warehouses in Houston and Los Angeles), not the whole market.
- Contact phone numbers and emails in this dataset are fake: say "the listing contact" (the broker firm), never \
read them out."""

ORCH_PROMPT = """You are the Warehouse PlayGround analyst: an industrial real-estate analyst who helps a buyer or operator \
decide "will this warehouse work for my operation?" across about 110 warehouses in Houston and Los Angeles \
(find -> shortlist -> inspect -> test -> decide). You do ANALYSIS with deterministic tools; the tools compute, \
you choose the right tool, decide what to investigate, and explain what the tool output means.

Answer format: SUBTLE AND CONCRETE. 1-2 short sentences, at most about 35 words: the answer itself with its key \
number(s) and source in a few words (e.g. "15550 Milner Rd: 194 docks (CoStar)."). No preamble ("One moment", \
"Great question"), no restating the question, no list of what you checked, no closing question unless you truly \
need an answer to continue. Add a caveat only if it changes the decision, in at most 6 words ("rent not listed"). \
Explain HOW or WHY (method, coverage, assumptions) only when the user asks ("why", "how", "explain", "more \
detail"); then up to 4 sentences. The screen shows the detail (tables, steps, evidence chips): never recite it. No markdown, lists \
or headings. Never write your reasoning, plans or tool choices as text: before a tool call write nothing or a \
2-5 word lead-in ending with a period ("Checking the docks."). Replies may be spoken.

Guided flow (the app opens with you asking "What are we looking for today?"):
- When the user states a need (business, size, trucks, market, must-haves), in ONE step call update_buyer_profile \
with only what they said AND search_warehouses with a query built from it (top=10). Reply with how many match \
and the #1 by address. Ask at most ONE short question, and only when a missing fact would change the ranking \
(size, market or truck type); otherwise ask nothing.
- "Take me to / show me / open <building>" -> ui openCard for it (it also moves the map). Never start a test \
unless the user asks for one in that message; offer it with suggest instead.
- Ordinal words ("first", "second", "the top two", "number 3", "the last one") refer to the bottom strip on \
screen in its order (see the session note). "Compare the first and second" -> compare_warehouses([#1 id, #2 id]) \
using the buyer's profile; it opens a side-by-side view over the map. Then say the recommended building and the \
single biggest difference in one sentence. "Close the comparison" / "back to the map" -> ui closeCompare.
- Curate to the buyer: rank and compare on what they care about, but only claim what the data shows; name an \
unknown that matters instead of guessing.

How to analyse:
- Map vague asks to a DEFINED metric and say which: "most loading docks" -> rank_attribute("docks_total"); \
"tallest" -> clear_height_ft; "biggest" -> rba_sqft; "cheapest" -> asking_rent; deeper yard -> truck_court_ft; \
more doors per area -> docks_per_10ksf. Aerial-only metrics (parking lot area, trucks/trailers seen, roof, solar, \
paved area) -> rank_by. Never refuse a superlative.
- Tiers: verified (CoStar) answers first; estimated values are ranges or lower bounds from imagery or the scan \
and must be called estimates ("at least 19 doors seen on the aerials"); unknown is NEVER zero. If an estimate or \
unknown could change the answer (e.g. a large building with no CoStar count), investigate it: call \
dock_evidence on the 2-3 largest unknown/estimated buildings and report what you found as estimates.
- "Compare this with others for my operation" -> find_comparables(selected id, profile or ops_text) then \
compare_warehouses([that id] + the comparables, same operation). Pick the operation from the buyer's words or \
profile (cross-dock is NOT storage: cross-dock = doors per SF, turns, both-side docks, court; storage = clear \
height, columns, floor). If the operation is a guess ("default"), say which one you used and offer the other. \
Compare 2-4 named buildings -> compare_warehouses directly. Recommend ONLY what compare_warehouses recommends \
(you may rephrase its recommendation text; add no new numbers) and mention the biggest unknown.
- "Can N trucks use this yard at once?" / "will 5 semis fit" -> yard_maneuver_test(id, trucks=[{type, count}]) \
(semi_53 unless they say box trucks or vans). It opens the maneuver test on screen; report the pre-check facts \
(doors vs trucks, court depth vs need) and say the animation will show waits and conflicts. A planner failure \
is "not found by our planner", never "impossible".
- "How many pallets will it hold?" / racks / storage / "cost per pallet position" / "which forklift" -> \
storage_capacity(id). Quote its positions (scanned area = measured layout; whole building = estimate), levels and \
why, and the truck trade-off; say $/position only when it returns one.
- Plain search ("warehouses in Houston over 50k") -> search_warehouses right away. Note buyer facts with \
update_buyer_profile when they mention business, size, trucks, equipment, market or must-haves.
- "Closest to <place type>" -> closest_to(place); hits are likely/possible.
- One building's facts -> building_brief. Interior or deep aerial questions -> task() with interior_analyst / \
vexcel_analyst; multi-building CoStar digging -> costar_analyst. Use subagents only when one tool can't answer.
- Drive the screens with `ui` when a tool didn't already (rank_attribute, compare_warehouses, rank_by, \
search_warehouses and yard_maneuver_test move the screen themselves). showDocks {id} opens the dock editor. \
Refer to buildings by street address, never by id.
- Inside the building there is ONE test: the interior planner. "Will my forklift fit / work inside?", "how many \
pallets?", "how many forklifts?" -> storage_capacity(id) (it opens the planner, where the user picks their \
operation; it reports forklift reach %, minutes per pallet and machines needed). Never offer a separate Equipment \
test. The old Yard test: prepare_test, read the plan back, run_test only after the user's yes in their NEXT message.
- Money: "what will it cost", "cheapest for my operation", "$ per pallet" -> cost_per_pallet(ids). Quote the
$ per pallet position per month and say where the rent comes from in a few words (asking rent / its own signed
lease / submarket comps / market-wide estimate). Never call an estimate the building's rent.
- Water: "will it flood", "flood zone", "is it high ground" -> flood_exposure(id). Say the indicator and the
FEMA zone, plus the one fact that drove it, in one sentence; add "not an insurance determination" once.
- Selling help: listing_contact; request_tour only logs the intent (nothing is sent; say so).
- Messages starting with [EVENT ...] are what the user just did on screen. Comment in 1-2 sentences from the facts \
given (one insight that matters for this buyer, or one unknown worth checking), then a next-step question.
- Call `suggest` with 2-3 next steps (analysis asks, e.g. "Compare for cross-dock", "Check dock evidence") in \
the same step as your last tool. If a tool errors, say so in one sentence and offer an alternative.

""" + RULES

COSTAR_PROMPT = """You are the CoStar analyst on a warehouse buying team. You answer multi-step questions from \
CoStar data: search and rank the catalog, explain rankings, read facts, listings (asking terms, listing contact), \
current tenants and the last sale. Work fast: at most 4 tool calls. Return a compact answer (<= 120 words) with \
the numbers you used and their source, addresses (not ids) plus ids in brackets, and what is unknown.
""" + RULES

VEXCEL_PROMPT = """You are the Vexcel aerial analyst. You read warehouses from the sky with S1's Vexcel \
intelligence: profile (parking, yard, roof, site, history, nearby places), rank by a metric, semantic search for \
place types near a building, open-vocabulary detection on ortho/oblique imagery, imagery views and metric \
definitions. Always give the capture date and method; search hits are likely/possible with their score. \
At most 5 tool calls. Return <= 120 words with numbers, sources and dates.
""" + RULES

INTERIOR_PROMPT = """You are the interior analyst. You read the Matterport-scan twin of a warehouse: roof deck \
height, CoStar-comparable clear height, lowest obstruction (P5), columns and bays, office area, scanned floor area \
(scans cover only part of the building: say which area). At most 3 tool calls. Return <= 100 words with sources.
""" + RULES

TEST_PROMPT = """You are the test engineer. You run the Yard test (trucks get in, turn, back into the docks, queue) \
and the Equipment test (machines fit and move inside), and explain the result: the verdict, and the ONE thing \
that limits it (tightest margin, pinch point, failed check, longest wait), plus the main assumption. run_test \
only works for a plan the user already confirmed. If the service isn't ready, say so. Return <= 100 words.
""" + RULES


def chat_model(max_tokens: int = 2000, effort: str = EFFORT):
    from langchain_anthropic import ChatAnthropic
    # Opus 5.5: thinking is always on (never send budget_tokens or thinking:disabled); depth via effort.
    return ChatAnthropic(model=MODEL, max_tokens=max_tokens, output_config={"effort": effort}, streaming=True,
                         max_retries=2)


_PROFILE_DONE = False


def _register_profile():
    """Drop deepagents' filesystem tools and the general-purpose subagent: the guide doesn't need them, and every
    tool schema costs latency on each turn."""
    global _PROFILE_DONE
    if _PROFILE_DONE:
        return
    from deepagents import HarnessProfile, register_harness_profile
    from deepagents.profiles import GeneralPurposeSubagentProfile
    register_harness_profile(f"anthropic:{MODEL}", HarnessProfile(
        excluded_tools=frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))
    _PROFILE_DONE = True


def strict_unsupported(reply: str, evidence: str) -> list[str]:
    """Digits in the reply that no tool returned (and the user didn't say). Unlike v1, small counts are NOT
    exempt ('8 trucks' must come from a tool); roundings of an evidence number (509,000 / 509 thousand ~ 508,553)
    are allowed."""
    have = numbers_in(evidence)
    hf = [float(h) for h in have if h.replace(".", "", 1).isdigit()]
    out = []
    for n in numbers_in(reply):
        if n in have:
            continue
        v = float(n)
        if any(abs(v - h) <= max(0.5, 0.05 * abs(h)) or abs(v * 1000 - h) <= 0.05 * abs(h) for h in hf):
            continue
        out.append(n)
    return sorted(out)


def _market(m: Optional[str]) -> Optional[str]:
    if not m:
        return None
    m = m.strip().lower()
    return "Los Angeles" if m.startswith(("la", "los", "l.a")) else "Houston" if m.startswith(("hou", "tx")) else None


def _j(o) -> str:
    return json.dumps(o, default=lambda x: x.item() if hasattr(x, "item") else str(x))


class Guide:
    """One buyer session (one websocket): the deep agent + buyer profile, shortlist and UI context."""

    def __init__(self, model=None, subagent_model=None):
        self.model = model
        self.subagent_model = subagent_model
        self.profile: dict = {"business": None, "size_sqft": None, "trucks": [], "equipment": [], "market": None,
                              "deal": None, "must_haves": [], "nice_to_haves": [], "notes": []}
        self.shortlist: list[int] = []
        self.context: dict = {}
        self.last_search: Optional[dict] = None
        self.proposal: Optional[dict] = None
        self.last_test: Optional[dict] = None
        self.tours: list[dict] = []
        self.turn_no = 0
        self.user_turn = True
        self.last_user_text = ""
        self.evidence: list[str] = []
        self.segments: list[str] = []           # last turn's text per AI message (the last is the answer)
        self.events_seen: set = set()
        self.recent_ui: dict = {}               # (cmd, id) -> time the guide itself issued it
        self.stage = "find"
        self.suggested = False
        self._emit: Optional[Callable[[dict], Awaitable[None]]] = None
        self.thread = uuid.uuid4().hex
        self.agent = self._build()

    # ---------------------------------------------------------- plumbing
    async def emit(self, ev: dict):
        if self._emit:
            await self._emit(ev)

    async def ui(self, cmd: str, args: dict):
        if cmd in ONE_ID and args.get("id") is not None:
            self.recent_ui[(cmd, int(args["id"]))] = time.monotonic()
        if cmd in ("openCard", "showInside", "showAerial", "flyTo") and args.get("id"):
            self.context["selected_id"] = int(args["id"])
        await self.emit({"type": "ui", "cmd": cmd, "args": args})

    def set_context(self, selected_id=None, chips=None, visible=None, market=None):
        if market in ("Houston", "Los Angeles"):           # the market tab on screen: the default for rankings
            self.context["market"] = market
        if selected_id is not None:
            self.context["selected_id"] = selected_id
        if chips is not None:
            self.context["chips"] = chips
        if visible is not None:                         # the bottom strip, in on-screen order (#1 = first)
            self.context["visible"] = [int(i) for i in visible if isinstance(i, (int, float, str)) and str(i).isdigit()][:10]

    def check_numbers(self, reply: str) -> list[str]:
        # Addresses on screen are not claims (e.g. "3200 Sam Houston Pky"), and small whole numbers 0-10 are counts the
        # agent makes over tool output (e.g. "3 dock positions"); everything else must appear in a tool result.
        ctx = [self._addr(i) for i in {self.context.get("selected_id"), *self.shortlist,
                                       *((self.last_search or {}).get("ids") or [])[:10],
                                       *(self.context.get("visible") or [])} if i]
        bad = strict_unsupported(reply, "\n".join([*self.evidence, *ctx]))
        return [n for n in bad if not re.fullmatch(r"(10|[0-9])", n.strip())]

    def _addr(self, pid) -> str:
        w = G.wh(pid)
        return w["address"] if w else str(pid)

    def _session_note(self) -> str:
        p = {k: v for k, v in self.profile.items() if v not in (None, [], "")}
        parts = [f"buyer profile: {_j(p) if p else 'nothing known yet'}"]
        if self.shortlist:
            parts.append("shortlist: " + "; ".join(f"{self._addr(i)} [{i}]" for i in self.shortlist))
        sid = self.context.get("selected_id")
        if sid:
            parts.append(f"on screen: {self._addr(sid)} [{sid}]")
        if self.context.get("market"):
            parts.append(f"market tab on screen: {self.context['market']} (rank and search there unless the user names another)")
        vis = self.context.get("visible") or []
        if vis:
            parts.append("bottom strip on screen, in order (\"first\" = #1): " + ", ".join(
                f"#{n} {self._addr(i)} [{i}]" for n, i in enumerate(vis, 1)))
        if self.last_search and not vis:
            parts.append("last results (best first): " + ", ".join(
                f"{self._addr(i)} [{i}]" for i in self.last_search["ids"][:5]))
        if self.proposal:
            parts.append(f"test plan awaiting the user's yes: {self.proposal['read_back']}")
        if self.context.get("chips"):
            parts.append("filter chips: " + _j(self.context["chips"])[:300])
        return "[Session: " + "; ".join(parts) + "]"

    # ---------------------------------------------------------- tools
    def _tool(self, fn):
        """Wrap an async tool: announce it, keep its JSON result as evidence, turn exceptions into tool errors."""
        @functools.wraps(fn)
        async def run(**kw):
            await self.emit({"type": "tool", "name": fn.__name__, "input": kw})
            try:
                out = await fn(**kw)
            except Exception as e:                      # a tool error is something the agent can speak about
                out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            txt = out if isinstance(out, str) else _j(out)
            self.evidence.append(txt)
            if fn.__name__ not in ("suggest",):
                try:
                    await self.emit({"type": "step", **AT.step_summary(fn.__name__, kw, out)})
                except Exception:
                    pass
            return txt
        from langchain_core.tools import StructuredTool
        return StructuredTool.from_function(coroutine=run, name=fn.__name__, description=(fn.__doc__ or "").strip())

    def _tools(self):  # noqa: C901 (one closure per tool, deliberately flat)
        g = self

        # ---------------- find
        async def search_warehouses(query: str, top: int = 5):
            """Search and rank our warehouse catalog from the buyer's words (size in SF, clear height, docks, market
            Houston/Los Angeles, lease/buy, available, 3D scan, business type). Empty query = browse all. Moves the
            map. Returns counts, the top results with checks and sources, and why others were excluded."""
            out, raw = await asyncio.to_thread(G.search, query, top, g.profile)
            g.last_search = raw
            g.stage = "results"
            await g.ui("search", {"query": raw["query"], "chips": raw["chips"]})
            return out

        async def rank_by(metric: str, market: Optional[str] = None, top: int = 5):
            """Rank warehouses by a Vexcel aerial metric. `metric` is a key (parking_area_sqft, paved_area_sqft,
            car_stalls_est, cars_seen, trucks_seen, trailers_seen, truck_court_ft, roof_condition, solar,
            tree_cover_pct, building_height_ft, footprint_sqft, parcel_acres, new_structure_m2) or the buyer's words
            ("biggest parking lot"); the tool picks the defined metric and alternatives. market: Houston | Los
            Angeles. Highlights the top results on the map."""
            m, alts = VX.interpret(metric)
            if m.startswith("distance_to:"):
                return await closest_to(m.split(":", 1)[1], market)
            mk = _market(market) or g.profile.get("market") or g.context.get("market")
            r = await VX.rank(m, mk, max(1, min(int(top or 5), 10)))
            defs = (await VX.metrics())["metrics"]
            used = r.get("metric", m)
            rows = [{**{k: x.get(k) for k in ("id", "address", "value", "unit", "source", "date", "confidence",
                                              "estimate", "note") if x.get(k) is not None}} for x in r.get("rows") or []]
            out = {"asked": metric, "metric": used, "definition": VX.definition_of(used, defs),
                   "alternatives": [{"metric": a, "definition": VX.definition_of(a, defs)} for a in alts[:2]],
                   "caveat": VX.CAVEATS.get(used), "market": mk, "top": rows, "coverage": r.get("coverage"), "notes": r.get("notes") or None,
                   "error": r.get("error")}
            if used != m:
                out["fallback"] = f"no values yet for {m}; ranked by {used} instead (say so)"
            if rows:
                g.stage = "results"
                g.last_search = {"query": metric, "chips": [], "ids": [x["id"] for x in rows],
                                 "raw": {"results": [], "excluded": [], "counts": {"shown": len(rows)}}}
                await g.ui("rankBy", {"metric": used, "market": mk})
                await g.ui("highlight", {"ids": [x["id"] for x in rows]})
                await g.ui("flyTo", {"id": rows[0]["id"]})
            return out

        async def closest_to(place: str, market: Optional[str] = None, among: Optional[list[int]] = None):
            """Find the warehouse closest to a place TYPE (grocery store, highway ramp, rail yard, port/containers,
            truck stop, residential) with Vexcel semantic search (calibrated likely/possible hits). `among` = ids to
            limit to (e.g. the last results when the user says 'which of these'); default: the whole catalog in
            `market`. Shows the place and the warehouse on the map."""
            ids = [int(i) for i in among or [] if G.wh(i)] or None
            r = await VX.closest_to(place, ids, _market(market) or g.profile.get("market") or g.context.get("market"))
            rows = [{k: x.get(k) for k in ("id", "address", "distance_m", "likelihood", "score", "label")}
                    for x in (r.get("rows") or [])[:5]]
            out = {"place": r.get("place") or place, "closest": rows, "source": r.get("source"),
                   "caveat": r.get("caveat"), "notes": r.get("notes") or None,
                   "how": "aerial semantic search: hits are likely/possible, never certain; straight-line distance"}
            best = (r.get("rows") or [None])[0]
            if best:
                if best.get("lat") is not None:
                    await g.ui("showPlace", {"lat": best["lat"], "lon": best["lon"],
                                             "label": f"{best.get('likelihood') or 'possible'}: "
                                                      f"{best.get('label') or place}",
                                             **({"thumb_url": best["thumb_url"]} if best.get("thumb_url") else {})})
                await g.ui("flyTo", {"id": best["id"]})
                await g.ui("highlight", {"ids": [x["id"] for x in rows]})
                await g.ui("results", {"title": f"Closest to {out['place']}", "ids": [x["id"] for x in rows],
                                       "values": {str(x["id"]): f"{x.get('likelihood') or 'possible'} · "
                                                  f"{round((x.get('distance_m') or 0) / 1609, 1)} mi"
                                                  for x in rows if x.get("distance_m") is not None}})
                g.stage = "results"
                g.last_search = {"query": f"closest to {place}", "chips": [], "ids": [x["id"] for x in rows],
                                 "raw": {"results": [], "excluded": [], "counts": {"shown": len(rows)}}}
            return out

        # ---------------- inspect
        async def building_brief(id: int):
            """The attributes that matter for one warehouse, with sources: availability + asking terms + listing
            contact, size vs the buyer's need, clear height (CoStar vs scan), docks, yard/truck court, parking,
            power, Vexcel roof/trucks/nearby, and `fit_for_buyer` mismatches/unknowns for THIS buyer."""
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            vx = await VX.profile(w["id"])
            vx = None if vx.get("error") else vx
            g.stage = "inspect"
            return await asyncio.to_thread(G.brief, w, g.profile, vx)

        async def find_address(text: str):
            """Resolve a spoken street address ('3200 Sam Houston') to our warehouse id."""
            w = G.find_by_address(text)
            return {"id": w["id"], "address": w["address"], "market": w["market"]} if w else \
                {"error": "no warehouse in our catalog matches that address"}

        # ---------------- buyer profile + shortlist
        async def update_buyer_profile(business: Optional[str] = None, size_sqft: Optional[float] = None,
                                       trucks: Optional[list[str]] = None, equipment_used: Optional[list[str]] = None,
                                       market: Optional[str] = None, deal: Optional[str] = None,
                                       must_haves: Optional[list[str]] = None,
                                       nice_to_haves: Optional[list[str]] = None, note: Optional[str] = None):
            """Remember what the buyer said about themselves (only what they said): business type, space need in
            SF, trucks (e.g. '53-foot trailers', 'box trucks', 'vans'), equipment (e.g. 'reach trucks',
            'forklifts'), market, lease/buy, must-haves, nice-to-haves. Returns the stored profile."""
            p, unknown = g.profile, []
            if business:
                p["business"] = G.norm_business(business) or business
            if size_sqft:
                p["size_sqft"] = float(size_sqft)
            for t in trucks or []:
                k = G.norm_truck(t)
                if k and k not in p["trucks"]:
                    p["trucks"].append(k)
                elif not k:
                    unknown.append(t)
            for e in equipment_used or []:
                k = G.norm_equipment(e)
                if k and k not in p["equipment"]:
                    p["equipment"].append(k)
                elif not k:
                    unknown.append(e)
            if market:
                p["market"] = "Los Angeles" if market.lower().startswith(("la", "los")) else "Houston"
            if deal in ("lease", "buy", "either"):
                p["deal"] = deal
            p["must_haves"] += [m for m in must_haves or [] if m not in p["must_haves"]]
            p["nice_to_haves"] += [m for m in nice_to_haves or [] if m not in p["nice_to_haves"]]
            if note:
                p["notes"].append(note)
            await g.ui("profile", {"profile": {k: v for k, v in p.items() if v not in (None, [], "")}})
            return {"profile": p, "not_in_library": unknown or None,
                    "library": {"trucks": list(G.TRUCKS)}}

        async def shortlist(action: str, id: Optional[int] = None):
            """The buyer's shortlist: action add | remove | show (show also opens the compare view)."""
            if action in ("add", "remove"):
                w = G.wh(id) if id is not None else None
                if not w:
                    return {"error": "which warehouse? give a valid id"}
                if action == "add" and w["id"] not in g.shortlist:
                    g.shortlist.append(w["id"])
                if action == "remove" and w["id"] in g.shortlist:
                    g.shortlist.remove(w["id"])
                await g.ui("shortlist", {"action": action, "id": w["id"]})
                g.stage = "shortlist"
            elif action == "show":
                await g.ui("shortlist", {"action": "show"})
                if len(g.shortlist) >= 2:
                    await g.ui("compare", {"ids": g.shortlist})
            else:
                return {"error": "action must be add, remove or show"}
            return {"shortlist": [{"id": i, "address": g._addr(i)} for i in g.shortlist]}

        # ---------------- selling help
        async def listing_contact(id: int):
            """Listing status, asking rent/price, available space, the listing contact (broker firm), current
            tenants and the last sale for one warehouse."""
            w = G.wh(id)
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            return {"address": w["address"], **G.listing_terms(w)}

        async def request_tour(id: int, when: Optional[str] = None, note: Optional[str] = None):
            """Log that the buyer wants a tour (nothing is sent externally)."""
            w = G.wh(id)
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            r = await asyncio.to_thread(G.log_tour, w["id"], when, note, g.profile)
            g.tours.append({"id": w["id"], "when": when})
            g.stage = "decide"
            return {"address": w["address"], **r}

        # ---------------- tests (gated)
        async def prepare_test(kind: str, id: int, trucks: Optional[list[str]] = None,
                               per_day: Optional[int] = None, equipment_key: Optional[str] = None):
            """Plan a Yard test (kind='yard': trucks e.g. ['semi_53'], optional per_day) or an Equipment test
            (kind='equipment': equipment_key e.g. 'reach_truck') for one warehouse. Returns a read-back to confirm
            with the user, or what's missing, or 'not ready' when that test service isn't available."""
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            if kind == "yard":
                path = "/api/yard/test"
                ts = [G.norm_truck(t) for t in trucks or []] or list(g.profile.get("trucks") or [])
                ts = [t for t in ts if t]
                if not ts:
                    return {"missing": ["trucks"], "options": list(G.TRUCKS)}
                payload = {"property_id": w["id"], "trucks": [{"type": t, **({"per_day": int(per_day)} if per_day
                                                                             else {})} for t in ts]}
                names = " and ".join(equipment()[t]["name"] for t in ts)
                read_back = f"Yard test at {w['address']}: {names}" + (f", {per_day} a day" if per_day else "") + \
                    ", turning and backing into the docks (doors marked in the scan if any; otherwise positions are " \
                    "assumed and I'll say so)."
            elif kind == "equipment":
                path = "/api/fit/test"
                k = G.norm_equipment(equipment_key or "") or next(iter(g.profile.get("equipment") or []), None)
                if not k:
                    return {"missing": ["equipment"], "options": [x for x, v in equipment().items()
                                                                  if v.get("indoor_ok")]}
                if not w.get("inspectable"):
                    return {"error": f"{w['address']} has no interior scan, so the equipment test can't run there"}
                payload = {"property_id": w["id"], "equipment": k, "route": {"to": "auto"}, "floor": "emptied"}
                read_back = f"Equipment test at {w['address']}: a {equipment()[k]['name']} driving from the docks " \
                            f"through the emptied floor."
            else:
                return {"error": "kind must be 'yard' or 'equipment'"}
            if not G.route_ready(path):
                return {"not_ready": True, "error": f"the {kind} test service isn't available yet",
                        "can_check_now": "truck court depth and doors from CoStar/Vexcel (building_brief)"
                        if kind == "yard" else "clear height and columns from the scan (building_brief)"}
            g.proposal = {"kind": kind, "path": path, "payload": payload, "read_back": read_back,
                          "turn": g.turn_no, "id": w["id"]}
            g.stage = "test"
            return {"read_back": read_back, "ask": "read this back and ask the user to confirm; run_test only "
                                                   "after their yes in the next message"}

        async def run_test(kind: str):
            """Run the test the user CONFIRMED (prepare_test in an earlier turn + the user's yes now). Opens the
            result on screen. Returns a compact verdict: what passed, the tightest margin / pinch point, queueing."""
            p = g.proposal
            if not p or p["kind"] != kind:
                return {"error": f"no {kind} test planned: call prepare_test and read the plan back first"}
            if p["turn"] >= g.turn_no or not g.user_turn or NO.search(g.last_user_text or ""):
                return {"error": "not confirmed yet: read the plan back and wait for the user's yes in their "
                                 "next message"}
            status, body = await G.post(p["path"], p["payload"])
            if status == 404 and body == "no route":
                return {"error": f"the {kind} test service isn't available yet"}
            if status != 200 or not isinstance(body, dict):
                return {"error": f"{kind} test failed ({status}): {str(body)[:300]}"}
            g.proposal = None
            summ = G.summarize_yard(body) if kind == "yard" else G.summarize_fit(body)
            g.last_test = {"kind": kind, "id": p["id"], "summary": summ}
            g.stage = "tested"
            cmd = "yardTest" if kind == "yard" else "equipmentTest"
            extra = {"trucks": p["payload"]["trucks"]} if kind == "yard" else {"equipment": p["payload"]["equipment"]}
            await g.ui(cmd, {"id": p["id"], **extra, "run_id": body.get("run_id")})
            g.events_seen.add(("testResult", str(body.get("run_id"))))
            return summ

        async def last_test_result():
            """The most recent test result in this session (compact)."""
            return g.last_test or {"error": "no test has run in this session"}

        # ---------------- screens
        async def ui(cmd: str, id: Optional[int] = None, ids: Optional[list[int]] = None,
                     options: Optional[dict] = None):
            """Drive the app. cmd: flyTo | openCard | showInside (inspectable only) | showAerial (options: view, year)
            | yardTest / equipmentTest (open the test form for `id`) | highlight / compare (`ids`) | showPlace
            (options: lat, lon, label) | rankBy (options: metric, market) | search (options: query) | shortlist
            (options: action). showAerial options: view, year."""
            a = dict(options or {})
            if cmd not in UI_CMDS:
                return {"error": f"unknown cmd {cmd}", "cmds": list(UI_CMDS)}
            if cmd in ("yardTest",) and not g._asked(ASK_YARD) or \
                    cmd in ("storageTest", "equipmentTest") and not g._asked(ASK_INSIDE):
                return {"error": "not opened: the user did not ask for a test in this message; offer it with suggest"}
            if cmd in ONE_ID:
                w = G.wh(id if id is not None else a.get("id"))
                if not w:
                    return {"error": "no valid warehouse id"}
                if cmd == "showInside" and not w.get("inspectable"):
                    return {"error": f"{w['address']} has no interior scan; offer the aerial instead"}
                a["id"] = w["id"]
                await g.ui(cmd, a)
                g.stage = "inspect" if cmd in ("openCard", "showInside", "showAerial") else g.stage
                return {"ok": True, "address": w["address"]}
            if cmd in ("highlight", "compare"):
                good = [int(i) for i in ids or a.get("ids") or [] if G.wh(i)]
                if not good:
                    return {"error": "no valid warehouse ids"}
                await g.ui(cmd, {"ids": good})
                return {"ok": True, "addresses": [g._addr(i) for i in good]}
            if cmd == "showPlace" and (a.get("lat") is None or a.get("lon") is None):
                return {"error": "showPlace needs lat and lon from a tool result"}
            if cmd == "search":
                a.setdefault("query", "")
            await g.ui(cmd, a)
            return {"ok": True}

        async def suggest(items: list[dict]):
            """Next-step chips under the orb: 2-3 items {label (2-4 words), say (what the user would say)}."""
            good = [{"label": str(i.get("label"))[:40], "say": str(i.get("say") or i.get("label"))[:160]}
                    for i in items or [] if isinstance(i, dict) and i.get("label")][:3]
            if good:
                g.suggested = True
                await g.emit({"type": "suggestions", "items": good})
            return {"ok": bool(good)}

        # ---------------- subagent-only tools (no screen changes)
        async def get_facts(id: int):
            """All known facts for one warehouse (CoStar + measured), each with source, confidence and alternatives."""
            w = G.wh(id)
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            full = await asyncio.to_thread(G._api().warehouse, int(id))
            facts = {k: {kk: v.get(kk) for kk in ("value", "source", "confidence", "detail", "alt")
                         if v.get(kk) not in (None, [], "")} for k, v in full["facts"].items()}
            keep = ("id", "address", "market", "subtype", "status", "building_class", "year_built", "inspectable",
                    "availability", "tenancy", "zoning", "lot_acres", "parking_spaces")
            return {**{k: full.get(k) for k in keep}, "facts": facts}

        async def explain_rank(id: int):
            """Why a warehouse ranks where it does in the last search (checks need vs have), or why excluded."""
            if not g.last_search:
                return {"error": "no search yet"}
            res = g.last_search["raw"]
            for r in res["results"]:
                if r["id"] == int(id):
                    return {k: r.get(k) for k in ("rank", "address", "mode", "score", "band", "checks",
                                                  "unverified_dealbreakers")}
            for e in res["excluded"]:
                if e["id"] == int(id):
                    return {"excluded": True, "address": e["address"], "why": e["why"]}
            return {"error": "not in the last search"}

        async def compare_buildings(ids: list[int]):
            """Side-by-side briefs (selling terms, size, clear height, docks, yard, power, Vexcel, fit) for 2-4 ids."""
            out = []
            for i in (ids or [])[:4]:
                w = G.wh(i)
                if w:
                    vx = await VX.profile(w["id"])
                    out.append(G.brief(w, g.profile, None if vx.get("error") else vx))
            return out or {"error": "no valid ids"}

        async def vexcel_profile(id: int):
            """S1's Vexcel profile for one warehouse: parking/yard, building/roof, site, history, nearby places."""
            return VX.compact(await VX.profile(int(id)))

        async def vexcel_rank(metric: str, market: Optional[str] = None, top: int = 5):
            """Rank warehouses by a Vexcel metric key (see vexcel_metrics)."""
            return await VX.rank(VX.interpret(metric)[0], market, int(top or 5))

        async def vexcel_search(text: str, near_id: int, radius_m: int = 1500):
            """Semantic search for a place type near one warehouse -> hits with distance, score, likelihood."""
            return await VX.search(text, near_id=int(near_id), radius_m=int(radius_m))

        async def vexcel_detect(id: int, labels: list[str], view: str = "ortho"):
            """Open-vocabulary detection (SAM3) of labels on ortho or an oblique view for one warehouse."""
            return await VX.detect(int(id), labels, view)

        async def vexcel_imagery(id: int, view: Optional[str] = None, year: Optional[int] = None):
            """Imagery available for one warehouse (ortho/oblique views, historical years)."""
            return await VX.imagery(int(id), view, year)

        async def vexcel_metrics():
            """Definitions of every Vexcel metric (what it measures, unit, method)."""
            return await VX.metrics()

        async def interior_facts(id: int):
            """Interior twin facts from the Matterport scan: deck, clear height (CoStar-comparable and P5),
            columns/bays, office area, scanned floor area, scan date."""
            w = G.wh(id)
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            i = await asyncio.to_thread(G.interior, w)
            return {"address": w["address"], "rba_sqft": w.get("rba_sqft"), "costar_clear_ft": w.get("clear_height_ft"),
                    "column_spacing_costar_ft": w.get("column_spacing_ft"),
                    "interior": i or "no measured warehouse-floor scan for this building",
                    "scans": [{k: s.get(k) for k in ("model_id", "class", "floor_sqft", "height_ft")}
                              for s in w.get("scans") or []]}

        # ---------------- analysis (deterministic, analytics.py)
        async def rank_attribute(attr: str, market: Optional[str] = None, order: Optional[str] = None,
                                 top: int = 5, among: Optional[list[int]] = None):
            """Rank warehouses by a CoStar/derived attribute in THREE tiers: verified (CoStar), estimated (imagery or
            scan: range or lower bound) and unknown (never 0). attr: docks_total | docks_per_10ksf | drive_ins |
            clear_height_ft | rba_sqft | truck_court_ft | column_spacing_ft | power_amps | parking_spaces |
            lot_acres | year_built | asking_rent | drive_in_h_ft, or the buyer's words ("most loading docks").
            order desc|asc (default per attribute; rent ascending). `among` limits to ids (e.g. the last results).
            Returns tops per tier, coverage counts and the largest unknown buildings. Moves the map."""
            mk = _market(market) or g.profile.get("market") or g.context.get("market")
            ids = [int(i) for i in among or [] if G.wh(i)] or None
            out = await asyncio.to_thread(AT.rank_attribute, attr, mk, order, top, ids)
            ranked = [x["id"] for x in out["verified_top"]] + [x["id"] for x in out["estimated_top"]]
            if ranked:
                g.stage = "results"
                g.last_search = {"query": attr, "chips": [], "ids": ranked,
                                 "raw": {"results": [], "excluded": [], "counts": {"shown": len(ranked)}}}
                if ids:                                     # ranked within a set: show exactly that list
                    await g.ui("results", {"title": f"{out.get('label', out['attr'])} ({'lowest' if out['order'] == 'asc' else 'highest'} first)",
                                           "ids": ranked[:10]})
                else:
                    await g.ui("rankAttribute", {"attr": out["attr"], "market": out["market"], "order": out["order"]})
                await g.ui("highlight", {"ids": ranked[:10]})
                await g.ui("flyTo", {"id": ranked[0]})
            return out

        async def dock_evidence(ids: list[int]):
            """Dock-door evidence for 1-6 warehouses: CoStar count, doors detected on the 4 oblique aerials, trailers
            parked at doors, doors a person confirmed in the dock editor, and a fused estimate {value | lo-hi,
            status verified|estimated|lower_bound|unknown, basis}. Use it to investigate unknown/estimated tiers."""
            return await asyncio.to_thread(AT.dock_evidence, [int(i) for i in ids or []])

        async def compare_warehouses(ids: list[int], profile: Optional[str] = None, ops_text: Optional[str] = None):
            """Compare 2-4 warehouses for an operation. profile: an operation key (cross_dock, pallet_storage,
            ecommerce, cold_storage, last_mile, manufacturing, or a buyer profile: pallet_3pl, food_beverage,
            ecommerce_fulfillment, last_mile_station, light_manufacturing, building_materials, auto_parts) or
            ops_text = the buyer's words. Picks 5-7 weighted criteria, gives per-criterion values with source and
            kind (verified/estimated/unknown), a winner only beyond tolerance, fit pass/flag/fail on deal-breakers,
            tradeoffs and a template recommendation. Opens the compare panel."""
            good = [int(i) for i in ids or [] if G.wh(i)]
            if len(good) < 2:
                return {"error": "compare needs 2-4 valid warehouse ids"}
            prof = profile or (None if ops_text else g.profile.get("business"))
            if not prof and not ops_text:               # curate to what the buyer told us, if anything
                bits = [g.profile.get(k) for k in ("business",) if g.profile.get(k)] + \
                       list(g.profile.get("must_haves") or []) + list(g.profile.get("nice_to_haves") or []) + \
                       [t.replace("_", " ") for t in g.profile.get("trucks") or []]
                ops_text = ", ".join(str(b) for b in bits) or None
            out, full = await asyncio.to_thread(AT.compare, good[:4], prof, ops_text)
            await g.ui("compare", {"ids": good[:4], "profile": full["profile"]})
            g.stage = "shortlist"
            return out

        async def find_comparables(id: int, profile: Optional[str] = None, ops_text: Optional[str] = None):
            """Up to 3 comparable warehouses for one building: same market, +-40% size, not failing the operation's
            deal-breakers, best weighted criteria score. profile / ops_text as in compare_warehouses."""
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            prof = profile or (None if ops_text else g.profile.get("business"))
            out = await asyncio.to_thread(AT.comparables, w["id"], prof, ops_text)
            cids = [int(i) for i in (out.get("ids") or [x.get("id") for x in out.get("comparables") or []]) if i]
            if cids:
                await g.ui("results", {"title": f"Comparable to {w.get('address')}", "ids": [w["id"], *cids]})
            return out

        async def yard_maneuver_test(id: int, trucks: list[dict]):
            """'Can N trucks use this yard at once?': opens the yard maneuver test on screen for one warehouse with
            trucks [{type: semi_53|box_truck_26|last_mile_van, count}] and returns deterministic pre-checks (dock
            doors vs trucks, truck court depth vs the depth the largest truck needs). The animated result comes
            back later as a testResult event."""
            if not g._asked(ASK_YARD):
                return {"error": "not run: the user did not ask for a yard test in this message. Do not run tests on "
                                 "your own; offer it with suggest (e.g. 'Yard test: 5 semis') and wait."}
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            ts = AT.parse_trucks(trucks or [])
            if not ts:
                t = next(iter(g.profile.get("trucks") or []), "semi_53")
                ts = [{"type": t, "count": 1}]
            out = await asyncio.to_thread(AT.yard_precheck, w["id"], ts)
            g.stage = "test"
            g.context["selected_id"] = w["id"]
            await g.ui("yardTest", {"id": w["id"], "trucks": ts, "mode": "maneuver"})
            return out

        async def storage_capacity(id: int, equipment: str = "reach_truck"):
            """Pallet storage on the REAL scanned floor: selective racks laid out around the scan's columns, levels
            from the scan's lowest obstruction and CoStar's sprinkler type, positions (scanned + whole-building
            estimate), SF and $ per position, positions lost to columns, and the same floor with counterbalance /
            reach / VNA trucks. Opens the storage planner on screen. Needs a Matterport warehouse-floor scan."""
            from proinspect import racks
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            try:
                p = await asyncio.to_thread(racks.plan, w["id"], equipment)
                t = await asyncio.to_thread(racks.compare_trucks, w["id"])
            except FileNotFoundError as e:
                return {"error": str(e)}
            g.stage = "test"
            g.context["selected_id"] = w["id"]
            if g._asked(ASK_INSIDE):                     # the numbers are fine to quote; the planner opens only on ask
                await g.ui("storageTest", {"id": w["id"]})
            keep = ("positions", "levels", "levels_basis", "aisle_ft", "scanned_sqft", "sqft_per_position",
                    "building_estimate", "cost_per_position_yr", "positions_lost_to_columns", "sprinkler", "clear")
            return {"address": w.get("address"), "equipment": p["equipment_name"], **{k: p[k] for k in keep},
                    "trucks": t["trucks"]}

        async def cost_per_pallet(ids: list[int], operation: Optional[str] = None):
            """True cost per pallet for 1-4 warehouses: rent (asking > the building's own signed lease > submarket
            lease comps > market-wide comps; each says which), racking amortised, forklift leases and operators for
            the operation, as $ per pallet position per month and $ per pallet moved. Assumed unit costs are
            labelled assumed. Use for "what will it cost", "which is cheapest for my operation"."""
            from proinspect import economics as EC
            if not EC.ENABLED:
                return {"error": "the cost model is switched off"}
            op = operation or g.profile.get("business") or "pallet_3pl"
            out = []
            for i in [int(x) for x in ids or [] if G.wh(x)][:4]:
                c = await asyncio.to_thread(EC.cost, i, op)
                out.append({"id": i, "address": c["address"], "headline": c["headline"],
                            "per_position_month": c["per_position_month"], "per_pallet_moved": c["per_pallet_moved"],
                            "rent": {k: c["rent"].get(k) for k in ("tier", "value", "low", "high", "basis")},
                            "lines": [{k: l[k] for k in ("label", "value", "source")} for l in c["lines"]],
                            "positions": c.get("positions"), "space_sqft": c["space"]["sqft"],
                            "note": c.get("plan_note")})
            return {"operation": op, "results": out} if out else {"error": "no valid warehouse ids"}

        async def flood_exposure(id: int):
            """Flood exposure of one warehouse from MEASURED Vexcel elevation (pad height above the surroundings, low
            spots, height above the nearest bayou/channel) plus the official FEMA flood zone; returns the indicator
            (Higher / Moderate / Lower exposure, partial when data is missing) and the rule that fired. Not an
            insurance flood determination. First run for a building can take ~30-60 s."""
            from proinspect import flood as FL
            if not FL.ENABLED:
                return {"error": "flood exposure is switched off"}
            w = G.wh(id) or G.find_by_address(str(id))
            if not w:
                return {"error": f"warehouse {id} is not in the catalog"}
            r = await asyncio.to_thread(FL.assess, w["id"], True, True)
            await g.ui("showFlood", {"id": w["id"]})
            return {"address": r.get("address"), "indicator": r.get("indicator"), "fema": (r.get("fema") or {}).get("label"),
                    "waterway": r.get("waterway"),
                    "facts": {k: {"value": v["value"], "unit": v["unit"], "source": v["source"]} for k, v in r["facts"].items()},
                    "missing": r.get("missing"), "note": r.get("note")}

        T = {f.__name__: self._tool(f) for f in (
            search_warehouses, rank_by, closest_to, building_brief, find_address, update_buyer_profile, shortlist,
            listing_contact, request_tour, prepare_test, run_test, last_test_result, ui, suggest, get_facts,
            explain_rank, compare_buildings, vexcel_profile, vexcel_rank, vexcel_search, vexcel_detect,
            vexcel_imagery, vexcel_metrics, interior_facts, rank_attribute, dock_evidence, compare_warehouses,
            find_comparables, yard_maneuver_test, storage_capacity, cost_per_pallet, flood_exposure)}
        orch = [T[n] for n in ("search_warehouses", "rank_attribute", "dock_evidence", "compare_warehouses", "cost_per_pallet",
                               "flood_exposure",
                               "find_comparables", "yard_maneuver_test", "storage_capacity", "rank_by", "closest_to", "building_brief",
                               "find_address", "update_buyer_profile", "shortlist", "listing_contact", "request_tour",
                               "prepare_test", "run_test", "ui", "suggest")]
        subs = {
            "costar_analyst": [T[n] for n in ("search_warehouses", "rank_attribute", "compare_warehouses",
                                              "get_facts", "explain_rank", "compare_buildings", "listing_contact",
                                              "building_brief")],
            "vexcel_analyst": [T[n] for n in ("vexcel_profile", "vexcel_rank", "vexcel_search", "vexcel_detect",
                                              "vexcel_imagery", "vexcel_metrics", "dock_evidence")],
            "interior_analyst": [T[n] for n in ("interior_facts", "get_facts")],
            "test_engineer": [T[n] for n in ("run_test", "last_test_result", "building_brief", "interior_facts")],
        }
        return orch, subs

    def _build(self):
        _register_profile()
        from deepagents import create_deep_agent
        from langgraph.checkpoint.memory import InMemorySaver
        orch, subs = self._tools()
        sm = self.subagent_model or (chat_model(1500) if self.model is None else self.model)
        subagents = [
            {"name": "costar_analyst", "description": "Multi-step CoStar work: search/rank, explain rankings, compare "
             "2-4 buildings in depth (facts, listings, tenants, last sale). Give it ids and the question.",
             "system_prompt": COSTAR_PROMPT, "tools": subs["costar_analyst"], "model": sm},
            {"name": "vexcel_analyst", "description": "Full Vexcel aerial investigation: profile, rank, place search "
             "near a building, detection on ortho/oblique, imagery history, metric definitions. Give it ids and "
             "the question.", "system_prompt": VEXCEL_PROMPT, "tools": subs["vexcel_analyst"], "model": sm},
            {"name": "interior_analyst", "description": "Interior questions from the Matterport scan twin (clear "
             "height, columns, bays, office, scanned area). Give it the id.",
             "system_prompt": INTERIOR_PROMPT, "tools": subs["interior_analyst"], "model": sm},
            {"name": "test_engineer", "description": "Runs a CONFIRMED Yard/Equipment test and explains the verdict "
             "and its limiting factor; or explains the last test in depth.",
             "system_prompt": TEST_PROMPT, "tools": subs["test_engineer"], "model": sm},
        ]
        return create_deep_agent(model=self.model or chat_model(1200), tools=orch, system_prompt=ORCH_PROMPT,
                                 subagents=subagents, checkpointer=InMemorySaver(), name="guide")

    # ---------------------------------------------------------- default next-step chips
    def derived_suggestions(self) -> list[dict]:
        sid = self.context.get("selected_id")
        addr = self._addr(sid) if sid else None
        trucks = self.profile.get("trucks") or []
        eq = self.profile.get("equipment") or []
        s: list[dict] = []
        if self.stage in ("inspect", "test") and sid:
            if sid not in self.shortlist:
                s.append({"label": "Add to shortlist", "say": f"Add {addr} to my shortlist"})
            if trucks:
                s.append({"label": "Yard test", "say": f"Can my {equipment()[trucks[0]]['name']} back into the docks "
                                                        f"at {addr}?"})
            elif eq:
                s.append({"label": "Interior planner", "say": f"Will my {equipment()[eq[0]]['name']} work inside {addr}?"})
            else:
                s.append({"label": "Show the aerial", "say": f"Show me the aerial of {addr}"})
            s.append({"label": "Compare for my operation",
                      "say": f"Compare {addr} with other warehouses suitable for my operation"})
        elif self.stage == "tested" and sid:
            s += [{"label": "Add to shortlist", "say": f"Add {addr} to my shortlist"},
                  {"label": "Compare options", "say": "Compare my shortlist"},
                  {"label": "Request a tour", "say": f"Request a tour of {addr}"}]
        elif self.stage in ("shortlist", "decide"):
            s += [{"label": "Compare shortlist", "say": "Compare my shortlist"},
                  {"label": "Which to pick?", "say": "Which one should I choose?"},
                  {"label": "Request a tour", "say": "Request a tour of the best one"}]
        elif self.stage == "results" and self.last_search and self.last_search["ids"]:
            top = self.last_search["ids"][0]
            s += [{"label": "Open the top one", "say": f"Tell me about {self._addr(top)}"},
                  {"label": "Most loading docks", "say": "Which of these has the most loading docks?"},
                  {"label": "Biggest parking lot", "say": "Which of these has the biggest parking lot?"}]
        else:
            s += [{"label": "Most loading docks", "say": "Find me the warehouse with the highest number of loading docks"},
                  {"label": "Tallest clear height", "say": "Which Houston warehouse has the tallest clear height?"},
                  {"label": "I'm a 3PL", "say": "I'm a 3PL running 53-foot trailers, about 40k square feet"}]
        return s[:3]

    # ---------------------------------------------------------- one turn
    async def _run(self, content: str, emit) -> str:
        from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
        self._emit = emit
        self.suggested = False
        said: list[str] = []
        segs: list[list[str]] = []          # text per AI message: the last one is the answer, earlier ones interim
        self.segments = []
        last_msg = None
        cfg = {"configurable": {"thread_id": self.thread}, "recursion_limit": 60}
        try:
            async for ns, (chunk, meta) in self.agent.astream({"messages": [HumanMessage(content)]}, config=cfg,
                                                              stream_mode="messages", subgraphs=True):
                if ns or not isinstance(chunk, AIMessage):
                    continue                                  # subagent tokens are not spoken
                if not isinstance(chunk, AIMessageChunk) and chunk.id and chunk.id == last_msg:
                    continue                                  # a full message we already streamed as chunks
                if chunk.id and chunk.id != last_msg:
                    segs.append([])
                    if last_msg is not None and said and not said[-1].endswith((" ", "\n")):
                        said.append(" ")
                        await emit({"type": "assistant_delta", "text": " "})
                    last_msg = chunk.id
                blocks = chunk.content if isinstance(chunk.content, list) else \
                    [{"type": "text", "text": chunk.content}] if chunk.content else []
                for b in blocks:
                    t = b.get("type") if isinstance(b, dict) else None
                    if t == "text" and b.get("text"):
                        said.append(b["text"])
                        if segs:
                            segs[-1].append(b["text"])
                        await emit({"type": "assistant_delta", "text": b["text"]})
                    elif t == "tool_use":
                        if not "".join(said).strip() and getattr(self, "speaking", True):   # spoken turns only: a lead-in fills the silence
                            lead = LEAD_INS.get(b.get("name"), "One moment.")
                            said.append(lead + " ")
                            await emit({"type": "assistant_delta", "text": lead + " "})
                        await emit({"type": "tool_pending"})
            reply = "".join(said).strip()
            self.segments = [x for x in ("".join(sg).strip() for sg in segs) if x]
            if not self.suggested:
                await emit({"type": "suggestions", "items": self.derived_suggestions()})
            return reply
        finally:
            self._emit = None

    def _asked(self, rx: re.Pattern) -> bool:
        """True only inside a user turn whose own words ask for this kind of test."""
        return bool(getattr(self, "user_turn", False) and rx.search(getattr(self, "last_user_text", "") or ""))

    async def turn(self, text: str, emit: Callable[[dict], Awaitable[None]]) -> str:
        """One spoken/typed user turn. Cancel-safe (barge-in): the checkpointer drops dangling tool calls."""
        self.turn_no += 1
        self.user_turn = True
        self.last_user_text = text
        self.evidence.append(text)
        if self.proposal and NO.search(text):
            self.proposal["turn"] = self.turn_no          # "no / change it" never counts as the confirmation
        return await self._run(f"{self._session_note()}\n{text}", emit)

    def _event_key(self, name: str, data: dict):
        d = data or {}
        k = d.get("run_id") or d.get("id") or d.get("property_id") or (d.get("result") or {}).get("run_id") \
            if isinstance(d, dict) else None
        if name == "searchResults":
            k = _j([d.get("query"), d.get("chips"), (d.get("ids") or [])[:5]])[:300]
        return (name, str(k if k is not None else _j(d)[:300]))

    async def _event_facts(self, name: str, data: dict) -> Optional[dict]:
        pid = data.get("id") or data.get("property_id")
        if name == "openCard":
            w = G.wh(pid)
            if not w:
                return None
            vx = await VX.profile(w["id"])
            self.stage = "inspect"
            return await asyncio.to_thread(G.brief, w, self.profile, None if vx.get("error") else vx)
        if name == "searchResults":
            if data.get("query") is not None or not self.last_search:
                out, raw = await asyncio.to_thread(G.search, data.get("query") or "", 5, self.profile)
                self.last_search = raw
            else:
                out = {"top": [{"id": i, "address": self._addr(i)} for i in self.last_search["ids"][:5]],
                       "counts": self.last_search["raw"]["counts"]}
            self.stage = "results"
            return out
        if name == "openScan":
            w = G.wh(pid)
            if not w:
                return None
            self.stage = "inspect"
            return {"address": w["address"], "interior": await asyncio.to_thread(G.interior, w),
                    "costar_clear_ft": w.get("clear_height_ft"), "column_spacing_costar_ft": w.get("column_spacing_ft"),
                    "what_to_look_at": "clear height vs racking, columns/bays, dock doors (mark them for tests)"}
        if name == "openAerial":
            w = G.wh(pid)
            if not w:
                return None
            vx = await VX.profile(w["id"])
            self.stage = "inspect"
            return {"address": w["address"], "view": data.get("view"), "year": data.get("year"),
                    "vexcel": VX.compact(vx), "what_to_look_at": "truck court and turning room, trailers at docks, roof, parking"}
        if name == "testResult":
            s = G.summarize_test_event(data)
            self.last_test = {"kind": s.get("kind"), "id": pid, "summary": s}
            self.stage = "tested"
            return s
        return dict(data)

    async def event(self, name: str, data: dict, emit: Callable[[dict], Awaitable[None]]) -> Optional[str]:
        """A UI event (the user clicked something). Comments at most once per event; None when skipped."""
        data = data if isinstance(data, dict) else {}
        # Speak up unprompted only when it is worth it: a test result, or the FIRST open of each building
        # (events_seen makes repeat opens silent). Search results are already on screen. At most one
        # unprompted comment every EVENT_GAP_S.
        if name not in ("testResult", "openCard"):
            return None
        now = time.monotonic()
        if now - getattr(self, "last_event_at", -1e9) < EVENT_GAP_S:
            return None
        key = self._event_key(name, data)
        if key in self.events_seen:
            return None
        self.last_event_at = now
        self.events_seen.add(key)
        pid = data.get("id") or data.get("property_id")
        if pid is not None and G.wh(pid):
            self.context["selected_id"] = int(pid)
            cmd = {"openCard": "openCard", "openScan": "showInside", "openAerial": "showAerial"}.get(name)
            t = self.recent_ui.get((cmd, int(pid))) if cmd else None
            if t is not None and time.monotonic() - t < 20:
                return None                     # the guide opened it itself and is already talking about it
        facts = await self._event_facts(name, data)
        if facts is None:
            return None
        txt = _j(facts)
        if len(txt) > 6000:
            txt = txt[:6000] + "...(truncated)"
        self.evidence.append(txt)
        self.turn_no += 1
        self.user_turn = False
        hint = {"openCard": "Name the single attribute that matters most for this buyer, or the one mismatch.",
                "searchResults": "Say the top 3 and why in one breath, plus what excluded the rest if notable.",
                "openScan": "Say what to look at inside for this buyer.",
                "openAerial": "Say what to look at from above for this buyer (one Vexcel insight with its date).",
                "testResult": "Say the verdict and its one limit."}.get(name, "Comment briefly.")
        msg = (f"{self._session_note()}\n[EVENT {name}: the user did this on screen; facts from tools: {txt}]\n"
               f"{hint} ONE short sentence (at most about 20 words), concrete, no question. No tools unless essential.")
        return await self._run(msg, emit)


def make_guide() -> Guide:
    return Guide()
