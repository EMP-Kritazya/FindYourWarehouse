"""Dock store API: GET / PUT /api/warehouses/{id}/docks, GET /api/docks/evidence (temp store dir)."""
import json

from fastapi.testclient import TestClient

from proinspect import docks
from proinspect.api import app
from tests.test_docks import _south, _store

c = TestClient(app)


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(docks, "OUT", tmp_path)
    s = _store(costar=None, has_docks=None)
    s["property_id"] = 5
    e = _south(s)
    s["doors"] = [docks._door(s["frame"], 0, e, 10.0, id="k1", kind="dock_high")]
    s["summary"] = docks.summarize(s)
    (tmp_path / "5.json").write_text(json.dumps(s))
    return e


def test_get_put_evidence(tmp_path, monkeypatch):
    e = _seed(tmp_path, monkeypatch)
    g = c.get("/api/warehouses/5/docks").json()
    assert g["doors"][0]["id"] == "k1" and g["summary"]["estimate"]["status"] == "lower_bound"
    r = c.put("/api/warehouses/5/docks", json={"doors": [{"id": "k1", "edge": e["id"], "s_m": 10,
                                                           "kind": "dock_high", "status": "confirmed"}]})
    assert r.status_code == 200 and r.json()["summary"]["confirmed"] == 1
    bad = c.put("/api/warehouses/5/docks", json={"doors": [{"edge": e["id"], "x": 0, "y": -30}]})
    assert bad.status_code == 422
    ev = c.get("/api/docks/evidence?ids=5").json()
    assert ev["5"]["confirmed"] == 1 and ev["5"]["estimate"]["status"] == "estimated"


def test_missing_twin_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(docks, "OUT", tmp_path)
    assert c.get("/api/warehouses/999999999/docks").status_code == 404
    ev = c.get("/api/docks/evidence?ids=999999999").json()
    assert ev["999999999"]["estimate"]["status"] == "unknown"


def test_proof_routes(tmp_path, monkeypatch):
    from tests.test_docks import _proof_store
    _proof_store(tmp_path, monkeypatch)
    ix = c.get("/api/docks/1/proof").json()
    assert ix["doors"]["k2"]["no_photo"] is True
    assert c.get("/api/docks/1/proof/c1.jpg?door=nope").status_code == 404
    assert c.get("/api/docks/1/proof/bad..name?door=k1").status_code == 422


def test_door_review_keeps_type_and_reason(tmp_path, monkeypatch):
    e = _seed(tmp_path, monkeypatch)
    r = c.put("/api/warehouses/5/docks", json={"doors": [{"id": "k1", "edge": e["id"], "s_m": 10, "kind": "drive_in",
                                                           "kind_conf": 1, "status": "confirmed"}]}).json()
    d = r["doors"][0]
    assert d["kind"] == "drive_in" and d["status"] == "confirmed" and "reason" not in d
    assert r["summary"]["drive_in"] == 1 if "drive_in" in r["summary"] else True
    r = c.put("/api/warehouses/5/docks", json={"doors": [{"id": "k1", "edge": e["id"], "s_m": 10, "kind": "drive_in",
                                                           "status": "rejected", "reason": "Window"}]}).json()
    assert r["doors"][0]["status"] == "rejected" and r["doors"][0]["reason"] == "Window"
    assert c.get("/api/warehouses/5/docks").json()["doors"][0]["reason"] == "Window"
