"""Run the interior twin on the hero scans, print vs CoStar, and render floor maps to build/spike/."""
import json
import sys
import time

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from proinspect.config import BUILD  # noqa: E402
from proinspect.twin.interior import measure  # noqa: E402

HEROES = [(237474, "BNGoeNCMHtk"), (238885, "suXvWyjFWvZ"), (278605, "3mN1d34pZnh")]
cat = {w["id"]: w for w in json.load(open(BUILD / "catalog/warehouses.json"))}
for pid, m in HEROES:
    t = time.time()
    r = measure(m)
    w = cat[pid]
    print(f"\n{pid} {m} ({time.time()-t:.0f}s) mesh={r['mesh']} note={r['mesh_note']}")
    print(f"  CoStar clear={w['clear_height_ft']} RBA={w['rba_sqft']}  | deck={r['deck_ft']} {r['deck_range_ft']} "
          f"clear={r['clear']} conf={r['clear_confidence']} coverage={r['overhead_coverage']} office_sf={r['office_sqft']}")
    print("  floor:", {k: r["floor"][k] for k in ("tilt_deg", "rms_cm", "indoor_sqft", "free_sqft", "pct_free",
                                                 "with_sky_sqft")})
    print("  columns:", {k: r["columns"][k] for k in ("interior", "pilasters", "structure", "bays")})
    g = np.load(BUILD / r["grid"]["file"])
    img = np.ones(g["floor"].shape + (3,))
    img[g["floor"]] = [.75, .75, .75]
    img[g["free"]] = [.25, .65, .35]
    img[g["blocked"]] = [.85, .25, .2]
    plt.figure(figsize=(8, 8))
    plt.imshow(img.transpose(1, 0, 2), origin="lower")
    if r["columns"]["positions"]:
        c, o = np.array(r["columns"]["positions"]), g["origin"]
        plt.scatter((c[:, 0] - o[0]) / 0.25, (c[:, 1] - o[1]) / 0.25, s=20, c="blue", marker="s")
    plt.title(f"{pid}: free (green), blocked (red), columns (blue)")
    plt.savefig(BUILD / f"spike/interior_{pid}.png", dpi=80)
    plt.close()
