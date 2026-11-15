"""Flood exposure: sink fill, indicator rules (never 'safe' on missing data), FEMA parsing, API flag."""
import numpy as np
from fastapi.testclient import TestClient

from proinspect import flood as FL
from proinspect.api import app


def test_priority_flood_fills_a_closed_depression_only():
    z = np.full((20, 20), 10.0)
    z[8:12, 8:12] = 9.0                                   # a 1 m deep pit
    z[:, 0] = 9.5                                         # an open edge drains, it is not a pit
    d = FL._priority_flood(z) - z
    assert np.allclose(d[8:12, 8:12], 1.0) and d[:, 0].max() == 0


def _finish(facts, fema=None, missing=None, waterway=None, tmp=None):
    out = {"facts": facts, "fema": fema or {}, "missing": missing or [], "waterway": waterway or {"name": "Greens Bayou"}}
    return FL._finish(out, tmp)["indicator"]


def _v(x):
    return {"value": x, "unit": "ft", "source": "measured", "basis": ""}


def test_indicator_rules(tmp_path):
    p = tmp_path / "x.json"
    assert _finish({"pad_ft": _v(4.0), "hand_ft": _v(25.0)}, {"level": "minimal"}, tmp=p)["level"] == "lower"
    assert _finish({"pad_ft": _v(4.0), "hand_ft": _v(25.0)}, {"level": "sfha", "sfha": True, "label": "Zone AE"}, tmp=p)["level"] == "higher"
    assert _finish({"pad_ft": _v(0.4), "hand_ft": _v(25.0)}, {"level": "minimal"}, tmp=p)["level"] == "moderate"  # at grade, high ground
    assert _finish({"pad_ft": _v(0.4), "hand_ft": _v(10.0)}, {"level": "minimal"}, tmp=p)["level"] == "higher"    # at grade, near a bayou
    assert _finish({"pad_ft": _v(4.0), "hand_ft": _v(3.0)}, {"level": "minimal"}, tmp=p)["level"] == "higher"
    assert _finish({"pad_ft": _v(2.0), "hand_ft": _v(25.0)}, {"level": "minimal"}, tmp=p)["level"] == "moderate"
    assert _finish({"pad_ft": _v(4.0), "hand_ft": _v(8.0)}, {"level": "minimal"}, tmp=p)["level"] == "moderate"
    assert _finish({"pad_ft": _v(4.0), "hand_ft": _v(25.0)}, {"level": "0.2pct"}, tmp=p)["level"] == "moderate"


def test_missing_inputs_are_never_called_safe(tmp_path):
    ind = _finish({"pad_ft": _v(4.0)}, {}, missing=["FEMA zone", "height above waterway"], tmp=tmp_path / "x.json")
    assert ind["level"] == "unclear" and ind["partial"]
    ind = _finish({"pad_ft": _v(4.0), "hand_ft": _v(25.0)}, {"level": "minimal"}, missing=["dock wells"], tmp=tmp_path / "y.json")
    assert ind["level"] == "lower" and ind["partial"]


def test_fema_parsing(monkeypatch):
    monkeypatch.setattr(FL, "_fema_query", lambda url, lon, lat: [{"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T", "STATIC_BFE": 62.0}])
    z = FL.fema_zone(29.9, -95.3)
    assert z["sfha"] and z["label"] == "Zone AE, BFE 62 ft"
    monkeypatch.setattr(FL, "_fema_query", lambda url, lon, lat: [])
    assert FL.fema_zone(29.9, -95.3)["label"] == "Outside FEMA's mapped flood areas"

    def boom(url, lon, lat):
        raise TimeoutError()
    monkeypatch.setattr(FL, "_fema_query", boom)
    assert "error" in FL.fema_zone(29.9, -95.3)


def test_api_serves_cache_only_by_default_and_respects_the_flag(monkeypatch):
    c = TestClient(app)
    monkeypatch.setattr(FL, "assess", lambda pid, compute=False: None if not compute else {"status": "computed"})
    assert c.get("/api/flood/12896757").json()["status"] == "not_run"
    assert c.get("/api/flood/12896757?run=true").json()["status"] == "computed"
    monkeypatch.setattr(FL, "ENABLED", False)
    assert c.get("/api/flood/12896757").status_code == 404
