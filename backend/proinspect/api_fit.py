"""Equipment test (inside) + the door store (Stream S4). docs/CONTRACTS.md.

    GET  /api/warehouses/{id}/doors?model_id=     the doors a person marked in the scan
    PUT  /api/warehouses/{id}/doors               {model_id?, doors:[{id?, kind, a, b, height_m?, sill_m?, source?,
                                                   normal_hint?}]} -> replaces the list (validated)
    DELETE /api/warehouses/{id}/doors?model_id=   removes every marked door of that scan
    POST /api/fit/test                            {property_id, model_id?, equipment, route, floor} -> fit result
    GET  /api/fit/{run_id}                        a cached fit result
"""
import json
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proinspect import doors as door_store
from proinspect.config import BUILD

router = APIRouter()


def _model(property_id, model_id=None):
    from proinspect.api_sim import pick_model
    return pick_model(property_id, model_id)


def _empty(property_id, model_id):
    return {"model_id": model_id, "property_id": property_id, "updated_at": None, "doors": []}


@router.get("/api/warehouses/{pid}/doors")
def get_doors(pid: int, model_id: Optional[str] = None):
    m = _model(pid, model_id)
    rec = door_store.load(m)
    if rec and rec.get("doors"):
        return rec
    ds, basis = door_store.from_dock_store(m, pid)       # aerial dock store mapped into the scan (2D editor)
    return {**_empty(pid, m), "doors": ds, "source": "dock_store" if ds else None, "basis": basis}


class DoorsBody(BaseModel):
    model_id: Optional[str] = None
    doors: list[dict]


@router.put("/api/warehouses/{pid}/doors")
def put_doors(pid: int, body: DoorsBody):
    m = _model(pid, body.model_id)
    if len(body.doors) > 200:
        raise HTTPException(422, "at most 200 doors per scan")
    try:
        return door_store.save(m, pid, body.doors)
    except door_store.DoorError as e:
        raise HTTPException(422, str(e))


@router.delete("/api/warehouses/{pid}/doors")
def delete_doors(pid: int, model_id: Optional[str] = None):
    m = _model(pid, model_id)
    door_store.delete(m)
    return _empty(pid, m)


class FitBody(BaseModel):
    property_id: int
    model_id: Optional[str] = None
    equipment: Union[str, dict[str, Any]]
    route: dict[str, Any] = {"to": "auto"}
    floor: str = "as_scanned"


@router.post("/api/fit/test")
def fit_test(body: FitBody):
    from proinspect.fit import FitError, run_for_model
    m = _model(body.property_id, body.model_id)
    try:
        return run_for_model(body.property_id, m, body.equipment, body.route, body.floor)
    except FitError as e:
        raise HTTPException(422, str(e))


@router.get("/api/fit/{run_id}")
def fit_get(run_id: str):
    if not run_id.isalnum():
        raise HTTPException(400, "bad run id")
    from proinspect import fit
    p = fit.FIT_DIR / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, f"fit run {run_id} not found")
    return json.loads(p.read_text())
