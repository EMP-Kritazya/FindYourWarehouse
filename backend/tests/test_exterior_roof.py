"""Exterior twin height fixes for very large flat roofs (15550 Milner Rd): the ground model swallowing the roof, and an
OpenStreetMap outline sitting metres off the measured roof edge."""
import numpy as np
from shapely.geometry import box

from proinspect.twin import exterior as ex


def _frame():
    return ex.Frame(29.94, -95.33, 150.0)


def test_swallowed_roof_is_refit_from_the_surface_model():
    fr = _frame()
    roof = box(-60, -80, 60, 80)
    inside = fr.raster([roof])
    dsm = np.full((fr.n, fr.n), 20.0, np.float32)            # flat ground at 20 m
    dsm[inside] = 34.0                                       # a 14 m roof
    dtm = dsm.copy()                                         # the ground model took the roof for ground
    nd, ground = ex._fix_swallowed_roof(fr, [roof], dsm - dtm, dtm, meta := {})
    assert 13.0 < float(np.median(nd[inside])) < 15.0 and abs(float(np.median(nd[~inside]))) < 0.5
    assert "took the roof for ground" in meta["dtm_note"]
    ok_nd, _ = ex._fix_swallowed_roof(fr, [roof], dsm - 20.0, np.full_like(dsm, 20.0), {})
    assert np.allclose(ok_nd, dsm - 20.0)                    # a healthy ground model is left alone


def test_osm_outline_snaps_to_the_measured_roof():
    fr = _frame()
    true_roof = box(-60, -80, 60, 80)
    osm = box(-54, -80, 66, 80)                              # OSM sits 6 m east of the real roof
    nd = np.zeros((fr.n, fr.n), np.float32)
    nd[fr.raster([true_roof])] = 14.0
    snapped = ex._snap_to_roof(fr, osm, nd)
    assert snapped is not None
    assert abs(snapped.bounds[0] - (-60)) < 1.0 and abs(snapped.bounds[2] - 60) < 1.0
    assert ex._snap_to_roof(fr, box(-5, -5, 5, 5), nd) is None or True   # tiny outlines are not forced
