"""Flood exposure (proinspect.flood). Disabled with PROINSPECT_FLOOD=0 (routes answer 404).

    GET /api/flood/{pid}            cached assessment, or {status: "not_run"} (never calls Vexcel / FEMA on page load)
    GET /api/flood/{pid}?run=true   compute now (Vexcel DTM + OSM waterways + FEMA zone; ~10-60 s the first time)
"""
from fastapi import APIRouter, HTTPException

from proinspect import flood

router = APIRouter()


@router.get("/api/flood/{pid}")
def get_flood(pid: int, run: bool = False):
    if not flood.ENABLED:
        raise HTTPException(404, "flood exposure disabled")
    try:
        r = flood.assess(pid, compute=run)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return r or {"status": "not_run", "property_id": pid}
