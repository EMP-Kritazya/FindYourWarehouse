"""Vexcel API 2.0 for warehouses: the outside of the building. Every call is disk-cached.

Low-level helpers are adapted from vexcel_quickstart.py / SiteWalk:
    property_extract(lat, lon)          Elements attributes for the parcel + structures (precomputed)
    ortho(wkt, ...)                     ortho JPEG bytes
    oblique_directions(wkt)             which oblique views exist (newest collection)
    oblique(wkt, direction, ...)        oblique JPEG bytes
    detect_raw(wkt, labels, view)       SAM3 open-vocabulary detection
    embedding_search(lat, lon, text)    semantic tile search
    collections(product, wkt)           capture history for any product (ortho, oblique, dsm, dtm, ...)
Note: WKT is POINT(lon lat).
"""
import base64
import io
import math
import os
import random
import time

import requests
from PIL import Image
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.cache import cached

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
DIRECTIONS = ["oblique-north", "oblique-south", "oblique-east", "oblique-west"]
VIEW_AXES = {"ortho": ((0, 1), (1, 0)), "oblique-north": ((0, 1), (1, 0)),
             "oblique-south": ((0, -1), (-1, 0)), "oblique-east": ((1, 0), (0, -1)),
             "oblique-west": ((-1, 0), (0, 1))}


def _headers():
    key = os.environ.get("VEXCEL_API_KEY")
    if not key:
        raise RuntimeError("VEXCEL_API_KEY missing from .env")
    return {"X-API-KEY": key}


def send(method, path, **kw):
    for attempt in range(6):
        r = requests.request(method, f"{API}/{path}", headers=_headers(), **kw)
        if r.status_code != 429 or attempt == 5:
            return r
        time.sleep(min(2 ** attempt, 30) + random.random())


def post_json(path, body):
    r = send("POST", path, json=body, timeout=300)
    if not r.ok:
        raise RuntimeError(f"/{path} failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def post_bytes(path, body):
    r = send("POST", path, json=body, timeout=300)
    if not r.ok:
        raise RuntimeError(f"/{path} failed ({r.status_code}): {r.text[:300]}")
    return r.content


def to_wkt(geometry):
    return shapely_wkt.loads(geometry).wkt if isinstance(geometry, str) else shape(geometry).wkt


def square_around(lat, lon, half_m):
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def mask_to_wkt(mask_png_b64, bbox_wkt, view):
    """Detection mask picture -> polygon in map coordinates (from vexcel_quickstart.py)."""
    mask = Image.open(io.BytesIO(base64.b64decode(mask_png_b64))).convert("L")
    w, h = mask.size
    k = min(1.0, 64 / max(w, h))
    mask = mask.resize((max(1, round(w * k)), max(1, round(h * k))))
    w, h = mask.size
    px = mask.load()
    runs = []
    for y in range(h):
        x = 0
        while x < w:
            if px[x, y] > 128:
                s0 = x
                while x < w and px[x, y] > 128:
                    x += 1
                runs.append(box(s0, y, x, y + 1))
            else:
                x += 1
    if not runs:
        return None
    shp = unary_union(runs).simplify(0.6)
    if shp.geom_type != "Polygon":
        shp = max(shp.geoms, key=lambda g: g.area)
    quad = list(shapely_wkt.loads(bbox_wkt).exterior.coords)[:4]
    cx, cy = sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4
    up, right = VIEW_AXES[view]

    def corner(su, sr):
        return max(quad, key=lambda p: su * ((p[0] - cx) * up[0] + (p[1] - cy) * up[1])
                   + sr * ((p[0] - cx) * right[0] + (p[1] - cy) * right[1]))
    tl, tr, br, bl = corner(1, -1), corner(1, 1), corner(-1, 1), corner(-1, -1)

    def to_map(x, y):
        u, v = x / w, y / h
        return tuple((1 - v) * ((1 - u) * tl[i] + u * tr[i]) + v * ((1 - u) * bl[i] + u * br[i]) for i in (0, 1))
    return Polygon([to_map(x, y) for x, y in shp.exterior.coords]).wkt


@cached("vexcel")
def property_extract(lat, lon):
    r = send("GET", "property/extract", timeout=120, params={
        "wkt": f"POINT({lon} {lat})", "srid": 4326, "layer": LAYERS, "latest": "true",
        "include": "precomputed", "unit-system": "imperial"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


@cached("vexcel")
def ortho(wkt, buffer_m=10, drawing=None, size=None, collection=None):
    body = {"wkt": wkt, "srid": 4326, "layer": LAYERS, "buffer-meter": buffer_m,
            "crop": "bounding_box", "image-format": "jpeg"}
    if drawing:
        body["drawing"] = drawing
    if size:
        body["destination-dimensions"] = size
    if collection:
        body["collection"] = collection
    return post_bytes("ortho/extract", body)


@cached("vexcel")
def oblique_directions(wkt):
    r = send("GET", "oriented/collections", timeout=120, params={
        "wkt": wkt, "srid": 4326, "layer": "urban", "product-type": "oblique",
        "include": "product-type,collection,last-capture-date"})
    if r.status_code == 404 or not r.ok:
        return []
    feats = r.json()["features"]
    newest = max((f["properties"]["last-capture-date"] for f in feats), default=None)
    have = {f["properties"]["product-type"] for f in feats if f["properties"]["last-capture-date"] == newest}
    return [d for d in DIRECTIONS if d in have]


@cached("vexcel")
def oblique(wkt, direction, drawing=None, buffer_m=10):
    body = {"wkt": wkt, "srid": 4326, "layer": "urban", "product-type": direction, "rotation": "natural",
            "buffer-meter": buffer_m, "crop": "bbox", "image-format": "jpeg/png"}
    if drawing:
        body["drawing"] = drawing
    return post_bytes("oriented/extract", body)


@cached("vexcel")
def detect_raw(wkt, labels, view="ortho", threshold=0.5):
    if view == "ortho":
        img = {"endpoint": "ortho/extract",
               "request": {"layer": LAYERS, "srid": 4326, "wkt": wkt, "crop": "bounding_box"}}
    else:
        img = {"endpoint": "oriented/extract",
               "request": {"layer": "urban", "srid": 4326, "wkt": wkt, "product-type": view,
                           "rotation": "natural", "crop": "bbox"}}
    return post_json("elements/detect", {
        "wkt": wkt, "srid": 4326, "buffer-meter": 0, "images": [img],
        "model-input": {"elements": [{"label": l, "model": "sam3", "detection-threshold": threshold}
                                     for l in labels],
                        "include": ["bbox", "confidence", "pixel-mask", "pixel-bbox"]}})


@cached("vexcel")
def embedding_search(lat, lon, text, half_m=1500, top=10):
    area = square_around(lat, lon, half_m)
    res = post_json("embedding/extract", {"wkt": area, "srid": 4326, "search-text": text,
                                          "include": "score,geometry,collection",
                                          "total-records": top, "page-size": top})
    out = []
    for f in res.get("features", []):
        c = shapely_wkt.loads(to_wkt(f["geometry"])).centroid
        out.append({"lat": c.y, "lon": c.x, "score": f["properties"].get("score"),
                    "tile_wkt": to_wkt(f["geometry"])})
    return out


@cached("vexcel")
def collections(product, wkt, layer=LAYERS):
    """Capture history for a product at an area: [{collection, layer, first, last}], newest first.
    Returns {"status": code} when the product isn't reachable with this key."""
    r = send("GET", f"{product}/collections", timeout=120, params={
        "wkt": wkt, "srid": 4326, "layer": layer,
        "include": "collection,first-capture-date,last-capture-date,layer"})
    if not r.ok:
        return {"status": r.status_code, "body": r.text[:200]}
    out = [{"collection": f["properties"].get("collection"), "layer": f["properties"].get("layer"),
            "first": (f["properties"].get("first-capture-date") or "")[:10],
            "last": (f["properties"].get("last-capture-date") or "")[:10]}
           for f in r.json().get("features", [])]
    return sorted(out, key=lambda c: c["last"], reverse=True)
