"""Free text -> search filters ("chips"), for the search bar and the voice Concierge. Nothing here is required:
an empty or unparseable query simply means "browse everything".

Two layers, for reliability:
  1. rules   deterministic regexes for numbers, places and keywords. Exact, testable, works offline.
  2. claude  structured-output extraction for phrasing the rules miss. Every filter Claude proposes must quote the
             words it came from; a filter whose quote is not in the query is dropped (no invented filters).
Merge: the rules win on any field both found (and the chip is flagged `conflict`); Claude fills the rest.
Every chip says what it understood, from which words, and by which layer, so the user can see and fix it.

    parse(text, use_claude=True) -> {"chips": [...], "unparsed": str, "requirements": Requirements}
"""
import json
import os
import re
from typing import Optional

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.requirements import Criterion, Requirements

# ------------------------------------------------------------------ fields a query can set
FIELDS = {
    # field: (label, kind)   kind: number | bool | text
    "market": ("Market", "text"),
    "size_min_sqft": ("Min size (SF)", "number"),
    "size_max_sqft": ("Max size (SF)", "number"),
    "clear_height_min_ft": ("Min clear height (ft)", "number"),
    "docks_min": ("Min dock doors", "number"),
    "drive_ins_min": ("Min drive-in doors", "number"),
    "power_amps_min": ("Min power (amps)", "number"),
    "heavy_power": ("Heavy power", "bool"),
    "three_phase": ("3-phase power", "bool"),
    "rail": ("Rail access", "bool"),
    "cranes": ("Cranes", "bool"),
    "esfr": ("ESFR sprinklers", "bool"),
    "deal": ("Deal", "text"),                       # lease | buy
    "available_only": ("Available now", "bool"),
    "inspectable_only": ("Has 3D interior", "bool"),
    "profile": ("Business type", "text"),
    "rent_max_psf_yr": ("Max rent ($/SF/yr)", "number"),
    "price_max": ("Max price ($)", "number"),
    "near": ("Near", "text"),
}
PROFILE_WORDS = {
    "ecommerce_fulfillment": r"e-?commerce|fulfil+ment|online (store|orders)|parcels?",
    "pallet_3pl": r"\b3pl\b|third[- ]party logistics|pallet storage",
    "food_beverage": r"food|beverage|grocery|restaurant supply|drinks?",
    "cold_storage": r"cold storage|refrigerat\w*|freezer|frozen|cold chain",
    "last_mile_station": r"last[- ]mile|delivery station|van fleet",
    "light_manufacturing": r"manufactur\w*|assembly|fabricat\w*|machine shop",
    "building_materials": r"lumber|building materials?|construction supply|steel yard",
    "auto_parts": r"auto ?parts|automotive",
}
NUM = r"(\d+(?:[.,]\d+)*)\s*(k|thousand|m|mm|million)?"
SF = r"\s*(?:sf|sq\.? ?ft\.?|square[- ]f(?:ee|oo)t|sqft)"
STOP = r"(?=\s+(?:with|that|which|and|over|under|above|below|for|at least|min|max|having|near|in|around)\b|[,.;]|$)"


def _num(s, unit=None):
    v = float(s.replace(",", ""))
    u = (unit or "").lower()
    return v * (1e3 if u in ("k", "thousand") else 1e6 if u in ("m", "mm", "million") else 1)


def _chip(field, value, said, source="rules", op=None):
    label, kind = FIELDS[field]
    return {"field": field, "label": label, "value": value, "said": said.strip(), "source": source,
            "op": op or ("≥" if field.endswith("_min") or "_min_" in field else "≤" if "max" in field else "="),
            "conflict": None}


# ------------------------------------------------------------------ layer 1: rules
def parse_rules(text: str) -> list[dict]:
    # "32-foot", "40,000-square-foot", "53-ft": the hyphen joins a number to its unit; keep the length the same so
    # character spans (for `unparsed`) still line up with the original text
    t = re.sub(r"(?<=\d)-(?=\s*(?:foot|feet|ft|square|sq|sf)\b)", " ", text.lower())
    chips = []
    used = []                                            # character spans consumed (for "unparsed")

    def take(m, *c):
        chips.extend(c)
        used.append(m.span())

    # size: ranges, bounds, or a bare "40k SF" (taken as roughly that size: -20% / +25%)
    m = re.search(rf"between\s+{NUM}(?:{SF})?\s+(?:and|to|-)\s+{NUM}{SF}", t) or \
        re.search(rf"{NUM}(?:{SF})?\s*(?:-|to)\s*{NUM}{SF}", t)
    if m:
        lo, hi = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        take(m, _chip("size_min_sqft", lo, m.group(0)), _chip("size_max_sqft", hi, m.group(0)))
    else:
        for m in re.finditer(rf"(over|above|at least|min(?:imum)?|more than|bigger than|larger than|>=?|\+)?\s*"
                             rf"{NUM}{SF}(\+)?(?:\s*(or more|plus|minimum|min|or less|max(?:imum)?))?", t):
            pre, v, post = (m.group(1) or ""), _num(m.group(2), m.group(3)), (m.group(5) or "") + (m.group(4) or "")
            if re.search(r"under|below|less|max|smaller|up to", t[max(0, m.start() - 12):m.start()] + post):
                take(m, _chip("size_max_sqft", v, m.group(0)))
            elif pre or re.search(r"more|plus|min|\+", post):
                take(m, _chip("size_min_sqft", v, m.group(0)))
            else:
                take(m, _chip("size_min_sqft", round(v * 0.8), m.group(0), op="≈"),
                     _chip("size_max_sqft", round(v * 1.25), m.group(0), op="≈"))
            break
        else:
            # "over 30k" with no unit: a bounded number with k/thousand (or >= 1,000) that isn't money/amps/feet
            for m in re.finditer(rf"(over|above|at least|more than|bigger than|larger than|under|below|less than|"
                                 rf"smaller than|up to)\s+(?<!\$){NUM}(?!\s*(?:amps?|a\b|ft|feet|foot|'|%|/|per))", t):
                if t[max(0, m.start(2) - 1)] == "$":
                    continue
                v = _num(m.group(2), m.group(3))
                if not (m.group(3) in ("k", "thousand") or v >= 1000):
                    continue
                key = "size_max_sqft" if m.group(1) in ("under", "below", "less than", "smaller than", "up to") \
                    else "size_min_sqft"
                take(m, _chip(key, v, m.group(0)))
                break
    m = re.search(r"(?:clear height|clear|ceiling(?: height)?)\s*(?:of|at least|min(?:imum)?|>=?)?\s*(\d+(?:\.\d+)?)\s*"
                  r"(?:ft|feet|foot|')|(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s*(?:clear|ceiling|clear height|tall)", t)
    if m:
        take(m, _chip("clear_height_min_ft", float(m.group(1) or m.group(2)), m.group(0)))
    m = re.search(r"(\d+)\+?\s*(?:loading\s+)?(?:dock(?:-high)?\s*(?:doors?|positions?)?|docks)\b", t)
    if m:
        take(m, _chip("docks_min", int(m.group(1)), m.group(0)))
    elif (m := re.search(r"\b(?:with|has|have|need|needs)\s+(?:loading\s+)?docks?\b|dock[- ]high|loading docks?", t)):
        take(m, _chip("docks_min", 1, m.group(0)))
    m = re.search(r"(\d+)?\s*(?:drive[- ]in|grade[- ]level|roll[- ]?up)\s*(?:doors?|bays?)?", t)
    if m:
        take(m, _chip("drive_ins_min", int(m.group(1) or 1), m.group(0)))
    m = re.search(r"(\d{3,5})\s*(?:amps?|a)\b", t)
    if m:
        take(m, _chip("power_amps_min", int(m.group(1)), m.group(0)))
    for field, pat in (("heavy_power", r"heavy power|high power"), ("three_phase", r"3[- ]?phase|three[- ]phase"),
                       ("rail", r"\brail\b|rail[- ]served|rail spur"), ("cranes", r"\bcranes?\b|overhead crane"),
                       ("esfr", r"\besfr\b")):
        if (m := re.search(pat, t)):
            take(m, _chip(field, True, m.group(0)))
    if (m := re.search(r"for sale|\bbuy\b|purchase|to own", t)):
        take(m, _chip("deal", "buy", m.group(0)))
    elif (m := re.search(r"for lease|for rent|\blease\b|\brent\b|leasing", t)):
        take(m, _chip("deal", "lease", m.group(0)))
    if (m := re.search(r"available|on the market|listed|vacant", t)):
        take(m, _chip("available_only", True, m.group(0)))
    if (m := re.search(r"\b3d(?: tour)?\b|matterport|scan(?:ned)?|virtual tour|see inside|interior tour", t)):
        take(m, _chip("inspectable_only", True, m.group(0)))
    if (m := re.search(r"\bhouston\b|\bhtx\b", t)):
        take(m, _chip("market", "Houston", m.group(0)))
    elif (m := re.search(r"los angeles|\bl\.?a\.?\b", t)):
        take(m, _chip("market", "Los Angeles", m.group(0)))
    for key, pat in PROFILE_WORDS.items():
        if (m := re.search(pat, t)):
            take(m, _chip("profile", key, m.group(0)))
            break
    if (m := re.search(r"(?:under|below|less than|max(?:imum)?|up to)\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:/|per)\s*"
                       r"(?:sf|sq ?ft|foot|square foot)(?:\s*(?:/|per)\s*(yr|year|mo|month))?", t)):
        v = float(m.group(1)) * (12 if (m.group(2) or "").startswith("mo") else 1)
        take(m, _chip("rent_max_psf_yr", v, m.group(0)))
    if (m := re.search(rf"(?:under|below|less than|max(?:imum)?|up to)\s*\$\s*{NUM}", t)) and \
            not re.search(r"(?:/|per)\s*(?:sf|sq|foot)", t[m.end():m.end() + 12]):
        take(m, _chip("price_max", _num(m.group(1), m.group(2)), m.group(0)))
    for m in re.finditer(rf"(?:near|around|close to|by|in)\s+(\d{{5}}|[a-z][a-z .'-]+?){STOP}", t):
        place = m.group(1).strip()
        measure = re.search(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|"
                            r"hundred|thousand)\b|\b(feet|foot|ft|sf|sq|square|amps?|dollars?|percent)\b", place)
        if not re.fullmatch(r"\d{5}", place) and measure:
            continue                                # "around thirty feet" is a measurement, not a place
        if place in ("houston", "los angeles", "la", "l.a", "htx") or re.fullmatch(r"(the )?(city|area)", place):
            continue
        take(m, _chip("near", place, m.group(0)))
        break
    return chips, used


def unparsed_words(text, used):
    keep = list(text)
    for a, b in used:
        for i in range(a, b):
            keep[i] = " "
    words = re.findall(r"[a-z0-9$]+", "".join(keep).lower())
    filler = {"a", "an", "the", "warehouse", "warehouses", "building", "buildings", "space", "find", "show", "me",
              "i", "need", "want", "looking", "for", "with", "and", "or", "of", "in", "at", "to", "that", "some",
              "any", "all", "please", "list", "search", "property", "properties", "industrial", "sf", "ft", "is",
              "has", "have", "my", "our", "we", "us", "on", "can", "you", "it"}
    return " ".join(w for w in words if w not in filler)


# ------------------------------------------------------------------ layer 2: Claude (structured output)
MODEL = "claude-opus-5-5"
SYSTEM = """You turn a warehouse search query into search filters. Extract ONLY what the user actually said.
For each filter give the field, its value, and `quote`: the exact words from the query it comes from (copied
verbatim). Never infer a number the user did not say. If the user says nothing usable, return no filters.
Fields: """ + "; ".join(f"{k} ({v[1]})" for k, v in FIELDS.items()) + """.
Notes: sizes in square feet ("40k" = 40000); clear height in feet; rent in $/SF/year (convert $/SF/month x12);
market is "Houston" or "Los Angeles"; deal is "lease" or "buy"; profile is one of: """ + ", ".join(PROFILE_WORDS) + """;
near is a neighborhood, city or ZIP the user named."""
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["filters"],
    "properties": {"filters": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["field", "number", "text", "flag", "quote"],
        "properties": {"field": {"type": "string", "enum": list(FIELDS)},
                       "number": {"type": ["number", "null"]}, "text": {"type": ["string", "null"]},
                       "flag": {"type": ["boolean", "null"]}, "quote": {"type": "string"}}}}},
}


def parse_claude(text: str) -> list[dict]:
    """Claude's reading of the query; filters whose quote is not in the query are dropped."""
    import anthropic
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=2000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": text}],
    )
    if resp.stop_reason == "refusal":
        return []
    body = next((b.text for b in resp.content if b.type == "text"), "")
    out = []
    for f in json.loads(body).get("filters", []):
        if f["field"] not in FIELDS or not f["quote"].strip() or f["quote"].lower() not in text.lower():
            continue                                    # no quote, no filter
        kind = FIELDS[f["field"]][1]
        val = f["number"] if kind == "number" else f["flag"] if kind == "bool" else f["text"]
        if val is None:
            continue
        out.append(_chip(f["field"], val, f["quote"], source="claude"))
    return out


# ------------------------------------------------------------------ merge + apply
def merge(rule_chips, claude_chips):
    by = {c["field"]: c for c in rule_chips}
    for c in claude_chips:
        r = by.get(c["field"])
        if r is None:
            by[c["field"]] = c
        elif str(r["value"]).lower() != str(c["value"]).lower():
            r["conflict"] = {"claude": c["value"], "kept": "rules"}
    return list(by.values())


def to_requirements(chips, catalog: Optional[list] = None, base: Optional[Requirements] = None) -> Requirements:
    """Chips become requirements. In plain search, a chip is a deal-breaker filter: buildings that fail it drop
    out, buildings where the fact is unknown stay in and are flagged '?' (never silently dropped)."""
    r = base.model_copy(deep=True) if base else Requirements()
    crit = {
        "clear_height_min_ft": ("clear_height_ft", "min"), "docks_min": ("docks_total", "min"),
        "drive_ins_min": ("drive_ins", "min"), "power_amps_min": ("power_amps", "min"),
        "rent_max_psf_yr": ("rent_psf_yr", "max"), "price_max": ("price", "max"),
    }
    flags = {"heavy_power": ("heavy_power", True), "cranes": ("cranes", True), "esfr": ("sprinklers", "ESFR")}
    for c in chips:
        f, v = c["field"], c["value"]
        if f in ("size_min_sqft", "size_max_sqft", "market", "deal", "available_only", "inspectable_only"):
            setattr(r, f, v)
        elif f in crit:
            name, op = crit[f]
            r.criteria[name] = Criterion(**{op: float(v)}, level="D", weight=4, said=c["said"])
        elif f in flags:
            name, val = flags[f]
            r.criteria[name] = Criterion(value=val, level="D", weight=3, said=c["said"])
        elif f == "three_phase":
            r.criteria["power_phases"] = Criterion(min=3, level="D", weight=3, said=c["said"])
        elif f == "profile":
            r.apply_profile(v)
            # a business type alone should not silently become hard filters in plain search
            for k, cr in r.criteria.items():
                if cr.said is None:
                    cr.level = "N"
        elif f == "near" and catalog:
            hit = resolve_place(v, catalog)
            c["resolved"] = hit
            if hit:
                from proinspect.requirements import Area
                r.area = Area(lat=hit["lat"], lon=hit["lon"], radius_mi=hit["radius_mi"])
    if r.size_max_sqft and not r.size_min_sqft:
        r.size_min_sqft = 0.0
    return r


def resolve_place(place: str, catalog: list) -> Optional[dict]:
    """Place a named area on the map using ONLY our data: a ZIP or a street/city matched in the catalog.
    Returns None (shown as 'couldn't place') rather than guessing coordinates."""
    p = place.strip().lower()
    hits = [w for w in catalog if w.get("lat") is not None and (
        (re.fullmatch(r"\d{5}", p) and w.get("zip") == p) or
        (len(p) >= 4 and (p in (w.get("address") or "").lower() or p == (w.get("city") or "").lower())))]
    if not hits:
        return None
    lat = sum(w["lat"] for w in hits) / len(hits)
    lon = sum(w["lon"] for w in hits) / len(hits)
    return {"lat": lat, "lon": lon, "radius_mi": 5 if re.fullmatch(r"\d{5}", p) else 3, "matched": len(hits),
            "basis": "ZIP" if re.fullmatch(r"\d{5}", p) else "address/city text in catalog"}


def parse(text: str, catalog: Optional[list] = None, use_claude: Optional[bool] = None) -> dict:
    text = (text or "").strip()
    rule_chips, used = parse_rules(text) if text else ([], [])
    left = unparsed_words(text, used) if text else ""
    if use_claude is None:                      # only pay for Claude when the rules left real words unread
        use_claude = bool(left) and bool(os.environ.get("ANTHROPIC_API_KEY")) and \
            os.environ.get("PROINSPECT_OFFLINE") != "1"
    claude_chips, error = [], None
    if use_claude and text:
        try:
            claude_chips = parse_claude(text)
        except Exception as e:                  # search must still work if the LLM is down
            error = f"{type(e).__name__}: {str(e)[:160]}"
    chips = merge(rule_chips, claude_chips)
    req = to_requirements(chips, catalog)
    return {"query": text, "chips": chips, "unparsed": left, "used_claude": bool(use_claude), "claude_error": error,
            "requirements": req}
