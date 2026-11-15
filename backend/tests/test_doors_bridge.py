"""Aerial dock-store doors are drawn inside a Matterport scan only when the scan-to-aerial alignment is trustworthy,
and only on the building's OUTER wall (never on containers or racks in the middle of the floor)."""
import json

import pytest

from proinspect import doors
from proinspect.config import BUILD

pytestmark = pytest.mark.skipif(not (BUILD / "docks" / "9235035.json").exists()
                                or not (BUILD / "twins" / "interior" / "1n5Ch6wDpH9.npz").exists(),
                                reason="needs the built dock store + interior twin")


def test_weak_unconfirmed_alignment_places_no_doors():
    # 9851 Fallbrook Pines Dr: automatic fit confidence 0.07, ambiguous by ~32 m -> nothing in 3D until a person confirms
    ds, basis = doors.from_dock_store("1n5Ch6wDpH9", 9235035)
    assert ds == []
    assert basis.get("needs_alignment") is True


def test_scan_verified_wall_places_doors_on_the_outer_wall():
    ds, basis = doors.from_dock_store("QHMtBN5Nnrc", 12896757)
    assert ds, basis
    G = doors._grid("QHMtBN5Nnrc")
    from scipy import ndimage
    G = {**G, "floor": ndimage.binary_fill_holes(G["floor"])}
    for d in ds:
        x, y = doors.showcase_to_rot(d["center"]["x"], d["center"]["z"], G["theta"])
        assert doors._boundary_dist(G, x, y) <= doors.SCAN_EDGE_TOL_M + 0.5


def test_scan_outline_is_a_closed_polygon():
    o = doors.scan_outline("1n5Ch6wDpH9")
    assert o and len(o) >= 4 and o[0] == o[-1]


def test_person_confirmed_alignment_with_shift_is_used(tmp_path, monkeypatch):
    from proinspect import docks
    store = json.loads((BUILD / "docks" / "9235035.json").read_text())
    store["registration"] = {**store["registration"], "confirmed_by_user": True, "flip": 0, "shift": [0.0, 0.0]}
    monkeypatch.setattr(docks, "load", lambda pid: store)
    ds, basis = doors.from_dock_store("1n5Ch6wDpH9", 9235035)
    assert basis["confirmed_by_user"] is True
    assert "needs_alignment" not in basis
