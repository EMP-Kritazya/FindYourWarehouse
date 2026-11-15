"""Deterministic analyst tools for the guide (CONTRACTS_v3 §2, plan B2). The LLM picks a tool and explains; these
compute. Each returns a compact, JSON-able dict the agent can quote, plus `step_summary` turns any tool result into
the one-line finding shown in the Analyst thread's reasoning trail.
"""
import re
from typing import Optional

from proinspect import analytics as A
from proinspect.library import equipment

TRUCK_TYPES = ("semi_53", "box_truck_26", "last_mile_van")


def _addr(pid) -> str:
    w = A.wh(pid)
    return (w or {}).get("address") or str(pid)


def _fmt_n(v) -> str:
    if v is None:
        return "?"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,.0f}"


# ------------------------------------------------------------------ rank by attribute
def rank_attribute(attr: str, market: Optional[str] = None, order: Optional[str] = None, top: int = 5,
                   ids: Optional[list[int]] = None) -> dict:
    r = A.rank_attribute(attr, order, market, 200, ids=ids)
    top = max(1, min(int(top or 5), 10))
    unit = r["unit"]
    ver = [{"id": x["id"], "address": x["address"], "value": x["value"], "source": x["source"],
            **({"note": x["alt"]} if x.get("alt") else {})} for x in r["verified"][:top]]
    est = [{"id": x["id"], "address": x["address"], "value": x["value"], "lo": x["lo"], "hi": x["hi"],
            "status": x["status"], "source": x["source"], "basis": (x.get("basis") or "")[:220]}
           for x in r["estimated"][:top]]
    unk = r["unknown"]
    return {"attr": r["attr"], "label": r["label"], "unit": unit, "order": r["order"], "market": r["market"],
            "definition": f"{r['label']} ({unit}); verified = CoStar, estimated = imagery/scan with a range or bound",
            "verified_top": ver, "estimated_top": est,
            "unknown_count": len(unk),
            "largest_unknown": [{"id": x["id"], "address": x["address"], "size_sqft": x["size_sqft"],
                                 "why": x["why"]} for x in unk[:5]],
            "coverage": r["coverage"], "note": r["note"],
            "investigate": "call dock_evidence on the largest unknown buildings if they could change the answer"
            if r["attr"] in ("docks_total", "docks_per_10ksf") and unk else None}


# ------------------------------------------------------------------ docks
def dock_evidence(ids: list[int]) -> dict:
    out = []
    for i in (ids or [])[:6]:
        w = A.wh(i)
        if not w:
            out.append({"id": i, "error": "not in the catalog"})
            continue
        s = A.dock_evidence(w["id"])
        e = s.get("estimate") or {}
        out.append({"id": w["id"], "address": w.get("address"), "costar_docks": s.get("costar_docks"),
                    "costar_has_docks": w.get("has_docks"), "detected_doors": s.get("detected_doors"),
                    "docked_trailers": s.get("docked_trailers"), "confirmed_by_person": s.get("confirmed"),
                    "estimate": {k: e.get(k) for k in ("value", "lo", "hi", "status")},
                    "basis": e.get("basis")})
    return {"buildings": out,
            "how": "CoStar count when it exists; otherwise doors seen on 4 oblique views + trailers parked at doors "
                   "(a lower bound: detection misses hidden doors); doors a person confirmed in the dock editor"}


# ------------------------------------------------------------------ compare + comparables
def compare(ids: list[int], profile: Optional[str] = None, ops_text: Optional[str] = None) -> tuple[dict, dict]:
    """-> (compact for the agent, full compare() result)."""
    c = A.compare(ids, profile, ops_text)
    addr = {x["id"]: x["address"] for x in c["candidates"]}
    rows = []
    for r in c["rows"]:
        rows.append({"criterion": r["label"], "weight": r["weight"],
                     "values": {addr[v["id"]]: v["display"] + ("" if v["kind"] == "verified" else
                                                               f" ({v['kind']})") for v in r["values"]},
                     "verdict": r["verdict"], "winner": addr.get(r.get("winner")) if r.get("winner") else None,
                     **({"winner_is_estimate": True} if r.get("estimated") and r.get("winner") else {})})
    out = {"operation": c["operation"]["name"], "operation_key": c["profile"],
           "operation_matched_by": c["operation"]["matched_by"],
           "criteria": [f"{x['label']} (weight {x['weight']}): {x['why']}" for x in c["criteria"]],
           "rows": rows, "fit": {addr[int(k)]: v for k, v in c["fit"].items()},
           "fit_why": {addr[int(k)]: v for k, v in c["fit_why"].items() if v},
           "recommendation": {"address": addr[c["recommendation"]["id"]], "id": c["recommendation"]["id"],
                              "text": c["recommendation"]["text"]},
           "tradeoffs": c["tradeoffs"],
           "rule": "rephrase the recommendation text; add no numbers that are not in rows"}
    return out, c


def comparables(pid: int, profile: Optional[str] = None, ops_text: Optional[str] = None) -> dict:
    r = A.find_comparables(pid, profile, ops_text)
    return {"for": {"id": r["id"], "address": _addr(r["id"]), "score": r["this_score"]},
            "operation": r["operation_name"], "operation_key": r["operation"],
            "comparables": r["scores"], "ids": r["ids"], "why": r["why"],
            "next": "compare_warehouses([this id] + ids, operation_key) to see the per-criterion comparison"}


# ------------------------------------------------------------------ yard maneuver pre-check
_TRUCK_RX = {"semi_53": r"semi|53|tractor|trailer|18.?wheel|big rig", "box_truck_26": r"box|26|straight",
             "last_mile_van": r"van|sprinter|last.?mile"}


def parse_trucks(trucks) -> list[dict]:
    """[{type, count}] from a list of dicts or words ('5 semis', ['3 box trucks', '2 vans'])."""
    out = []
    items = trucks if isinstance(trucks, list) else [trucks]
    for t in items:
        if isinstance(t, dict):
            typ = t.get("type") if t.get("type") in TRUCK_TYPES else None
            if not typ:
                typ = next((k for k, rx in _TRUCK_RX.items() if re.search(rx, str(t.get("type") or ""), re.I)),
                           "semi_53")
            out.append({"type": typ, "count": max(1, min(int(t.get("count") or 1), 12))})
        elif isinstance(t, str) and t.strip():
            m = re.search(r"(\d+)", t)
            typ = next((k for k, rx in _TRUCK_RX.items() if re.search(rx, t, re.I)), "semi_53")
            out.append({"type": typ, "count": max(1, min(int(m.group(1)) if m else 1, 12))})
    merged: dict = {}
    for x in out:
        merged[x["type"]] = merged.get(x["type"], 0) + x["count"]
    return [{"type": k, "count": min(v, 12)} for k, v in merged.items()]


def yard_precheck(pid: int, trucks: list[dict]) -> dict:
    """Deterministic facts that bound 'can N trucks use this yard at once' before the maneuver test runs."""
    w = A.wh(pid)
    if not w:
        return {"error": f"warehouse {pid} not in the catalog"}
    eq = equipment()
    n = sum(t["count"] for t in trucks)
    docks = A.criterion_value("docks_total", w)
    court = A.criterion_value("truck_court_ft", w)
    need = max((eq[t["type"]].get("truck_court_depth_ft") or 0) for t in trucks) if trucks else None
    need_name = max(trucks, key=lambda t: eq[t["type"]].get("truck_court_depth_ft") or 0)["type"] if trucks else None
    checks = []
    if docks["kind"] == "unknown":
        checks.append(f"dock door count unknown: {docks['basis']}")
    else:
        dv = docks["value"] if docks["value"] is not None else docks["lo"]
        bound = "at least " if docks.get("status") == "lower_bound" else ""
        if dv is not None and dv >= n:
            checks.append(f"{bound}{_fmt_n(dv)} dock doors ({docks['source']}) for {n} trucks at once")
        else:
            checks.append(f"{bound}{_fmt_n(dv)} dock doors ({docks['source']}) for {n} trucks: the rest must "
                          "wait or stage in the yard")
    if court["kind"] == "unknown":
        checks.append(f"truck court depth unknown; a {eq[need_name]['name']} needs about {need} ft")
    else:
        cv = court["value"]
        verdict = "clears" if cv >= need else "is short of"
        checks.append(f"truck court {_fmt_n(cv)} ft ({court['source']}"
                      f"{', estimate' if court['kind'] == 'estimated' else ''}) {verdict} the ~{need} ft a "
                      f"{eq[need_name]['name']} needs")
    return {"id": w["id"], "address": w.get("address"), "trucks": trucks, "trucks_total": n,
            "precheck": checks, "docks": {k: docks.get(k) for k in ("value", "lo", "hi", "kind", "source")},
            "truck_court_ft": {k: court.get(k) for k in ("value", "lo", "hi", "kind", "source")},
            "required_court_ft": need,
            "test_status": "opened on screen with these trucks; NOT run or finished yet (say it is opening, not that "
                           "you ran it). The maneuver test (kinematic truck model + space-time reservations) reports "
                           "waits and conflicts; its result arrives as a testResult event. Planner failures are 'not "
                           "found by our planner', never 'impossible'."}


# ------------------------------------------------------------------ the reasoning trail
def _ids_of(out) -> list[int]:
    if not isinstance(out, dict):
        return []
    for k in ("ids",):
        if isinstance(out.get(k), list):
            return [int(i) for i in out[k] if isinstance(i, int)][:6]
    ids = []
    for k in ("verified_top", "estimated_top", "top", "closest", "buildings", "comparables"):
        for x in out.get(k) or []:
            if isinstance(x, dict) and isinstance(x.get("id"), int):
                ids.append(x["id"])
    if isinstance(out.get("id"), int):
        ids.insert(0, out["id"])
    return ids[:6]


def step_summary(tool: str, kw: dict, out) -> dict:
    """One-line finding for a tool call: {tool, summary, ids, ok}."""
    if not isinstance(out, dict):
        return {"tool": tool, "summary": "done", "ids": [], "ok": True}
    if out.get("error"):
        return {"tool": tool, "summary": f"error: {str(out['error'])[:120]}", "ids": [], "ok": False}
    s = None
    try:
        if tool == "rank_attribute":
            cov = out["coverage"]
            lead = out["verified_top"][0] if out["verified_top"] else None
            s = (f"{out['label']} ({out['order']}): {cov['verified']} verified, {cov['estimated']} estimated, "
                 f"{cov['unknown']} unknown" + (f"; top verified {lead['address']} = {_fmt_n(lead['value'])} "
                                                  f"{out['unit']}" if lead else ""))
        elif tool == "dock_evidence":
            parts = []
            for b in out["buildings"][:3]:
                e = b.get("estimate") or {}
                v = e.get("value") if e.get("value") is not None else e.get("lo")
                tag = {"lower_bound": "≥", "verified": "", "estimated": "~"}.get(e.get("status"), "")
                parts.append(f"{b.get('address')}: {tag}{_fmt_n(v)} ({e.get('status')})" if v is not None
                             else f"{b.get('address')}: unknown")
            s = "; ".join(parts)
        elif tool == "compare_warehouses":
            won = sum(1 for r in out["rows"] if r["verdict"] == "winner")
            s = (f"{out['operation']}: {len(out['rows'])} criteria, {won} decided; recommends "
                 f"{out['recommendation']['address']}")
        elif tool == "find_comparables":
            s = f"{len(out['ids'])} comparables for {out['for']['address']} ({out['operation']}): " + \
                ", ".join(x["address"] for x in out["comparables"])
        elif tool == "yard_maneuver_test":
            s = f"{out['trucks_total']} trucks at {out['address']}: " + "; ".join(out["precheck"])
        elif tool == "search_warehouses":
            c = out.get("counts") or {}
            s = f"{c.get('shown', c.get('kept', '?'))} matches" + (
                f"; top {out['top'][0]['address']}" if out.get("top") else "")
        elif tool == "rank_by":
            t = out.get("top") or []
            s = f"{out.get('metric')}: top {t[0]['address']} = {_fmt_n(t[0].get('value'))} {t[0].get('unit') or ''}" \
                if t else f"{out.get('metric')}: no values"
        elif tool == "ui":
            s = f"screen: {kw.get('cmd')}" + (" ok" if out.get("ok") else "")
        elif tool == "update_buyer_profile":
            p = {k: v for k, v in (out.get("profile") or {}).items() if v not in (None, [], "")}
            s = "noted: " + ", ".join(f"{k}={v}" for k, v in p.items())[:200] if p else "nothing new"
        elif tool == "building_brief":
            s = f"{out.get('address')}: {len(out.get('fit_for_buyer') or [])} fit notes"
        elif tool == "closest_to":
            c = out.get("closest") or []
            s = f"closest to {out.get('place')}: {c[0]['address']} ({_fmt_n(c[0].get('distance_m'))} m)" if c \
                else f"no {out.get('place')} found"
    except (KeyError, IndexError, TypeError):
        s = None
    if s is None:
        keys = [k for k in out if out[k] not in (None, [], {}, "")][:4]
        s = "returned " + ", ".join(keys) if keys else "done"
    return {"tool": tool, "summary": s[:300], "ids": _ids_of(out), "ok": True}
