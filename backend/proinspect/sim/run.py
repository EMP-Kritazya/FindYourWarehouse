"""Replications: run a shift R times with independent seeds and report every KPI as P10 / P50 / P90.

    simulate(site, profile_key, runs=10, seed=0, volume_scale=1.0) -> summary dict
Determinism: the same seed gives the same event log (log_hash); the base seed is reported with the result.
"""
import time

import numpy as np

from proinspect.library import profiles
from proinspect.sim.layout import generate
from proinspect.sim.shift import Shift


def simulate(site, profile_key, runs=10, seed=0, volume_scale=1.0, fleet=None, keep_trace=True):
    prof = profiles()[profile_key]
    lay = generate(site, fleet or prof["fleet"])
    seeds = np.random.SeedSequence(seed).generate_state(runs)
    reps, t0 = [], time.time()
    for i, s in enumerate(seeds):
        sh = Shift(lay, profile_key, seed=int(s), fleet=fleet)
        r = sh.run(volume_scale=volume_scale)
        if keep_trace and i == 0:
            r["trace"] = {"nodes": sh.g.xy.round(2).tolist(),
                          "vehicles": [{"id": v.id, "kind": v.kind, "keys": v.trace,
                                        "hop_s": [round(v.hop_empty, 3), round(v.hop_loaded, 3)]}
                                       for v in sh.vehicles],
                          # congestion per lane-graph node (sim frame): seconds waited to enter it, reroutes
                          "node_wait_s": {int(n): round(w, 2) for n, w in sh.traffic.wait_s.items()},
                          "node_reroutes": {int(n): c for n, c in sh.traffic.reroute_at.items()},
                          "door_of_lane": list(sh.lane_door),
                          "bays": [[round(b[k], 3) for k in ("x0", "x1", "y0", "y1")] for b in lay.bays],
                          "lanes": [dict(ln) for ln in lay.lanes], "levels": lay.levels}
        reps.append(r)
    keys = reps[0]["kpi"].keys()
    band = {}
    for k in keys:
        vals = [r["kpi"][k] for r in reps if r["kpi"][k] is not None]
        band[k] = None if not vals else {"p10": float(np.percentile(vals, 10)), "p50": float(np.percentile(vals, 50)),
                                         "p90": float(np.percentile(vals, 90)), "n": len(vals)}
    return {"profile": profile_key, "site": site.name, "runs": runs, "base_seed": seed,
            "volume_scale": volume_scale, "seconds": round(time.time() - t0, 1), "kpi": band,
            "layout": reps[0]["layout"], "fleet": reps[0]["vehicles"], "assumptions": reps[0]["assumptions"],
            "example": reps[0], "log_hashes": [r["log_hash"] for r in reps]}
