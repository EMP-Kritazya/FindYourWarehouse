"""Analytics routes (CONTRACTS_v3 §2): rank by attribute in tiers, compare, comparables, criteria."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from proinspect import analytics as A

router = APIRouter()


@router.get("/api/rank/attribute")
def rank_attribute(attr: str, order: Optional[str] = Query(None, pattern="^(asc|desc)$"),
                   market: Optional[str] = None, top: int = Query(20, ge=1, le=200)):
    try:
        return A.rank_attribute(attr, order, market, top)
    except KeyError as e:
        raise HTTPException(400, str(e).strip("'\""))


class CompareBody(BaseModel):
    ids: list[int]
    profile: Optional[str] = None
    ops_text: Optional[str] = None


@router.post("/api/compare")
def compare(body: CompareBody):
    try:
        return A.compare(body.ids, body.profile, body.ops_text)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/comparables/{pid}")
def comparables(pid: int, profile: Optional[str] = None, ops_text: Optional[str] = None, n: int = 3):
    try:
        return A.find_comparables(pid, profile, ops_text, n)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@router.get("/api/criteria")
def criteria(profile: Optional[str] = None, ops_text: Optional[str] = None):
    out = A.select_criteria(profile, ops_text)
    out["operations"] = [{"key": k, "name": v.get("name", k)} for k, v in A.library()["operations"].items()]
    return out


@router.get("/api/docks/evidence_lite/{pid}")
def dock_evidence(pid: int):
    """S4's view of dock evidence (S1's store when present, else the oblique lower bound)."""
    if not A.wh(pid):
        raise HTTPException(404, f"warehouse {pid} not in the catalog")
    return A.dock_evidence(pid)
