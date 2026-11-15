"""The guide's door into Vexcel intelligence (S1), with honest local fallbacks.

S1 owns the real thing:
  * `proinspect.vexcel_rank`: metrics(), definition(), metric_for(phrase), rank(metric, market, top)
  * `proinspect.twin.vexcel_profile`: load(pid) (precomputed, no network), nearby_hits(), resolve_place(), VOCAB
  * `proinspect.api_vexcel`: HTTP routes; any profile/detect/imagery functions found there are used too.
Every S1 import is guarded: when a piece isn't there (S1 is built in parallel), we fall back to what is on disk:
  * profile/rank -> the exterior twins (build/twins/exterior/<id>.json, Vexcel-derived, dated) and CoStar;
  * search -> the raw Vexcel embedding search (tools/vexcel.py, disk-cached), every hit only "possible";
  * detect -> raw SAM3 around the CoStar point; imagery -> the twin's cached ortho/oblique files.
Each result carries `source` and, where known, a capture `date`, so the agent can say where a number came from.
"""
import asyncio
import inspect
import json
import math
from functools import lru_cache
from typing import Optional

from proinspect.config import BUILD

# ------------------------------------------------------------------ metric definitions (fallback for S1 metrics())
LOCAL_METRICS: dict[str, dict] = {
    "parking_area_sqft": {"label": "parking lot area", "unit": "sqft",
                          "definition": "area Vexcel's SAM3 model marks as 'parking lot' on the newest ortho",
                          "alternatives": ["car_stalls_est", "paved_area_sqft"]},
    "paved_area_sqft": {"label": "paved area", "unit": "sqft",
                        "definition": "parcel area x Vexcel paved share: parking, drives and truck courts",
                        "alternatives": ["parking_area_sqft", "truck_court_ft"]},
    "car_stalls_est": {"label": "estimated car stalls", "unit": "stalls",
                       "definition": "parking lot area / 350 SF per stall (an estimate)",
                       "alternatives": ["parking_area_sqft"]},
    "costar_parking_spaces": {"label": "parking spaces (CoStar)", "unit": "spaces",
                              "definition": "parking spaces recorded by CoStar research",
                              "alternatives": ["paved_area_sqft"]},
    "cars_seen": {"label": "cars seen", "unit": "count", "definition": "cars on the parcel in one aerial capture",
                  "alternatives": ["car_stalls_est"]},
    "trucks_seen": {"label": "trucks seen", "unit": "count",
                    "definition": "trucks on the parcel in one aerial capture", "alternatives": ["trailers_seen"]},
    "trailers_seen": {"label": "trailers seen", "unit": "count",
                      "definition": "semi trailers on the parcel in one aerial capture", "alternatives": ["trucks_seen"]},
    "truck_court_ft": {"label": "truck court depth", "unit": "ft",
                       "definition": "paved depth in front of the dock wall (Vexcel imagery + DSM)",
                       "alternatives": ["paved_area_sqft"]},
    "roof_condition": {"label": "roof condition", "unit": "1-5",
                       "definition": "Vexcel roof condition score, 1 poor to 5 excellent", "alternatives": []},
    "solar": {"label": "rooftop solar", "unit": "yes/no", "definition": "solar panels detected by Vexcel",
              "alternatives": []},
    "tree_cover_pct": {"label": "tree cover", "unit": "%", "definition": "Vexcel parcel tree-cover share",
                       "alternatives": []},
    "building_height_ft": {"label": "building height", "unit": "ft",
                           "definition": "roof top from the Vexcel DSM minus terrain (P90), not clear height",
                           "alternatives": []},
}
CAVEATS = {   # S1's caveats the guide must say when relevant
    "car_stalls_est": "an upper-bound estimate: SAM3's parking lot can include truck courts and drive aisles",
    "building_height_ft": "height to the roof top from the DSM, not clear height",
    "parking_area_sqft": "SAM3 segmentation on one ortho; low confidence",
    "trucks_seen": "a snapshot on one capture day", "trailers_seen": "a snapshot on one capture day",
    "cars_seen": "a snapshot on one capture day",
}
ALIASES = {"parking_lot_area_sqft": "parking_area_sqft", "parking_lot_area": "parking_area_sqft",
           "car_stalls": "car_stalls_est", "stalls": "car_stalls_est"}

PHRASES = [   # fallback when S1's metric_for is missing; first match wins
    (("stall", "parking space", "parking spot"), "car_stalls_est"),
    (("parking", "car park"), "parking_area_sqft"),
    (("paved", "pavement", "yard", "outdoor storage"), "paved_area_sqft"),
    (("truck court", "court", "apron"), "truck_court_ft"),
    (("trailer",), "trailers_seen"),
    (("truck",), "trucks_seen"),
    (("roof",), "roof_condition"),
    (("solar",), "solar"),
    (("tree", "green", "shade"), "tree_cover_pct"),
    (("tall", "height", "highest"), "building_height_ft"),
]


def _vr():
    try:
        from proinspect import vexcel_rank
        return vexcel_rank
    except ImportError:
        return None


def _vp():
    try:
        from proinspect.twin import vexcel_profile
        return vexcel_profile
    except ImportError:
        return None


def _av():
    try:
        from proinspect import api_vexcel
        return api_vexcel
    except ImportError:
        return None


def s1_ready() -> dict:
    vr, vp, av = _vr(), _vp(), _av()
    return {"rank": bool(vr and hasattr(vr, "rank")), "metrics": bool(vr and hasattr(vr, "metrics")),
            "profile": bool(vp and hasattr(vp, "load")), "search": bool(vp and hasattr(vp, "nearby_hits")),
            "detect": bool(av and callable(getattr(av, "detect", None))),
            "imagery": bool(av and callable(getattr(av, "imagery", None)))}


def interpret(metric_or_phrase: str) -> tuple[str, list[str]]:
    """'biggest parking lot' -> ('parking_area_sqft', ['car_stalls_est', 'paved_area_sqft']). Never refuses:
    an unknown phrase falls back to paved area."""
    s = (metric_or_phrase or "").strip().lower()
    s = ALIASES.get(s, s)
    vr = _vr()
    if vr:
        try:
            if s.startswith("distance_to:") or s in vr.METRICS:
                d = vr.definition(s)
                return d["metric"], list(d.get("alternatives") or [])
            m, alts = vr.metric_for(s)
            if m:
                return m, list(alts or [])
        except Exception:
            pass
    if s in LOCAL_METRICS or s.startswith("distance_to:"):
        return s, list(LOCAL_METRICS.get(s, {}).get("alternatives", []))
    for words, m in PHRASES:
        if any(w in s for w in words):
            return m, list(LOCAL_METRICS[m]["alternatives"])
    return "paved_area_sqft", ["parking_area_sqft"]


async def _call(fn, **cands):
    """Call a function with whichever candidate kwargs it accepts; FastAPI Query()/Path() defaults become plain
    defaults, so endpoint functions work when called directly."""
    sig = inspect.signature(fn)
    kw = {}
    for name, p in sig.parameters.items():
        if name in cands and cands[name] is not None:
            kw[name] = cands[name]
        elif p.default is not inspect.Parameter.empty and type(p.default).__module__.startswith("fastapi"):
            d = getattr(p.default, "default", None)
            kw[name] = None if d is ... else d
    out = await fn(**kw) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, **kw)
    if hasattr(out, "body") and hasattr(out, "media_type"):
        out = json.loads(out.body)
    if hasattr(out, "model_dump"):
        out = out.model_dump()
    return out


# ------------------------------------------------------------------ local data
def _catalog():
    from proinspect import api
    return api.catalog()


def _wh(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return next((w for w in _catalog() if w["id"] == pid), None)


@lru_cache(maxsize=512)
def _exterior(pid: int) -> Optional[dict]:
    try:
        return json.loads((BUILD / "twins" / "exterior" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None


def miles(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(a))


def val(x):
    """S1 facts are {value, unit, source, date, ...}; plain values pass through."""
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def local_profile(pid: int) -> Optional[dict]:
    """A contract-shaped partial profile from the exterior twin (Vexcel attributes + imagery we already pulled)."""
    e = _exterior(int(pid))
    w = _wh(pid)
    if not e:
        return None
    img, site, b, v = e.get("imagery", {}), e.get("site", {}), e.get("building", {}), e.get("vehicles", {})
    acres, paved = site.get("parcel_acres"), site.get("paved_pct_of_parcel")
    paved_sqft = round(acres * 43560 * paved / 100) if acres and paved is not None else None
    courts = e.get("courts") or []
    court = max(courts, key=lambda c: (c.get("kind") == "dock court", c.get("doors") or 0)) if courts else None
    seen = sum(x.get("footprint_sqft") or 0 for x in e.get("buildings") or []) or b.get("footprint_sqft") or 0
    costar = (w or {}).get("rba_sqft") or (w or {}).get("gba_sqft")
    mismatch = bool(e.get("parcel_mismatch") or (costar and seen < 0.3 * costar))
    if mismatch:                   # probably the wrong parcel: its yard/parking numbers must not rank
        paved_sqft, v, site, b, acres, paved, court = None, {}, {}, {}, None, None, None
    return {
        "id": int(pid), "source": "exterior twin (Vexcel attributes + imagery, cached)",
        "captured": {"parcel_attrs": img.get("parcel_date"), "dsm": img.get("dsm_date")},
        "parking": {"paved_area_sqft": paved_sqft, "parking_lot_area_sqft": None, "car_stalls_est": None,
                    "cars_seen": v.get("car"), "trucks_seen": v.get("truck"), "trailers_seen": v.get("semi trailer"),
                    "truck_court_ft": court.get("depth_ft") if court else None,
                    "truck_court_note": court.get("verdict_53ft") if court else None},
        "building": {"footprint_sqft": b.get("footprint_sqft"), "height_ft_dsm": b.get("height_ft_dsm_p90"),
                     "roof_condition": b.get("roof_condition"), "roof_material": b.get("roof_material"),
                     "roof_defects": b.get("roof_defects"), "solar": b.get("solar")},
        "site": {"parcel_acres": acres, "paved_pct": paved, "tree_cover_pct": site.get("tree_cover_pct_vexcel")},
        "nearby": None,
        "warnings": (["parcel probably wrong (Vexcel buildings << CoStar RBA): exterior facts suppressed"]
                     if mismatch else []) + ["no S1 Vexcel profile yet: parking lot area and nearby places unknown"],
    }


def compact(prof: dict) -> dict:
    """An S1 profile (fact dicts) -> small agent-facing dict: values with unit/date/confidence, nearby distances."""
    if not prof or prof.get("error"):
        return prof
    out = {"id": prof.get("id"), "captured": prof.get("captured"), "source": prof.get("source") or "S1 Vexcel profile"}
    for sec in ("parking", "building", "site"):
        d = {}
        for k, f in (prof.get(sec) or {}).items():
            if isinstance(f, dict) and "value" in f:
                if f.get("value") is None and not f.get("note"):
                    continue
                d[k] = {kk: f.get(kk) for kk in ("value", "unit", "date", "confidence", "estimate", "note")
                        if f.get(kk) not in (None, "", [])}
                if f.get("value") is None:
                    d[k]["value"] = None
            elif f is not None:
                d[k] = f
        out[sec] = d
    ch = (prof.get("history") or {}).get("change")
    if ch:
        out["change"] = {k: ch.get(k) for k in ("years", "new_structure_m2", "removed_m2", "note")}
    near = {}
    for k, n in (prof.get("nearby") or {}).items():
        if not isinstance(n, dict):
            continue
        h = n.get("nearest") or {}
        near[k] = {"label": n.get("label"), "distance_m": h.get("distance_m"), "likelihood": h.get("likelihood"),
                   "caveat": n.get("caveat")} if h else {"label": n.get("label"), "found": False,
                                                          "note": n.get("note")}
    if near:
        out["nearby"] = near
    if prof.get("warnings"):
        out["warnings"] = prof["warnings"][:3]
    return out


def _local_value(metric: str, pid: int):
    w = _wh(pid) or {}
    if metric == "costar_parking_spaces":
        return w.get("parking_spaces"), "CoStar", None
    p = local_profile(pid)
    if not p:
        return None, None, None
    date = p["captured"].get("dsm") or p["captured"].get("parcel_attrs")
    m = {"paved_area_sqft": p["parking"]["paved_area_sqft"], "cars_seen": p["parking"]["cars_seen"],
         "trucks_seen": p["parking"]["trucks_seen"], "trailers_seen": p["parking"]["trailers_seen"],
         "truck_court_ft": p["parking"]["truck_court_ft"], "roof_condition": p["building"]["roof_condition"],
         "solar": p["building"]["solar"], "tree_cover_pct": p["site"]["tree_cover_pct"],
         "building_height_ft": p["building"]["height_ft_dsm"]}
    return m.get(metric), "Vexcel (exterior twin)", date


def local_rank(metric: str, market: Optional[str] = None, top: int = 5) -> list[dict]:
    rows = []
    for w in _catalog():
        if market and w.get("market") != market:
            continue
        v, src, date = _local_value(metric, w["id"])
        if v is None:
            continue
        rows.append({"id": w["id"], "address": w.get("address"), "value": float(v) if isinstance(v, bool) else v,
                     "unit": LOCAL_METRICS.get(metric, {}).get("unit"), "source": src, "date": date})
    rows.sort(key=lambda r: float(r["value"]), reverse=True)
    return rows[:top]


# ------------------------------------------------------------------ public API (async)
async def metrics() -> dict:
    vr = _vr()
    if vr:
        try:
            ms = await asyncio.to_thread(vr.metrics)
            return {"source": "S1", "metrics": {m["metric"]: m for m in ms}}
        except Exception as e:
            return {"source": "local definitions", "metrics": LOCAL_METRICS, "note": f"S1 metrics failed: {e}"}
    return {"source": "local definitions", "metrics": LOCAL_METRICS}


def definition_of(metric: str, defs: dict) -> Optional[str]:
    if metric.startswith("distance_to:") and _vr():
        try:
            return _vr().definition(metric).get("definition")
        except Exception:
            return None
    return (defs.get(metric) or LOCAL_METRICS.get(metric) or {}).get("definition")


async def profile(pid: int) -> dict:
    """Full S1 profile when precomputed (no network), else S1's API function, else the local twin profile."""
    vp, av = _vp(), _av()
    if vp:
        try:
            p = await asyncio.to_thread(vp.load, int(pid))
            if p and not p.get("error"):
                return p
        except Exception:
            pass
    fn = getattr(av, "profile", None) if av else None
    if callable(fn):
        try:
            p = await asyncio.wait_for(_call(fn, id=int(pid), pid=int(pid), property_id=int(pid), compute=False), 25)
            if isinstance(p, dict) and p and not p.get("error"):
                return p
        except Exception:
            pass
    loc = local_profile(int(pid))
    return loc or {"error": f"no Vexcel data for {pid} yet"}


async def rank(metric: str, market: Optional[str] = None, top: int = 5) -> dict:
    """Rank warehouses by a metric. Falls down a chain when the metric has no values (and says so)."""
    metric = ALIASES.get(metric, metric)
    chain = [metric]
    if metric == "parking_area_sqft":
        chain = ["parking_area_sqft", "paved_area_sqft", "costar_parking_spaces"]
    elif metric == "car_stalls_est":
        chain = ["car_stalls_est", "costar_parking_spaces"]
    notes = []
    vr = _vr()
    for m in chain:
        if vr and m != "costar_parking_spaces":
            try:
                r = await asyncio.to_thread(vr.rank, m, market, top)
                rows = r.get("results") or []
                if rows:
                    cov = r.get("coverage") or {}
                    if cov.get("profiled") and cov.get("warehouses_in_market") and \
                            cov["profiled"] < cov["warehouses_in_market"]:
                        notes.append(f"Vexcel profiles cover {cov['profiled']} of {cov['warehouses_in_market']} "
                                     f"warehouses in this market so far; {cov.get('unknown', 0)} profiled have no value")
                    return {"metric": m, "asked": metric, "rows": rows, "source": "S1 Vexcel rank",
                            "coverage": cov, "notes": notes}
                notes.append(f"S1 has no values yet for {m}")
            except Exception as e:
                notes.append(f"S1 rank failed for {m}: {str(e)[:120]}")
        if m == "costar_parking_spaces" or not vr:
            rows = await asyncio.to_thread(local_rank, m, market, top)
            if rows:
                return {"metric": m, "asked": metric, "rows": rows, "source": rows[0]["source"], "notes": notes}
            notes.append(f"no local values for {m}")
    return {"metric": metric, "rows": [], "notes": notes, "error": "no values for this metric yet"}


async def search(text: str, near_id: Optional[int] = None, lat=None, lon=None, radius_m: int = 3000) -> dict:
    """Semantic search for a place type ('grocery store') near a warehouse or a point (calibrated by S1)."""
    if near_id is not None:
        w = _wh(near_id)
        if not w:
            return {"error": f"warehouse {near_id} not in the catalog"}
        lat, lon = w["lat"], w["lon"]
    if lat is None or lon is None:
        return {"error": "need near_id or lat/lon"}
    av = _av()
    if av and callable(getattr(av, "search", None)):
        try:
            r = await asyncio.to_thread(av.search, text, near_id, lat, lon, int(radius_m))
            hits = [{k: v for k, v in h.items() if k != "tile_wkt"} for h in (r.get("results") or [])[:8]]
            return {"text": text, "near_id": near_id, "hits": hits, "place": r.get("place"),
                    "calibrated": r.get("calibrated"), "note": r.get("note"),
                    "source": "S1 Vexcel search (embedding; likely/possible, never certain)"}
        except Exception:
            pass
    vp = _vp()
    if vp:
        try:
            hits = await asyncio.to_thread(vp.nearby_hits, lat, lon, text, int(radius_m), None, 20, True)
            for h in hits:
                h.pop("tile_wkt", None)
            return {"text": text, "near_id": near_id, "hits": hits[:8], "source": getattr(vp, "EMBED_SOURCE", "S1"),
                    "calibrated": bool(hits and hits[0].get("calibrated"))}
        except Exception as e:
            err = f"S1 search failed: {str(e)[:120]}"
    else:
        err = "S1 search not available"
    from proinspect.tools import vexcel as vx
    try:
        raw = await asyncio.to_thread(vx.embedding_search, lat, lon, text, int(min(radius_m, 3000)), 8)
    except Exception as e:
        return {"error": f"Vexcel search failed: {type(e).__name__}", "note": err}
    hits = [{"lat": h["lat"], "lon": h["lon"], "score": round(h["score"], 4) if h.get("score") else None,
             "distance_m": round(miles(lat, lon, h["lat"], h["lon"]) * 1609.34), "likelihood": "possible"}
            for h in raw]
    hits.sort(key=lambda h: h["distance_m"])
    return {"text": text, "near_id": near_id, "hits": hits, "note": err,
            "source": "Vexcel embedding search (uncalibrated: every hit is only 'possible')"}


async def closest_to(place: str, candidate_ids: Optional[list[int]] = None, market: Optional[str] = None) -> dict:
    """Warehouses sorted by distance to the nearest likely `place`. One S1 rank call over the precomputed profiles
    (calibrated place types); otherwise a live search around each candidate in parallel."""
    vp = _vp()
    key = vp.resolve_place(place) if vp else None
    notes = []
    if key and _vr():
        r = await rank(f"distance_to:{key}", market, 50)
        rows = r.get("rows") or []
        if candidate_ids:
            sub = [x for x in rows if x.get("id") in set(candidate_ids)]
            if sub:
                rows = sub
            elif rows:
                notes.append("none of the current results has a likely hit; showing the whole catalog")
        if rows:
            out = []
            for x in rows:
                h = x.get("hit") or {}
                out.append({"id": x["id"], "address": x.get("address"), "distance_m": x.get("value"),
                            "likelihood": h.get("likelihood") or x.get("confidence"), "score": h.get("score"),
                            "lat": h.get("lat"), "lon": h.get("lon"), "thumb_url": h.get("thumb_url"),
                            "label": h.get("label")})
            cav = (vp.VOCAB.get(key) or {}).get("caveat")
            return {"place": key, "rows": out, "source": "S1 Vexcel rank distance_to (precomputed profiles)",
                    "caveat": cav, "notes": notes + (r.get("notes") or [])}
        notes += r.get("notes") or []
    ids = list(candidate_ids or [])
    if not ids:
        ids = [w["id"] for w in _catalog() if not market or w.get("market") == market][:8]

    async def one(pid):
        r = await search(place, near_id=pid)
        hits = [h for h in r.get("hits") or [] if h.get("distance_m") is not None]
        likely = [h for h in hits if h.get("likelihood") == "likely"]
        best = min(likely or hits, key=lambda h: h["distance_m"]) if hits else None
        return {"id": pid, "address": (_wh(pid) or {}).get("address"), "best": best, "source": r.get("source")}
    res = await asyncio.gather(*[one(p) for p in ids[:10]])
    rows = sorted([r for r in res if r.get("best")], key=lambda r: r["best"]["distance_m"])
    out = [{"id": r["id"], "address": r["address"], **{k: r["best"].get(k) for k in
            ("distance_m", "likelihood", "score", "lat", "lon", "thumb_url", "label")}} for r in rows]
    return {"place": place, "rows": out, "source": res[0]["source"] if res else None,
            "notes": notes + [f"live search around {len(ids[:10])} candidates"]}


async def detect(pid: int, labels: list[str], view: str = "ortho") -> dict:
    av = _av()
    fn = getattr(av, "detect", None) if av else None
    if callable(fn):
        try:
            return await _call(fn, id=int(pid), pid=int(pid), property_id=int(pid), labels=labels,
                               label=",".join(labels), text=",".join(labels), view=view)
        except Exception as e:
            return {"error": f"Vexcel detect failed: {str(e)[:160]}"}
    w = _wh(pid)
    if not w:
        return {"error": f"warehouse {pid} not in the catalog"}
    from proinspect.tools import vexcel as vx
    try:                                         # raw SAM3 on a square around the CoStar point
        res = await asyncio.to_thread(vx.detect_raw, vx.square_around(w["lat"], w["lon"], 120), list(labels),
                                      view, 0.4)
    except Exception as e:
        return {"error": f"Vexcel detect failed: {type(e).__name__}"}
    counts = {}
    for f in (res or {}).get("features", []) if isinstance(res, dict) else []:
        lab = (f.get("properties") or {}).get("label") or (f.get("properties") or {}).get("element")
        counts[lab] = counts.get(lab, 0) + 1
    return {"id": int(pid), "view": view, "counts": counts, "threshold": 0.4,
            "source": "Vexcel elements/detect (SAM3), 240 m square around the CoStar point (raw, not parcel-clipped)"}


async def imagery(pid: int, view: Optional[str] = None, year: Optional[int] = None) -> dict:
    av = _av()
    fn = getattr(av, "imagery", None) if av else None
    if callable(fn):
        try:
            return await _call(fn, id=int(pid), pid=int(pid), property_id=int(pid), view=view, year=year)
        except Exception as e:
            return {"error": f"Vexcel imagery failed: {str(e)[:160]}"}
    from proinspect.api_twin import _media
    p = await profile(int(pid))
    return {"source": "exterior twin media", "media": _media(int(pid)),
            "ortho_dates": (p.get("history") or {}).get("ortho_dates") if isinstance(p, dict) else None}
