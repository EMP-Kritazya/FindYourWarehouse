import sys
import time

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from proinspect.config import BUILD  # noqa: E402
from proinspect.library import profiles  # noqa: E402
from proinspect.sim import layout as L  # noqa: E402
from proinspect.sim.site import from_interior, synthetic  # noqa: E402


def draw(lay, name):
    s = lay.site
    img = np.ones(s.floor.shape + (3,))
    img[s.floor] = [.85, .85, .85]
    img[s.floor & ~s.free] = [.3, .3, .3]
    img[lay.rack] = [.2, .4, .8]
    plt.figure(figsize=(7, 9))
    plt.imshow(img.transpose(1, 0, 2), origin="lower", extent=[0, s.nx * .25, 0, s.ny * .25])
    for ln in lay.lanes:
        plt.gca().add_patch(plt.Rectangle((ln["x0"], ln["y0"]), ln["x1"] - ln["x0"], ln["y1"] - ln["y0"],
                                          fc="orange", alpha=.6))
    for d in s.doors:
        plt.scatter(d.x, s.wall_y, c="red", marker="s", s=40)
    plt.title(f"{name}: {len(lay.bays)} bays x {lay.levels} lvls = {lay.positions} pos, aisle {lay.params['aisle_ft']} ft")
    plt.savefig(BUILD / f"spike/layout_{name}.png", dpi=70)
    plt.close()


p = profiles()["pallet_3pl"]
t = time.time()
lay = L.generate(synthetic(60, 90, doors=6, columns=(15.24, 15.85)), p["fleet"])
print("synthetic:", lay.checks, lay.params["putaway_vehicle"], f"{time.time()-t:.1f}s")
draw(lay, "synthetic")
for m in sys.argv[1:] or ["suXvWyjFWvZ", "BNGoeNCMHtk"]:
    t = time.time()
    s = from_interior(m)
    lay = L.generate(s, p["fleet"])
    print(m, round(s.area_sqft), "sqft", lay.checks, s.assumptions, f"{time.time()-t:.1f}s")
    draw(lay, m)
