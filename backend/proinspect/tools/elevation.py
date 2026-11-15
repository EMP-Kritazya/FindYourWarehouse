"""Vexcel elevation: DSM (every surface: roofs, trees) and DTM (bare ground), by flight year.

Vexcel serves GeoTIFFs (float32, tiled, deflate, EPSG:3857). We decode them with numpy + zlib
(no GDAL), then resample onto a local metric grid centred on the building:

    x = metres east, y = metres north, z = metres above the DTM ground at the centre.

    collections(lat, lon, "dsm")              flights covering the point, newest first
    grid(lat, lon, half_m, res_m, collection) DSM/DTM heights on the local grid (cached)
    heightgrids(pid, years)                   DSM per year, vertical datum aligned to the newest
"""
import math
import struct
import zlib

import numpy as np

from proinspect.cache import cached
from proinspect.tools.vexcel import LAYERS, send, square_around

R_MERC = 6378137.0
NODATA = -9999.0


# ------------------------------------------------------------------ GeoTIFF decoding
_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}


def _tags(b):
    end = ">" if b[:2] == b"MM" else "<"
    off = struct.unpack(end + "I", b[4:8])[0]
    n = struct.unpack(end + "H", b[off:off + 2])[0]
    tags = {}
    for i in range(n):
        e = b[off + 2 + 12 * i: off + 14 + 12 * i]
        tag, typ, cnt = struct.unpack(end + "HHI", e[:8])
        fmt, size = _TYPES.get(typ, ("B", 1))
        nbytes = size * cnt
        data = e[8:8 + nbytes] if nbytes <= 4 else b[struct.unpack(end + "I", e[8:12])[0]:][:nbytes]
        if typ == 2:
            tags[tag] = data.decode(errors="ignore")
        elif typ == 5:
            tags[tag] = struct.unpack(end + "I" * (2 * cnt), data)
        else:
            tags[tag] = struct.unpack(end + fmt * cnt, data)
    return end, tags


def read_geotiff(b):
    """-> (heights float32 [rows, cols] with NaN for no-data, meta). Supports tiled or striped,
    uncompressed or deflate, float32 single band (what Vexcel's dsm/dtm extract returns)."""
    end, t = _tags(b)
    W, H = t[256][0], t[257][0]
    comp = t.get(259, (1,))[0]
    bps = t.get(258, (32,))[0]
    if bps != 32:
        raise ValueError(f"expected float32 elevation, got {bps} bits")
    dt = np.dtype(end + "f4")

    def dec(chunk):
        return zlib.decompress(chunk) if comp in (8, 32946) else chunk

    if 322 in t:  # tiled
        tw, th = t[322][0], t[323][0]
        across = math.ceil(W / tw)
        a = np.full((math.ceil(H / th) * th, across * tw), np.nan, dtype=np.float32)
        for k, (o, c) in enumerate(zip(t[324], t[325])):
            tile = np.frombuffer(dec(b[o:o + c]), dtype=dt).reshape(th, tw)
            r, cc = divmod(k, across)
            a[r * th:(r + 1) * th, cc * tw:(cc + 1) * tw] = tile
    else:  # stripped
        rps = t.get(278, (H,))[0]
        parts = [np.frombuffer(dec(b[o:o + c]), dtype=dt).reshape(-1, W) for o, c in zip(t[273], t[279])]
        a = np.vstack(parts).astype(np.float32)
        del rps
    a = a[:H, :W].copy()
    nod = float(t[42113].strip("\x00 ")) if 42113 in t else NODATA
    a[(a == nod) | (a < -1000)] = np.nan
    sx, sy = t[33550][0], t[33550][1]
    tie = t[33922]  # (i, j, k, X, Y, Z): pixel (i, j) is at map (X, Y)
    meta = {"width": W, "height": H, "sx": sx, "sy": sy, "x0": tie[3] - tie[0] * sx, "y0": tie[4] + tie[1] * sy,
            "crs": t.get(34737, "")}
    xmp = t.get(700)
    if isinstance(xmp, str):
        for key in ("collection", "first-capture-date", "last-capture-date", "camera-technology"):
            tag = f"<Vexcel:{key}>"
            if tag in xmp:
                meta[key] = xmp.split(tag)[1].split("<")[0]
    return a, meta


# ------------------------------------------------------------------ raw cached calls
@cached("vexcel")
def _collections(lat, lon, product):
    r = send("GET", f"{product}/collections", timeout=120, params={
        "wkt": square_around(lat, lon, 30), "srid": 4326, "layer": LAYERS,
        "include": "collection,first-capture-date,last-capture-date,layer"})
    if not r.ok:
        return []
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        out.append({"collection": p.get("collection"), "layer": p.get("layer"),
                    "first": (p.get("first-capture-date") or "")[:10], "last": (p.get("last-capture-date") or "")[:10]})
    return sorted(out, key=lambda c: c["last"], reverse=True)


@cached("vexcel")
def _extract(product, lat, lon, half_m, layer, collection, px):
    body = {"wkt": square_around(lat, lon, half_m), "srid": 4326, "layer": layer,
            "destination-dimensions": f"{px},{px}"}
    if collection:
        body["collection"] = collection
    r = send("POST", f"{product}/extract", json=body, timeout=300)
    if not r.ok:
        raise RuntimeError(f"/{product}/extract failed ({r.status_code}): {r.text[:200]}")
    return r.content


# ------------------------------------------------------------------ public API
def collections(lat, lon, product="dsm"):
    return _collections(round(lat, 6), round(lon, 6), product)


def _merc(lat, lon):
    return R_MERC * math.radians(lon), R_MERC * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def to_local(lat, lon, lat0, lon0):
    """lat/lon -> metres east/north of (lat0, lon0) (equirectangular; < 1 cm error over 1 km)."""
    return ((np.asarray(lon) - lon0) * 111_320.0 * math.cos(math.radians(lat0)),
            (np.asarray(lat) - lat0) * 110_574.0)


def to_latlon(x, y, lat0, lon0):
    return lat0 + np.asarray(y) / 110_574.0, lon0 + np.asarray(x) / (111_320.0 * math.cos(math.radians(lat0)))


def grid(lat, lon, half_m=200, res_m=0.5, collection=None, layer=None, product="dsm"):
    """Heights on a local grid: row 0 = north edge, col 0 = west edge; cell centres at
    x = -half + (c + 0.5) * res, y = half - (r + 0.5) * res. Returns (z, meta)."""
    if collection and not layer:
        layer = next((c["layer"] for c in collections(lat, lon, product) if c["collection"] == collection), None)
    layer = layer or LAYERS
    n = int(round(2 * half_m / res_m))
    # ask Vexcel for a slightly larger picture so the local square is fully covered after reprojection
    px = int(n * 1.25)
    raw = _extract(product, round(lat, 6), round(lon, 6), half_m * 1.2, layer, collection, px)
    src, meta = read_geotiff(raw)
    xs = -half_m + (np.arange(n) + 0.5) * res_m
    ys = half_m - (np.arange(n) + 0.5) * res_m
    X, Y = np.meshgrid(xs, ys)
    la, lo = to_latlon(X, Y, lat, lon)
    mx = R_MERC * np.radians(lo)
    my = R_MERC * np.log(np.tan(np.pi / 4 + np.radians(la) / 2))
    # Vexcel resampled to destination-dimensions: actual pixel size = extent / dims
    sx = meta["sx"] * meta["width"] / src.shape[1]
    sy = meta["sy"] * meta["height"] / src.shape[0]
    fc = (mx - meta["x0"]) / sx - 0.5
    fr = (meta["y0"] - my) / sy - 0.5
    z = _bilinear(src, fr, fc)
    meta.update({"half_m": half_m, "res_m": res_m, "n": n, "lat0": lat, "lon0": lon, "product": product,
                 "layer": layer})
    return z.astype(np.float32), meta


def _bilinear(a, fr, fc):
    h, w = a.shape
    r0 = np.clip(np.floor(fr).astype(int), 0, h - 2)
    c0 = np.clip(np.floor(fc).astype(int), 0, w - 2)
    dr = np.clip(fr - r0, 0, 1)
    dc = np.clip(fc - c0, 0, 1)
    v = (a[r0, c0] * (1 - dr) * (1 - dc) + a[r0 + 1, c0] * dr * (1 - dc)
         + a[r0, c0 + 1] * (1 - dr) * dc + a[r0 + 1, c0 + 1] * dr * dc)
    # fall back to nearest where bilinear touched no-data
    bad = np.isnan(v)
    if bad.any():
        v[bad] = a[np.clip(np.round(fr[bad]).astype(int), 0, h - 1), np.clip(np.round(fc[bad]).astype(int), 0, w - 1)]
    return v


def year_of(c):
    return int(c["last"][:4]) if c.get("last") else None


def pick_collections(lat, lon, years=None, product="dsm"):
    """One collection per calendar year (prefer the 'urban' layer: finest GSD), newest first."""
    best = {}
    rank = {"urban": 0, "urban-r": 1, "wide-area": 2, "urban-g": 3}
    for c in collections(lat, lon, product):
        y = year_of(c)
        if y is None or (years and y not in years):
            continue
        if y not in best or rank.get(c["layer"], 9) < rank.get(best[y]["layer"], 9):
            best[y] = c
    return [best[y] for y in sorted(best, reverse=True)]


def ground(lat, lon, half_m=200, res_m=0.5):
    """Bare-earth DTM on the same grid (newest DTM available)."""
    cs = collections(lat, lon, "dtm")
    if not cs:
        return None, None
    return grid(lat, lon, half_m, res_m, cs[0]["collection"], cs[0]["layer"], "dtm")


def heightgrids(lat, lon, half_m=200, res_m=0.5, years=None):
    """{year: (dsm, meta)} with every year's vertical datum shifted to match the newest
    (median difference over cells that did not change by more than 2 m)."""
    picks = pick_collections(lat, lon, years)
    out = {}
    ref = None
    for c in picks:
        z, meta = grid(lat, lon, half_m, res_m, c["collection"], c["layer"])
        meta["collection"], meta["first"], meta["last"] = c["collection"], c["first"], c["last"]
        if ref is None:
            ref = z
            meta["datum_shift"] = 0.0
        else:
            d = ref - z
            stable = np.abs(d - np.nanmedian(d)) < 2.0
            shift = float(np.nanmedian(d[stable])) if stable.any() else float(np.nanmedian(d))
            z = z + shift
            meta["datum_shift"] = round(shift, 3)
        out[year_of(c)] = (z, meta)
    return out


