"""Dock store v3 (proinspect.docks): store shape, save validation, usable doors, estimate rules, GSD scaling, span
fill and docked-trailer geometry. Synthetic stores only (no network, no build/ files touched except a temp OUT)."""
import json
import math

import pytest

from proinspect import docks
from proinspect.twin import exterior as ex

FRAME = {"lat0": 29.6, "lon0": -95.37}


def _store(costar=None, has_docks=None, doors=()):
    """A 60 x 30 m box: edges as twin.exterior._edges numbers them."""
    twin = {"frame": {**FRAME, "half_m": 120, "res_m": 0.5},
            "buildings": [{"index": 0, "footprint_local": [[-30, -15], [30, -15], [30, 15], [-30, 15], [-30, -15]]}]}
    s = {"property_id": 1, "version": 3, "frame": dict(FRAME), "buildings": docks.buildings_of(twin),
         "gate": None, "registration": None, "doors": list(doors),
         "costar": {"docks": costar, "docks_internal": None, "has_docks": has_docks, "drive_ins": None}}
    return s


def _south(s):
    return next(e for e in s["buildings"][0]["edges"] if e["facing"] == "S")


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(docks, "OUT", tmp_path)
    s = _store(costar=12, has_docks=True)
    e = _south(s)
    s["doors"] = [docks._door(s["frame"], 0, e, 10.0, id="k1", kind="dock_high", kind_conf=0.5,
                              evidence=[{"type": "oblique", "conf": 0.5, "ref": "x", "note": ""}])]
    s["summary"] = docks.summarize(s)
    (tmp_path / "1.json").write_text(json.dumps(s))
    return s


def test_edges_have_outward_normals_and_facing():
    s = _store()
    es = s["buildings"][0]["edges"]
    assert sorted(e["facing"] for e in es) == ["E", "N", "S", "W"]
    e = _south(s)
    assert e["normal"] == [0.0, -1.0] or e["normal"] == [-0.0, -1.0]
    assert math.isclose(e["len_m"], 60.0)


def test_door_position_on_edge_and_lonlat():
    s = _store()
    e = _south(s)
    d = docks._door(s["frame"], 0, e, 12.0)
    assert d["y"] == -15.0 and math.isclose(d["x"], e["a"][0] + e["t"][0] * 12.0, abs_tol=0.01)
    assert abs(d["lat"] - FRAME["lat0"]) < 0.001 and d["status"] == "proposed" and d["kind"] == "unknown"


def test_estimate_rules():
    s = _store(costar=23, has_docks=True)
    assert docks.summarize(s)["estimate"] == {**docks.summarize(s)["estimate"], "value": 23, "status": "verified"}
    s = _store(costar=None, has_docks=None)
    est = docks.summarize(s)["estimate"]
    assert est["status"] == "unknown" and est["value"] is None          # unknown is never 0
    e = _south(s)
    s["doors"] = [docks._door(s["frame"], 0, e, x, kind=k) for x, k in ((5, "dock_high"), (10, "unknown"),
                                                                        (20, "drive_in"))]
    est = docks.summarize(s)["estimate"]
    assert est["status"] == "lower_bound" and est["lo"] == 2 and est["value"] is None and est["hi"] is None
    s = _store(costar=None, has_docks=False)
    assert docks.summarize(s)["estimate"]["value"] == 0                  # CoStar explicitly says no docks


def test_save_validates_and_snaps(tmp_store):
    e = _south(tmp_store)
    body = {"doors": [{"id": "k1", "edge": e["id"], "x": -20.0, "y": -14.2, "kind": "dock_high",
                       "status": "confirmed"},
                      {"edge": e["id"], "s_m": 30.0, "kind": "drive_in"}],
            "gate": {"x": 0, "y": -60}}
    out = docks.save(1, body)
    d0 = out["doors"][0]
    assert d0["y"] == -15.0 and d0["status"] == "confirmed"              # snapped onto the wall line
    assert out["doors"][1]["status"] == "user_added" and out["doors"][1]["id"].startswith("u")
    assert out["gate"]["source"] == "user"
    assert out["summary"]["confirmed"] == 2
    with pytest.raises(docks.DockValidationError, match="within 1.5 m"):
        docks.save(1, {"doors": [{"edge": e["id"], "x": 0.0, "y": -18.0}]})
    with pytest.raises(docks.DockValidationError, match="kind"):
        docks.save(1, {"doors": [{"edge": e["id"], "s_m": 3, "kind": "garage"}]})
    with pytest.raises(docks.DockValidationError, match="status"):
        docks.save(1, {"doors": [{"edge": e["id"], "s_m": 3, "status": "maybe"}]})
    with pytest.raises(docks.DockValidationError, match="edge"):
        docks.save(1, {"doors": [{"edge": 99, "s_m": 3}]})


def test_usable_doors_never_rejected(tmp_store):
    e = _south(tmp_store)
    docks.save(1, {"doors": [{"id": "a", "edge": e["id"], "s_m": 5, "kind": "dock_high", "status": "rejected"},
                             {"id": "b", "edge": e["id"], "s_m": 10, "kind": "dock_high", "status": "proposed"},
                             {"id": "c", "edge": e["id"], "s_m": 15, "kind": "unknown", "status": "confirmed"},
                             {"id": "d", "edge": e["id"], "s_m": 20, "kind": "drive_in", "status": "confirmed"}]})
    u = docks.usable_doors(1)
    assert [d["id"] for d in u] == ["c", "b"]                            # confirmed first, rejected / drive-in out
    assert all(k in u[0] for k in ("x", "y", "normal", "width_ft", "kind", "status"))
    assert [d["id"] for d in docks.usable_doors(1, kinds=("drive_in",))] == ["d"]


def test_response_scale_and_gsd_fix():
    meta = {"pixel-bbox": "100,200,4096,1000", "width": 2048, "height": 500, "min-gsd": 0.07, "max-gsd": 0.07,
            "omega": 0.785, "phi": 0.0}
    assert ex.response_scale(meta) == (2.0, 2.0)
    w, h, tilt = ex._door_metrics({"pixel-bbox": [0, 0, 20, 30]}, meta)
    assert math.isclose(w, 20 * 2 * 0.07)                              # returned px -> native px -> metres
    native = {**meta, "pixel-bbox": "0,0,2048,500"}
    assert ex.response_scale(native) == (1.0, 1.0)
    assert ex.response_scale({}) == (1.0, 1.0)


def test_classify_dock_door_with_seal_is_not_drive_in():
    assert ex._classify(3.7, 3.2, None)[0] == "dock_high"               # 12 x 10.5 ft: dock door + seal
    assert ex._classify(3.8, 4.3, None)[0] == "drive_in"                # 12.5 x 14 ft


def test_span_fill_stays_in_run_and_under_costar():
    s = _store(costar=6, has_docks=True)
    e = _south(s)
    ds = [docks._door(s["frame"], 0, e, x, kind="dock_high") for x in (10.0, 14.2, 22.6)]   # one gap of 2 pitches
    fill = docks.span_fill(s, ds)
    assert len(fill) == 1 and math.isclose(fill[0]["s_m"], 18.4, abs_tol=0.05)
    assert fill[0]["status"] == "inferred" and fill[0]["evidence"][0]["type"] == "costar_span"
    s["costar"]["docks"] = 3                                             # already 3 seen: nothing to add
    assert docks.span_fill(s, ds) == []
    s["costar"]["docks"] = None                                          # no CoStar count: never fill
    assert docks.span_fill(s, ds) == []
    s["costar"]["docks"] = 50
    assert docks.span_fill(s, ds[:1]) == []                              # one door is not a run


def test_merge_pools_evidence():
    s = _store()
    e = _south(s)
    a = docks._door(s["frame"], 0, e, 10.0, kind="unknown",
                    evidence=[{"type": "oblique", "conf": 0.4, "ref": "", "note": ""}])
    b = docks._door(s["frame"], 0, e, 11.5, kind="dock_high", kind_conf=0.85,
                    evidence=[{"type": "trailer", "conf": 0.7, "ref": "", "note": ""}])
    m = docks.merge_along([a, b])
    assert len(m) == 1 and m[0]["kind"] == "dock_high" and m[0]["s_m"] == 11.5
    assert {v["type"] for v in m[0]["evidence"]} == {"oblique", "trailer"}


def test_docked_trailer_geometry(monkeypatch):
    """Nose-in at the south wall -> a dock; parallel to it, or 30 m out in a row -> not a dock."""
    from shapely.geometry import Polygon
    s = _store()
    from proinspect.tools import elevation as el

    def ll(poly):
        pts = []
        for x, y in poly:
            lat, lon = el.to_latlon(x, y, FRAME["lat0"], FRAME["lon0"])
            pts.append((float(lon), float(lat)))
        return Polygon(pts)
    nose_in = ll([(0, -15.5), (2.6, -15.5), (2.6, -31.5), (0, -31.5)])
    parallel = ll([(0, -16), (16, -16), (16, -18.6), (0, -18.6)])
    far_row = ll([(10, -45), (12.6, -45), (12.6, -61), (10, -61)])
    monkeypatch.setattr(docks, "_vehicle_masks",
                        lambda pid, twin: ([("trailer", 0.5, g, "t") for g in (nose_in, parallel, far_row)], []))
    hits, miss = docks.docked_trailers(1, {"frame": FRAME}, s)
    assert len(hits) == 1
    eid, sv, conf, label, note, poly = hits[0]
    assert eid == _south(s)["id"] and "nose-in" in note


def _two_buildings(costar_sqft):
    """Building 0: 60 x 30 m (19,375 SF); building 1: 20 x 10 m (2,153 SF) to its east."""
    twin = {"frame": {**FRAME, "half_m": 120, "res_m": 0.5},
            "buildings": [{"index": 0, "footprint_sqft": 19375, "main": True,
                           "footprint_local": [[-30, -15], [30, -15], [30, 15], [-30, 15], [-30, -15]]},
                          {"index": 1, "footprint_sqft": 2153, "main": False,
                           "footprint_local": [[40, -5], [60, -5], [60, 5], [40, 5], [40, -5]]}]}
    s = {"property_id": 1, "version": 3, "frame": dict(FRAME), "buildings": docks.buildings_of(twin),
         "gate": None, "registration": None, "doors": [],
         "costar": {"docks": None, "docks_internal": None, "has_docks": None, "drive_ins": None,
                    "sqft": costar_sqft, "stories": 1}}
    return s


def _edge_of(s, bid, facing):
    b = next(b for b in s["buildings"] if b["id"] == bid)
    return next(e for e in b["edges"] if e["facing"] == facing)


def test_estimate_counts_only_the_property_building():
    s = _two_buildings(costar_sqft=2000)                 # CoStar's size matches the SMALL building
    assert docks.property_building(s) == 1
    e0, e1 = _edge_of(s, 0, "S"), _edge_of(s, 1, "S")
    s["doors"] = ([docks._door(s["frame"], 0, e0, x, kind="dock_high") for x in (5, 10, 15, 20)]
                  + [docks._door(s["frame"], 1, e1, 8.0, kind="dock_high")])
    sm = docks.summarize(s)
    assert sm["estimate"]["status"] == "lower_bound" and sm["estimate"]["lo"] == 1
    assert "4 more doors" in sm["estimate"]["basis"]
    assert sm["detected_doors"] == 0 and sm["dock_high"] == 5          # parcel-wide keys keep their meaning
    s["costar"]["sqft"] = 20000                          # now it matches the big one
    assert docks.property_building(s) == 0 and docks.summarize(s)["estimate"]["lo"] == 4


def test_implausible_density_is_unknown_not_a_count():
    s = _two_buildings(costar_sqft=19375)
    e0 = _edge_of(s, 0, "S")
    s["doors"] = [docks._door(s["frame"], 0, e0, 1.0 + 3.6 * i, kind="dock_high") for i in range(9)]   # 4.6/10k SF
    est = docks.summarize(s)["estimate"]
    assert est["status"] == "unknown" and est["value"] is None and est["lo"] is None
    assert "per 10,000 SF" in est["basis"]
    s["doors"] = s["doors"][:7]                           # 3.6 / 10k SF: plausible
    assert docks.summarize(s)["estimate"]["status"] == "lower_bound"


def test_geometric_max_caps_doors_on_a_short_facade():
    s = _two_buildings(costar_sqft=None)
    s["costar"]["sqft"] = None
    e = _edge_of(s, 0, "E")                               # 30 m wall: room for 8 + 1 doors at 3.6 m
    s["doors"] = [docks._door(s["frame"], 0, e, 0.5 + 2.9 * i, kind="dock_high") for i in range(10)]
    s["buildings"][0]["footprint_sqft"] = 10 ** 6          # density rule out of the way
    est = docks.summarize(s)["estimate"]
    assert est["status"] == "unknown" and "room for at most" in est["basis"]


def test_same_size_buildings_pick_the_one_under_the_costar_point():
    from proinspect.tools import elevation as el
    s = _two_buildings(costar_sqft=19375)
    s["buildings"][1]["footprint_sqft"] = 19375            # twins
    lat, lon = el.to_latlon(50.0, 0.0, FRAME["lat0"], FRAME["lon0"])
    s["costar"].update(lat=float(lat), lon=float(lon))
    assert docks.property_building(s) == 1


# ------------------------------------------------------------------ proof
def test_fit_boxes_rescales_and_refuses_misframed_images():
    b, note = docks.fit_boxes([[10, 20, 30, 40]], (1000, 500), (500, 250))
    assert b == [[5.0, 10.0, 15.0, 20.0]] and "rescaled" in note
    b, note = docks.fit_boxes([[10, 20, 30, 40]], (1000, 500), (1000, 500))
    assert b == [[10, 20, 30, 40]] and note is None
    b, note = docks.fit_boxes([[10, 20, 30, 40]], (1000, 500), (1000, 600))      # framing differs: never draw
    assert b is None and "not drawn" in note
    assert docks.fit_boxes([[1, 1, 1, 1]], (None, None), (10, 10))[0] is None


def _proof_store(tmp_path, monkeypatch):
    monkeypatch.setattr(docks, "OUT", tmp_path)
    monkeypatch.setattr(docks, "PROOF", tmp_path / "proof")
    s = _store(costar=5, has_docks=True)
    e = _south(s)
    hit = {"crop_id": "c1", "px_bbox": [100, 50, 40, 30], "conf": 0.48, "label": "loading dock door"}
    s["proof_crops"] = {"c1": {"crop_id": "c1", "wkt": "POLYGON((0 0,1 0,1 1,0 0))", "view": "oblique-north",
                               "edge": e["id"], "date": "2026-01-29", "image": "img", "width": 400, "height": 200,
                               "hits": [{k: hit[k] for k in ("px_bbox", "label", "conf")},
                                        {"px_bbox": [200, 50, 40, 30], "label": "roll-up door", "conf": 0.4}]}}
    s["doors"] = [docks._door(s["frame"], 0, e, 10.0, id="k1", kind="dock_high",
                              evidence=[{"type": "oblique", "conf": 0.48, "ref": "facade-crop:oblique-north",
                                         "note": "", "crop_ids": ["c1"], "hits": [hit]}]),
                  docks._door(s["frame"], 0, e, 14.0, id="k2", kind="dock_high", status="inferred",
                              evidence=[{"type": "costar_span", "conf": 0.4, "ref": "costar:5", "note": ""}])]
    s["summary"] = docks.summarize(s)
    (tmp_path / "1.json").write_text(json.dumps(s))
    return s


def test_proof_index_shape_and_no_photo_for_inferred(tmp_path, monkeypatch):
    _proof_store(tmp_path, monkeypatch)
    ix = docks.proof_index(1)["doors"]
    item = ix["k1"][0]
    assert item["crop_id"] == "c1" and item["camera_dir"] == "north" and item["date"] == "2026-01-29"
    assert item["url"] == "/api/docks/1/proof/c1.jpg?door=k1" and item["hits"][0]["conf"] == 0.48
    assert ix["k2"] == {"no_photo": True, "why": docks.NO_PHOTO_INFERRED}


def test_proof_image_draws_with_mocked_vexcel(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from proinspect.tools import vexcel as vx
    _proof_store(tmp_path, monkeypatch)
    calls = []

    def fake_oblique(wkt, view, drawing=None, buffer_m=10):
        calls.append((view, buffer_m))
        b = io.BytesIO()
        Image.new("RGB", (200, 100), (255, 255, 255)).save(b, "JPEG")      # half the detection size
        return b.getvalue()
    monkeypatch.setattr(vx, "oblique", fake_oblique)
    p = docks.proof_image(1, "c1", "k1")
    assert p.exists() and calls == [("oblique-north", 0)]
    im = Image.open(p).convert("RGB")
    assert im.width == 200 and im.height > 100                              # footer added
    r, g, b = im.getpixel((50, 25))                                         # teal box edge at 100/2, 50/2
    assert g > 150 and b > 150 and r < 150
    docks.proof_image(1, "c1", "k1")
    assert len(calls) == 1                                                  # rendered once


def test_osm_footprint_replaces_a_wrong_parcel(monkeypatch):
    """Vexcel parcel at the CoStar point is the wrong one: use the OSM building under the point when its size matches."""
    from proinspect import docks as D
    from proinspect.yard import roads
    lat0, lon0 = 29.94, -95.33
    dlat, dlon = 100 / 111320.0, 150 / (111320.0 * 0.866)          # ~200 m x 300 m = 60,000 m2 ~ 646k SF
    big = [(lat0 - dlat, lon0 - dlon), (lat0 - dlat, lon0 + dlon), (lat0 + dlat, lon0 + dlon), (lat0 + dlat, lon0 - dlon),
           (lat0 - dlat, lon0 - dlon)]
    shed = [(lat0 + 0.004, lon0), (lat0 + 0.004, lon0 + 0.0002), (lat0 + 0.0042, lon0 + 0.0002), (lat0 + 0.004, lon0)]
    monkeypatch.setattr(roads, "buildings", lambda *a, **k: [{"id": 1, "tags": {}, "nodes": shed},
                                                            {"id": 2, "tags": {}, "nodes": big}])
    twin = {"frame": {"lat0": lat0, "lon0": lon0}}
    fp = D.osm_footprint(twin, {"lat": lat0, "lon": lon0, "rba_sqft": 640000, "stories": 1})
    assert fp and fp["source"].endswith("way 2") and 0.9 < fp["footprint_sqft"] / 646000 < 1.1
    assert D.osm_footprint(twin, {"lat": lat0, "lon": lon0, "rba_sqft": 50000, "stories": 1}) is None   # size mismatch
