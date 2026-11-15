"""Storage capacity planner routes (proinspect.racks).

    GET  /api/racks/{pid}?equipment=reach_truck&orientation=perpendicular   auto layout + metrics + 2D plan + scene
    POST /api/racks/{pid}  {equipment?, orientation?, edits:{deleted:[row_id], moved:{row_id: dx_m}}}
    GET  /api/racks/{pid}/trucks?orientation=                                 the equipment trade-off strip
    GET  /api/racks/{pid}/summary                                             the card / compare line
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proinspect import racks

router = APIRouter()


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/api/racks/{pid}")
def get_plan(pid: int, equipment: str = "reach_truck", orientation: str = "perpendicular"):
    return _run(racks.plan, pid, equipment, orientation)


class Edits(BaseModel):
    equipment: str = "reach_truck"
    orientation: str = "perpendicular"
    edits: Optional[dict] = None


@router.post("/api/racks/{pid}")
def post_plan(pid: int, body: Edits):
    e = body.edits or {}
    if len(e.get("deleted") or []) > 500 or len(e.get("moved") or {}) > 500:
        raise HTTPException(422, "too many edits")
    return _run(racks.plan, pid, body.equipment, body.orientation, e)


@router.get("/api/racks/{pid}/trucks")
def get_trucks(pid: int, orientation: str = "perpendicular"):
    return _run(racks.compare_trucks, pid, orientation)


@router.get("/api/racks/{pid}/summary")
def get_summary(pid: int):
    return _run(racks.summary, pid)
