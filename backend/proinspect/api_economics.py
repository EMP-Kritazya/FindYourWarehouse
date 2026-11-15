"""True cost per pallet (proinspect.economics). Disabled with PROINSPECT_COST=0 (routes answer 404).

    GET  /api/economics/{pid}?operation=     cost lines with provenance, $/position/month, $/pallet moved
    POST /api/economics/{pid}                {operation?, overrides?: {rent_psf, sqft, wage_hour, ...}}
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proinspect import economics

router = APIRouter()


class Body(BaseModel):
    operation: Optional[str] = None
    overrides: Optional[dict] = None


def _run(pid, operation, overrides):
    if not economics.ENABLED:
        raise HTTPException(404, "cost model disabled")
    try:
        return economics.cost(pid, operation, overrides)
    except (KeyError, StopIteration, FileNotFoundError) as e:
        raise HTTPException(404, f"warehouse {pid} not in the catalog") from e


@router.get("/api/economics/{pid}")
def get_cost(pid: int, operation: Optional[str] = None):
    return _run(pid, operation, None)


@router.post("/api/economics/{pid}")
def post_cost(pid: int, body: Body):
    return _run(pid, body.operation, body.overrides)
