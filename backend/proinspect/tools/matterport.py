"""Matterport Model API (GraphQL): rooms, panos, mesh, geocoordinates. Adapted from SiteWalk.

Auth: basic auth with MATTERPLOT_API_KEY (user) and MATTERPLOT_API_TOKEN (password).
Signed asset URLs expire, so we cache bytes and text, never URLs.
Model API axes are (+X, +Y, +Z up); the Showcase SDK uses (+X, -Z, +Y).
"""
import io
import math
import os

import numpy as np
import requests
from PIL import Image

from proinspect import config  # noqa: F401  (loads .env)
from proinspect.cache import cached

URL = "https://api.matterport.com/api/models/graph"


def _gql(query, variables=None):
    auth = (os.environ["MATTERPLOT_API_KEY"], os.environ["MATTERPLOT_API_TOKEN"])
    r = requests.post(URL, auth=auth, json={"query": query, "variables": variables or {}}, timeout=90)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(f"Matterport API: {d['errors'][0].get('message')}")
    return d["data"]


@cached("matterport")
def model_overview(model_id):
    """Rooms (labels, floor area, ceiling height, panos), pano positions, floors, geocoordinates."""
    m = _gql("""query($id: ID!) { model(id: $id) {
        id name created
        geocoordinates { latitude longitude altitude }
        floors { id label sequence }
        rooms { id label classifications { label confidence }
                dimensions(units: imperial) { areaFloor height } panoLocations { id } }
        panoLocations { id position { x y z } } } }""", {"id": model_id})["model"]
    rooms = []
    for r in m.get("rooms") or []:
        labels = [c["label"] for c in r.get("classifications") or [] if c.get("label") != "Void"]
        dims = r.get("dimensions") or {}
        rooms.append({"room_id": r["id"], "label": labels[0] if labels else (r.get("label") or "Unlabeled"),
                      "also": labels[1:], "sqft": round(dims.get("areaFloor") or 0) or None,
                      "ceiling_ft": round(dims["height"], 1) if dims.get("height") else None,
                      "pano_ids": [p["id"] for p in r.get("panoLocations") or []]})
    panos = m.get("panoLocations") or []
    return {"model_id": model_id, "name": m.get("name"), "created": m.get("created"),
            "geo": m.get("geocoordinates"), "floors": m.get("floors") or [], "rooms": rooms,
            "pano_count": len(panos),
            "pano_positions": {p["id"]: p.get("position") for p in panos}}


@cached("matterport")
def mesh_obj(model_id, res="50k"):
    """Raw OBJ text of the textured mesh at a resolution (50k, 500k, ...)."""
    m = _gql("""query($id: ID!){ model(id:$id){ assets{ meshes{ format resolution url } } } }""",
             {"id": model_id})["model"]
    meshes = m["assets"]["meshes"] or []
    pick = next((x for x in meshes if x["resolution"] == res), None) or (meshes[0] if meshes else None)
    if not pick:
        raise ValueError(f"model {model_id} has no mesh assets")
    return requests.get(pick["url"], timeout=180).text


def parse_obj(text):
    V, F = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            V.append([float(a) for a in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
            for k in range(1, len(idx) - 1):
                F.append([idx[0] - 1, idx[k] - 1, idx[k + 1] - 1])
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def normals(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1) / 2
    with np.errstate(invalid="ignore", divide="ignore"):
        n = n / (2 * area[:, None])
    return np.nan_to_num(n), area


def wall_axes(V, F):
    """Dominant horizontal wall direction (radians in [0, pi/2)), area-weighted over vertical faces."""
    n, area = normals(V, F)
    vert = np.abs(n[:, 2]) < 0.2
    ang = np.arctan2(n[vert, 1], n[vert, 0]) % (math.pi / 2)
    w = area[vert]
    s, c = np.sum(w * np.sin(4 * ang)), np.sum(w * np.cos(4 * ang))
    return (math.atan2(s, c) / 4) % (math.pi / 2)


@cached("matterport")
def pano_mosaic(model_id, pano_id, resolution="2k"):
    """JPEG bytes: the 4 horizontal skybox faces as a 2x2 mosaic (1600 px)."""
    m = _gql("""query($id: ID!) { model(id: $id) { panoLocations { id skybox(resolution: "%s") { children } } } }"""
             % resolution, {"id": model_id})["model"]
    pano = next((p for p in m["panoLocations"] if p["id"] == pano_id), None)
    if not pano or not (pano.get("skybox") or {}).get("children"):
        raise ValueError(f"pano {pano_id} has no skybox images")
    faces = [Image.open(io.BytesIO(requests.get(u, timeout=60).content)).convert("RGB")
             for u in pano["skybox"]["children"][1:5]]  # 0 = up, 1-4 = around, 5 = down
    tile = 800
    mosaic = Image.new("RGB", (tile * 2, tile * 2))
    for i, f in enumerate(faces):
        mosaic.paste(f.resize((tile, tile)), ((i % 2) * tile, (i // 2) * tile))
    buf = io.BytesIO()
    mosaic.save(buf, "JPEG", quality=85)
    return buf.getvalue()
