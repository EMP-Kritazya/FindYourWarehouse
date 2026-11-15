"""Deterministic computations behind the guide's tools. Agents decide; these compute (no LLM in here).

Everything returns small JSON-able dicts with sources, so the voice agent can quote numbers and say where they
came from. Unknown stays None ("?"), never 0.
"""
import json
import re
from datetime import datetime, timezone
from typing import Optional

from proinspect.config import BUILD
from proinspect.library import equipment, profiles

TRUCKS = ("semi_53", "box_truck_26", "last_mile_van")
TRUCK_WORDS = {"semi_53": ("53", "semi", "tractor", "trailer", "18 wheel", "18-wheel", "big rig", "wb-67"),
               "box_truck_26": ("box truck", "26", "straight truck"),
               "last_mile_van": ("van", "sprinter", "last mile", "last-mile")}


def _api():
    from proinspect import api              # lazy: api.py imports the routers at startup
    return api


def wh(pid) -> Optional[dict]:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return next((w for w in _api().catalog() if w["id"] == pid), None)


def find_by_address(text: str) -> Optional[dict]:
    """'3200 Sam Houston' -> the catalog record (street number + first word of the street must match)."""
    t = (text or "").lower()
    m = re.search(r"\b(\d{2,6})\s+([a-z]+)", t)
    if not m:
        return None
    num, word = m.groups()
    for w in _api().catalog():
        a = (w.get("address") or "").lower()
        if a.startswith(num + " ") and word in a:
            return w
    return None


# ------------------------------------------------------------------ buyer profile
def norm_truck(s: str) -> Optional[str]:
    s = (s or "").lower().strip()
    if s in TRUCKS:
        return s
    for k, words in TRUCK_WORDS.items():
        if any(x in s for x in words):
            return k
    return None


def norm_equipment(s: str) -> Optional[str]:
    s = (s or "").lower().strip().replace("-", " ")
    eq = equipment()
    if s.replace(" ", "_") in eq:
        return s.replace(" ", "_")
    s1 = s.rstrip("s")
    for k, v in eq.items():
        name = (v.get("name") or "").lower()
        if s1 and (s1 in name or s1 in k.replace("_", " ")):
            return k
    if "forklift" in s:
        return "counterbalance_electric"
    return None


def norm_business(s: str) -> Optional[str]:
    s = (s or "").lower().strip()
    if s in profiles():
        return s
    from proinspect.query import PROFILE_WORDS
    for k, rx in PROFILE_WORDS.items():
        if re.search(rx, s):
            return k
    for k, p in profiles().items():
        if s and s in (p.get("name") or "").lower():
            return k
    return None


# ------------------------------------------------------------------ search
def search(query: str, top: int = 5, profile: Optional[dict] = None) -> tuple[dict, dict]:
    """-> (agent-facing summary, raw api result). The buyer profile's truck feeds the ranking."""
    from proinspect.query import parse, parse_rules, unparsed_words
    from proinspect.requirements import Requirements
    api = _api()
    top = max(1, min(int(top or 5), 10))
    query = re.sub(r"(\d)\s*-\s*(foot|feet|ft)\b", r"\1 \2", query or "")
    chips = parse(query, api.catalog(), use_claude=False)["chips"] if query else []
    base = None
    if profile and profile.get("trucks"):
        t = next((t for t in profile["trucks"] if t in TRUCKS), None)
        if t:
            base = Requirements(truck=t)
    res = api.search(api.SearchBody(chips=chips, limit=200, requirements=base))
    res["query"] = query
    rows = []
    for r in res["results"][:top]:
        w = wh(r["id"]) or {}
        rows.append({"id": r["id"], "rank": r.get("rank"), "address": r["address"], "market": r["market"],
                     "score": r.get("score"), "inspectable": r.get("inspectable"),
                     "size_sqft": w.get("rba_sqft"), "clear_height_ft": w.get("clear_height_ft"),
                     "availability": w.get("availability"),
                     "checks": [{k: c.get(k) for k in ("label", "need", "have", "source", "status")}
                                for c in r.get("checks", [])][:5]})
    why = {}
    for e in res["excluded"]:
        for reason in (e.get("why") or [])[:1]:
            key = re.sub(r"[\d,.]+", "#", str(reason))[:60]
            why[key] = why.get(key, 0) + 1
    _, used = parse_rules(query) if query else (None, [])
    out = {"understood": [f"{c['label']} {c['op']} {c['value']} (from '{c['said']}')" for c in chips],
           "not_understood": (unparsed_words(query, used) or None) if query else None,
           "mode": res["mode"], "counts": res["counts"], "top": rows,
           "excluded_reasons": sorted(why.items(), key=lambda kv: -kv[1])[:3],
           "note": "null = unknown; the catalog is CoStar's scan list (about 110 warehouses), not the whole market"}
    return out, {"query": query, "chips": chips, "ids": [r["id"] for r in res["results"]], "raw": res}


# ------------------------------------------------------------------ one building
def listing_terms(w: dict) -> dict:
    act = [l for l in w.get("listings") or [] if l.get("status") == "Active"]
    out = []
    for l in act[:3]:
        d = {"kind": l.get("kind"), "available_sqft": l.get("available_sqft"),
             "listing_contact": l.get("broker")}
        if l.get("kind") == "lease":
            d["asking_rent_psf_yr"] = ([l.get("rent_low_psf_yr"), l.get("rent_high_psf_yr")]
                                       if l.get("rent_low_psf_yr") else "not disclosed")
            d["service_type"] = l.get("service_type")
            d["min_divisible_sqft"] = l.get("min_divisible_sqft")
        else:
            d["asking_price"] = l.get("price_low") or "not disclosed"
            d["cap_rate"] = l.get("cap_rate")
        out.append(d)
    return {"availability": w.get("availability"), "active_listings": out,
            "contact_note": "listing contact = the broker firm on the CoStar listing; phone/email in this dataset "
                            "are fake placeholders, so say 'listing contact', never read them out",
            "last_sale": w.get("last_sale"), "tenants": w.get("tenants")}


def interior(w: dict) -> Optional[dict]:
    from proinspect.api_twin import _interior, best_scan
    s = best_scan(w)
    if not s:
        return None
    i = _interior(s["model_id"]) or {}
    clear = i.get("clear") or {}
    deck = i.get("deck_ft")
    return {"model_id": s["model_id"], "scanned_floor_sqft": s.get("floor_sqft"),
            "deck_ft": deck, "clear_costar_comparable_ft": round(deck - 1.1, 1) if deck else None,
            "lowest_obstruction_p5_ft": clear.get("p5_ft"), "clear_confidence": i.get("clear_confidence"),
            "columns": (i.get("columns") or {}).get("structure"), "bays": (i.get("columns") or {}).get("bays"),
            "office_sqft": i.get("office_sqft"), "scan_date": (i.get("scan_created") or "")[:10] or None,
            "method": "Matterport mesh: roof deck minus 1.1 ft is CoStar-comparable (MAE 2.3 ft); P5 obstruction "
                      "is the conservative rack height"}


def fit_flags(w: dict, prof: dict, vx: Optional[dict]) -> list[str]:
    """Mismatches/unknowns that matter for THIS buyer (deterministic comparisons only)."""
    flags = []
    need = prof.get("size_sqft")
    if need:
        avail = [l.get("available_sqft") for l in w.get("listings") or [] if l.get("status") == "Active"
                 and l.get("available_sqft")]
        have = max(avail) if avail else w.get("rba_sqft")
        if have:
            r = have / need
            what = "available space" if avail else "building size (RBA)"
            if r < 0.85:
                flags.append(f"{what} {have:,.0f} SF is below the ~{need:,.0f} SF need")
            elif r > 2.5:
                flags.append(f"{what} {have:,.0f} SF is much larger than the ~{need:,.0f} SF need")
            else:
                flags.append(f"{what} {have:,.0f} SF fits the ~{need:,.0f} SF need")
    eq = equipment()
    for t in prof.get("trucks") or []:
        e = eq.get(t) or {}
        req = e.get("truck_court_depth_ft")
        court, src = w.get("truck_court_ft"), "CoStar"
        if court is None and vx:
            court, src = _v((vx.get("parking") or {}).get("truck_court_ft")), "Vexcel (low confidence)"
        if req:
            if court is None:
                flags.append(f"truck court depth unknown; a {e.get('name')} needs about {req} ft: run the yard test")
            elif court < req:
                flags.append(f"truck court {court} ft ({src}) is short of the ~{req} ft a {e.get('name')} needs")
            else:
                flags.append(f"truck court {court} ft ({src}) clears the ~{req} ft a {e.get('name')} needs")
    clear = w.get("clear_height_ft")
    for k in prof.get("equipment") or []:
        e = eq.get(k) or {}
        lift = e.get("lift_height_ft")
        if lift and clear:
            if clear < lift:
                flags.append(f"clear height {clear} ft (CoStar) is below a {e.get('name')}'s {lift} ft lift")
        elif lift and not clear:
            flags.append(f"clear height unknown in CoStar; check the scan for a {e.get('name')}")
        if e.get("min_aisle_ft"):
            flags.append(f"{e.get('name')} needs about {e['min_aisle_ft']} ft aisles: the interior planner checks it")
    if w.get("availability") != "available":
        flags.append(f"not actively listed ({w.get('availability')})")
    return flags


def _v(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def brief(w: dict, prof: dict, vx: Optional[dict]) -> dict:
    """The few attributes that matter at openCard, with sources. vx = the Vexcel profile (S1 or local), or None."""
    v = vx or {}
    park, bld, site = v.get("parking") or {}, v.get("building") or {}, v.get("site") or {}
    nearby = {}
    for k, n in (v.get("nearby") or {}).items():
        h = (n or {}).get("nearest") if isinstance(n, dict) else None
        if h and h.get("distance_m") is not None:
            nearby[k] = {"distance_m": h["distance_m"], "likelihood": h.get("likelihood"), "label": n.get("label")}
    cap = v.get("captured") or {}
    return {
        "id": w["id"], "address": w.get("address"), "market": w.get("market"), "subtype": w.get("subtype"),
        "year_built": w.get("year_built"), "class": w.get("building_class"),
        "size": {"rba_sqft": w.get("rba_sqft"), "lot_acres": w.get("lot_acres"), "source": "CoStar"},
        "selling": listing_terms(w),
        "clear_height": {"costar_ft": w.get("clear_height_ft"), "scan": interior(w)},
        "doors": {"docks_costar": w.get("docks_external"), "drive_ins_costar": w.get("drive_ins"),
                  "drive_in_size_ft": [w.get("drive_in_w_ft"), w.get("drive_in_h_ft")],
                  "note": "door detection on aerials is only a lower bound"},
        "yard": {"truck_court_ft_costar": w.get("truck_court_ft"),
                 "truck_court_ft_vexcel": _v(park.get("truck_court_ft")),
                 "paved_area_sqft_vexcel": _v(park.get("paved_area_sqft")),
                 "parking_lot_area_sqft_vexcel": _v(park.get("parking_lot_area_sqft") or park.get("parking_area_sqft")),
                 "parking_spaces_costar": w.get("parking_spaces"),
                 "trucks_seen": _v(park.get("trucks_seen")), "trailers_seen": _v(park.get("trailers_seen"))},
        "power": {"amps": w.get("power_amps"), "volts": w.get("power_volts"), "phases": w.get("power_phases")},
        "sprinklers": w.get("sprinklers"), "column_spacing_ft": w.get("column_spacing_ft"),
        "vexcel": {"roof_condition_1to5": _v(bld.get("roof_condition")), "roof_defects": _v(bld.get("roof_defects")),
                   "solar": _v(bld.get("solar")), "tree_cover_pct": _v(site.get("tree_cover_pct")),
                   "nearby_likely_or_possible": nearby or None,
                   "imagery_date": cap.get("ortho") or cap.get("dsm") or cap.get("parcel_attrs"),
                   "source": v.get("source") or ("S1 Vexcel profile" if vx else None)},
        "inspectable": w.get("inspectable"),
        "fit_for_buyer": fit_flags(w, prof, vx),
    }


# ------------------------------------------------------------------ tests (S3 yard, S4 fit) in-process
def _paths(routes):
    for r in routes:
        if getattr(r, "path", None):
            yield r.path
        sub = getattr(getattr(r, "original_router", None), "routes", None)   # FastAPI >= 0.13x included routers
        if sub:
            yield from _paths(sub)


def route_ready(path: str) -> bool:
    return path in set(_paths(_api().app.routes))


async def post(path: str, payload: dict, timeout: float = 300):
    import httpx
    api = _api()
    if not route_ready(path):
        return 404, "no route"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://local",
                                 timeout=timeout) as c:
        r = await c.post(path, json=payload)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def summarize_yard(body: dict) -> dict:
    fits = body.get("fits") or []
    ok = [f for f in fits if f.get("ok")]
    worst = min(fits, key=lambda f: f.get("margin_m") if f.get("margin_m") is not None else 1e9) if fits else None
    q = body.get("queue") or {}
    return {"run_id": body.get("run_id"), "doors_tested": len(body.get("doors") or []),
            "door_source": sorted({d.get("source") for d in body.get("doors") or [] if d.get("source")}),
            "fits_ok": len(ok), "fits_total": len(fits),
            "tightest": worst and {"door_id": worst.get("door_id"), "vehicle": worst.get("vehicle"),
                                   "ok": worst.get("ok"), "margin_m": worst.get("margin_m"),
                                   "conflicts": [c.get("what") for c in worst.get("conflicts") or []][:3]},
            "queue": {k: q.get(k) for k in ("max_in_yard", "max_on_street", "wait_p50_min", "wait_p90_min",
                                            "door_utilization")},
            "assumptions": (body.get("assumptions") or [])[:4]}


def summarize_fit(body: dict) -> dict:
    checks = body.get("checks") or []
    bad = [c for c in checks if c.get("ok") is False]
    return {"run_id": body.get("run_id"), "model_id": body.get("model_id"),
            "equipment": (body.get("equipment") or {}).get("name") or body.get("equipment"),
            "checks_ok": len(checks) - len(bad), "checks_total": len(checks),
            "failed": [{k: c.get(k) for k in ("kind", "need", "have", "unit", "note")} for c in bad][:4],
            "pinch_points": len(body.get("pinch_points") or []),
            "tightest_pinch": min(body.get("pinch_points") or [{}], key=lambda p: p.get("width_m") or 1e9) or None,
            "reachable_pct": body.get("reachable_pct"), "assumptions": (body.get("assumptions") or [])[:4]}


def summarize_test_event(data: dict) -> dict:
    """A testResult event from the UI: compact whichever result shape it carries."""
    r = data.get("result") if isinstance(data.get("result"), dict) else data
    kind = data.get("kind") or ("yard" if "fits" in r or "queue" in r else "equipment" if "checks" in r else None)
    if kind == "yard":
        return {"kind": "yard", **summarize_yard(r)}
    if kind == "equipment":
        return {"kind": "equipment", **summarize_fit(r)}
    return {"kind": kind, "data": json.dumps(r, default=str)[:1500]}


# ------------------------------------------------------------------ selling help
def log_tour(pid: int, when: Optional[str], note: Optional[str], profile: dict) -> dict:
    d = BUILD / "guide"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "property_id": int(pid), "when": when,
           "note": note, "buyer": {k: profile.get(k) for k in ("business", "size_sqft", "trucks", "equipment")}}
    with open(d / "tour_requests.jsonl", "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return {"logged": True, "sent": False,
            "note": "tour request logged in ProInspect only; nothing was sent to the listing contact"}
