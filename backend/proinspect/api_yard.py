"""Yard test API (S3): POST /api/yard/test, GET /api/yard/{run_id}. Results are cached under build/yard/<run_id>.json;
the run id hashes the normalised input AND the yard code plus the twin files, so a code or twin change never serves a
stale result."""
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from proinspect.config import BUILD

router = APIRouter()
OUT = BUILD / "yard"
CODE = [Path(__file__).resolve().parent / "yard" / f for f in ("kinematics.py", "queue.py", "site.py", "run.py")]


class Truck(BaseModel):
    type: str
    per_day: float | None = None
    per_hour: list[float] | None = None


class YardRequest(BaseModel):
    property_id: int
    trucks: list[Truck]
    door_ids: list[str] | None = None
    dock_minutes: dict[str, float] | None = None
    hours: list[float] | None = Field(default=None, min_length=2, max_length=2)


def _norm(req: YardRequest):
    d = req.model_dump()
    d["hours"] = d["hours"] or [6, 16]
    d["trucks"] = sorted(({k: v for k, v in t.items() if v is not None} for t in d["trucks"]),
                         key=lambda t: t["type"])
    d["door_ids"] = sorted(d["door_ids"]) if d["door_ids"] else None
    return d


def run_id_for(d):
    h = hashlib.sha256(json.dumps(d, sort_keys=True).encode())
    for p in CODE:
        h.update(p.read_bytes())
    ext = BUILD / "twins" / "exterior"
    for p in (ext / f"{d['property_id']}.json", ext / f"{d['property_id']}.npz",
              BUILD / "doors"):
        if p.is_file():
            h.update(str(p.stat().st_mtime_ns).encode())
        elif p.is_dir():
            h.update(str(sorted((q.name, q.stat().st_mtime_ns) for q in p.glob("*.json"))).encode())
    return h.hexdigest()[:16]


@router.post("/api/yard/test")
def yard_test(req: YardRequest):
    from proinspect.yard.run import TYPES, yard_test as run
    bad = [t.type for t in req.trucks if t.type not in TYPES]
    if bad or not req.trucks:
        raise HTTPException(422, f"truck types must be among {list(TYPES)} (got {bad or 'none'})")
    d = _norm(req)
    rid = run_id_for(d)
    p = OUT / f"{rid}.json"
    if p.exists():
        return json.loads(p.read_text())
    try:
        res = run(d)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res = {"run_id": rid, "input": d, **res}
    OUT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, separators=(",", ":")))
    return res


# ------------------------------------------------------------------ v3: multi-truck maneuver test
MAN = OUT / "maneuver"
MAN_CODE = [Path(__file__).resolve().parent / "yard" / f
            for f in ("kinematics.py", "site.py", "run.py", "plan.py", "pursuit.py", "reserve.py", "route.py",
                      "hybrid.py")]


class ManTruck(BaseModel):
    type: str
    count: int = Field(default=1, ge=1, le=12)


class GatePt(BaseModel):
    lon: float
    lat: float


class ManeuverRequest(BaseModel):
    property_id: int
    trucks: list[ManTruck]
    dwell_s: float | None = Field(default=None, ge=0, le=3600)
    door_ids: list[str] | None = None
    gate: GatePt | None = None
    seed: int | None = 0
    stagger_s: float | None = Field(default=None, ge=0, le=600)


def _man_id(d):
    h = hashlib.sha256(json.dumps(d, sort_keys=True).encode())
    for p in MAN_CODE:
        h.update(p.read_bytes())
    ext = BUILD / "twins" / "exterior"
    for p in (ext / f"{d['property_id']}.json", ext / f"{d['property_id']}.npz",
              BUILD / "docks" / f"{d['property_id']}.json"):
        if p.is_file():
            h.update(str(p.stat().st_mtime_ns).encode())
    try:                                            # the dock store module (S1) changes the doors too
        from proinspect import docks
        h.update(Path(docks.__file__).read_bytes())
    except Exception:  # noqa: BLE001
        pass
    return "m" + h.hexdigest()[:16]


def _jsonable(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(type(o))


@router.post("/api/yard/maneuver")
def yard_maneuver(req: ManeuverRequest):
    from proinspect.yard import plan
    bad = [t.type for t in req.trucks if t.type not in plan.TYPES]
    if bad or not req.trucks:
        raise HTTPException(422, f"truck types must be among {list(plan.TYPES)} (got {bad or 'none'})")
    d = req.model_dump()
    d["trucks"] = [{"type": t["type"], "count": t["count"]} for t in d["trucks"]]
    d["door_ids"] = sorted(d["door_ids"]) if d["door_ids"] else None
    rid = _man_id(d)
    p = MAN / f"{rid}.json"
    if p.exists():
        res = json.loads(p.read_text())
        res["cached"] = True
        return res
    try:
        res = plan.maneuver(d)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res = {"run_id": rid, "input": d, **res}
    MAN.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, separators=(",", ":"), default=_jsonable))
    return json.loads(p.read_text())


@router.get("/api/yard/maneuver/{run_id}")
def yard_maneuver_get(run_id: str):
    if not run_id.isalnum():
        raise HTTPException(400, "bad run id")
    p = MAN / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, "no such maneuver run")
    res = json.loads(p.read_text())
    res["cached"] = True
    return res


@router.get("/api/yard/{run_id}")
def yard_get(run_id: str):
    if not run_id.isalnum():
        raise HTTPException(400, "bad run id")
    p = OUT / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, "no such yard run")
    return json.loads(p.read_text())


# ------------------------------------------------------------------ yard verdict (card + compare)
VERDICT_TEST = {"type": "semi_53", "count": 5}


def _verdict_req(pid):
    return ManeuverRequest(property_id=pid, trucks=[ManTruck(**VERDICT_TEST)])


def verdict_from(res):
    """The buyer-facing yard verdict from a standard maneuver run: one comparable line plus the numbers behind it."""
    s = res.get("summary") or {}
    doors = res.get("doors_used") or []
    dock_capable = [d for d in doors if d.get("kind") != "drive_in"]
    backin_ok = [d for d in dock_capable if (d.get("ok") or {}).get("semi_53")]
    reached = sorted({v["target"]["id"] for v in res.get("vehicles") or []
                      if v.get("status") == "done" and (v.get("target") or {}).get("kind") == "door"})
    n, done = s.get("trucks") or 0, s.get("completed") or 0
    at_once = s.get("docked_max") or 0
    mins = round((s.get("makespan_s") or 0) / 60, 1)
    wait = round(((s.get("worst_wait") or {}).get("s") or 0) / 60, 1)
    if done < n:
        grade = "poor"
    elif at_once <= 1 or wait > 5 or len(reached) <= 2:
        grade = "limited"
    else:
        grade = "good"
    line = (f"53' back-in fits {len(backin_ok)} of {len(dock_capable)} dock doors · "
            + (f"{done}/{n} trucks docked and left in {mins} min, {at_once} at a time"
               + (f", up to {wait} min queue" if wait >= 1 else "") if done else
               f"no truck could be driven from the street entrance to a door ({done}/{n})"))
    limits = []
    for cls in ("geometric", "congestion", "assumption", "planner"):
        for i in res.get("issues") or []:
            if i.get("class") == cls and i.get("trucks") is not None and len(limits) < 2:
                limits.append({"class": cls, "text": i["text"]})
    confirmed = all(d.get("status") in ("confirmed", "user_added") for d in dock_capable) and dock_capable
    return {"status": "computed", "run_id": res.get("run_id"), "grade": grade, "line": line,
            "test": "5 × 53 ft tractor-trailers arriving 30 s apart, 90 s at the door (the same test for every building)",
            "dock_doors": len(dock_capable), "semi_backin_ok": len(backin_ok), "doors_reached": reached,
            "docked_max": at_once, "trucks": n, "completed": done, "makespan_min": mins, "worst_wait_min": wait,
            "limits": limits, "gate_source": (res.get("gate") or {}).get("source"),
            "basis": ("dock doors confirmed by a person" if confirmed else
                      "dock doors proposed from Vexcel obliques (not yet confirmed)") +
                     "; property bounds and gate from the parcel/CoStar lot and OpenStreetMap"}


@router.get("/api/yard/verdict/{pid}")
def yard_verdict(pid: int, run: bool = False):
    """The standard yard verdict. Cached runs answer instantly; run=true computes it (10-30 s cold)."""
    req = _verdict_req(pid)
    d = req.model_dump()
    d["trucks"] = [{"type": t["type"], "count": t["count"]} for t in d["trucks"]]
    d["door_ids"] = None
    p = MAN / f"{_man_id(d)}.json"
    if p.exists():
        return verdict_from(json.loads(p.read_text()))
    if not run:
        return {"status": "not_run", "test": "5 × 53 ft tractor-trailers (the same test for every building)"}
    return verdict_from(yard_maneuver(req))
