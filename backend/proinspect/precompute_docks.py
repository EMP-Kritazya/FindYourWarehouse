"""Build the v3 dock store for every warehouse (resumable; failures are skipped with a reason).

    cd backend && ../.venv/bin/python -m proinspect.precompute_docks [--force] [--refresh-twins] [--ids 1,2]
                                                                      [--cache-only] [--workers 3]

--refresh-twins   re-run twin.exterior.measure (from the Vexcel disk cache where possible) so the twin's oblique doors
                  carry the GSD fix; a twin that is missing is always measured (paid calls).
--cache-only      no paid Vexcel calls at all (facade crops not in the cache are skipped and noted).
Stores the user already edited keep their confirmed / rejected / user_added doors (docks.build keep_user).
Writes build/docks/_precompute.json: {pid: {"ok": bool, "why"?, "doors"?, "estimate"?}}.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from proinspect import cache, docks
from proinspect.config import BUILD



def one(pid, force=False, refresh_twins=False, cache_only=False):
    p = docks._path(pid)
    if p.exists() and not force:
        try:
            s = json.loads(p.read_text())
            if s.get("build_tag") == docks.BUILD_TAG:
                return {"ok": True, "skipped": "up to date", "doors": len(s["doors"]),
                        "estimate": s["summary"]["estimate"]["status"]}
        except json.JSONDecodeError:
            pass
    twin = BUILD / "twins" / "exterior" / f"{pid}.json"
    if refresh_twins or not twin.exists():
        from proinspect.twin.exterior import measure
        try:
            if twin.exists() or cache_only:
                with cache.cache_only():
                    measure(pid)
            else:
                measure(pid)
        except cache.CacheMiss:
            if not twin.exists():
                return {"ok": False, "why": "no exterior twin and the Vexcel calls are not cached"}
            measure(pid)                       # cache incomplete for this twin: one paid refresh
        except Exception as e:                 # noqa: BLE001
            if not twin.exists():
                return {"ok": False, "why": f"exterior twin failed: {type(e).__name__}: {str(e)[:160]}"}
    s = docks.build(pid, facades_cache_only=cache_only)
    return {"ok": True, "doors": len(s["doors"]), "estimate": s["summary"]["estimate"]["status"],
            "notes": s.get("notes")}


def _metrics(pairs):
    import numpy as np
    if not pairs:
        return {"n": 0}
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    corr = float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() and b.std() else None
    rec = np.minimum(a, b) / np.maximum(b, 1)
    return {"n": len(pairs), "mae": round(float(np.mean(np.abs(a - b))), 1),
            "median_abs_err": round(float(np.median(np.abs(a - b))), 1),
            "corr": None if corr is None else round(corr, 2),
            "median_recall": round(float(np.median(rec)), 2),
            "sites_over_costar": int((a > b).sum()), "sites_zero": int((a == 0).sum())}


def score(before_dir=None, vision=None):
    """Dock counts vs CoStar's external dock count on the sites that have one -> build/validation/docks.md."""
    cat = {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}
    ext = BUILD / "twins" / "exterior"
    rows = []
    for pid, w in sorted(cat.items()):
        n = w.get("docks_external")
        if n is None or not docks._path(pid).exists():
            continue
        st = json.loads(docks._path(pid).read_text())
        live = [d for d in st["doors"] if d["status"] != "rejected"]
        ev = lambda d, t: any(v["type"] == t for v in d["evidence"])          # noqa: E731
        row = {"id": pid, "costar": n,
               "store_detected": sum(d["kind"] in ("dock_high", "unknown") and d["status"] != "inferred" for d in live),
               "store_dock_high": sum(d["kind"] == "dock_high" and d["status"] != "inferred" for d in live),
               "store_with_inferred": sum(d["kind"] in ("dock_high", "unknown") for d in live),
               "trailer": sum(ev(d, "trailer") for d in live),
               "facade_crop": sum(any(v["type"] == "oblique" and str(v.get("ref", "")).startswith("facade-crop")
                                      for v in d["evidence"]) for d in live),
               "drive_in": sum(d["kind"] == "drive_in" for d in live)}
        main = {b["id"] for b in st["buildings"] if b.get("main")}
        row["store_main"] = sum(d["kind"] in ("dock_high", "unknown") and d["status"] != "inferred"
                                and d["building"] in main for d in live)
        row["buildings"] = len(st["buildings"])
        cur = json.loads((ext / f"{pid}.json").read_text()) if (ext / f"{pid}.json").exists() else None
        if cur:
            row["twin_after"] = cur["doors"]["dock_high"] + cur["doors"]["uncertain"]
            row["twin_after_dock_high"] = cur["doors"]["dock_high"]
        if before_dir:
            from pathlib import Path
            bp = Path(before_dir) / f"{pid}.json"
            if bp.exists():
                old = json.loads(bp.read_text())
                row["twin_before"] = old["doors"]["dock_high"] + old["doors"]["uncertain"]
                row["twin_before_dock_high"] = old["doors"]["dock_high"]
        if vision and str(pid) in vision:
            v = vision[str(pid)]
            if "error" not in v:
                row["vision"] = sum(x.get("dock_high_doors", 0) for x in v.values() if isinstance(x, dict)
                                    and x.get("facade_visible"))
        rows.append(row)
    def m(key):
        return _metrics([(r[key], r["costar"]) for r in rows if r.get(key) is not None])
    def dh(key):
        rs = [r for r in rows if r.get(key) is not None]
        return round(100 * sum(r[key] > 0 for r in rs) / max(len(rs), 1))
    out = {"rows": rows, "before": m("twin_before"), "twin_after": m("twin_after"), "store": m("store_detected"), "store_main": m("store_main"),
           "store_with_inferred": m("store_with_inferred"),
           "dock_high_share": {"before": dh("twin_before_dock_high"), "twin_after": dh("twin_after_dock_high"),
                               "store": dh("store_dock_high")}}
    vs = [r for r in rows if r.get("vision") is not None]
    if vs:
        out["vision"] = _metrics([(r["vision"], r["costar"]) for r in vs])
        out["vision_same_sites_store"] = _metrics([(r["store_detected"], r["costar"]) for r in vs])
        out["vision_same_sites_before"] = _metrics([(r["twin_before"], r["costar"]) for r in vs
                                                    if r.get("twin_before") is not None])
    (BUILD / "validation").mkdir(parents=True, exist_ok=True)
    (BUILD / "validation" / "docks.json").write_text(json.dumps(out, indent=1))
    return out


def to_markdown(out, extra=""):
    def line(name, m):
        if not m or not m.get("n"):
            return f"| {name} | 0 | | | | | | |"
        return (f"| {name} | {m['n']} | {m['mae']} | {m['median_abs_err']} | {m['corr']} | {m['median_recall']} | "
                f"{m['sites_zero']} | {m['sites_over_costar']} |")
    L = ["# Dock detection vs CoStar (v3 dock store)", "",
         "Ground truth: CoStar `numExternalLoadingDocks` on the warehouses that have it. CoStar counts every dock on the "
         "property; the imagery counts what is visible, so detections are a LOWER BOUND by design. "
         "Regenerate: `cd backend && ../.venv/bin/python -m proinspect.precompute_docks --score`.", "",
         "| method | n | MAE | median abs err | corr | median recall | sites with 0 | sites over CoStar |",
         "|---|---|---|---|---|---|---|---|",
         line("before: v2 twin (site-wide oblique, native GSD on downsampled px)", out.get("before")),
         line("v2 twin re-measured with the GSD fix + seal-aware classes", out.get("twin_after")),
         line("v3 store: facade crops + v2 twin + docked trailers (no CoStar input)", out.get("store")),
         line("v3 store, main building only (CoStar's count is per property; parks share parcels)",
              out.get("store_main")),
         line("v3 store incl. span fill (USES CoStar N: circular, not a skill score)",
              out.get("store_with_inferred")), ""]
    d = out["dock_high_share"]
    L += [f"Sites with >= 1 door classed dock-high: before {d['before']}%, re-measured twin {d['twin_after']}%, "
          f"v3 store {d['store']}%.", ""]
    if out.get("vision"):
        L += ["## Vision POC (Claude claude-opus-5-5 on the 4 obliques, camera-facing facade counts)", "",
              "| method (same sites) | n | MAE | median abs err | corr | median recall | sites with 0 | over CoStar |",
              "|---|---|---|---|---|---|---|---|",
              line("before (v2 twin)", out.get("vision_same_sites_before")),
              line("v3 store (SAM3 facade crops + trailers)", out.get("vision_same_sites_store")),
              line("Claude vision, dock-high count", out.get("vision")), ""]
    if extra:
        L += [extra, ""]
    L += ["## Per site", "",
          "| id | CoStar | before | twin re-measured | store detected (dock-high) | +inferred | trailers | facade-crop doors | drive-in | vision |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["rows"]:
        L.append(f"| {r['id']} | {r['costar']} | {r.get('twin_before', '')} | {r.get('twin_after', '')} | "
                 f"{r['store_detected']} ({r['store_dock_high']}) | {r['store_with_inferred']} | {r['trailer']} | "
                 f"{r['facade_crop']} | {r['drive_in']} | {r.get('vision', '')} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh-twins", action="store_true")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--score", action="store_true", help="only score the stores vs CoStar -> build/validation")
    ap.add_argument("--before", help="dir of pre-fix exterior twin JSONs, for the 'before' column")
    ap.add_argument("--vision", help="vision POC results JSON")
    ap.add_argument("--notes", help="markdown file appended to docks.md")
    a = ap.parse_args(argv)
    if a.score:
        from pathlib import Path
        vis = json.loads(Path(a.vision).read_text()) if a.vision else None
        out = score(a.before, vis)
        extra = Path(a.notes).read_text() if a.notes else ""
        (BUILD / "validation" / "docks.md").write_text(to_markdown(out, extra))
        print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
        return out
    cat = json.loads((BUILD / "catalog" / "warehouses.json").read_text())
    ids = [int(x) for x in a.ids.split(",")] if a.ids else [w["id"] for w in cat]
    ids = sorted(ids, key=lambda i: (i != 12896757, i))          # the demo hero first
    log_p = docks.OUT / "_precompute.json"
    docks.OUT.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_p.read_text()) if log_p.exists() else {}
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        futs = {pool.submit(one, pid, a.force, a.refresh_twins, a.cache_only): pid for pid in ids}
        for i, f in enumerate(as_completed(futs), 1):
            pid = futs[f]
            try:
                r = f.result()
            except Exception as e:             # noqa: BLE001
                r = {"ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}
            log[str(pid)] = r
            log_p.write_text(json.dumps(log, indent=1, default=float))
            print(f"[{i}/{len(ids)} {time.time() - t0:.0f}s] {pid}: "
                  + (f"{r.get('doors')} doors, estimate {r.get('estimate')}" if r["ok"] else f"FAILED {r['why']}"),
                  flush=True)
    bad = {k: v for k, v in log.items() if not v.get("ok")}
    print(f"done: {len(log) - len(bad)} ok, {len(bad)} failed", file=sys.stderr)
    return log


if __name__ == "__main__":
    main()
