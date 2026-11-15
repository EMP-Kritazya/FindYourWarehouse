"""Deterministic analytics over the warehouse catalog + measured facts (CONTRACTS_v3 §2). No LLM decides a number here.

    rank_attribute(attr, order, market, top)   three tiers: verified (CoStar) / estimated (imagery or scan, with a
                                               range or a lower bound) / unknown (with why). Unknown is NEVER 0.
    dock_evidence(pid)                         S1's `proinspect.docks.evidence` when it exists, else a lower bound
                                               from the exterior twin's oblique doors.
    select_criteria(profile, ops_text)         operation type -> 5-7 weighted criteria (library/criteria.yaml)
    compare(ids, profile, ops_text)            per-criterion values, winner only beyond tolerance, tradeoffs, fit,
                                               template recommendation
    find_comparables(pid, profile, ops_text)   same market, +-40% size, not failing a deal-breaker, top 3

Every value is a dict {value, lo, hi, source, confidence, kind: verified|estimated|unknown, basis}; `status` on an
estimate is "estimated" (a range), "lower_bound" (at least lo) or "upper_bound" (at most hi).
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from proinspect.config import BUILD
from proinspect.library import equipment, profiles

LIB = Path(__file__).resolve().parent / "library" / "criteria.yaml"
CLEAR_MAE_FT = 2.3            # build/validation/interior.md (held-out scans)
DRIVE_IN_H_MAE_FT = 3.3       # build/validation/exterior.json


# ------------------------------------------------------------------ inputs (all read-only files)
@lru_cache
def library() -> dict:
    return yaml.safe_load(LIB.read_text())


@lru_cache
def _catalog() -> tuple:
    return tuple(json.loads((BUILD / "catalog" / "warehouses.json").read_text()))


def catalog() -> list[dict]:
    return list(_catalog())


def wh(pid) -> Optional[dict]:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return next((w for w in _catalog() if w["id"] == pid), None)


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=512)
def _exterior(pid: int):
    return _json(BUILD / "twins" / "exterior" / f"{pid}.json")


@lru_cache(maxsize=512)
def _vexcel(pid: int):
    return _json(BUILD / "vexcel" / f"{pid}.json")


def _measured(pid: int) -> dict:
    try:
        from proinspect.facts import measured_facts
        return measured_facts().get(pid, {})
    except Exception:
        return {}


def _vx(pid: int, group: str, key: str):
    v = _vexcel(pid) or {}
    x = (v.get(group) or {}).get(key)
    return x if isinstance(x, dict) and x.get("value") is not None else None


def _size(w: dict):
    return w.get("rba_sqft") or w.get("gba_sqft")


# ------------------------------------------------------------------ value records
def V(value, source, confidence=0.8, basis=None, alt=None):
    return {"value": value, "lo": value, "hi": value, "source": source, "confidence": confidence,
            "kind": "verified", "status": "verified", "basis": basis, **({"alt": alt} if alt else {})}


def E(value, lo, hi, source, confidence, basis, status="estimated"):
    return {"value": value, "lo": lo, "hi": hi, "source": source, "confidence": round(confidence, 2),
            "kind": "estimated", "status": status, "basis": basis}


def U(why):
    return {"value": None, "lo": None, "hi": None, "source": None, "confidence": 0.0, "kind": "unknown",
            "status": "unknown", "basis": why}


# ------------------------------------------------------------------ docks
def _fallback_dock_evidence(pid: int) -> dict:
    """Lower bound from the exterior twin's oblique doors (dock_high + uncertain). Never says 0 from 'none seen'."""
    w = wh(pid) or {}
    ext, inn = w.get("docks_external"), w.get("docks_internal")
    costar = (ext or 0) + (inn or 0) if (ext is not None or inn is not None) else (0 if w.get("has_docks") is False
                                                                                    else None)
    e = _exterior(pid)
    v = _vexcel(pid) or {}
    trailers = ((e or {}).get("vehicles") or {}).get("trailers_at_docks")
    d = (e or {}).get("doors") or {}
    detected = (d.get("dock_high") or 0) + (d.get("uncertain") or 0) if e else None
    out = {"costar_docks": costar, "costar_drive_ins": w.get("drive_ins"), "detected_doors": detected,
           "docked_trailers": trailers, "confirmed": 0, "source": "fallback: exterior twin obliques"}
    if ext is not None or inn is not None:
        out["estimate"] = {"value": costar, "lo": costar, "hi": costar, "status": "verified",
                           "basis": "CoStar loading-dock count"}
    elif w.get("has_docks") is False and not detected:
        out["estimate"] = {"value": 0, "lo": 0, "hi": 0, "status": "verified",
                           "basis": "CoStar records no loading docks (hasLoadingDocks = false)"}
    elif not e:
        out["estimate"] = {"value": None, "lo": None, "hi": None, "status": "unknown",
                           "basis": "no CoStar count and no exterior imagery twin for this site"}
    elif e.get("parcel_mismatch") or v.get("parcel_mismatch"):
        out["estimate"] = {"value": None, "lo": None, "hi": None, "status": "unknown",
                           "basis": "no CoStar count; the Vexcel parcel looks wrong, so imagery doors are not used"}
    else:
        lb = max(detected or 0, trailers or 0, 1 if w.get("has_docks") else 0)
        if lb:
            parts = []
            if detected:
                parts.append(f"{detected} dock-type doors seen on {len(e['imagery'].get('obliques') or [])} "
                             f"oblique views ({(e['imagery'].get('dsm_date') or '')[:4]})")
            if trailers:
                parts.append(f"{trailers} trailers parked at doors")
            if w.get("has_docks") and not parts:
                parts.append("CoStar says it has docks but gives no count")
            out["estimate"] = {"value": lb, "lo": lb, "hi": None, "status": "lower_bound",
                               "basis": "at least " + "; ".join(parts) + ". Obliques miss doors hidden by trailers "
                                        "or foreshortened, so the true count can be much higher."}
        else:
            out["estimate"] = {"value": None, "lo": None, "hi": None, "status": "unknown",
                               "basis": "no CoStar count and no dock-type doors visible on the obliques "
                                        "(detection misses many doors: this is not 0)"}
    return out


def dock_evidence(pid) -> dict:
    """{costar_docks, detected_doors, docked_trailers, confirmed, estimate:{value, lo, hi, status, basis}}."""
    pid = int(pid)
    try:
        from proinspect import docks as S1
        s = S1.evidence(pid)
        if isinstance(s, dict) and isinstance(s.get("estimate"), dict):
            return {**s, "source": s.get("source") or "dock store v3"}
    except Exception:
        pass
    return _fallback_dock_evidence(pid)


def _docks_value(w: dict) -> dict:
    """CoStar count -> verified. Otherwise the dock evidence (same order as S1's summary): a person's confirmed
    doors or an imagery lower bound beat CoStar's bare 'no docks' flag; that flag alone is a verified 0."""
    ext, inn = w.get("docks_external"), w.get("docks_internal")
    if ext is not None or inn is not None:
        return V((ext or 0) + (inn or 0), "CoStar", 0.8, "CoStar external + internal loading docks")
    ev = dock_evidence(w["id"])
    est = ev.get("estimate") or {}
    st = est.get("status")
    if st == "verified" and est.get("value") is not None:
        src = "CoStar" if w.get("has_docks") is False and est["value"] == 0 else "confirmed by a person"
        return V(est["value"], src, 0.7 if src == "CoStar" else 0.75, est.get("basis"))
    if st in ("estimated", "lower_bound") and (est.get("value") is not None or est.get("lo") is not None):
        lo = est.get("lo") if est.get("lo") is not None else est.get("value")
        conf = 0.75 if ev.get("confirmed") and est.get("hi") == lo else 0.3 if st == "lower_bound" else 0.45
        src = "confirmed by a person" if conf == 0.75 else "Vexcel obliques"
        return E(est.get("value") if est.get("value") is not None else lo, lo, est.get("hi"), src, conf,
                 est.get("basis"), status=st)
    if w.get("has_docks") is False:
        return V(0, "CoStar", 0.7, "CoStar records no loading docks (hasLoadingDocks = false)")
    return U(est.get("basis") or "CoStar has no dock count and imagery gave no estimate")


# ------------------------------------------------------------------ one value per (criterion | attribute, building)
def _per(x: dict, size, scale: float, digits: int = 2) -> dict:
    """Scale a value record by 1/size (docks -> docks per 10k SF etc.)."""
    if x["kind"] == "unknown":
        return x
    if not size:
        return U("building size unknown")
    f = lambda v: None if v is None else round(v / size * scale, digits)   # noqa: E731
    y = dict(x, value=f(x["value"]), lo=f(x["lo"]), hi=f(x["hi"]))
    y["basis"] = (x.get("basis") or "") + f" / {size:,.0f} SF"
    return y


def _court(w: dict) -> dict:
    if w.get("truck_court_ft") is not None:
        return V(w["truck_court_ft"], "CoStar", 0.8, "CoStar truck court depth")
    e = _exterior(w["id"])
    if e and e.get("courts") and not e.get("parcel_mismatch"):
        c = max(e["courts"], key=lambda c: (c["kind"] == "dock court", c["doors"]) if "kind" in c else
                (0, c.get("doors") or 0))
        return E(c["depth_ft"], c.get("p10_ft"), c.get("p90_ft"), "Vexcel DSM + ortho", 0.35,
                 f"{c.get('facing')}-facing court in front of {c.get('doors')} detected doors, P10-P90 "
                 f"{c.get('p10_ft')}-{c.get('p90_ft')} ft (imagery {(e['imagery'].get('dsm_date') or '')[:10]}); "
                 "method validated on only 3 sites")
    return U("CoStar has no truck court depth and no court was measured from imagery (no dock doors detected)")


def _clear(w: dict) -> dict:
    if w.get("clear_height_ft") is not None:
        return V(w["clear_height_ft"], "CoStar", 0.8, "CoStar clear height")
    f = _measured(w["id"]).get("clear_height_ft")
    if f is not None and f.value is not None:
        v = float(f.value)
        return E(v, round(v - CLEAR_MAE_FT, 1), round(v + CLEAR_MAE_FT, 1), "Matterport scan", f.confidence,
                 f"scan roof deck - 1.1 ft (CoStar-comparable, MAE {CLEAR_MAE_FT} ft); {f.detail}")
    return U("CoStar has no clear height and there is no warehouse-floor scan")


def _columns(w: dict) -> dict:
    cs = w.get("column_spacing_ft")
    nums = [x for x in cs if isinstance(x, (int, float))] if isinstance(cs, list) else []
    if nums:
        return V(min(nums), "CoStar", 0.8, f"CoStar column spacing {' x '.join(f'{x:g}' for x in nums)} ft "
                                           "(tighter dimension)")
    f = _measured(w["id"]).get("column_spacing_ft")
    if f is not None and isinstance(f.value, str):
        n = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", f.value)]
        if n:
            v = min(n)
            return E(v, v - 2, v + 2, "Matterport scan", f.confidence, f"fitted bay {f.value} ft on interior "
                                                                        "columns (tighter dimension)")
        if "clear span" in f.value:
            return U("the scanned area is clear span (no interior columns seen), but the whole building is not "
                     "scanned")
    return U("CoStar has no column spacing and the scan gave no confident bay fit")


def _size_value(w: dict) -> dict:
    if w.get("rba_sqft"):
        return V(round(w["rba_sqft"]), "CoStar", 0.85, "CoStar rentable building area (RBA)")
    if w.get("gba_sqft"):
        return V(round(w["gba_sqft"]), "CoStar", 0.8, "CoStar gross building area (GBA)")
    fp = _vx(w["id"], "building", "footprint_sqft")
    if fp:
        return E(fp["value"], None, None, "Vexcel footprint", 0.5, "roof footprint from aerial (not floor area)")
    return U("no building size")


def _drive_ins(w: dict) -> dict:
    if w.get("drive_ins") is not None:
        return V(w["drive_ins"], "CoStar", 0.8, "CoStar drive-in doors")
    e = _exterior(w["id"])
    n = ((e or {}).get("doors") or {}).get("drive_in") if e and not e.get("parcel_mismatch") else None
    if n:
        return E(n, n, None, "Vexcel obliques", 0.3, f"at least {n} drive-in doors seen on the obliques",
                 status="lower_bound")
    return U("CoStar has no drive-in count and none were seen on the obliques (not 0)")


def _drive_in_h(w: dict) -> dict:
    if w.get("drive_in_h_ft") is not None:
        return V(w["drive_in_h_ft"], "CoStar", 0.8, "CoStar drive-in door height")
    e = _exterior(w["id"])
    hs = [d.get("height_ft") for d in ((e or {}).get("doors") or {}).get("items") or []
          if d.get("class") == "drive_in" and d.get("height_ft")] if e and not e.get("parcel_mismatch") else []
    if hs:
        h = round(max(hs), 1)
        return E(h, round(h - DRIVE_IN_H_MAE_FT, 1), round(h + DRIVE_IN_H_MAE_FT, 1), "Vexcel obliques", 0.35,
                 f"tallest of {len(hs)} drive-in doors measured on obliques (MAE {DRIVE_IN_H_MAE_FT} ft)")
    return U("no drive-in door height from CoStar or imagery")


def _parking(w: dict) -> dict:
    if w.get("parking_spaces") is not None:
        return V(w["parking_spaces"], "CoStar", 0.8, "CoStar parking spaces")
    c = _vx(w["id"], "parking", "car_stalls_est")
    if c and not (_vexcel(w["id"]) or {}).get("parcel_mismatch"):
        return E(c["value"], None, c["value"], "Vexcel ortho", 0.3,
                 "upper bound: SAM3 parking-lot area / 350 SF per stall (median 1.6x CoStar)", status="upper_bound")
    return U("no CoStar parking count and no parking lot detected")


def _lot(w: dict, per_ksf: bool) -> dict:
    size = _size(w)
    if w.get("lot_acres"):
        v = V(w["lot_acres"], "CoStar", 0.8, "CoStar lot size")
    else:
        a = _vx(w["id"], "site", "parcel_acres")
        if a and not (_vexcel(w["id"]) or {}).get("parcel_mismatch"):
            v = E(a["value"], a["value"] * 0.9, a["value"] * 1.1, "Vexcel parcel", 0.5,
                  f"Vexcel parcel polygon ({a.get('date')})")
        else:
            return U("no lot size from CoStar or a trustworthy Vexcel parcel")
    return _per(v, size, 1000, 3) if per_ksf else v


def _staging(w: dict) -> dict:
    if (_vexcel(w["id"]) or {}).get("parcel_mismatch"):
        return U("the Vexcel parcel looks wrong for this property")
    p = _vx(w["id"], "parking", "paved_area_sqft")
    if not p:
        return U("no paved-area measurement from imagery")
    t = _vx(w["id"], "parking", "trailers_seen")
    tr = f"; {t['value']} trailers seen on {t.get('date')}" if t else ""
    v = p["value"]
    return E(v, round(v * 0.85), round(v * 1.15), "Vexcel DSM + ortho", p.get("confidence") or 0.5,
             f"paved yard incl. car parking and drive aisles ({p.get('date')}){tr}")


def asking_rent(w: dict) -> dict:
    """Lowest active asking rent, $/SF/yr. Listing rates are already per SF per year; service types differ."""
    lease = [l for l in w.get("listings") or [] if l.get("kind") == "lease" and l.get("status") == "Active"]
    if not lease:
        return U("no active lease listing")
    rated = [l for l in lease if l.get("rent_low_psf_yr") and l.get("rent_unit") in (None, "SquareFeet")]
    if not rated:
        return U("active lease listing, but the asking rent is not disclosed")
    lo = min(l["rent_low_psf_yr"] for l in rated)
    hi = max(l.get("rent_high_psf_yr") or l["rent_low_psf_yr"] for l in rated)
    svc = sorted({l.get("service_type") or "Unknown" for l in rated})
    note = "triple net (tenant also pays taxes, insurance, CAM)" if svc == ["TripleNet"] else \
        f"service type {', '.join(svc)}: not directly comparable with NNN rents"
    x = V(round(lo, 2), "CoStar listing", 0.8, f"lowest active asking rent, $/SF/yr; {note}")
    x["hi"] = round(hi, 2)
    x["service_type"] = svc
    return x


def _bool(w: dict, field: str, label: str) -> dict:
    v = w.get(field)
    if v is None:
        return U(f"CoStar does not say ({label})")
    return V(bool(v), "CoStar", 0.8, f"CoStar {label}")


def _turns(w: dict, op: dict) -> dict:
    d = _docks_value(w)
    if d["kind"] == "unknown":
        return U("needs a dock-high door count: " + (d.get("basis") or ""))
    hours = op.get("shift_hours") or 10
    truck = op.get("truck") or "semi_53"
    turn = (equipment().get(truck) or {}).get("dock_turn_min") or 35
    per = hours * 60 / turn
    f = lambda v: None if v is None else round(v * per)   # noqa: E731
    return E(f(d["value"]), f(d["lo"]), f(d["hi"]), "derived", min(0.5, d["confidence"]),
             f"estimate: {'at least ' if d['status'] == 'lower_bound' else ''}{d['value']} dock doors ({d['source']}) "
             f"x {hours} h x 60 / {turn} min per {(equipment().get(truck) or {}).get('name', truck)} turn "
             "(equipment.yaml); assumes every door works all shift",
             status="lower_bound" if d["status"] == "lower_bound" else "estimated")


def criterion_value(key: str, w: dict, op: Optional[dict] = None) -> dict:
    op = op or {}
    size = _size(w)
    if key in ("docks_total", "docks"):
        return _docks_value(w)
    if key == "docks_per_10ksf":
        return _per(_docks_value(w), size, 10_000)
    if key == "dock_turn_capacity_est":
        return _turns(w, op)
    if key == "cross_dock_config":
        return _bool(w, "cross_dock", "cross-dock configuration")
    if key == "truck_court_ft":
        return _court(w)
    if key == "trailer_staging":
        return _staging(w)
    if key == "clear_height_ft":
        return _clear(w)
    if key == "column_spacing_ft":
        return _columns(w)
    if key in ("usable_floor", "rba_sqft"):
        return _size_value(w)
    if key == "drive_ins":
        return _drive_ins(w)
    if key == "drive_in_h_ft":
        return _drive_in_h(w)
    if key == "power_amps":
        return V(w["power_amps"], "CoStar", 0.8, "CoStar electrical service") if w.get("power_amps") else \
            U("CoStar has no power figure")
    if key == "heavy_power":
        return _bool(w, "heavy_power", "heavy power")
    if key in ("parking", "parking_spaces"):
        return _parking(w)
    if key == "lot_acres":
        return _lot(w, per_ksf=False)
    if key == "lot_acres_per_ksf":
        return _lot(w, per_ksf=True)
    if key == "year_built":
        return V(w["year_built"], "CoStar", 0.8, "CoStar year built") if w.get("year_built") else \
            U("CoStar has no year built")
    if key == "asking_rent":
        return asking_rent(w)
    raise KeyError(key)


# ------------------------------------------------------------------ rank_attribute
ATTRS = {   # key -> (label, unit, default order)
    "docks_total": ("Loading docks", "doors", "desc"),
    "docks_per_10ksf": ("Docks per 10k SF", "per 10k SF", "desc"),
    "drive_ins": ("Drive-in doors", "doors", "desc"),
    "clear_height_ft": ("Clear height", "ft", "desc"),
    "rba_sqft": ("Building size", "SF", "desc"),
    "truck_court_ft": ("Truck court depth", "ft", "desc"),
    "column_spacing_ft": ("Column spacing (tighter bay)", "ft", "desc"),
    "power_amps": ("Power", "A", "desc"),
    "parking_spaces": ("Car parking", "spaces", "desc"),
    "lot_acres": ("Lot size", "acres", "desc"),
    "year_built": ("Year built", "", "desc"),
    "asking_rent": ("Asking rent", "$/SF/yr", "asc"),
    "drive_in_h_ft": ("Drive-in door height", "ft", "desc"),
}
ATTR_WORDS = [   # vague words -> attribute (longest match first)
    ("docks per", "docks_per_10ksf"), ("dock density", "docks_per_10ksf"),
    ("loading dock", "docks_total"), ("dock door", "docks_total"), ("docks", "docks_total"), ("dock", "docks_total"),
    ("drive-in", "drive_ins"), ("drive in", "drive_ins"), ("grade level", "drive_ins"), ("roll-up", "drive_ins"),
    ("clear height", "clear_height_ft"), ("ceiling", "clear_height_ft"), ("tallest", "clear_height_ft"),
    ("height", "clear_height_ft"),
    ("truck court", "truck_court_ft"), ("court", "truck_court_ft"), ("apron", "truck_court_ft"),
    ("column", "column_spacing_ft"), ("bay", "column_spacing_ft"),
    ("power", "power_amps"), ("amps", "power_amps"), ("electric", "power_amps"),
    ("parking", "parking_spaces"), ("car stalls", "parking_spaces"),
    ("lot size", "lot_acres"), ("acre", "lot_acres"), ("land", "lot_acres"),
    ("newest", "year_built"), ("year built", "year_built"), ("oldest", "year_built"),
    ("rent", "asking_rent"), ("cheapest", "asking_rent"), ("price per", "asking_rent"),
    ("biggest", "rba_sqft"), ("largest", "rba_sqft"), ("square feet", "rba_sqft"), ("size", "rba_sqft"),
]


def attr_for(phrase: str) -> Optional[str]:
    t = (phrase or "").lower().strip()
    if t in ATTRS:
        return t
    t = t.replace("_", " ")
    for w, k in sorted(ATTR_WORDS, key=lambda x: -len(x[0])):
        if w in t:
            return k
    return None


def _market(m: Optional[str]) -> Optional[str]:
    if not m:
        return None
    t = m.strip().lower()
    if t.startswith(("la", "los", "l.a")):
        return "Los Angeles"
    if t.startswith(("hou", "tx")):
        return "Houston"
    return None


def rank_attribute(attr: str, order: Optional[str] = None, market: Optional[str] = None, top: int = 20,
                   ids: Optional[list[int]] = None) -> dict:
    key = attr_for(attr)
    if not key:
        raise KeyError(f"unknown attribute {attr!r}; supported: {sorted(ATTRS)}")
    label, unit, default = ATTRS[key]
    order = order if order in ("asc", "desc") else default
    mk = _market(market)
    pool = [w for w in _catalog() if (not mk or w.get("market") == mk) and (not ids or w["id"] in set(ids))]
    ver, est, unk = [], [], []
    for w in pool:
        x = criterion_value(key, w)
        base = {"id": w["id"], "address": w.get("address"), "market": w.get("market"), "size_sqft": _size(w)}
        if x["kind"] == "verified":
            ver.append({**base, "value": x["value"], "source": x["source"], "confidence": x["confidence"],
                        "basis": x.get("basis"), **({"alt": x["alt"]} if x.get("alt") else {}),
                        **({"hi": x["hi"]} if x.get("hi") != x["value"] else {})})
        elif x["kind"] == "estimated":
            est.append({**base, "value": x["value"], "lo": x["lo"], "hi": x["hi"], "status": x["status"],
                        "source": x["source"], "confidence": x["confidence"], "basis": x.get("basis")})
        else:
            unk.append({**base, "why": x.get("basis")})
    sgn = -1 if order == "desc" else 1
    ver.sort(key=lambda r: (sgn * float(r["value"]), r["id"]))
    est.sort(key=lambda r: (r["value"] is None, sgn * float(r["value"] if r["value"] is not None else 0), r["id"]))
    unk.sort(key=lambda r: (-(r["size_sqft"] or 0), r["id"]))          # largest unknown first: worth investigating
    top = max(1, min(int(top or 20), 200))
    out = {"attr": key, "label": label, "unit": unit, "order": order, "market": mk,
           "verified": ver[:top], "estimated": est[:top], "unknown": unk,
           "coverage": {"verified": len(ver), "estimated": len(est), "unknown": len(unk)},
           "note": "verified = CoStar (or confirmed by a person); estimated = imagery/scan with a range or bound; "
                   "unknown is never counted as 0. The catalog is CoStar's scan list, not the whole market."}
    if key in ("docks_total", "docks_per_10ksf"):
        zeros = sum(1 for r in ver if r["value"] == 0)
        if zeros:
            out["note"] += f" {zeros} verified entries are 0 because CoStar records no loading docks there."
    return out


# ------------------------------------------------------------------ criteria selection
def _ops() -> dict:
    return library()["operations"]


def operation_for_text(text: str) -> Optional[str]:
    """Keyword rules: the longest matching keyword wins."""
    t = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    best = None
    for k, op in _ops().items():
        for kw in [k.replace("_", " ")] + list(op.get("keywords") or []):
            kw = kw.lower()
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", t):
                if best is None or len(kw) > best[0]:
                    best = (len(kw), k)
    if best:
        return best[1]
    try:
        from proinspect.query import PROFILE_WORDS
        for k, rx in PROFILE_WORDS.items():
            if re.search(rx, t) and k in _ops():
                return k
    except Exception:
        pass
    return None


def _claude_operation(text: str) -> Optional[str]:
    """Only when the rules found nothing: ask Claude to pick ONE operation key (never numbers)."""
    try:
        import anthropic
        keys = sorted(_ops())
        schema = {"type": "object", "properties": {"operation": {"type": ["string", "null"], "enum": keys + [None]}},
                  "required": ["operation"], "additionalProperties": False}
        r = anthropic.Anthropic(timeout=15).beta.messages.create(
            model="claude-opus-5-5", max_tokens=200,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": "Which warehouse operation type best matches this description? "
                       f"Answer null if none fits. Types: {', '.join(keys)}.\n\n{text}"}])
        txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        k = json.loads(txt).get("operation")
        return k if k in _ops() else None
    except Exception:
        return None


def select_criteria(profile: Optional[str] = None, ops_text: Optional[str] = None, use_claude: bool = True) -> dict:
    lib = library()
    ops = lib["operations"]
    key, how = None, None
    if profile and profile in ops:
        key, how = profile, "profile"
    elif profile:
        key = operation_for_text(profile)
        how = "profile words" if key else None
    if not key and ops_text:
        key = operation_for_text(ops_text)
        how = "keywords" if key else None
        if not key and use_claude:
            key = _claude_operation(ops_text)
            how = "claude" if key else None
    if not key:
        key, how = lib.get("default", "pallet_storage"), "default"
    op = ops[key]
    defs = lib["criteria"]
    crits = []
    for c in op["criteria"]:
        d = defs[c["key"]]
        crits.append({"key": c["key"], "label": d["label"], "unit": d.get("unit") or "", "better": d["better"],
                      "weight": int(c.get("weight", 3)), "why": c.get("why"), "level": c.get("level", "N"),
                      **{k: c[k] for k in ("min", "max", "value") if k in c}})
    return {"operation": key, "name": op.get("name", key), "matched_by": how, "profile": op.get("profile"),
            "shift_hours": op.get("shift_hours"), "truck": op.get("truck"), "criteria": crits}


# ------------------------------------------------------------------ compare
def _tol(key: str, a, b) -> float:
    t = library()["criteria"][key].get("tol") or {}
    m = max(abs(float(a or 0)), abs(float(b or 0)))
    return max(float(t.get("abs", 0)), float(t.get("rel", 0)) * m)


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else None


def _interval(x: dict, sign: float):
    """(point, lo, hi) in 'bigger is better' space."""
    v, lo, hi = _num(x["value"]), _num(x["lo"]), _num(x["hi"])
    if x["kind"] == "verified":
        lo = hi = v
    else:
        if x.get("status") == "lower_bound":
            hi = math.inf
        if x.get("status") == "upper_bound":
            lo = -math.inf if lo is None else lo
        lo = v if lo is None else lo
        hi = v if hi is None else hi
        if v is None:
            v = lo if lo not in (None, -math.inf) else hi
    if sign < 0:
        v, lo, hi = -v, -hi, -lo
    return v, lo, hi


def winner(key: str, values: list[dict], better: str) -> dict:
    """values: [{id, value, lo, hi, kind, status}]. A winner only when every value is known and the best one beats
    every other by more than the tolerance (intervals for estimates). Else 'tie' (within tolerance) or 'unknown'."""
    sign = 1.0 if better == "higher" else -1.0
    known = [x for x in values if x["kind"] != "unknown" and (x["value"] is not None or x["lo"] is not None
                                                                or x["hi"] is not None)]
    if len(known) < 2 or len(known) < len(values):
        return {"winner": None, "verdict": "unknown", "leader": _leader(known, sign) if len(known) >= 2 else None}
    iv = {x["id"]: _interval(x, sign) for x in known}
    order = sorted(known, key=lambda x: (-iv[x["id"]][0], x["id"]))
    best, second = order[0], order[1]
    bp, blo, _ = iv[best["id"]]
    sp = iv[second["id"]][0]
    others_hi = max(iv[x["id"]][2] for x in order[1:])
    tol = _tol(key, bp * sign, sp * sign)
    if blo - others_hi > tol:
        return {"winner": best["id"], "verdict": "winner", "estimated": best["kind"] == "estimated"
                or any(x["kind"] == "estimated" for x in order[1:])}
    if abs(bp - sp) <= tol:
        return {"winner": None, "verdict": "tie"}
    return {"winner": None, "verdict": "unknown", "leader": best["id"],
            "why": "ranges overlap: an estimate could go either way"}


def _leader(known, sign):
    if not known:
        return None
    return max(known, key=lambda x: (_interval(x, sign)[0], -x["id"]))["id"]


def _fmt(x: dict, unit: str) -> str:
    """Compact display for a 4-column panel: the row label carries long units ('per 10k SF', 'turns/day')."""
    v = x.get("value")
    if v is None and x.get("status") == "lower_bound":
        v = x.get("lo")
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if unit == "SF" and abs(v) >= 10_000:
        s = f"{v / 1000:,.0f}k"
    elif abs(v) >= 100 or float(v).is_integer():
        s = f"{v:,.0f}"
    else:
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
    if unit == "$/SF/yr":
        s = "$" + s
    pre = "≥ " if x.get("status") == "lower_bound" else "≤ " if x.get("status") == "upper_bound" else \
        "~" if x.get("kind") == "estimated" else ""
    u = {"ft": " ft", "SF": " SF", "A": " A", "acres": " ac"}.get(unit, "")
    return pre + s + u


def _check_dealbreaker(c: dict, x: dict) -> tuple[str, Optional[str]]:
    """-> (pass|flag|fail, reason). Only a VERIFIED value can fail; an estimate or unknown flags."""
    if c.get("level") != "D" or not any(k in c for k in ("min", "max", "value")):
        return "pass", None
    if x["kind"] == "unknown":
        return "flag", f"{c['label']} unknown (deal-breaker)"
    v, lo, hi = x["value"], x["lo"], x["hi"]
    if "value" in c:
        ok = bool(v) == bool(c["value"]) if isinstance(c["value"], bool) else str(c["value"]).lower() in str(v).lower()
        return ("pass", None) if ok else ("fail" if x["kind"] == "verified" else "flag",
                                          f"{c['label']}: {_fmt(x, c['unit'])} (needs {'yes' if c['value'] is True else c['value']})")
    need = f"≥ {c['min']:g}" if "min" in c else f"≤ {c['max']:g}"
    if "min" in c:
        if x["kind"] == "verified":
            return ("pass", None) if v >= c["min"] else ("fail", f"{c['label']} {_fmt(x, c['unit'])} (needs {need})")
        top = math.inf if x.get("status") == "lower_bound" else (hi if hi is not None else v)
        bot = lo if lo is not None else v
        if bot is not None and bot >= c["min"]:
            return "pass", None
        if top is not None and top < c["min"]:
            return "flag", f"{c['label']} estimated {_fmt(x, c['unit'])} (needs {need})"
        return "flag", f"{c['label']} estimate {_fmt(x, c['unit'])} can't confirm {need}"
    if x["kind"] == "verified":
        return ("pass", None) if v <= c["max"] else ("fail", f"{c['label']} {_fmt(x, c['unit'])} (needs {need})")
    return "flag", f"{c['label']} estimate {_fmt(x, c['unit'])} can't confirm {need}"


def _short(addr: Optional[str]) -> str:
    return (addr or "?").split(",")[0]


def compare(ids: list[int], profile: Optional[str] = None, ops_text: Optional[str] = None,
            use_claude: bool = True) -> dict:
    ws = []
    for i in ids or []:
        w = wh(i)
        if w and w not in ws:
            ws.append(w)
    if len(ws) < 2:
        raise ValueError("compare needs 2-4 catalog ids")
    ws = ws[:4]
    sel = select_criteria(profile, ops_text, use_claude=use_claude)
    op = {"shift_hours": sel["shift_hours"], "truck": sel["truck"]}
    rows, fit, fit_why = [], {w["id"]: "pass" for w in ws}, {w["id"]: [] for w in ws}
    for c in sel["criteria"]:
        vals = []
        for w in ws:
            x = criterion_value(c["key"], w, op)
            vals.append({"id": w["id"], **{k: x.get(k) for k in ("value", "lo", "hi", "source", "confidence", "kind",
                                                                  "status", "basis")},
                         "display": _fmt(x, c["unit"])})
            st, why = _check_dealbreaker(c, x)
            if st == "fail":
                fit[w["id"]] = "fail"
            elif st == "flag" and fit[w["id"]] == "pass":
                fit[w["id"]] = "flag"
            if why:
                fit_why[w["id"]].append(why)
        win = winner(c["key"], vals, c["better"])
        rows.append({"key": c["key"], "label": c["label"], "unit": c["unit"], "better": c["better"],
                     "weight": c["weight"], "level": c["level"], "values": vals, **win})
    # points: weight of each criterion won
    pts = {w["id"]: sum(r["weight"] for r in rows if r.get("winner") == w["id"]) for w in ws}
    unk = {w["id"]: sum(r["weight"] for r in rows for v in r["values"] if v["id"] == w["id"] and v["kind"] == "unknown")
           for w in ws}
    addr = {w["id"]: _short(w.get("address")) for w in ws}
    alive = [w for w in ws if fit[w["id"]] != "fail"] or ws
    # passing every deal-breaker beats an unverified one; then criteria won (by weight); then fewer unknowns
    rec = max(alive, key=lambda w: ({"pass": 1, "flag": 0, "fail": -1}[fit[w["id"]]], pts[w["id"]], -unk[w["id"]],
                                    _size(w) or 0, -w["id"]))
    rid = rec["id"]
    won = sorted([r for r in rows if r.get("winner") == rid], key=lambda r: -r["weight"])
    basis = [r["key"] for r in won[:3]]
    lab = lambda rs: ", ".join(r["label"][:1].lower() + r["label"][1:] for r in rs)    # noqa: E731
    s = []
    if all(fit[w["id"]] == "fail" for w in ws):
        s.append(f"None of these passes every {sel['name']} deal-breaker; {addr[rid]} comes closest.")
    elif won:
        s.append(f"On {sel['name']} criteria, {addr[rid]} comes out ahead on {lab(won[:3])}.")
    elif fit[rid] == "pass":
        s.append(f"{addr[rid]} is the safest {sel['name']} pick: it passes every deal-breaker, though it doesn't "
                 "win a criterion outright.")
    else:
        s.append(f"No building clearly leads on {sel['name']} criteria; {addr[rid]} is the least uncertain pick.")
    others = sorted([w for w in ws if w["id"] != rid], key=lambda w: -pts[w["id"]])
    if others and pts[others[0]["id"]]:
        o = others[0]
        ahead = f"{addr[o['id']]} is ahead on {lab([r for r in rows if r.get('winner') == o['id']][:2])}"
        why0 = (fit_why[o["id"]] or [""])[0].replace(" (deal-breaker)", "")
        s.append(ahead + (f", but it has an open deal-breaker ({why0[:1].lower() + why0[1:]})." if fit[o["id"]] == "flag"
                          and why0 else f", but it fails a deal-breaker ({why0[:1].lower() + why0[1:]})."
                          if fit[o["id"]] == "fail" and why0 else "."))
    undecided = [r for r in rows if r["verdict"] == "unknown"]
    if fit[rid] == "flag":
        s.append(f"Check before deciding: {fit_why[rid][0]}.")
    elif undecided:
        s.append(f"{len(undecided)} of {len(rows)} criteria can't be decided yet ({lab(undecided[:3])}).")
    tradeoffs = _tradeoffs(rows, ws, rid, addr, fit, fit_why)
    return {"profile": sel["operation"], "operation": {k: sel[k] for k in ("operation", "name", "matched_by")},
            "criteria": [{k: c.get(k) for k in ("key", "label", "weight", "why", "level", "min", "max", "value")
                          if c.get(k) is not None} for c in sel["criteria"]],
            "candidates": [{"id": w["id"], "address": w.get("address"), "market": w.get("market"),
                            "size_sqft": _size(w), "availability": w.get("availability"),
                            "inspectable": w.get("inspectable"),
                            "thumb": f"/media/twins/exterior/{w['id']}_ortho.jpg"
                            if (BUILD / "twins" / "exterior" / f"{w['id']}_ortho.jpg").exists() else None,
                            "points": pts[w["id"]]} for w in ws],
            "rows": rows, "tradeoffs": tradeoffs, "fit": fit, "fit_why": fit_why,
            "recommendation": {"id": rid, "text": " ".join(s[:3]), "basis": basis},
            "method": "winner only when every value is known and the best beats the rest by more than the "
                      "criterion's tolerance (ranges for estimates); deal-breakers fail only on verified values"}


def _tradeoffs(rows, ws, rid, addr, fit, fit_why) -> list[str]:
    out = []
    by_id = lambda r, i: next(v for v in r["values"] if v["id"] == i)   # noqa: E731
    pts = {w["id"]: sum(r["weight"] for r in rows if r.get("winner") == w["id"]) for w in ws}
    for o in sorted([w for w in ws if w["id"] != rid], key=lambda w: (-pts[w["id"]], w["id"]))[:2]:
        mine = sorted([r for r in rows if r.get("winner") == rid], key=lambda r: -r["weight"])
        theirs = sorted([r for r in rows if r.get("winner") == o["id"]], key=lambda r: -r["weight"])
        if mine and theirs:
            a, b = mine[0], theirs[0]
            out.append(f"{addr[rid]} wins on {a['label'].lower()} ({by_id(a, rid)['display']} vs "
                       f"{by_id(a, o['id'])['display']}), but {addr[o['id']]} wins on {b['label'].lower()} "
                       f"({by_id(b, o['id'])['display']} vs {by_id(b, rid)['display']}).")
        elif theirs:
            b = theirs[0]
            out.append(f"{addr[o['id']]} wins on {b['label'].lower()} ({by_id(b, o['id'])['display']} vs "
                       f"{by_id(b, rid)['display']}).")
        elif mine:
            out.append(f"{addr[rid]} matches or beats {addr[o['id']]} on every criterion that can be decided.")
    for w in ws:
        if fit[w["id"]] == "fail":
            out.append(f"{addr[w['id']]} fails a deal-breaker: {next(x for x in fit_why[w['id']] if 'needs' in x)}.")
    for w in ws:
        if fit[w["id"]] == "flag" and fit_why[w["id"]] and len(out) < 3:
            out.append(f"{addr[w['id']]} needs checking: {'; '.join(fit_why[w['id']][:2])}.")
    est = [r for r in rows if r.get("verdict") == "winner" and r.get("estimated")]
    for r in est[:1]:
        out.append(f"{r['label']}: {addr[r['winner']]} leads on an estimate from imagery or the scan, "
                   "not a CoStar figure.")
    unk = [r for r in rows if r["verdict"] == "unknown" and any(v["kind"] == "unknown" for v in r["values"])]
    if unk:
        r = max(unk, key=lambda r: r["weight"])
        who = [addr[v["id"]] for v in r["values"] if v["kind"] == "unknown"]
        chk = library()["criteria"][r["key"]].get("check")
        out.append(f"{r['label']} is unknown for {', '.join(who)}" + (f": {chk}." if chk else "."))
    return out[:4]


# ------------------------------------------------------------------ comparables
def _scores(pool: list[dict], sel: dict) -> dict:
    """Weighted score 0-100 per building over the selected criteria: each criterion min-max normalised over the
    pool's known values (estimates at their point value); unknown counts 0.5 (as in rank.py)."""
    op = {"shift_hours": sel["shift_hours"], "truck": sel["truck"]}
    vals = {c["key"]: {w["id"]: criterion_value(c["key"], w, op) for w in pool} for c in sel["criteria"]}
    tot = {w["id"]: 0.0 for w in pool}
    wsum = sum(c["weight"] for c in sel["criteria"]) or 1
    for c in sel["criteria"]:
        nums = {i: _num(x["value"]) for i, x in vals[c["key"]].items() if x["kind"] != "unknown"
                and _num(x["value"]) is not None}
        lo, hi = (min(nums.values()), max(nums.values())) if nums else (0, 0)
        for i in tot:
            if i in nums and hi > lo:
                s = (nums[i] - lo) / (hi - lo)
                s = s if c["better"] == "higher" else 1 - s
            else:
                s = 0.5
            tot[i] += c["weight"] * s
    return {i: round(100 * v / wsum) for i, v in tot.items()}


def find_comparables(pid: int, profile: Optional[str] = None, ops_text: Optional[str] = None, n: int = 3,
                     use_claude: bool = True) -> dict:
    w0 = wh(pid)
    if not w0:
        raise KeyError(f"warehouse {pid} not in the catalog")
    sel = select_criteria(profile, ops_text, use_claude=use_claude)
    size = _size(w0)
    pool = [w for w in _catalog() if w["id"] != w0["id"] and w.get("market") == w0.get("market")
            and w.get("status", "Existing") == "Existing"]
    if size:
        pool = [w for w in pool if _size(w) and 0.6 * size <= _size(w) <= 1.4 * size]
    op = {"shift_hours": sel["shift_hours"], "truck": sel["truck"]}
    keep = []
    for w in pool:
        if not any(_check_dealbreaker(c, criterion_value(c["key"], w, op))[0] == "fail" for c in sel["criteria"]):
            keep.append(w)
    sc = _scores(keep + [w0], sel)
    keep.sort(key=lambda w: (-sc[w["id"]], w["id"]))
    top = keep[:max(1, int(n or 3))]
    why = (f"same market ({w0.get('market')})" + (f", {0.6 * size:,.0f}-{1.4 * size:,.0f} SF (±40% of "
                                                    f"{size:,.0f} SF)" if size else "")
           + f", not failing a {sel['name']} deal-breaker; ranked by the weighted {sel['name']} criteria "
             f"score ({len(pool)} in the size band, {len(keep)} pass)")
    return {"id": w0["id"], "ids": [w["id"] for w in top], "why": why, "operation": sel["operation"],
            "operation_name": sel["name"],
            "scores": [{"id": w["id"], "address": w.get("address"), "score": sc[w["id"]], "size_sqft": _size(w)}
                       for w in top], "this_score": sc[w0["id"]]}
