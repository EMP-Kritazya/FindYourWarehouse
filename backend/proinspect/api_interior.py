"""Interior planner routes (proinspect.interior).

    POST /api/interior/{pid}   {operation, machine?, keep?:[object ids], orientation?, zone_types?:{z1: type}, edits?}
    GET  /api/interior/{pid}/summary?operation=      the card / compare verdict
    GET  /api/interior/operations                    recipes (operation -> zones, machine, why)
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proinspect import interior

router = APIRouter()


class Body(BaseModel):
    operation: str = "pallet_3pl"
    machine: Optional[str] = None
    keep: Optional[list[str]] = None
    orientation: str = "perpendicular"
    zone_types: Optional[dict] = None
    edits: Optional[dict] = None


@router.get("/api/interior/operations")
def operations():
    from proinspect.library import profiles
    P = profiles()
    return [{"key": k, "name": (P.get(k) or {}).get("name", k), "why": r["why"], "machine": r["machine"],
             "zones": r["zones"]} for k, r in interior.RECIPES.items()]


@router.post("/api/interior/{pid}")
def post_plan(pid: int, body: Body):
    try:
        return interior.plan(pid, body.operation, body.machine, body.keep, body.orientation, body.zone_types, body.edits)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/api/interior/{pid}/summary")
def get_summary(pid: int, operation: Optional[str] = None):
    return interior.summary(pid, operation)
