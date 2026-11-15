"""Phase 0: which warehouse scans actually cover the warehouse floor?

For every Matterport model on a warehouse-type property: rooms + mesh (50k), then
  floor_sqft   area of upward-facing mesh triangles within 0.5 m of the floor level
  span_ft      the scan's horizontal extent (x, y)
  height_ft    95th-percentile height of downward-facing (ceiling) surfaces above the floor
Writes build/spike/scan_coverage.json.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from proinspect.config import BUILD
from proinspect.tools.matterport import mesh_obj, model_overview, normals, parse_obj
from proinspect.warehouses import dec, scans_by_property, warehouses

M2_TO_SQFT, M_TO_FT = 10.7639, 3.28084


def measure(model_id):
    ov = model_overview(model_id)
    V, F = parse_obj(mesh_obj(model_id))
    n, area = normals(V, F)
    cz = V[F].mean(axis=1)[:, 2]
    up, down = n[:, 2] > 0.9, n[:, 2] < -0.9
    hist, edges = np.histogram(cz[up], bins=200, weights=area[up])
    floor = float(edges[np.argmax(hist)] + (edges[1] - edges[0]) / 2) if up.any() else float(np.percentile(V[:, 2], 2))
    near = up & (np.abs(cz - floor) < 0.5)
    ceil = cz[down] - floor
    ceil = ceil[ceil > 2.0]
    return {"model_id": model_id, "name": ov["name"], "created": ov["created"], "panos": ov["pano_count"],
            "floors": len(ov["floors"]),
            "rooms": [(r["label"], r["sqft"], r["ceiling_ft"]) for r in ov["rooms"]],
            "floor_sqft": round(float(area[near].sum()) * M2_TO_SQFT),
            "span_ft": [round(float(np.ptp(V[:, i])) * M_TO_FT) for i in (0, 1)],
            "height_ft": round(float(np.percentile(ceil, 95)) * M_TO_FT, 1) if len(ceil) else None,
            "max_room_ceiling_ft": max([r["ceiling_ft"] or 0 for r in ov["rooms"]] or [0]) or None}


def main():
    W, S = warehouses(), scans_by_property()
    jobs = [(pid, m) for pid in W for m in S.get(pid, [])]
    print(f"{len(jobs)} scans on {len(W)} warehouses", file=sys.stderr)
    out = []
    with ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(measure, m): (pid, m) for pid, m in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            pid, m = futs[f]
            r = W[pid]; st = (r.get("propertyIndustrial") or r.get("propertyFlex") or {}).get("structural") or {}
            row = {"property_id": pid, "building": r.get("buildingName"),
                   "address": " ".join(str(r["primaryAddress"].get(k, "")) for k in ("streetNumber", "streetName", "streetType")),
                   "market": r["primaryAddress"].get("researchMarketId"),
                   "subtype": dec("PropertySubtype", r.get("propertySubtype")),
                   "rba": r.get("rentableBuildingAreaSquareFeet"), "costar_clear_ft": st.get("ceilingHeightFeet")}
            try:
                row.update(measure(m)); row["ok"] = True
            except Exception as e:
                row.update({"model_id": m, "ok": False, "error": str(e)[:200]})
            out.append(row)
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    (BUILD / "spike").mkdir(parents=True, exist_ok=True)
    (BUILD / "spike" / "scan_coverage.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {len(out)} rows; ok={sum(r['ok'] for r in out)}", file=sys.stderr)


if __name__ == "__main__":
    main()
