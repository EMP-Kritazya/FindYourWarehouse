import numpy as np

from proinspect.twin import interior
from proinspect.twin.interior import cluster_1d, fit_spacing


def test_spacing_prefers_true_grid_over_submultiple():
    lines = [(x, 3) for x in (0.0, 15.24, 30.48, 45.72)]          # 50 ft grid, also fits 25 ft
    fit = fit_spacing(lines)
    assert abs(fit["spacing_m"] - 15.24) < 0.1


def test_spacing_with_a_missing_line():
    lines = [(x, 3) for x in (0.0, 12.0, 36.0, 48.0)]             # 12 m grid, line at 24 m not seen
    assert abs(fit_spacing(lines)["spacing_m"] - 12.0) < 0.1


def test_too_few_lines_gives_no_spacing():
    assert fit_spacing([(0.0, 2), (12.0, 2)]) is None


def test_cluster_1d_groups_nearby_positions():
    assert [n for _, n in cluster_1d([0.0, 0.1, 0.2, 10.0, 10.3])] == [3, 2]


def test_stub_mesh_is_rejected(monkeypatch):
    big = "\n".join(f"v {x} {y} 0" for x in range(0, 60, 2) for y in range(0, 60, 2))
    faces = "\n".join(f"f {i} {i+1} {i+30}" for i in range(1, 12001))
    meshes = {"500k": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3", "50k": big + "\n" + faces}
    monkeypatch.setattr(interior, "mesh_obj", lambda m, r: meshes[r])
    V, F, res, note = interior.load_mesh("x")
    assert res == "50k" and "unusable" in note
