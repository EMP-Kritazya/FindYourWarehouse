"""Run the exterior twin on hero properties and draw the evidence on the ortho (build/spike/exterior_<pid>.png)."""
import json
import sys
import time

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

from proinspect.config import BUILD  # noqa: E402
from proinspect.twin.exterior import measure  # noqa: E402

STOP_COLOR = {"landscape": "lime", "obstacle": "red", "building": "magenta", "max_80m": "white",
              "grid_edge": "gray"}
DOOR_COLOR = {"dock_high": "cyan", "drive_in": "orange", "uncertain": "yellow"}
cat = {w["id"]: w for w in json.load(open(BUILD / "catalog/warehouses.json"))}
for pid in [int(a) for a in sys.argv[1:]] or [238885, 237474, 278605]:
    t = time.time()
    r = measure(pid)
    w = cat[pid]
    print(f"\n{pid} {w['address']} ({time.time()-t:.0f}s)  CoStar docks={w['docks_external']} "
          f"drive-ins={w['drive_ins']} court={w['truck_court_ft']}")
    print("  imagery:", r["imagery"])
    print("  building:", {k: v for k, v in r["building"].items() if k != "footprint_local"})
    print("  doors:", {k: v for k, v in r["doors"].items() if k not in ("items", "note")})
    for d in r["doors"]["items"]:
        print(f"    bldg{d['building']} edge{d['edge']} {d['facing']} s={d['s_m']} {d['width_ft']}x{d['height_ft']}ft {d['class']} "
              f"seen={d['seen']} {d['views']} conf={d['confidence']} {d['cues']}")
    for c in r["courts"]:
        print("  court:", {k: v for k, v in c.items() if k != "ray_detail"})
    print("  vehicles:", r["vehicles"], " site:", r["site"])
    f = r["frame"]
    img = np.asarray(Image.open(BUILD / f"twins/exterior/{pid}_ortho.jpg"))
    h = f["half_m"]
    plt.figure(figsize=(10, 10))
    plt.imshow(img, extent=[-h, h, -h, h])
    for b in r["buildings"]:
        fp = np.array(b["footprint_local"])
        plt.plot(fp[:, 0], fp[:, 1], color="yellow", lw=1.5)
    for c in r["courts"]:
        for ray in c["ray_detail"]:
            (x0, y0), (x1, y1) = ray["start"], ray["end"]
            plt.plot([x0, x1], [y0, y1], color=STOP_COLOR.get(ray["stop"], "white"), lw=1)
            plt.scatter([x1], [y1], s=8, c=STOP_COLOR.get(ray["stop"], "white"))
    for d in r["doors"]["items"]:
        plt.scatter(d["x"], d["y"], s=60, c=DOOR_COLOR[d["class"]], edgecolors="black", zorder=5)
    plt.title(f"{pid}: doors (cyan dock-high, orange drive-in, yellow uncertain); rays by stop reason")
    plt.savefig(BUILD / f"spike/exterior_{pid}.png", dpi=90)
    plt.close()
