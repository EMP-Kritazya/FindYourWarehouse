"""HTTP API for the web app. Search works with zero requirements; nothing here invents data.

    GET  /api/health
    GET  /api/warehouses                      catalog (map pins + cards)
    GET  /api/warehouses/{id}                 one catalog record with its facts
    POST /api/search   {query?, chips?, requirements?}  -> parsed chips + ranked/browse rows
    GET  /api/library                         equipment + business profiles
    POST /api/requirements/missing {requirements, feature} -> what that feature still needs

Run:  cd backend && ../.venv/bin/uvicorn proinspect.api:app --reload --port 8000
"""
import json
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from proinspect.config import BUILD
from proinspect.facts import measured_facts
from proinspect.library import equipment, profiles
from proinspect.query import parse, to_requirements
from proinspect.rank import catalog_facts, rank
from proinspect.requirements import Requirements

app = FastAPI(title="ProInspect API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173",
                                                  "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"])


@lru_cache
def catalog() -> list[dict]:
    p = BUILD / "catalog" / "warehouses.json"
    if not p.exists():
        raise RuntimeError("catalog missing: run `python -m proinspect.catalog` first")
    return json.loads(p.read_text())


def _pin(w):
    return {k: w.get(k) for k in ("id", "name", "address", "city", "market", "lat", "lon", "subtype", "rba_sqft",
                                  "clear_height_ft", "year_built", "availability", "inspectable", "photo")}


@app.get("/api/health")
def health():
    return {"ok": True, "warehouses": len(catalog())}


@app.get("/api/warehouses")
def warehouses():
    return [_pin(w) for w in catalog()]


@app.get("/api/warehouses/{pid}")
def warehouse(pid: int):
    w = next((w for w in catalog() if w["id"] == pid), None)
    if not w:
        raise HTTPException(404, f"warehouse {pid} not in the catalog")
    from proinspect.rank import merge
    facts = {k: vars(v) for k, v in merge(catalog_facts(w), measured_facts().get(pid, {})).items()}
    return {**w, "facts": facts}


class SearchBody(BaseModel):
    query: Optional[str] = None
    chips: Optional[list[dict]] = None            # chips the user edited in the UI (take precedence)
    requirements: Optional[Requirements] = None   # full requirements from the Concierge / fit match
    limit: int = 200


@app.post("/api/search")
def search(body: SearchBody):
    parsed = parse(body.query or "", catalog()) if body.query else {"chips": [], "unparsed": "", "used_claude": False,
                                                                     "claude_error": None}
    chips = body.chips if body.chips is not None else parsed["chips"]
    req = to_requirements(chips, catalog(), base=body.requirements)
    rows = rank(req, catalog(), measured_facts())
    keep = [r for r in rows if not r["excluded"]]
    return {
        "query": body.query, "chips": chips, "unparsed": parsed.get("unparsed"),
        "used_claude": parsed.get("used_claude"), "claude_error": parsed.get("claude_error"),
        "mode": rows[0]["mode"] if rows else "browse",
        "counts": {"shown": len(keep), "excluded": len(rows) - len(keep), "total": len(catalog())},
        "requirements": req.model_dump(),
        "results": [{k: v for k, v in r.items() if k != "facts"} for r in keep[:body.limit]],
        "excluded": [{"id": r["id"], "address": r["address"], "why": r["excluded_because"]}
                     for r in rows if r["excluded"]],
    }


@app.get("/api/library")
def library():
    return {"equipment": equipment(), "profiles": profiles()}


class MissingBody(BaseModel):
    requirements: Requirements = Requirements()
    feature: str = "simulate"


@app.post("/api/requirements/missing")
def missing(body: MissingBody):
    return {"feature": body.feature, "missing": body.requirements.missing_for(body.feature),
            "ready": body.requirements.ready_for(body.feature)}


# ---------------------------------------------------------------- feature routers (one owner each)
# Each stream adds routes ONLY in its own module, exposing `router = APIRouter()`; nothing else edits this file.
import importlib  # noqa: E402

from fastapi.staticfiles import StaticFiles  # noqa: E402

for _mod in ("proinspect.api_sim", "proinspect.api_twin", "proinspect.api_concierge", "proinspect.api_vexcel",
             "proinspect.api_yard", "proinspect.api_fit", "proinspect.api_docks", "proinspect.api_analytics"):
    try:
        app.include_router(importlib.import_module(_mod).router)
    except ModuleNotFoundError as _e:
        if _e.name != _mod:
            raise
(BUILD / "media").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=BUILD), name="media")      # build/... served at /media/...
