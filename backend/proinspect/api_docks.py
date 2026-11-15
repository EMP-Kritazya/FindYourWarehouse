"""Dock store API (CONTRACTS_v3 section 1):
    GET /api/warehouses/{id}/docks          the store (built from the exterior twin on first use)
    PUT /api/warehouses/{id}/docks          {doors:[...], gate?, registration?} from the 2D dock editor
    GET /api/docks/evidence?ids=1,2,3       {id: summary}
"""
import re

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from proinspect import docks

router = APIRouter()


@router.get("/api/warehouses/{pid}/docks")
def get_docks(pid: int):
    try:
        store = docks.load(pid)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return _with_outline(store)


def _with_outline(store):
    """Attach the scan floor outline (scan rot frame) to the registration: the 2D editor's alignment overlay."""
    reg = (store or {}).get("registration") or {}
    if reg.get("model_id") and not reg.get("scan_outline"):
        try:
            from proinspect.doors import scan_outline
            store = {**store, "registration": {**reg, "scan_outline": scan_outline(reg["model_id"])}}
        except Exception:                                              # noqa: BLE001  (overlay is optional)
            pass
    return store


@router.put("/api/warehouses/{pid}/docks")
def put_docks(pid: int, body: dict = Body(...)):
    try:
        return _with_outline(docks.save(pid, body))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except docks.DockValidationError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/api/docks/evidence")
def get_evidence(ids: str = Query(..., description="comma-separated property ids")):
    out = {}
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            pid = int(tok)
        except ValueError as e:
            raise HTTPException(422, f"bad property id {tok!r}") from e
        try:
            out[str(pid)] = docks.evidence(pid)
        except FileNotFoundError as e:
            out[str(pid)] = {"error": str(e), "estimate": {"value": None, "lo": None, "hi": None,
                                                         "status": "unknown", "basis": "no exterior twin"}}
    return out


@router.get("/api/docks/{pid}/proof")
def get_proof_index(pid: int):
    """Per door: the photo crops (Vexcel oblique / ortho) whose SAM3 hits put it there, or why there is none."""
    try:
        return docks.proof_index(pid)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/api/docks/{pid}/proof/{name}")
def get_proof_image(pid: int, name: str, door: str = Query(...)):
    """The crop with every SAM3 box (grey) and this door's boxes (teal); the Vexcel image is fetched on demand, once."""
    m = re.fullmatch(r"([A-Za-z0-9_]+)\.jpg", name)
    if not m or not re.fullmatch(r"[A-Za-z0-9_-]+", door):
        raise HTTPException(422, "expected {crop_id}.jpg and a door id")
    try:
        path = docks.proof_image(pid, m.group(1), door)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:                                         # noqa: BLE001  (Vexcel down / offline)
        raise HTTPException(502, f"proof image unavailable: {type(e).__name__}: {str(e)[:160]}") from e
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.post("/api/warehouses/{pid}/docks/align")
def align_docks(pid: int, body: dict = Body(...)):
    """One click on a real dock door in the scan: {model_id, x, z} (Showcase metres) -> slide the aerial doors along
    that wall onto it (registration.shift, confirmed by a person)."""
    from proinspect import doors as door_store
    try:
        x, z, mid = float(body["x"]), float(body["z"]), str(body["model_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "body must be {model_id, x, z}")
    out = door_store.align_to_click(pid, mid, x, z)
    if not out.get("ok"):
        raise HTTPException(422, out.get("why") or "could not align")
    return out
