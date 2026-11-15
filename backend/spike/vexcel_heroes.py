"""Phase 0: what does Vexcel tell us about the hero warehouses?"""
import collections as C
import json
import sys

from proinspect.config import BUILD
from proinspect.tools import vexcel as vx
from proinspect.warehouses import warehouses

HEROES = [237474, 238885, 278605, 358742, 238029, 12896757]
LABELS = ["loading dock door", "semi trailer", "truck", "car", "shipping container", "forklift"]
out = {}
for pid in (int(a) for a in sys.argv[1:]) if len(sys.argv) > 1 else HEROES:
    r = warehouses()[pid]; a = r["primaryAddress"]; lat, lon = a["latitude"], a["longitude"]
    row = {"pid": pid, "name": r.get("buildingName"), "lat": lat, "lon": lon}
    ext = vx.property_extract(lat, lon)
    if not ext:
        row["parcel"] = None; wkt = vx.square_around(lat, lon, 80)
    else:
        p = ext["features"][0]["properties"]; wkt = vx.to_wkt(ext["features"][0]["geometry"])
        sts = p.get("structures", [])
        row["parcel"] = {"collection": p.get("metadata.collection"), "date": p.get("metadata.ortho-date-estimate"),
                         "structures": len(sts), "footprints_sqft": [round(s.get("footprint.area") or 0) for s in sts][:6],
                         "parcel_keys": sorted(k for k in p if k != "structures")[:80],
                         "structure_keys": sorted(sts[0].keys())[:120] if sts else []}
    row["obliques"] = vx.oblique_directions(wkt)
    det = {}
    for view in ["ortho"] + row["obliques"][:1]:
        res = vx.detect_raw(wkt, LABELS, view)
        cnt = C.Counter()
        for f in res.get("features", []):
            cnt[f["properties"]["label"]] += len(f["properties"]["detections"])
        det[view] = dict(cnt)
    row["detect"] = det
    for prod in ("dsm", "dtm"):
        cs = vx.collections(prod, vx.square_around(lat, lon, 30))
        row[prod] = cs if isinstance(cs, dict) else [(c["collection"], c["layer"], c["last"]) for c in cs[:6]]
    cs = vx.collections("ortho", vx.square_around(lat, lon, 30))
    row["ortho_history"] = cs if isinstance(cs, dict) else [c["last"] for c in cs]
    out[pid] = row
    print(json.dumps({k: v for k, v in row.items() if k != "parcel"}, default=str))
    if row["parcel"]:
        print("   parcel:", {k: v for k, v in row["parcel"].items() if not k.endswith("keys")})
(BUILD / "spike").mkdir(parents=True, exist_ok=True)
(BUILD / "spike" / "vexcel_heroes.json").write_text(json.dumps(out, indent=1, default=str))
