"""Twin + config endpoints (Stream B).

    GET /api/config                    {matterport_sdk_key, markets}
    GET /api/warehouses/{id}/twin      exterior twin (minus ray_detail), interior summary for the best warehouse-floor
                                       scan, every scan, and media URLs under /media/
Reads only files under build/ (no network); results are cached in memory by file mtime.
"""
import json
import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.config import BUILD

router = APIRouter()
MARKETS = ["Houston", "Los Angeles"]


@router.get("/api/config")
def get_config():
    return {"matterport_sdk_key": os.environ.get("MATTERPLOT_SDK_KEY") or os.environ.get("MATTERPORT_SDK_KEY"),
            "markets": MARKETS}


@lru_cache(maxsize=4)
def _catalog(mtime):
    return {w["id"]: w for w in json.loads((BUILD / "catalog" / "warehouses.json").read_text())}


def catalog_record(pid):
    p = BUILD / "catalog" / "warehouses.json"
    return _catalog(p.stat().st_mtime).get(pid) if p.exists() else None


@lru_cache(maxsize=512)
def _json(path, mtime):
    return json.loads(open(path).read())


def read_json(p):
    return _json(str(p), p.stat().st_mtime) if p.exists() else None


def best_scan(w):
    """The warehouse-floor scan with the largest floor that has an interior twin (else None)."""
    scans = [s for s in (w or {}).get("scans") or [] if s.get("class") == "warehouse_floor"
             and (BUILD / "twins" / "interior" / f"{s['model_id']}.npz").exists()]
    return max(scans, key=lambda s: s.get("floor_sqft") or 0) if scans else None


def _exterior(pid):
    e = read_json(BUILD / "twins" / "exterior" / f"{pid}.json")
    if e is None:
        return None
    e = dict(e)
    e["courts"] = [{k: v for k, v in c.items() if k != "ray_detail"} for c in e.get("courts") or []]
    return e


def _interior(model_id):
    i = read_json(BUILD / "twins" / "interior" / f"{model_id}.json")
    if i is None:
        return None
    cols = {k: v for k, v in (i.get("columns") or {}).items() if k not in ("positions", "pilaster_positions")}
    return {"model_id": model_id, "deck_ft": i.get("deck_ft"), "deck_range_ft": i.get("deck_range_ft"),
            "clear": i.get("clear"), "clear_confidence": i.get("clear_confidence"),
            "office_sqft": i.get("office_sqft"), "floor": i.get("floor"), "columns": cols,
            "overhead_coverage": i.get("overhead_coverage"), "scan_created": i.get("scan_created"),
            "mesh_note": i.get("mesh_note")}


def _media(pid):
    d = BUILD / "twins" / "exterior"
    ortho = d / f"{pid}_ortho.jpg"
    obliques = {}
    for p in sorted(d.glob(f"{pid}_*.jpg")):
        view = p.stem[len(str(pid)) + 1:]
        if view != "ortho":
            obliques[view] = f"/media/twins/exterior/{p.name}"
    return {"ortho": f"/media/twins/exterior/{ortho.name}" if ortho.exists() else None, "obliques": obliques}


@router.get("/api/warehouses/{pid}/twin")
def twin(pid: int):
    w = catalog_record(pid)
    if w is None:
        raise HTTPException(404, f"warehouse {pid} not in the catalog")
    scan = best_scan(w)
    return {"property_id": pid,
            "exterior": _exterior(pid),
            "interior": _interior(scan["model_id"]) if scan else None,
            "scans": [{k: s.get(k) for k in ("model_id", "class", "floor_sqft", "height_ft")}
                      for s in w.get("scans") or []],
            "media": _media(pid)}
