"""Measured facts from the twins, in the ranker's Fact form, with honest confidences.

Confidences sit BELOW CoStar's (0.8) on purpose: a measurement fills a gap CoStar leaves, and is shown next to
CoStar's value when both exist, but it does not silently override a researched record. The validation report
(build/validation/) is what justifies each confidence; change them only with new validation numbers.

Rules that keep this honest:
  - "no doors detected" is NOT "0 docks": door facts are only emitted when doors were actually seen.
  - clear height uses the CoStar-comparable statistic (roof deck - 1.1 ft, validated MAE 2.3 ft on held-out
    scans); the conservative lowest-obstruction P5 is carried in `detail` for rack planning.
"""
import json
from functools import lru_cache

from proinspect.config import BUILD
from proinspect.rank import Fact

CONF = {"high": 0.7, "medium": 0.55, "low": 0.35}
DECK_TO_COSTAR_CLEAR_FT = 1.1          # from build/validation/interior.md (held-out half)


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def exterior_facts(pid) -> dict:
    r = _load(BUILD / "twins" / "exterior" / f"{pid}.json")
    if not r:
        return {}
    out = {}
    date = r["imagery"].get("dsm_date") or r["imagery"].get("parcel_date")
    d = r["doors"]
    # Door detection under-counts badly (validation: corr -0.07 with CoStar docks; long dock walls are foreshortened
    # in obliques). So detections are only a LOWER BOUND, and "none seen" is never a fact.
    if d["total"] and not r.get("parcel_mismatch"):
        if d["dock_high"] + d["uncertain"]:
            out["docks_total"] = Fact(d["dock_high"] + d["uncertain"], "vexcel", 0.2,
                                      f"at least {d['dock_high'] + d['uncertain']} dock-type doors seen on obliques",
                                      bound="lower")
        if d["drive_in"]:
            out["drive_ins"] = Fact(d["drive_in"], "vexcel", 0.2, f"at least {d['drive_in']} drive-in doors seen",
                                    bound="lower")
    if r["courts"] and not r.get("parcel_mismatch"):
        c = max(r["courts"], key=lambda c: (c["kind"] == "dock court", c["doors"]))
        out["truck_court_ft"] = Fact(c["depth_ft"], "vexcel", min(CONF[c["confidence"]], 0.5),
                                     f"{c['facing']}-facing court, {c['doors']} doors, P10-P90 {c['p10_ft']}-"
                                     f"{c['p90_ft']} ft, imagery {date}; method validated on only 3 sites")
    b = r["building"]
    if b.get("roof_condition") is not None:
        out["roof_condition"] = Fact(b["roof_condition"], "vexcel", 0.6,
                                     f"Vexcel 1-5 scale; defects: {', '.join(b['roof_defects']) or 'none'}")
    return out


def interior_facts(w: dict) -> dict:
    scan = next((s for s in w.get("scans", []) if s["class"] == "warehouse_floor"), None)
    if not scan:
        return {}
    r = _load(BUILD / "twins" / "interior" / f"{scan['model_id']}.json")
    if not r or not r.get("deck_ft"):
        return {}
    out = {}
    conf = CONF.get(r["clear_confidence"], 0.35)
    p5 = (r.get("clear") or {}).get("p5_ft")
    out["clear_height_ft"] = Fact(round(r["deck_ft"] - DECK_TO_COSTAR_CLEAR_FT, 1), "matterport", conf,
                                  f"deck {r['deck_ft']} ft - {DECK_TO_COSTAR_CLEAR_FT}; lowest obstruction P5 "
                                  f"{p5} ft (use for rack height); scan {scan['model_id']}")
    bays = r["columns"].get("bays") or {}
    if all((bays.get(k) or {}).get("confident") for k in ("x", "y")):
        out["column_spacing_ft"] = Fact(f"{bays['x']['spacing_ft']} x {bays['y']['spacing_ft']}", "matterport",
                                        0.6, "fitted on interior columns")
    elif r["columns"]["structure"].startswith("clear span"):
        out["column_spacing_ft"] = Fact("clear span", "matterport", 0.5, "no interior columns in the scanned area")
    return out


@lru_cache
def measured_facts() -> dict:
    """{propertyId: {fact: Fact}} for every warehouse with a twin on disk."""
    cat = json.loads((BUILD / "catalog" / "warehouses.json").read_text())
    out = {}
    for w in cat:
        f = {**exterior_facts(w["id"]), **interior_facts(w)}
        if f:
            out[w["id"]] = f
    return out
