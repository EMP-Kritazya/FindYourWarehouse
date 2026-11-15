"""3D site surface from Vexcel elevation (no new Vexcel calls).

    GET /api/twins/{pid}/terrain        -> {url, texture, bounds:[w,s,e,n], max_h_m, source, res_m}
    GET /api/twins/{pid}/terrain.png    -> terrain-RGB PNG (Mapbox encoding) of the site's nDSM

The exterior twin already holds the nDSM (Vexcel DSM minus DTM = height of buildings, trees and vehicles above the
ground) on the same 0.5 m grid as its ortho JPEG. Draped together in deck.gl's TerrainLayer, they give a photoreal 3D
model of the property. Ground is flattened to 0 (the basemap is flat), heights are clipped to 0-80 m, light speckle
is median-filtered, and the edge tapers to 0 so the patch has no cliff at its border.
"""
import io
import json

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from proinspect.config import BUILD

router = APIRouter()
EXT = BUILD / "twins" / "exterior"
SIZE = 513                    # 2^n + 1, what deck.gl's martini mesher wants
TAPER_M = 12.0


def _frame(pid):
    p = EXT / f"{pid}.json"
    if not p.exists() or not (EXT / f"{pid}.npz").exists():
        raise HTTPException(404, f"no exterior twin for {pid}")
    return json.loads(p.read_text())["frame"]


def bounds(frame):
    """Same placement the map uses for the flat ortho (MapView.tsx), so photo and heights line up."""
    import math
    d_lat = frame["half_m"] / 111320
    d_lon = frame["half_m"] / (111320 * math.cos(math.radians(frame["lat0"])))
    return [frame["lon0"] - d_lon, frame["lat0"] - d_lat, frame["lon0"] + d_lon, frame["lat0"] + d_lat]


def terrain_rgb(pid):
    from PIL import Image
    from scipy import ndimage
    out = EXT / f"{pid}_terrain_rgb.png"
    npz = EXT / f"{pid}.npz"
    if out.exists() and out.stat().st_mtime >= npz.stat().st_mtime:
        return out.read_bytes()
    h = np.load(npz)["ndsm"].astype(np.float32)               # row 0 = north, like the ortho
    h = np.nan_to_num(h, nan=0.0)
    h = ndimage.median_filter(np.clip(h, 0.0, 80.0), size=3)
    h[h < 0.4] = 0.0                                           # ground and kerbs: flat
    n = h.shape[0]
    h = ndimage.zoom(h, SIZE / n, order=1)[:SIZE, :SIZE]
    if h.shape != (SIZE, SIZE):
        h = np.pad(h, ((0, SIZE - h.shape[0]), (0, SIZE - h.shape[1])))
    res = 2 * _frame(pid)["half_m"] / SIZE
    k = max(1, int(TAPER_M / res))
    ramp = np.minimum(np.arange(SIZE), np.arange(SIZE)[::-1]) / k
    w = np.clip(np.minimum.outer(ramp, ramp), 0, 1)
    h = h * w
    v = np.round((h + 10000.0) * 10.0).astype(np.uint32)       # Mapbox terrain-RGB
    rgb = np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], axis=-1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG")
    out.write_bytes(buf.getvalue())
    return buf.getvalue()


@router.get("/api/twins/{pid}/terrain")
def terrain_meta(pid: int):
    f = _frame(pid)
    if not (EXT / f"{pid}_ortho.jpg").exists():
        raise HTTPException(404, f"no ortho for {pid}")
    h = np.load(EXT / f"{pid}.npz")["ndsm"]
    return {"url": f"/api/twins/{pid}/terrain.png", "texture": f"/media/twins/exterior/{pid}_ortho.jpg",
            "bounds": bounds(f), "res_m": round(2 * f["half_m"] / SIZE, 3),
            "max_h_m": round(float(np.nanpercentile(np.asarray(h, np.float32), 99.5)), 1),
            "source": "Vexcel DSM minus DTM (nDSM) draped with the Vexcel ortho"}


@router.get("/api/twins/{pid}/terrain.png")
def terrain_png(pid: int):
    _frame(pid)
    return Response(terrain_rgb(pid), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
