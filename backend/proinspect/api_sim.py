"""Simulation endpoints (Stream B).

    POST /api/sim/run {property_id, profile, runs=10, volume_scale=1.0, model_id?} -> {run_id, summary, fusion, ...}
    GET  /api/sim/{run_id}/scene       Matterport scene (docs/CONTRACTS.md) for the replay
    GET  /api/sim/heroes               precomputed demo runs (build/sim/heroes.json, written by proinspect.precompute)

Runs are cached on disk under build/sim/<run_id>/ keyed by (property, model, profile, runs, scale, code version);
the code version hashes the simulator + fusion sources, so a changed method never serves a stale result.
"""
import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proinspect.config import BUILD

router = APIRouter()
SIM_DIR = BUILD / "sim"
_PKG = Path(__file__).resolve().parent
_SOURCES = sorted([*(_PKG / "sim").glob("*.py"), _PKG / "twin" / "fuse.py", _PKG / "library" / "equipment.yaml",
                   _PKG / "library" / "profiles.yaml"])
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def code_version():
    h = hashlib.sha256()
    for p in _SOURCES:
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def run_key(property_id, model_id, profile, runs, volume_scale):
    blob = json.dumps([int(property_id), model_id, profile, int(runs), round(float(volume_scale), 3), code_version()])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _lock(key):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def pick_model(property_id, model_id=None):
    from proinspect.api_twin import best_scan, catalog_record
    w = catalog_record(property_id)
    if w is None:
        raise HTTPException(404, f"warehouse {property_id} not in the catalog")
    if model_id:
        if not any(s["model_id"] == model_id for s in w.get("scans") or []):
            raise HTTPException(400, f"scan {model_id} does not belong to property {property_id}")
        if not (BUILD / "twins" / "interior" / f"{model_id}.npz").exists():
            raise HTTPException(409, f"scan {model_id} has no interior twin yet (run twin.interior.measure)")
        return model_id
    s = best_scan(w)
    if not s:
        raise HTTPException(409, f"property {property_id} has no measured warehouse-floor scan to simulate in")
    return s["model_id"]


def run(property_id, profile, runs=10, volume_scale=1.0, model_id=None):
    """Simulate (or load from disk) and write summary.json + scene.json. Returns the stored record."""
    from proinspect.library import profiles
    if profile not in profiles():
        raise HTTPException(400, f"unknown profile '{profile}' (have: {sorted(profiles())})")
    if not 1 <= int(runs) <= 50:
        raise HTTPException(400, "runs must be 1-50")
    if not 0.1 <= float(volume_scale) <= 5:
        raise HTTPException(400, "volume_scale must be 0.1-5")
    model_id = pick_model(property_id, model_id)
    rid = run_key(property_id, model_id, profile, runs, volume_scale)
    d = SIM_DIR / rid
    with _lock(rid):
        if (d / "summary.json").exists() and (d / "scene.json").exists():
            rec = json.loads((d / "summary.json").read_text())
            rec["cached"] = True
            return rec
        from proinspect.sim.export import scene
        from proinspect.sim.run import simulate
        from proinspect.twin.fuse import fused_site
        site, fusion = fused_site(property_id, model_id)
        summary = simulate(site, profile, runs=int(runs), volume_scale=float(volume_scale))
        interior = json.loads((BUILD / "twins" / "interior" / f"{model_id}.json").read_text())
        floor_z = float((interior.get("floor") or {}).get("z0") or 0.0)
        sc = scene(site, summary, run_id=rid, property_id=property_id, model_id=model_id, floor_z=floor_z)
        fusion = {k: v for k, v in fusion.items() if k != "registration"} | {
            "registration": {k: v for k, v in (fusion.get("registration") or {}).items() if k != "all"} or None}
        rec = {"run_id": rid, "property_id": property_id, "model_id": model_id, "profile": profile,
               "runs": int(runs), "volume_scale": float(volume_scale), "code_version": code_version(),
               "scene_url": f"/api/sim/{rid}/scene", "fusion": fusion, "sanity": sanity(summary),
               "summary": {k: v for k, v in summary.items() if k not in ("example", "trace")}}
        d.mkdir(parents=True, exist_ok=True)
        (d / "scene.json").write_text(json.dumps(sc, separators=(",", ":"), default=float))
        (d / "summary.json").write_text(json.dumps(rec, default=float))
        rec["cached"] = False
        return rec


def sanity(summary):
    """Is this run demo-worthy? Every truck done, layout density plausible, nothing unreachable. Checks use the P10
    band, so 'all_trucks_done' means in at least 90% of the replications."""
    k = summary["kpi"]

    def p(name, q="p10"):
        return (k.get(name) or {}).get(q)
    checks = {
        "all_trucks_done": p("trucks_done", "p10") is not None and p("trucks_done", "p10") == p("trucks_planned", "p90"),
        "density_ok": bool(summary["layout"].get("density_ok")),
        "no_unreachable_tasks": (p("unreachable_tasks", "p90") or 0) == 0,
        "pallets_received_all": p("pallets_received", "p10") == p("pallets_received_plan", "p90"),
        "pallets_shipped_all": p("pallets_shipped", "p10") == p("pallets_shipped_plan", "p90"),
    }
    checks["ok"] = all(checks.values())
    return checks


class RunBody(BaseModel):
    property_id: int
    profile: str
    runs: int = 10
    volume_scale: float = 1.0
    model_id: Optional[str] = None


@router.post("/api/sim/run")
def post_run(body: RunBody):
    rec = run(body.property_id, body.profile, body.runs, body.volume_scale, body.model_id)
    return rec


@router.get("/api/sim/heroes")
def heroes():
    p = SIM_DIR / "heroes.json"
    return json.loads(p.read_text()) if p.exists() else []


@router.get("/api/sim/{run_id}/scene")
def get_scene(run_id: str):
    if not run_id.isalnum():
        raise HTTPException(400, "bad run id")
    p = SIM_DIR / run_id / "scene.json"
    if not p.exists():
        raise HTTPException(404, f"no scene for run {run_id}: POST /api/sim/run first")
    from fastapi.responses import FileResponse
    return FileResponse(p, media_type="application/json")


@router.get("/api/sim/{run_id}")
def get_run(run_id: str):
    if not run_id.isalnum():
        raise HTTPException(400, "bad run id")
    p = SIM_DIR / run_id / "summary.json"
    if not p.exists():
        raise HTTPException(404, f"no run {run_id}")
    return json.loads(p.read_text())
