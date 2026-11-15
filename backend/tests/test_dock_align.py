"""One-click along-wall alignment of aerial dock doors in the scan (12896757 / QHMtBN5Nnrc)."""
import json
import shutil

import pytest

from proinspect.config import BUILD

pytestmark = pytest.mark.skipif(not (BUILD / "docks" / "12896757.json").exists()
                                or not (BUILD / "twins" / "interior" / "QHMtBN5Nnrc.npz").exists(),
                                reason="needs the dock store + interior twin")
PID, MID = 12896757, "QHMtBN5Nnrc"


@pytest.fixture()
def restore(tmp_path):
    p = BUILD / "docks" / f"{PID}.json"
    keep = tmp_path / "keep.json"
    shutil.copy(p, keep)
    yield
    shutil.copy(keep, p)


def test_labels_run_left_to_right_from_outside():
    from proinspect import docks
    st = docks.load(PID)
    south = sorted([d for d in st["doors"] if d["label"].startswith("S")], key=lambda d: int(d["label"][1:]))
    xs = [d["x"] for d in south]
    assert xs == sorted(xs)                      # south wall seen from outside: west (left) to east (right)


def test_click_two_metres_off_moves_that_door_onto_the_click(restore):
    from proinspect import doors
    ds, _ = doors.from_dock_store(MID, PID)
    dock = sorted([x for x in ds if x["kind"] == "dock_high"], key=lambda x: x["center"]["x"])
    d = dock[len(dock) // 2]                     # a door mid-wall, so the 2 m slide keeps it inside the scan
    ax, az = d["b"]["x"] - d["a"]["x"], d["b"]["z"] - d["a"]["z"]
    L = (ax * ax + az * az) ** 0.5
    cx, cz = d["center"]["x"] + 2.0 * ax / L, d["center"]["z"] + 2.0 * az / L    # a real door 2 m along the wall
    out = doors.align_to_click(PID, MID, cx, cz)
    assert out["ok"] and abs(abs(out["moved_m"]) - 2.0) < 0.3
    ds2, basis = doors.from_dock_store(MID, PID)
    moved = next(x for x in ds2 if x["id"] == out["door_id"])
    assert abs(moved["center"]["x"] - cx) < 0.6 and abs(moved["center"]["z"] - cz) < 0.6
    assert basis["confirmed_by_user"] is True


def test_far_click_is_rejected(restore):
    from proinspect import doors
    ds, _ = doors.from_dock_store(MID, PID)
    d = ds[0]
    ax, az = d["b"]["x"] - d["a"]["x"], d["b"]["z"] - d["a"]["z"]
    L = (ax * ax + az * az) ** 0.5
    far = doors.align_to_click(PID, MID, d["center"]["x"] + 200 * ax / L, d["center"]["z"] + 200 * az / L)
    assert not far["ok"]


def test_marked_doors_reach_the_map_after_alignment(restore):
    """Align with one click, mark a door in the scan where the aerial has none -> it appears in the dock store."""
    from proinspect import docks, doors
    ds, _ = doors.from_dock_store(MID, PID)
    dock = sorted([x for x in ds if x["kind"] == "dock_high"], key=lambda x: x["center"]["x"])
    mid = dock[len(dock) // 2]
    assert doors.align_to_click(PID, MID, mid["center"]["x"], mid["center"]["z"])["ok"]
    west = dock[0]                                    # mark a door 12 m further west along the wall (no aerial door)
    ax, az = west["b"]["x"] - west["a"]["x"], west["b"]["z"] - west["a"]["z"]
    L = (ax * ax + az * az) ** 0.5
    ux, uz = ax / L, az / L
    sign = -1 if west["center"]["x"] < mid["center"]["x"] else 1
    cx, cz = west["center"]["x"] + sign * 12 * ux, west["center"]["z"] + sign * 12 * uz
    p = BUILD / "doors" / f"{MID}.json"
    had = p.exists()
    try:
        doors.save(MID, PID, [{"kind": "dock_high", "a": {"x": cx - 1.4 * ux, "y": 0, "z": cz - 1.4 * uz},
                               "b": {"x": cx + 1.4 * ux, "y": 0, "z": cz + 1.4 * uz}}])
        out = doors.sync_marked_to_dock_store(MID, PID)
        assert out["synced"] == 1
        st = docks.load(PID)
        assert any(d["status"] in ("user_added", "confirmed") and any(e.get("type") == "user" for e in d["evidence"])
                   for d in st["doors"])
    finally:
        if not had and p.exists():
            p.unlink()
