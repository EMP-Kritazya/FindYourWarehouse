"""Fusion: registering a scan floor to the aerial footprint and placing doors where the obliques saw them."""
import json
import math

import numpy as np
import pytest
from shapely.geometry import Polygon

from proinspect.sim import site as site_mod
from proinspect.twin import fuse
from proinspect.twin.exterior import _edges

ORIGIN, THETA, CELL = (-12.0, 6.0), 0.4, 0.25
PHI, T = 0.3, (40.0, -25.0)


def l_floor():
    f = np.zeros((172, 230), bool)
    f[5:165, 3:223] = True                                  # 40 x 55 m
    f[85:165, 150:223] = False                      # notch: 20 x 18.25 m corner removed
    return f


def angle_err(a, b):
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def to_enu(x, y):
    c, s = math.cos(PHI), math.sin(PHI)
    return c * x - s * y + T[0], s * x + c * y + T[1]


def floor_polygon_rot(f):
    """Exact outline of the L floor in the rot frame (cell edges)."""
    x0, y0 = ORIGIN
    i0, i1, j0, j1 = 5, 165, 3, 223
    ni, nj = 85, 150
    pts = [(i0, j0), (i1, j0), (i1, nj), (ni, nj), (ni, j1), (i0, j1)]
    return [(x0 + i * CELL, y0 + j * CELL) for i, j in pts]


def make_ext(floor, doors_rot_x=(), symmetric=False):
    poly_rot = floor_polygon_rot(floor)
    if symmetric:
        xs, ys = zip(*poly_rot)
        poly_rot = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    fp = Polygon([to_enu(x, y) for x, y in poly_rot]).buffer(0.3, join_style=2)
    coords = [list(p) for p in fp.exterior.coords]
    edges = _edges(Polygon(coords), 0)
    # the facade whose outward normal is the scan's 'S' side (rot frame -y) mapped to ENU
    want = (math.sin(PHI), -math.cos(PHI))
    ei = max(range(len(edges)), key=lambda i: edges[i]["n"][0] * want[0] + edges[i]["n"][1] * want[1])
    y_wall = ORIGIN[1] + 3 * CELL - 0.3
    items = []
    for x in doors_rot_x:
        ex, ey = to_enu(x, y_wall)
        items.append({"edge": ei, "building": 0, "facing": edges[ei]["facing"], "x": ex, "y": ey,
                      "class": "dock_high", "confidence": 0.7})
    return {"frame": {"lat0": 29.0, "lon0": -95.0}, "buildings": [{"index": 0, "footprint_local": coords}],
            "doors": {"items": items}, "courts": [], "parcel_mismatch": False}


@pytest.fixture
def scan(tmp_path, monkeypatch):
    f = l_floor()
    d = tmp_path / "twins" / "interior"
    d.mkdir(parents=True)
    np.savez_compressed(d / "FUSE.npz", floor=f, free=f, structure=np.zeros_like(f), origin=np.array(ORIGIN),
                        theta=np.array([THETA]), grid=np.array([CELL]))
    (d / "FUSE.json").write_text(json.dumps({"clear": {"p5_ft": 30.0}, "deck_ft": 32.0}))
    (tmp_path / "twins" / "exterior").mkdir(parents=True)
    monkeypatch.setattr(fuse, "BUILD", tmp_path)
    monkeypatch.setattr(site_mod, "BUILD", tmp_path)
    monkeypatch.setattr(fuse, "_geo_prior", lambda *a: None)
    monkeypatch.setattr(fuse, "wall_heights", lambda *a: None)
    return tmp_path, f


def test_registration_recovers_a_known_placement(scan):
    _, f = scan
    reg = fuse.register(1, "FUSE", make_ext(f))
    assert reg["ok"], reg["why"]
    assert angle_err(reg["phi"], PHI) < 0.02
    # a scan point lands where the known transform puts it (within the 0.5 m raster)
    x, y = ORIGIN[0] + 20.0, ORIGIN[1] + 10.0
    ex, ey = fuse.rot_to_enu(reg, x, y)
    assert math.dist((ex[0], ey[0]), to_enu(x, y)) < 1.0
    assert reg["margin"] >= fuse.MIN_MARGIN and reg["inside"] > 0.95


def test_doors_land_on_the_matching_scan_wall(scan):
    tmp, f = scan
    xs = [ORIGIN[0] + 5 * CELL + d for d in (8.0, 16.0, 24.0)]
    (tmp / "twins" / "exterior" / "1.json").write_text(json.dumps(make_ext(f, xs)))
    site, rep = fuse.fused_site(1, "FUSE")
    assert rep["doors_source"] == "vexcel" and rep["dock_side"] == "S"
    assert [d.source for d in site.doors] == ["vexcel"] * 3
    assert [d.x for d in site.doors] == pytest.approx([8.0, 16.0, 24.0], abs=1.0)
    assert any("Vexcel oblique" in a and "registration confidence" in a for a in site.assumptions)


def test_symmetric_scan_falls_back_to_assumed_doors(scan, monkeypatch):
    tmp, _ = scan
    f = np.zeros((172, 230), bool)
    f[5:165, 3:223] = True
    np.savez_compressed(tmp / "twins" / "interior" / "FUSE.npz", floor=f, free=f, structure=np.zeros_like(f),
                        origin=np.array(ORIGIN), theta=np.array([THETA]), grid=np.array([CELL]))
    (tmp / "twins" / "exterior" / "2.json").write_text(json.dumps(make_ext(f, [ORIGIN[0] + 10], symmetric=True)))
    site, rep = fuse.fused_site(2, "FUSE")
    assert rep["doors_source"] == "assumed" and "ambiguous" in rep["why"]
    assert all(d.source == "assumed" for d in site.doors)
    assert any("doors ASSUMED" in a for a in site.assumptions)


def test_flip_is_resolved_only_by_a_decisive_wall_cue(scan, monkeypatch):
    tmp, _ = scan
    f = np.zeros((172, 230), bool)
    f[5:165, 3:223] = True
    np.savez_compressed(tmp / "twins" / "interior" / "FUSE.npz", floor=f, free=f, structure=np.zeros_like(f),
                        origin=np.array(ORIGIN), theta=np.array([THETA]), grid=np.array([CELL]))
    ext = make_ext(f, [ORIGIN[0] + 10, ORIGIN[0] + 20], symmetric=True)
    walls = {"S": {"full_height": 1.0}, "N": {"full_height": 0.1}, "E": {"full_height": 0.2}, "W": {"full_height": 0.2}}
    monkeypatch.setattr(fuse, "wall_heights", lambda *a: {"sides": walls})
    reg = fuse.register(3, "FUSE", ext)
    assert reg["ok"] and reg["flip"]["decisive"]
    assert angle_err(reg["phi"], PHI) < 0.02
    walls["N"]["full_height"] = 0.9                         # both candidate walls full height: undecided
    reg = fuse.register(3, "FUSE", ext)
    assert not reg["ok"] and not reg["flip"]["decisive"]


def test_no_exterior_twin_is_flagged(scan):
    site, rep = fuse.fused_site(99, "FUSE")
    assert rep["doors_source"] == "assumed"
    assert any("no aerial" in a for a in site.assumptions)
