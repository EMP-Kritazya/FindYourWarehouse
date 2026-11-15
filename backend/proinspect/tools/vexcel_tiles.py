"""Vexcel ortho basemap tiles (WMTS, GoogleMapsCompatible-style) proxied for the browser.

Verified 2026-11-14 against GetCapabilities (parsed quietly: the document EMBEDS THE API KEY in its URLs, so it is
never logged, stored or forwarded):
  * layers urban, urban-r, urban-g, wide-area, graysky; each layer's TileMatrixSet has the SAME id as the layer;
  * every set is EPSG:3857, 256 px tiles, TopLeftCorner (-20037508.34, 20037508.35), TileMatrix ids "0".."22",
    i.e. exactly XYZ/slippy: z -> TileMatrix, x -> TileCol, y -> TileRow (no y flip);
  * each layer declares per-zoom row/col limits (its coverage bbox); tiles outside them are not requested.
  * Low zooms (<= ~12) come back as PNG with transparent no-coverage areas; high zooms as JPEG.
  * A uniform tile is NOT necessarily blank (a white warehouse roof at z20 has std ~1), so "blank" means
    fully transparent or exactly constant.

    tile(z, x, y) -> (bytes, media_type, layer | None)     disk-cached under cache/tiles/z/x/y.{jpg,png,none}
Layer order: urban (7.5 cm, newest), then wide-area (15 cm), then urban-r. No coverage -> transparent 256 PNG.
"""
import io
import json
import os
import random
import re
import threading
import time

import requests
from PIL import Image

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.cache import OFFLINE
from proinspect.config import CACHE_DIR

WMTS = "https://api.vexcelgroup.com/v2/ortho/wmts"
LAYERS = ["urban", "wide-area", "urban-r"]
TILE_DIR = CACHE_DIR / "tiles"
LIMITS_FILE = TILE_DIR / "_limits.json"
_SESSION = requests.Session()
_SEM = threading.Semaphore(6)          # concurrent upstream tile fetches
_LOCK = threading.Lock()
_limits = None


def _transparent_png():
    b = io.BytesIO()
    Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(b, "PNG", optimize=True)
    return b.getvalue()


EMPTY_PNG = _transparent_png()


def _key():
    k = os.environ.get("VEXCEL_API_KEY")
    if not k:
        raise RuntimeError("VEXCEL_API_KEY missing from .env")
    return k


def _get(params, timeout=30):
    for attempt in range(4):
        r = _SESSION.get(WMTS, params=params, headers={"X-API-KEY": _key()}, timeout=timeout)
        if r.status_code != 429 or attempt == 3:
            return r
        time.sleep(min(2 ** attempt, 8) + random.random())


def parse_limits(xml_text):
    """Capabilities XML -> {layer: {z: [minRow, maxRow, minCol, maxCol]}}. Only ids and integers are kept,
    so nothing from the document's URLs (which carry the key) survives."""
    out = {}
    for m in re.finditer(r"<TileMatrixSetLink>\s*<TileMatrixSet>([^<]+)</TileMatrixSet>(.*?)</TileMatrixSetLink>",
                         xml_text, re.S):
        lim = {}
        for t in re.finditer(r"<TileMatrix>(\d+)</TileMatrix>\s*<MinTileRow>(\d+)</MinTileRow>\s*"
                             r"<MaxTileRow>(\d+)</MaxTileRow>\s*<MinTileCol>(\d+)</MinTileCol>\s*"
                             r"<MaxTileCol>(\d+)</MaxTileCol>", m.group(2)):
            lim[int(t.group(1))] = [int(t.group(i)) for i in range(2, 6)]
        out[m.group(1).strip()] = lim
    return out


def limits():
    """Per-layer coverage limits, read once from GetCapabilities and stored WITHOUT any URL."""
    global _limits
    if _limits is not None:
        return _limits
    with _LOCK:
        if _limits is not None:
            return _limits
        if LIMITS_FILE.exists():
            raw = json.loads(LIMITS_FILE.read_text())
            _limits = {L: {int(z): v for z, v in d.items()} for L, d in raw.items()}
            return _limits
        if OFFLINE:
            _limits = {}
            return _limits
        try:
            r = _get({"SERVICE": "WMTS", "REQUEST": "GetCapabilities"}, timeout=60)
            parsed = parse_limits(r.text) if r.ok else {}
        except requests.RequestException:
            parsed = {}
        if parsed:
            TILE_DIR.mkdir(parents=True, exist_ok=True)
            LIMITS_FILE.write_text(json.dumps(parsed))
        _limits = parsed
        return _limits


def in_coverage(layer, z, x, y):
    lim = limits().get(layer)
    if not lim:                       # unknown limits: let the server decide
        return True
    b = lim.get(z)
    if b is None:
        return False
    return b[0] <= y <= b[1] and b[2] <= x <= b[3]


def is_blank(data):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return True
    if im.mode in ("RGBA", "LA", "P"):
        a = im.convert("RGBA").getchannel("A")
        if a.getextrema()[1] == 0:
            return True
    lo_hi = im.convert("RGB").getextrema()
    return all(lo == hi for lo, hi in lo_hi)


def _fetch(layer, z, x, y):
    params = {"service": "wmts", "request": "getTile", "layer": layer, "Style": "RGB", "TileMatrixSet": layer,
              "TileMatrix": z, "TileRow": y, "TileCol": x, "format": "image/jpeg"}
    with _SEM:
        r = _get(params)
    if not r.ok:
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0]
    if not ctype.startswith("image/") or is_blank(r.content):
        return None
    return r.content, ctype


def _paths(z, x, y):
    d = TILE_DIR / str(z) / str(x)
    return d, {"jpg": d / f"{y}.jpg", "png": d / f"{y}.png", "none": d / f"{y}.none"}


def tile(z, x, y):
    """-> (bytes, media_type, layer|None). Never raises for coverage gaps; returns a transparent PNG."""
    if z < 0 or z > 22 or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return EMPTY_PNG, "image/png", None
    d, p = _paths(z, x, y)
    if p["jpg"].exists():
        return p["jpg"].read_bytes(), "image/jpeg", "cache"
    if p["png"].exists():
        return p["png"].read_bytes(), "image/png", "cache"
    if p["none"].exists() or OFFLINE:
        return EMPTY_PNG, "image/png", None
    for layer in LAYERS:
        if not in_coverage(layer, z, x, y):
            continue
        try:
            got = _fetch(layer, z, x, y)
        except requests.RequestException:
            return EMPTY_PNG, "image/png", None            # transient: don't cache a miss
        if got:
            data, ctype = got
            d.mkdir(parents=True, exist_ok=True)
            out = p["png"] if ctype == "image/png" else p["jpg"]
            out.write_bytes(data)
            return data, ("image/png" if ctype == "image/png" else "image/jpeg"), layer
    d.mkdir(parents=True, exist_ok=True)
    p["none"].write_bytes(b"")
    return EMPTY_PNG, "image/png", None


def lonlat_to_tile(lat, lon, z):
    import math
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y
