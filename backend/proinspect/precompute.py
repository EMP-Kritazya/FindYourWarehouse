"""Precompute the demo: exterior twins for the hero properties (if missing) and the hero simulation runs.

    cd backend && ../.venv/bin/python -m proinspect.precompute [--runs 10] [--force-exterior]

Writes build/sim/<run_id>/{summary,scene}.json for each hero and build/sim/heroes.json (served by
GET /api/sim/heroes). Each hero carries its sanity checks; `demo_worthy` is False when any check fails, and the
headline says so instead of hiding it.
"""
import argparse
import json

from proinspect.config import BUILD

HEROES = [
    (12896757, "QHMtBN5Nnrc", "pallet_3pl"),
    (12896757, "QHMtBN5Nnrc", "food_beverage"),
    (238885, "suXvWyjFWvZ", "light_manufacturing"),
    (237474, "BNGoeNCMHtk", "pallet_3pl"),
]


def _p50(kpi, k):
    b = kpi.get(k)
    return b["p50"] if b else None


def headline(rec):
    k = rec["summary"]["kpi"]
    f = rec["fusion"]
    doors = {"vexcel": "doors from Vexcel obliques", "court_side_assumed_positions":
             "dock wall from the Vexcel truck court, door positions assumed"}.get(f.get("doors_source"),
                                                                                    "door positions ASSUMED")
    parts = [f"{_p50(k, 'trucks_done'):.0f}/{_p50(k, 'trucks_planned'):.0f} trucks",
             f"{_p50(k, 'pallets_received'):.0f} pallets in, {_p50(k, 'pallets_shipped'):.0f} out"]
    if _p50(k, "last_truck_out_h") is not None:
        parts.append(f"last truck out {_p50(k, 'last_truck_out_h'):.1f} h")
    if _p50(k, "unload_53ft_min") is not None:
        parts.append(f"53' unload {_p50(k, 'unload_53ft_min'):.0f} min")
    parts.append(f"congestion {100 * (_p50(k, 'congestion_delay_share') or 0):.0f}% of travel")
    s = f"P50 of {rec['runs']} runs: " + ", ".join(parts) + f" ({doors})"
    bad = [c for c, ok in rec["sanity"].items() if c != "ok" and not ok]
    return s + (f". NOT demo-ready: {', '.join(bad)}" if bad else "")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--force-exterior", action="store_true")
    a = ap.parse_args(argv)
    from proinspect.api_sim import run
    for pid in sorted({h[0] for h in HEROES}):
        p = BUILD / "twins" / "exterior" / f"{pid}.json"
        if a.force_exterior or not p.exists():
            from proinspect.twin.exterior import measure
            print(f"exterior twin {pid} ...", flush=True)
            measure(pid)
    out = []
    for pid, model, profile in HEROES:
        rec = run(pid, profile, runs=a.runs, model_id=model)
        h = {"property_id": pid, "model_id": model, "profile": profile, "run_id": rec["run_id"],
             "scene_url": rec["scene_url"], "headline": headline(rec), "demo_worthy": rec["sanity"]["ok"],
             "sanity": rec["sanity"], "doors_source": rec["fusion"].get("doors_source"),
             "registration_confidence": (rec["fusion"].get("registration") or {}).get("confidence"),
             "layout": {k: rec["summary"]["layout"].get(k) for k in ("positions", "sqft_per_position", "density_ok",
                                                                      "doors", "staging_lanes", "levels",
                                                                      "bays_dropped_unreachable")}}
        print(json.dumps(h), flush=True)
        out.append(h)
    (BUILD / "sim").mkdir(parents=True, exist_ok=True)
    (BUILD / "sim" / "heroes.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {BUILD / 'sim' / 'heroes.json'}")


if __name__ == "__main__":
    main()
