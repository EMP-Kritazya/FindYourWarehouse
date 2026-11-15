"""True cost per pallet: what it costs per month to store a pallet (and to move one) in this building for an operation.

    rent      $/SF/yr x SF used                  rent_comps tier: recorded (asking / own lease) or estimate (comps)
    racking   positions x $/position / years     positions: interior planner (calculated); $/position: assumed
    machines  machines needed x lease $/month    machines: interior planner (calculated); lease: assumed
    operators machines x shifts x hours x wage   assumed wage by market

Read-only on the rest of the app: it calls interior.plan() (cached) and never changes it. Every line carries its
source; a missing input makes the result unknown or rough, never zero.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import yaml

from proinspect import rent_comps

ENABLED = os.environ.get("PROINSPECT_COST", "1") != "0"
COSTS = yaml.safe_load((Path(__file__).resolve().parent / "library" / "costs.yaml").read_text())
RACK_TYPES = ("selective", "dense", "cantilever", "shelving")


def _line(label, value, source, basis, unit="$/yr"):
    return {"label": label, "value": None if value is None else round(float(value)), "unit": unit, "source": source,
            "basis": basis}


def _num(overrides, key, default):
    v = (overrides or {}).get(key)
    try:
        return float(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default


def cost(pid, operation=None, overrides=None):
    from proinspect import interior, racks
    pid = int(pid)
    w = racks._catalog(pid)
    op = operation or "pallet_3pl"
    ov = overrides or {}
    out = {"property_id": pid, "address": w.get("address"), "market": w.get("market"), "operation": op,
           "assumptions_editable": ["rent_psf", "sqft", "rack_per_position", "machine_lease_month", "wage_hour",
                                    "shifts_per_day", "working_days_year", "amortise_years"]}

    # ---- rent
    rent = rent_comps.rent_for(w)
    if ov.get("rent_psf") not in (None, ""):
        rent = {**rent, "tier": "yours", "value": _num(ov, "rent_psf", None), "source": "yours", "basis": "your rent"}
    leased = [ln for ln in w.get("listings") or [] if ln.get("kind") == "lease" and ln.get("available_sqft")]
    rba = w.get("rba_sqft") or w.get("gba_sqft")
    if ov.get("sqft") not in (None, ""):
        sqft, sqft_src, sqft_basis = _num(ov, "sqft", None), "yours", "your space"
    elif leased:
        sqft, sqft_src, sqft_basis = float(max(ln["available_sqft"] for ln in leased)), "recorded", "available SF on the CoStar listing"
    else:
        sqft, sqft_src, sqft_basis = (float(rba) if rba else None), "recorded", "the whole building (CoStar RBA)"
    frac = (sqft / float(rba)) if sqft and rba else 1.0
    frac = max(0.0, min(1.0, frac))
    out["rent"] = rent
    out["space"] = {"sqft": round(sqft) if sqft else None, "source": sqft_src, "basis": sqft_basis,
                    "share_of_building": round(frac, 3)}

    # ---- the operation in this building (interior planner, cached; read only)
    try:
        p = interior.plan(pid, op)
    except FileNotFoundError as e:
        p, out["plan_note"] = None, f"no measured warehouse floor to plan on ({str(e)[:120]})"
    except ValueError as e:
        p, out["plan_note"] = None, str(e)[:160]
    lines, positions, rough = [], None, False
    rent_yr = rent["value"] * sqft if rent.get("value") is not None and sqft else None
    lines.append(_line("Rent", rent_yr, rent.get("source", "unknown"),
                       (f"{rent['value']:.2f} $/SF/yr × {sqft:,.0f} SF · {rent['basis']}" if rent_yr is not None
                        else rent["basis"])))
    if p:
        est = p.get("building_estimate") or {}
        scale = (est.get("ratio") or 1.0) * frac
        rough = not est.get("reliable", False)
        positions = int(round(p["positions"] * scale)) if p.get("positions") else 0
        rack_yr, rack_basis = 0.0, []
        years = _num(ov, "amortise_years", COSTS["amortise_years"]["value"])
        for z in p.get("zones") or []:
            t = z.get("type")
            if t not in RACK_TYPES or not z.get("positions"):
                continue
            rate = _num((ov.get("rack_per_position") or {}), t, COSTS["rack_per_position"][t]["value"])
            n = z["positions"] * scale
            rack_yr += n * rate / years
            rack_basis.append(f"{n:,.0f} {t} × ${rate:,.0f}")
        lines.append(_line("Racking (amortised)", rack_yr, "assumed",
                           f"{' + '.join(rack_basis) or 'no racks'} over {years:g} years (installed cost: assumed)"))
        pr = p.get("proofs") or {}
        mkey = p.get("machine")
        moves = (pr.get("daily_pallet_moves") or 0) * frac
        pph, shift_h = pr.get("pallets_per_hour"), pr.get("shift_hours") or 8
        machines = math.ceil(moves / (shift_h * pph * 0.8)) if moves and pph else None
        lease = _num((ov.get("machine_lease_month") or {}), mkey, (COSTS["machine_lease_month"].get(mkey) or {}).get("value"))
        shifts = _num(ov, "shifts_per_day", COSTS["shifts_per_day"]["value"])
        wage = _num(ov, "wage_hour", (COSTS["operator_wage_hour"].get(w.get("market")) or {}).get("value"))
        days = _num(ov, "working_days_year", COSTS["working_days_year"]["value"])
        lines.append(_line("Forklifts (lease)", machines * lease * 12 if machines and lease else None, "assumed",
                           f"{machines} × {p.get('machine_name')} (calculated from {moves:,.0f} pallet moves/day) × "
                           f"${lease:,.0f}/month (assumed lease)" if machines and lease else "no pallet volume for this operation"))
        ops = machines * shifts if machines else None
        lines.append(_line("Operators", ops * shift_h * wage * days if ops and wage else None, "assumed",
                           f"{ops:g} operator{'s' if ops != 1 else ''} × {shift_h:g} h × {days:g} days × ${wage:g}/h "
                           f"(assumed loaded wage, {w.get('market')})" if ops and wage else "no operators needed"))
        out["operation_name"] = p.get("operation_name")
        out["positions"] = {"value": positions, "source": "calculated", "rough": rough,
                            "basis": (f"{p['positions']:,} positions on the scanned floor × {scale:.2f} "
                                      f"(building scale-up {est.get('ratio', 1):g}{f' × {frac:.0%} of the building leased' if frac < 1 else ''})")
                                     + (" · the scan covers little of the building, so this is rough" if rough else "")}
        out["moves_per_day"] = round(moves) if moves else None
    total = sum(l["value"] for l in lines if l["value"] is not None)
    have_rent = lines[0]["value"] is not None
    out["lines"] = lines
    out["total_year"] = {"value": round(total) if total else None, "complete": have_rent and p is not None,
                         "note": None if have_rent else "rent unknown: the total covers operating costs only"}
    per_pos = (total / 12 / positions) if positions and have_rent and total else None
    per_pos_ops = (sum(l["value"] or 0 for l in lines[1:]) / 12 / positions) if positions else None
    out["per_position_month"] = {"value": round(per_pos, 2) if per_pos else None, "rough": rough,
                                 "ops_only": round(per_pos_ops, 2) if per_pos_ops else None,
                                 "basis": "all costs ÷ 12 ÷ pallet positions"}
    moves_yr = (out.get("moves_per_day") or 0) * _num(ov, "working_days_year", COSTS["working_days_year"]["value"])
    out["per_pallet_moved"] = {"value": round(total / moves_yr, 2) if moves_yr and have_rent and total else None,
                               "basis": "all costs ÷ pallet moves per year"}
    if per_pos:
        out["headline"] = f"${per_pos:,.2f} per pallet position per month" + (" (rough)" if rough else "")
    elif per_pos_ops:
        out["headline"] = f"${per_pos_ops:,.2f} per position per month before rent (rent unknown)"
    else:
        out["headline"] = "cost per pallet unknown: " + (out.get("plan_note") or "no positions")
    return out
