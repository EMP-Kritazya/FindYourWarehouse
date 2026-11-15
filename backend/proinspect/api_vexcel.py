"""S1 Vexcel intelligence: basemap tiles, per-property Vexcel profile, rankings, semantic search.

PYTHON FUNCTIONS FOR THE AGENT (S2): `from proinspect import api_vexcel as vex`
    vex.profile(id, compute=True) -> dict
        The Vexcel profile (docs/CONTRACTS.md): parking / building / site / history / nearby. Every value is a fact
        {value, unit, source, date, method, confidence[, note, estimate, alt]}; value None = unknown (see note).
        Precomputed for the 110 warehouses (fast); computed on demand otherwise (~10-60 s, network).
    vex.rank(metric, market=None, top=10) -> {metric: {metric,label,unit,definition,source,order,alternatives},
                                              market, results:[{rank,id,address,market,value,unit,source,date,
                                              confidence,note?,hit?}], unknown:[...], coverage:{...}}
        Say the metric's `label` + `definition` aloud and offer `alternatives` for ambiguous superlatives.
        metric ∈ metrics() names, or "distance_to:<place>" (grocery, highway_ramp, rail_yard, truck_stop,
        port_container, residential; aliases like "grocery store" / "highway" / "port" work).
    vex.metric_for(phrase) -> (metric | None, [alternatives])   "biggest parking lot" -> "parking_area_sqft"
    vex.search(text, near_id=None, lat=None, lon=None, radius_m=3000) -> {query, place, calibrated, center,
                                              results:[{lat,lon,score,distance_m,likelihood,label,thumb_url}], nearest_likely, note}
        Live semantic search over Vexcel aerial tiles, nearest first. likelihood is "likely" | "possible", NEVER
        certain. Free text that isn't a calibrated place term is uncalibrated: every hit is at most "possible".
    vex.detect(id, labels, view="ortho", threshold=0.3) -> {id, view, date, collection, counts:{label:n},
                                              detections:[{label, confidence, lat, lon, area_sqft}], note}
        SAM3 open-vocabulary detection on the parcel (ortho or oblique-north/south/east/west). Live, cached.
    vex.imagery(id, view="ortho", year=None) -> {url, view, date, collection, available_years, note}
        A JPEG under /media/vexcel/img/ (ortho of the parcel for that year, or an oblique). For showAerial.
    vex.metrics() -> [definition]                 every rank metric with label, unit, definition, source

HTTP
    GET /api/tiles/{z}/{x}/{y}.jpg               Vexcel ortho basemap (key never reaches the browser)
    GET /api/vexcel/metrics
    GET /api/vexcel/rank?metric=&market=&top=
    GET /api/vexcel/search?text=&near_id=|lat=&lon=&radius_m=
    GET /api/vexcel/{id}/imagery?view=&year=
    GET /api/vexcel/{id}/detect?labels=a,b&view=&threshold=
    GET /api/vexcel/{id}?compute=true
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from shapely import wkt as shp_wkt

from proinspect import vexcel_rank
from proinspect.config import BUILD
from proinspect.tools import vexcel as vx
from proinspect.tools import vexcel_tiles
from proinspect.twin import vexcel_profile as vp

router = APIRouter()
IMG = BUILD / "vexcel" / "img"
VIEWS = ["ortho"] + vx.DIRECTIONS


# ------------------------------------------------------------------ plain functions (S2 imports these)
def _catalog():
    return vexcel_rank._catalog()


def _parcel(pid):
    w = _catalog().get(int(pid))
    if not w:
        raise KeyError(f"{pid} is not in the warehouse catalog")
    ext = vx.property_extract(w["lat"], w["lon"])
    if not ext or not ext.get("features"):
        return w, None
    return w, shp_wkt.loads(vx.to_wkt(ext["features"][0]["geometry"]))


def profile(id, compute=True):
    prof = vp.load(id)
    if prof is None and compute:
        prof = vp.build(id)
    return prof


def rank(metric, market=None, top=10):
    return vexcel_rank.rank(metric, market, top)


def metric_for(phrase):
    return vexcel_rank.metric_for(phrase)


def metrics():
    return vexcel_rank.metrics()


def search(text, near_id=None, lat=None, lon=None, radius_m=3000):
    exclude = None
    if near_id is not None:
        w, exclude = _parcel(near_id)
        lat, lon = w["lat"], w["lon"]
    if lat is None or lon is None:
        raise ValueError("give near_id or lat/lon")
    key = vp.resolve_place(text)
    hits = vp.nearby_hits(float(lat), float(lon), key or text, float(radius_m), exclude=exclude)
    for h in hits:
        h.pop("tile_wkt", None)
    note = (f"Calibrated term '{key}' ({vp.VOCAB[key]['label']}): {vp.VOCAB[key]['caveat']}" if key else
            "Uncalibrated free-text search: scores are only comparable within this query, so every hit is "
            "'possible'.")
    return {"query": text, "place": key, "calibrated": bool(key), "center": {"lat": lat, "lon": lon},
            "radius_m": radius_m, "results": hits,
            "nearest_likely": next((h for h in hits if h["likelihood"] == "likely"), None),
            "source": vp.EMBED_SOURCE, "note": note}


def detect(id, labels, view="ortho", threshold=0.3):
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    labels = [l.strip() for l in (labels if isinstance(labels, list) else str(labels).split(",")) if l.strip()][:5]
    if not labels:
        raise ValueError("give at least one label")
    w, parcel = _parcel(id)
    if parcel is None:
        return {"id": int(id), "view": view, "counts": {}, "detections": [], "note": "no Vexcel parcel here"}
    res = vx.detect_raw(parcel.envelope.wkt, labels, view, float(threshold))
    meta = vp._image_meta(res)
    dets, counts = [], {l: 0 for l in labels}
    for label, conf, g in vp.masks_to_geoms(res, view):
        if view == "ortho" and not parcel.intersects(g):
            continue
        counts[label] = counts.get(label, 0) + 1
        c = g.centroid
        dets.append({"label": label, "confidence": round(conf, 2), "lat": round(c.y, 6), "lon": round(c.x, 6),
                     "area_sqft": round(vp.area_m2(g) * vp.M2_TO_SF) if view == "ortho" else None})
    return {"id": int(id), "view": view, "labels": labels, "threshold": threshold, "date": meta["date"],
            "collection": meta["collection"], "counts": counts, "detections": dets,
            "source": "Vexcel elements/detect (SAM3)",
            "note": ("Open-vocabulary detection: counts are what SAM3 saw on the capture day, not a census."
                     + (" Oblique positions are approximate." if view != "ortho" else ""))}


def imagery(id, view="ortho", year=None):
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    w, parcel = _parcel(id)
    if parcel is None:
        raise KeyError(f"no Vexcel parcel at {id}")
    env = parcel.envelope.wkt
    cols = vx.collections("ortho", env)
    cols = cols if isinstance(cols, list) else []
    years = sorted({int(c["last"][:4]) for c in cols if c.get("last")})
    IMG.mkdir(parents=True, exist_ok=True)
    note = None
    if view == "ortho":
        pick = None
        if year:
            rank_l = {"urban": 0, "urban-r": 1, "wide-area": 2, "urban-g": 3}
            cands = [c for c in cols if (c.get("last") or "")[:4] == str(year)]
            pick = min(cands, key=lambda c: rank_l.get(c.get("layer"), 9)) if cands else None
            if not pick:
                note = f"no ortho captured in {year}; showing the newest"
        pick = pick or (cols[0] if cols else None)
        coll = pick["collection"] if pick and year and not note else None
        data = vx.ortho(env, 15, None, None, coll)
        date = pick["last"] if pick else None
        collection = pick["collection"] if pick else None
    else:
        data = vx.oblique(env, view, None, 15)
        date, collection = None, None
        note = "Obliques come from the newest oblique collection" + (" (year ignored)" if year else "")
    name = f"{int(id)}_{view}_{collection or 'latest'}.jpg"
    (IMG / name).write_bytes(data)
    return {"id": int(id), "url": f"/media/vexcel/img/{name}", "view": view, "date": date, "collection": collection,
            "available_years": years, "source": "Vexcel ortho/extract" if view == "ortho" else
            "Vexcel oriented/extract", "note": note}


# ------------------------------------------------------------------ HTTP
@router.get("/api/tiles/{z}/{x}/{y}.jpg")
def tiles(z: int, x: int, y: int):
    data, media, layer = vexcel_tiles.tile(z, x, y)
    headers = {"Cache-Control": "public, max-age=86400"}
    if layer:
        headers["X-Tile-Source"] = layer
    return Response(content=data, media_type=media, headers=headers)


@router.get("/api/vexcel/metrics")
def http_metrics():
    return metrics()


@router.get("/api/vexcel/rank")
def http_rank(metric: str, market: Optional[str] = None, top: int = 10):
    try:
        return rank(metric, market, top)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/vexcel/search")
def http_search(text: str, near_id: Optional[int] = None, lat: Optional[float] = None,
                lon: Optional[float] = None, radius_m: float = Query(3000, ge=100, le=5000)):
    try:
        return search(text, near_id, lat, lon, radius_m)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Vexcel search failed: {str(e)[:200]}")


@router.get("/api/vexcel/{pid}/imagery")
def http_imagery(pid: int, view: str = "ortho", year: Optional[int] = None):
    try:
        return imagery(pid, view, year)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Vexcel imagery failed: {str(e)[:200]}")


@router.get("/api/vexcel/{pid}/detect")
def http_detect(pid: int, labels: str, view: str = "ortho", threshold: float = Query(0.3, ge=0.05, le=0.95)):
    try:
        return detect(pid, labels, view, threshold)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Vexcel detect failed: {str(e)[:200]}")


@router.get("/api/vexcel/{pid}")
def http_profile(pid: int, compute: bool = True):
    try:
        prof = profile(pid, compute)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if prof is None:
        raise HTTPException(404, f"no precomputed Vexcel profile for {pid}")
    return prof


