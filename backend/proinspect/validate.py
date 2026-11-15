"""Validation: how close are our measurements to an independent reference? Published, never hidden.

CoStar is treated as a noisy label (its "clear height" convention varies), not as truth. For each measured
quantity we report n, bias, MAE, % within a tolerance, and which of our statistics agrees best with CoStar.
Splits are by property (one scan per property), so a building shared by many PropertyIds counts once.

    python -m proinspect.validate            -> build/validation/interior.json + .md
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from proinspect.config import BUILD
from proinspect.twin.interior import measure


def _stats(pred, ref, tol):
    d = np.array(pred, float) - np.array(ref, float)
    return {"n": int(len(d)), "bias": round(float(d.mean()), 2), "mae": round(float(np.abs(d).mean()), 2),
            "within_tol_pct": round(100 * float((np.abs(d) <= tol).mean()), 1), "tol": tol,
            "p90_abs_err": round(float(np.percentile(np.abs(d), 90)), 2)}


def run_interior(workers=4):
    cat = json.loads((BUILD / "catalog" / "warehouses.json").read_text())
    seen, jobs = set(), []
    for w in cat:
        best = next((s for s in w["scans"] if s["class"] == "warehouse_floor"), None)
        if best and best["model_id"] not in seen:          # one row per physical scan
            seen.add(best["model_id"])
            jobs.append((w, best["model_id"]))
    rows, errors = [], []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(measure, m): (w, m) for w, m in jobs}
        for f in as_completed(futs):
            w, m = futs[f]
            try:
                r = f.result()
            except Exception as e:
                errors.append({"property_id": w["id"], "model_id": m, "error": str(e)[:200]})
                continue
            rows.append({"property_id": w["id"], "model_id": m, "address": w["address"],
                         "costar_clear_ft": w["clear_height_ft"], "rba_sqft": w["rba_sqft"],
                         "deck_ft": r["deck_ft"], "p5_ft": (r["clear"] or {}).get("p5_ft"),
                         "p50_ft": (r["clear"] or {}).get("p50_ft"), "confidence": r["clear_confidence"],
                         "coverage": r["overhead_coverage"], "indoor_sqft": r["floor"]["indoor_sqft"],
                         "office_sqft": r["office_sqft"], "structure": r["columns"]["structure"],
                         "costar_columns": w.get("column_spacing_ft"), "bays": r["columns"]["bays"],
                         "mesh": r["mesh"], "mesh_note": r["mesh_note"]})
    rows.sort(key=lambda x: x["property_id"])

    report = {"scans": len(jobs), "measured": len(rows), "errors": errors, "clear_height": {}, "rows": rows}
    ok = [r for r in rows if r["costar_clear_ft"] and r["deck_ft"] and r["p5_ft"] and r["p50_ft"]]
    for conf in ("all", "high"):
        sub = ok if conf == "all" else [r for r in ok if r["confidence"] == "high"]
        if len(sub) < 3:
            continue
        ref = [r["costar_clear_ft"] for r in sub]
        report["clear_height"][conf] = {
            stat: _stats([r[stat] for r in sub], ref, 2.0) for stat in ("p5_ft", "p50_ft", "deck_ft")}
        # deck minus a constant offset: the offset learned on half the data, tested on the other half
        idx = np.arange(len(sub))
        rng = np.random.default_rng(0)
        rng.shuffle(idx)
        a, b = idx[: len(idx) // 2], idx[len(idx) // 2:]
        off = float(np.median([sub[i]["deck_ft"] - sub[i]["costar_clear_ft"] for i in a]))
        report["clear_height"][conf]["deck_minus_offset"] = {
            "offset_ft": round(off, 1), **_stats([sub[i]["deck_ft"] - off for i in b],
                                                  [sub[i]["costar_clear_ft"] for i in b], 2.0)}
    area = [r for r in rows if r["rba_sqft"]]
    if area:
        ratio = np.array([r["indoor_sqft"] / r["rba_sqft"] for r in area])
        report["scan_area_vs_rba"] = {"n": len(area), "median_ratio": round(float(np.median(ratio)), 2),
                                      "p10": round(float(np.percentile(ratio, 10)), 2),
                                      "p90": round(float(np.percentile(ratio, 90)), 2),
                                      "note": "scans often cover part of the building; ratio < 1 is expected"}
    cols = [r for r in rows if r["costar_columns"] and r["bays"]]
    report["columns_with_costar_reference"] = [
        {"property_id": r["property_id"], "costar": r["costar_columns"],
         "measured": {k: (v or {}).get("spacing_ft") for k, v in r["bays"].items()}} for r in cols]
    out = BUILD / "validation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "interior.json").write_text(json.dumps(report, indent=1, default=float))
    return report


def to_markdown(rep):
    lines = ["# Interior measurement vs CoStar", "",
             f"Scans measured: {rep['measured']} of {rep['scans']} (errors: {len(rep['errors'])}).", "",
             "CoStar is a noisy reference: its clear-height convention varies between buildings.", "",
             "| subset | statistic | n | bias ft | MAE ft | within ±2 ft | P90 abs err |", "|---|---|---|---|---|---|---|"]
    for conf, stats in rep["clear_height"].items():
        for stat, s in stats.items():
            name = stat + (f" (offset {s['offset_ft']} ft, held-out half)" if "offset_ft" in s else "")
            lines.append(f"| {conf} | {name} | {s['n']} | {s['bias']} | {s['mae']} | {s['within_tol_pct']}% | "
                         f"{s['p90_abs_err']} |")
    if "scan_area_vs_rba" in rep:
        a = rep["scan_area_vs_rba"]
        lines += ["", f"Scanned indoor floor / RBA: median {a['median_ratio']} (P10 {a['p10']}, P90 {a['p90']}), "
                  f"n={a['n']}. {a['note']}."]
    lines += ["", f"Column spacing with a CoStar reference: {len(rep['columns_with_costar_reference'])} buildings."]
    for c in rep["columns_with_costar_reference"]:
        lines.append(f"- {c['property_id']}: CoStar {c['costar']} ft, measured {c['measured']}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ exterior
def run_exterior(workers=3, ids=None):
    """Doors, door sizes and truck courts vs CoStar, over every warehouse (or `ids`)."""
    from proinspect.twin.exterior import measure as measure_ext
    cat = json.loads((BUILD / "catalog" / "warehouses.json").read_text())
    todo = [w for w in cat if ids is None or w["id"] in ids]
    rows, errors = [], []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(measure_ext, w["id"]): w for w in todo}
        for i, f in enumerate(as_completed(futs), 1):
            w = futs[f]
            try:
                r = f.result()
            except Exception as e:
                errors.append({"property_id": w["id"], "error": f"{type(e).__name__}: {str(e)[:160]}"})
                continue
            items = r["doors"]["items"]
            di = [d for d in items if d["class"] == "drive_in"]
            rows.append({"property_id": w["id"], "market": w["market"],
                         "costar_docks": w["docks_external"], "costar_has_docks": w["has_docks"],
                         "costar_drive_ins": w["drive_ins"], "costar_drive_in_wh": [w["drive_in_w_ft"],
                                                                                   w["drive_in_h_ft"]],
                         "costar_court_ft": w["truck_court_ft"],
                         "doors": r["doors"]["total"], "dock_high": r["doors"]["dock_high"],
                         "drive_in": r["doors"]["drive_in"], "uncertain": r["doors"]["uncertain"],
                         "drive_in_wh_median": [float(np.median([d["width_ft"] for d in di])),
                                                float(np.median([d["height_ft"] for d in di]))] if di else None,
                         "court_ft": r["courts"][0]["depth_ft"] if r["courts"] else None,
                         "court_conf": r["courts"][0]["confidence"] if r["courts"] else None,
                         "obliques": len(r["imagery"]["obliques"]), "roof": r["building"]["roof_condition"]})
            if i % 10 == 0:
                print(f"  exterior {i}/{len(todo)}", file=sys.stderr)
    rows.sort(key=lambda x: x["property_id"])

    def corr(a, b):
        return round(float(np.corrcoef(a, b)[0, 1]), 2) if len(a) > 2 and np.std(a) and np.std(b) else None
    rep = {"measured": len(rows), "errors": errors, "rows": rows}
    d = [r for r in rows if r["costar_docks"] is not None]
    if d:
        a, b = [r["dock_high"] + r["uncertain"] for r in d], [r["costar_docks"] for r in d]
        rep["dock_count"] = {**_stats(a, b, 2), "corr": corr(a, b),
                             "detected_any_when_costar_has_docks_pct":
                                 round(100 * np.mean([r["doors"] > 0 for r in d if r["costar_docks"] > 0]), 1)}
    g = [r for r in rows if r["costar_drive_ins"] is not None]
    if g:
        a, b = [r["drive_in"] for r in g], [r["costar_drive_ins"] for r in g]
        rep["drive_in_count"] = {**_stats(a, b, 1), "corr": corr(a, b)}
    s = [r for r in rows if r["drive_in_wh_median"] and r["costar_drive_in_wh"][0] and r["costar_drive_in_wh"][1]]
    if s:
        rep["drive_in_size"] = {
            "width": _stats([r["drive_in_wh_median"][0] for r in s], [r["costar_drive_in_wh"][0] for r in s], 2),
            "height": _stats([r["drive_in_wh_median"][1] for r in s], [r["costar_drive_in_wh"][1] for r in s], 2)}
    c = [r for r in rows if r["costar_court_ft"] and r["court_ft"]]
    rep["truck_court"] = {**(_stats([r["court_ft"] for r in c], [r["costar_court_ft"] for r in c], 10) if c else
                             {"n": 0}),
                          "pairs": [(r["property_id"], r["costar_court_ft"], r["court_ft"], r["court_conf"]) for r in c]}
    rep["no_doors_found"] = sum(r["doors"] == 0 for r in rows)
    out = BUILD / "validation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "exterior.json").write_text(json.dumps(rep, indent=1, default=float))
    return rep


if __name__ == "__main__" and "exterior" in sys.argv:
    rep = run_exterior()
    print(json.dumps({k: v for k, v in rep.items() if k != "rows"}, indent=1, default=float))
elif __name__ == "__main__":
    rep = run_interior()
    md = to_markdown(rep)
    (BUILD / "validation" / "interior.md").write_text(md)
    print(md)
    if rep["errors"]:
        print("errors:", rep["errors"], file=sys.stderr)
