"""Vexcel basemap proxy: coverage parsing, blank detection, no key leak, live Houston tile (skipped offline)."""
import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from proinspect.api import app
from proinspect.tools import vexcel_tiles as vt

client = TestClient(app)
HOU = (29.5969035, -95.373001)        # 12896757, 3200 Sam Houston Pky


def _online():
    return bool(os.environ.get("VEXCEL_API_KEY")) and os.environ.get("PROINSPECT_OFFLINE") != "1"


def test_parse_limits_keeps_only_ids_and_ints():
    xml = ("<TileMatrixSetLink><TileMatrixSet>urban</TileMatrixSet><TileMatrixSetLimits><TileMatrixLimits>"
           "<TileMatrix>3</TileMatrix><MinTileRow>2</MinTileRow><MaxTileRow>5</MaxTileRow>"
           "<MinTileCol>0</MinTileCol><MaxTileCol>7</MaxTileCol></TileMatrixLimits></TileMatrixSetLimits>"
           "</TileMatrixSetLink><ResourceURL template='https://x/wmts?api_key=SECRET'/>")
    lim = vt.parse_limits(xml)
    assert lim == {"urban": {3: [2, 5, 0, 7]}}
    assert "SECRET" not in repr(lim)


def test_blank_detection_keeps_uniform_real_roofs():
    b = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(b, "PNG")
    assert vt.is_blank(b.getvalue())
    b = io.BytesIO()
    im = Image.new("RGB", (8, 8), (220, 220, 220))
    im.putpixel((1, 1), (222, 221, 220))                  # a near-white roof is NOT blank
    im.save(b, "PNG")
    assert not vt.is_blank(b.getvalue())


def test_out_of_range_tile_is_transparent_png():
    r = client.get("/api/tiles/3/99/99.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "max-age=86400" in r.headers["cache-control"]


@pytest.mark.skipif(not _online(), reason="needs Vexcel network")
def test_houston_tile_is_jpeg_and_never_contains_key():
    z = 18
    x, y = vt.lonlat_to_tile(*HOU, z)
    r = client.get(f"/api/tiles/{z}/{x}/{y}.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "public, max-age=86400"
    key = os.environ["VEXCEL_API_KEY"]
    assert key.encode() not in r.content
    assert all(key not in v for v in r.headers.values())
    if vt.LIMITS_FILE.exists():
        assert key.encode() not in vt.LIMITS_FILE.read_bytes()


@pytest.mark.skipif(not _online(), reason="needs Vexcel network")
def test_open_ocean_has_no_coverage():
    x, y = vt.lonlat_to_tile(30.0, -40.0, 15)
    r = client.get(f"/api/tiles/15/{x}/{y}.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
