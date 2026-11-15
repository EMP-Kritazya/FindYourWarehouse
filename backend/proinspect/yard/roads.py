"""Truck gates from the road network (OpenStreetMap via Overpass; free, no key, cached on disk).

Where trucks really enter a property is where a driveway / service road meets the public street. The aerial paved mask
cannot tell a street entrance from a courtyard, so this is the first source of gate candidates; the aerial heuristics
in plan._gate remain the fallback when OSM has no driveway here.

    ways(lat0, lon0, half_m)                        -> raw OSM ways [{id, tags, nodes:[(lat, lon)]}] (cached)
    gate_candidates(lat0, lon0, half_m, parcel)     -> [{x, y, nx, ny, street_dir, method, osm}] in local ENU
"""
import math

import httpx

from proinspect.cache import cached
from proinspect.tools import elevation as el

OVERPASS = "https://overpass-api.de/api/interpreter"
PUBLIC = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
          "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "living_street"}
INSIDE_M = 12.0               # the gate point sits this far along the driveway from the street


@cached("osm")
def ways(lat0, lon0, half_m):
    d_lat = half_m / 111320.0
    d_lon = half_m / (111320.0 * math.cos(math.radians(lat0)))
    bbox = f"{lat0 - d_lat:.6f},{lon0 - d_lon:.6f},{lat0 + d_lat:.6f},{lon0 + d_lon:.6f}"
    q = f'[out:json][timeout:25];way["highway"]({bbox});out tags geom;'
    r = httpx.post(OVERPASS, data={"data": q}, timeout=40,
                   headers={"User-Agent": "proinspect-hackathon/1.0 (yard gate finder)"})
    r.raise_for_status()
    out = []
    for e in r.json().get("elements", []):
        if e.get("type") == "way" and e.get("geometry"):
            out.append({"id": e["id"], "tags": e.get("tags", {}),
                        "nodes": [(g["lat"], g["lon"]) for g in e["geometry"]]})
    return out


def _enu(lat0, lon0, pts):
    return [tuple(float(v) for v in el.to_local(la, lo, lat0, lon0)) for la, lo in pts]


def gate_candidates(lat0, lon0, half_m, parcel=None):
    """Points where a service road / driveway joins a public road, moved INSIDE_M along the driveway (toward the
    property), with the inward direction (nx, ny point OUT to the street, as plan._gate expects). Ranked by distance
    to the parcel (driveways touching the parcel first)."""
    try:
        ws = ways(lat0, lon0, half_m)
    except Exception:                                             # noqa: BLE001  (no network: aerial fallback)
        return []
    public, service = [], []
    for w in ws:
        hw = w["tags"].get("highway")
        pts = _enu(lat0, lon0, w["nodes"])
        if hw in PUBLIC:
            public.append((w, pts))
        elif hw == "service" and w["tags"].get("service") not in ("drive-through", "emergency_access"):
            service.append((w, pts))
    pub_nodes = {(round(x, 1), round(y, 1)) for _, pts in public for x, y in pts}

    def near_public(p, tol=3.0):
        return any(math.hypot(p[0] - a, p[1] - b) <= tol for a, b in pub_nodes) or any(
            _seg_dist(p, pts) <= tol for _, pts in public)

    out = []
    for w, pts in service:
        for end, seq in ((pts[0], pts), (pts[-1], pts[::-1])):
            if len(seq) < 2 or not near_public(end):
                continue
            gx, gy, ux, uy = _along(seq, INSIDE_M)               # point INSIDE_M into the driveway, direction inward
            dist_parcel = 0.0
            if parcel is not None:
                from shapely.geometry import Point
                dist_parcel = float(parcel.distance(Point(gx, gy)))
            street = _street_dir(end, public)
            out.append({"x": gx, "y": gy, "nx": -ux, "ny": -uy, "street_dir": street, "dist_parcel_m": dist_parcel,
                        "method": f"gate from OpenStreetMap: driveway (way {w['id']}"
                                  f"{', ' + w['tags']['service'] if w['tags'].get('service') else ''}) "
                                  "joining the public street",
                        "osm": w["id"]})
    out.sort(key=lambda c: c["dist_parcel_m"])
    return out


def _seg_dist(p, pts):
    best = 1e9
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        best = min(best, math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy))
    return best


def _along(seq, dist):
    """Point `dist` metres along the polyline from its first vertex, and the local direction there."""
    acc = 0.0
    for (ax, ay), (bx, by) in zip(seq, seq[1:]):
        L = math.hypot(bx - ax, by - ay)
        if L <= 1e-6:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        if acc + L >= dist:
            t = dist - acc
            return ax + ux * t, ay + uy * t, ux, uy
        acc += L
    (ax, ay), (bx, by) = seq[-2], seq[-1]
    L = math.hypot(bx - ax, by - ay) or 1.0
    return bx, by, (bx - ax) / L, (by - ay) / L


def _street_dir(p, public):
    """Unit direction of the public road at the junction (for the approach/exit stub)."""
    best = None
    for _, pts in public:
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            d = _seg_dist(p, [(ax, ay), (bx, by)])
            if best is None or d < best[0]:
                L = math.hypot(bx - ax, by - ay) or 1.0
                best = (d, ((bx - ax) / L, (by - ay) / L))
    return list(best[1]) if best else None
