"""The door store: doors a PERSON marked in the Matterport scan (docs/CONTRACTS.md "door store").

File build/doors/{model_id}.json. Each door is the two edges a person clicked in Showcase (metres, y up), plus
derived centre / width / outward normal. Nothing here places or guesses a door.

    load(model_id) -> dict | None
    save(model_id, property_id, doors) -> dict          validates + derives, replaces the whole list
    normalise(model_id, raw_door, prev=None) -> dict    one door: validation + derived fields (raises DoorError)
    to_sim(model_id, site) -> [Door]                    into a sim Site frame (proinspect.sim.export)
    dock_side(model_id) -> 'S'|'N'|'W'|'E'|None         the scan-grid side most marked doors face (for from_interior)
    to_enu(model_id, property_id) -> list | None        local ENU of the exterior twin via twin.fuse.register
    to_enu_report(model_id, property_id) -> (list | None, why)

Outward normal: perpendicular to the edge segment, pointing to the side of the wall WITHOUT scanned floor
(interior twin grid); falls back to the client's hint (the camera stands inside), then to +perpendicular.
"""
import json
import math
from datetime import datetime, timezone

import numpy as np

from proinspect.config import BUILD
from proinspect.sim.export import mesh_to_rot, rot_to_mesh, showcase_to_model, showcase_to_sim

KINDS = ("dock_high", "drive_in", "personnel")
SOURCES = ("marked_in_scan", "dev_fixture")
MIN_W, MAX_W = 1.0, 6.0
WALL_ANGLE_TOL_DEG = 20.0     # the door line must run along one of the scan's wall axes
WALL_DIST_TOL_M = 3.0         # ...and both edges must lie within this of the scanned floor's boundary


class DoorError(ValueError):
    pass


def _dir():
    return BUILD / "doors"


def _path(model_id):
    if not model_id or "/" in model_id or ".." in model_id:
        raise DoorError(f"bad model id {model_id!r}")
    return _dir() / f"{model_id}.json"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(model_id):
    p = _path(model_id)
    return json.loads(p.read_text()) if p.exists() else None


def delete(model_id):
    p = _path(model_id)
    if p.exists():
        p.unlink()


def doors_of(model_id):
    rec = load(model_id)
    return (rec or {}).get("doors") or []


# ------------------------------------------------------------------ scan geometry (rot frame of the interior npz)
def _grid(model_id):
    p = BUILD / "twins" / "interior" / f"{model_id}.npz"
    if not p.exists():
        return None
    g = np.load(p)
    return {"floor": g["floor"].astype(bool), "origin": [float(v) for v in g["origin"]],
            "theta": float(g["theta"][0]), "cell": float(g["grid"][0]) if "grid" in g.files else 0.25}


def showcase_to_rot(x, z, theta):
    X, Y, _ = showcase_to_model(x, 0.0, z)
    rx, ry = mesh_to_rot(X, Y, theta)
    return float(rx), float(ry)


def rot_dir_to_showcase(dx, dy, theta):
    X, Y = rot_to_mesh(dx, dy, theta)
    return float(X), float(-Y)                        # model (X, Y) -> showcase (x, z) = (X, -Y)


def _floor_at(G, x, y):
    i = int(math.floor((x - G["origin"][0]) / G["cell"]))
    j = int(math.floor((y - G["origin"][1]) / G["cell"]))
    f = G["floor"]
    return 0 <= i < f.shape[0] and 0 <= j < f.shape[1] and bool(f[i, j])


def _floor_share(G, cx, cy, nx, ny, d0=0.5, d1=3.0):
    """Share of floor cells sampled along the normal (nx, ny), d0..d1 m from the door centre."""
    hits = [_floor_at(G, cx + nx * d, cy + ny * d) for d in np.arange(d0, d1 + 1e-9, 0.25)]
    return sum(hits) / len(hits)


def _boundary_dist(G, x, y):
    from scipy import ndimage
    if "edt" not in G:
        f = G["floor"]
        edge = f & ~ndimage.binary_erosion(f)
        G["edt"] = ndimage.distance_transform_edt(~edge) * G["cell"]
    i = int(math.floor((x - G["origin"][0]) / G["cell"]))
    j = int(math.floor((y - G["origin"][1]) / G["cell"]))
    e = G["edt"]
    if not (0 <= i < e.shape[0] and 0 <= j < e.shape[1]):
        di = max(0, -i, i - e.shape[0] + 1) * G["cell"]
        dj = max(0, -j, j - e.shape[1] + 1) * G["cell"]
        return math.hypot(di, dj) + float(e[min(max(i, 0), e.shape[0] - 1), min(max(j, 0), e.shape[1] - 1)])
    return float(e[i, j])


def _pt(p, name):
    try:
        return {k: float(p[k]) for k in ("x", "y", "z")}
    except (TypeError, KeyError, ValueError):
        raise DoorError(f"door edge '{name}' must be {{x, y, z}} in Showcase metres")


def normalise(model_id, d, prev=None, G=None, floor_y=None):
    """Validate one raw door ({kind, a, b, height_m?, sill_m?, source?, id?, normal_hint?}) and derive the rest."""
    kind = d.get("kind") or "dock_high"
    if kind not in KINDS:
        raise DoorError(f"door kind must be one of {KINDS}, not {kind!r}")
    source = d.get("source") or "marked_in_scan"
    if source not in SOURCES:
        raise DoorError(f"door source must be one of {SOURCES}, not {source!r}")
    a, b = _pt(d.get("a"), "a"), _pt(d.get("b"), "b")
    dx, dz = b["x"] - a["x"], b["z"] - a["z"]
    w = math.hypot(dx, dz)
    if not (MIN_W <= w <= MAX_W):
        raise DoorError(f"door width {w:.2f} m is outside {MIN_W}-{MAX_W} m: click the two side edges of one door")
    if abs(a["y"] - b["y"]) > 4.0:
        raise DoorError(f"the two edges are {abs(a['y'] - b['y']):.1f} m apart in height: click both near the same height")
    cx, cz = (a["x"] + b["x"]) / 2, (a["z"] + b["z"]) / 2
    px, pz = -dz / w, dx / w                       # a perpendicular in Showcase (x, z)
    G = G if G is not None else _grid(model_id)
    normal_basis = "perpendicular (no scan grid)"
    if G is not None:
        th = G["theta"]
        ax_, ay_ = showcase_to_rot(a["x"], a["z"], th)
        bx_, by_ = showcase_to_rot(b["x"], b["z"], th)
        ang = math.degrees(math.atan2(by_ - ay_, bx_ - ax_)) % 90.0
        off = min(ang, 90.0 - ang)
        if off > WALL_ANGLE_TOL_DEG:
            raise DoorError(f"the door line is {off:.0f} deg off the building's wall directions: click the two side "
                            "edges of the same door, on its wall")
        for nm, (x, y) in (("a", (ax_, ay_)), ("b", (bx_, by_))):
            dist = _boundary_dist(G, x, y)
            if dist > WALL_DIST_TOL_M:
                raise DoorError(f"edge '{nm}' is {dist:.1f} m from any wall of the scanned floor: click on the door's "
                                "edge, on the wall")
        # outward = the side of the wall with less scanned floor
        crx, cry = showcase_to_rot(cx, cz, th)
        p2x, p2y = showcase_to_rot(cx + px, cz + pz, th)
        nrx, nry = p2x - crx, p2y - cry
        s_plus, s_minus = _floor_share(G, crx, cry, nrx, nry), _floor_share(G, crx, cry, -nrx, -nry)
        if abs(s_plus - s_minus) >= 0.2:
            if s_plus > s_minus:
                px, pz = -px, -pz
            normal_basis = "away from the scanned floor"
        else:
            G = None
    if G is None:
        hint = d.get("normal_hint") or d.get("normal")
        if isinstance(hint, dict) and "x" in hint and "z" in hint:
            if px * float(hint["x"]) + pz * float(hint["z"]) < 0:
                px, pz = -px, -pz
            normal_basis = "client hint (away from the camera, which stands inside)"
    h = d.get("height_m")
    sill = d.get("sill_m")
    for nm, v in (("height_m", h), ("sill_m", sill)):
        if v is not None and not (0.0 <= float(v) <= 12.0):
            raise DoorError(f"{nm} {v} is not a plausible door dimension (0-12 m)")
    return {"id": d.get("id"), "kind": kind,
            "a": {k: round(v, 3) for k, v in a.items()}, "b": {k: round(v, 3) for k, v in b.items()},
            "center": {"x": round(cx, 3), "z": round(cz, 3)}, "width_m": round(w, 2),
            "height_m": round(float(h), 2) if h is not None else None,
            "sill_m": round(float(sill), 2) if sill is not None else None,
            "normal": {"x": round(px, 4), "z": round(pz, 4)}, "normal_basis": normal_basis,
            "source": source, "marked_at": (prev or {}).get("marked_at") or d.get("marked_at") or _now()}


def save(model_id, property_id, doors):
    """Replace the door list of `model_id`. Each door is validated; ids d1, d2... are kept or assigned."""
    prev = {d["id"]: d for d in doors_of(model_id) if d.get("id")}
    G = _grid(model_id)
    out, used = [], set()
    for d in doors:
        n = normalise(model_id, d, prev.get(d.get("id")), G=G)
        out.append(n)
        if n["id"]:
            if n["id"] in used:
                raise DoorError(f"duplicate door id {n['id']}")
            used.add(n["id"])
    k = 1
    for n in out:
        if not n["id"]:
            while f"d{k}" in used:
                k += 1
            n["id"] = f"d{k}"
            used.add(n["id"])
    rec = {"model_id": model_id, "property_id": int(property_id) if property_id is not None else None,
           "updated_at": _now(), "doors": out}
    _dir().mkdir(parents=True, exist_ok=True)
    p = _path(model_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(p)
    return rec


# ------------------------------------------------------------------ into other frames
def dock_side(model_id, kinds=("dock_high", "drive_in")):
    """Scan-grid side ('S' low-y, 'N', 'W' low-x, 'E') most marked truck doors face, or None."""
    ds = [d for d in doors_of(model_id) if d["kind"] in kinds]
    G = _grid(model_id)
    if not ds or G is None:
        return None
    votes = {}
    for d in ds:
        x0, y0 = showcase_to_rot(d["center"]["x"], d["center"]["z"], G["theta"])
        x1, y1 = showcase_to_rot(d["center"]["x"] + d["normal"]["x"], d["center"]["z"] + d["normal"]["z"],
                                       G["theta"])
        nx, ny = x1 - x0, y1 - y0
        side = ("E" if nx > 0 else "W") if abs(nx) > abs(ny) else ("N" if ny > 0 else "S")
        votes[side] = votes.get(side, 0) + 1
    return max(votes, key=votes.get)


def to_sim(model_id, site, doors=None):
    """Marked doors as sim `Door`s in `site`'s frame. Door.x = position along the dock wall (sim u). Extra
    attributes: id, u, v (centre, sim metres), nu, nv (outward unit normal, sim frame), height_m, sill_m,
    on_dock_wall (normal points out through the low-y dock wall)."""
    from proinspect.sim.site import Door
    tf = site.transform
    out = []
    for d in (doors if doors is not None else doors_of(model_id)):
        cx, cz = d["center"]["x"], d["center"]["z"]
        (u,), (v,) = (np.atleast_1d(t) for t in showcase_to_sim(cx, 0.0, cz, tf))
        (u1,), (v1,) = (np.atleast_1d(t) for t in showcase_to_sim(cx + d["normal"]["x"], 0.0, cz + d["normal"]["z"], tf))
        nu, nv = float(u1 - u), float(v1 - v)
        L = math.hypot(nu, nv) or 1.0
        door = Door(float(u), "drive_in" if d["kind"] == "drive_in" else ("personnel" if d["kind"] == "personnel"
                                                                          else "dock_high"),
                    d.get("source", "marked_in_scan"), float(d["width_m"]))
        door.id, door.u, door.v, door.nu, door.nv = d["id"], float(u), float(v), nu / L, nv / L
        door.height_m, door.sill_m = d.get("height_m"), d.get("sill_m")
        door.on_dock_wall = door.nv < -0.7
        out.append(door)
    return out


# Outward normal (rot frame) of the wall that sim.site._rotate_dock_wall_down brings to low-y for each side label.
# Verified numerically: np.rot90(k=1) ('E') brings the LOW-x side down and k=3 ('W') the HIGH-x side, so the labels
# are mirrored in x relative to that function's docstring. QHMtBN5Nnrc 'E' -> rot -x -> Showcase z ~ 65 (CLAUDE.md).
SIDE_NORMAL = {"E": (-1.0, 0.0), "W": (1.0, 0.0), "N": (0.0, 1.0), "S": (0.0, -1.0)}
SCAN_EDGE_TOL_M = 3.0         # an aerial door is "in the scan" when it lands this close to the scanned floor edge
TRUST_REG = 0.5               # automatic registration confidence needed to place aerial doors in a scan unconfirmed


def from_dock_store(model_id, property_id):
    """Doors from the v3 dock store (aerial, ENU on the footprint; proinspect.docks) mapped INTO the scan through the
    scan-to-footprint registration, as door-store-shaped dicts (source 'dock_store'). Returns (doors, basis).

    Which of the two 180-deg registration hypotheses is used, in order of trust:
      1. the person's choice in the 2D dock editor (registration.confirmed_by_user, flip 0/180);
      2. a scan-verified dock wall (twin.fuse.SCAN_VERIFIED_DOCK_WALL): the flip that puts the aerial dock doors on it;
      3. otherwise the best automatic fit, flagged unconfirmed.
    Only doors that land within SCAN_EDGE_TOL_M of the scanned floor's edge are returned (a scan often covers only
    part of the building); rejected doors never are."""
    try:
        from proinspect import docks
        store = docks.load(property_id)
    except Exception as e:                                        # noqa: BLE001
        return [], {"why": f"no dock store: {type(e).__name__}"}
    reg = (store or {}).get("registration") or {}
    if reg.get("model_id") != model_id or reg.get("phi") is None:
        return [], {"why": "the dock store has no registration for this scan"}
    G = _grid(model_id)
    if G is None:
        return [], {"why": f"scan {model_id} has no interior twin"}
    cand = [d for d in store.get("doors") or [] if d.get("status") != "rejected"]
    if not cand:
        return [], {"why": "no doors in the dock store"}
    hyps = {0: (reg["phi"], reg["t"])}
    if reg.get("phi_flipped") is not None and reg.get("t_flipped"):
        hyps[180] = (reg["phi_flipped"], reg["t_flipped"])

    def place(phi, t):
        c, s = math.cos(-phi), math.sin(-phi)
        out = []
        for d in cand:
            ex, ey = d["x"] - t[0], d["y"] - t[1]
            rx, ry = c * ex - s * ey, s * ex + c * ey
            nx, ny = d["normal"]
            rnx, rny = c * nx - s * ny, s * nx + c * ny
            out.append((d, rx, ry, rnx, rny))
        return out

    from proinspect.twin.fuse import SCAN_VERIFIED_DOCK_WALL
    if reg.get("confirmed_by_user"):
        flip, how = int(reg.get("flip") or 0), "alignment confirmed by a person in the dock editor"
        sx, sy = (reg.get("shift") or [0.0, 0.0])[:2]               # the person slid the scan outline (ENU m)
        ph, tt = hyps.get(flip, hyps[0])
        hyps[flip] = (ph, [tt[0] + float(sx), tt[1] + float(sy)])
    elif not (model_id in SCAN_VERIFIED_DOCK_WALL or (reg.get("ok") and (reg.get("confidence") or 0) >= TRUST_REG)):
        # A weak automatic fit (e.g. confidence 0.07, ambiguous by tens of metres) would put aerial doors on the wrong
        # wall or in the middle of the floor. Show nothing in 3D until a person confirms the alignment in 2D.
        return [], {"why": f"scan-to-aerial alignment not confirmed (automatic fit confidence {reg.get('confidence')}); "
                           "open the dock editor, check the scan outline, Flip / move it, then press 'Alignment looks right'",
                    "needs_alignment": True, "confidence": reg.get("confidence"), "of": len(cand), "in_scan": 0}
    else:
        flip, how = 0, "automatic footprint fit (high confidence)"
        if model_id in SCAN_VERIFIED_DOCK_WALL and 180 in hyps:
            sx, sy = SIDE_NORMAL[SCAN_VERIFIED_DOCK_WALL[model_id][0]]
            score = {}
            for f, (phi, t) in hyps.items():
                dock = [p for p in place(phi, t) if p[0]["kind"] != "drive_in"] or place(phi, t)
                score[f] = sum(p[3] * sx + p[4] * sy for p in dock) / len(dock)
            flip = max(score, key=score.get)
            how = "flip chosen so the aerial dock doors face the scan-verified dock wall"
    phi, t = hyps.get(flip, hyps[0])
    th = G["theta"]
    from scipy import ndimage
    # the building's OUTER wall only: fill holes so containers/racks/rooms inside the floor don't count as walls
    G = {**G, "floor": ndimage.binary_fill_holes(G["floor"])}
    G.pop("edt", None)
    out = []
    for d, rx, ry, rnx, rny in place(phi, t):
        if _boundary_dist(G, rx, ry) > SCAN_EDGE_TOL_M:
            continue
        # the door must face OUT of the building: floor behind it (inside), no floor in front of it (outside)
        if _floor_share(G, rx, ry, -rnx, -rny) < 0.6 or _floor_share(G, rx, ry, rnx, rny, 1.0, 4.0) > 0.2:
            continue
        # snap the door centre onto the scanned floor's edge along its normal, so it sits on the scanned wall
        for k in range(0, 25):
            step = (k // 2 + 1) * 0.25 * (1 if k % 2 == 0 else -1)
            if _boundary_dist(G, rx + rnx * step, ry + rny * step) < _boundary_dist(G, rx, ry):
                rx, ry = rx + rnx * step, ry + rny * step
        w = (d.get("width_ft") or 9.0) * 0.3048
        tx, ty = -rny, rnx                                         # along-wall direction (rot frame)
        pts = []
        for px, py in ((rx - tx * w / 2, ry - ty * w / 2), (rx + tx * w / 2, ry + ty * w / 2), (rx, ry)):
            X, Y = rot_to_mesh(px, py, th)
            pts.append({"x": round(float(X), 3), "y": 0.0, "z": round(float(-Y), 3)})
        nX, nZ = rot_dir_to_showcase(rnx, rny, th)
        L = math.hypot(nX, nZ) or 1.0
        out.append({"id": d["id"], "kind": d["kind"] if d["kind"] in KINDS else "dock_high",
                    "a": pts[0], "b": pts[1], "center": {"x": pts[2]["x"], "z": pts[2]["z"]},
                    "width_m": round(w, 3), "height_m": round(d["height_ft"] * 0.3048, 3) if d.get("height_ft") else None,
                    "sill_m": None, "normal": {"x": round(nX / L, 4), "z": round(nZ / L, 4)},
                    "source": "dock_store", "status": d.get("status"), "facing": d.get("facing"),
                    "label": d.get("label"), "approx": not reg.get("confirmed_by_user")})
    return out, {"registration": how, "flip": flip, "confidence": reg.get("confidence"),
                 "confirmed_by_user": bool(reg.get("confirmed_by_user")), "in_scan": len(out), "of": len(cand),
                 "updated_at": store.get("updated_at")}


def scan_outline(model_id, tol=0.5):
    """The scanned floor's outer outline in the scan's rot frame (the frame the dock store's registration maps:
    ENU = R(phi) p_rot + t), simplified, for the 2D dock editor's alignment overlay. None when unavailable."""
    G = _grid(model_id)
    if G is None:
        return None
    from scipy import ndimage
    from shapely.geometry import box
    from shapely.ops import unary_union
    f = ndimage.binary_fill_holes(G["floor"])
    lab, n = ndimage.label(f)
    if n > 1:
        f = lab == 1 + int(np.argmax(ndimage.sum(f, lab, range(1, n + 1))))
    k = max(1, int(round(1.0 / G["cell"])))                       # 1 m blocks, then merge runs into rectangles
    nx, ny = f.shape[0] // k, f.shape[1] // k
    coarse = f[:nx * k, :ny * k].reshape(nx, k, ny, k).mean(axis=(1, 3)) >= 0.5
    step, (ox, oy) = k * G["cell"], G["origin"]
    rects = []
    for i in range(nx):
        j = 0
        while j < ny:
            if coarse[i, j]:
                e = j
                while e < ny and coarse[i, e]:
                    e += 1
                rects.append(box(ox + i * step, oy + j * step, ox + (i + 1) * step, oy + e * step))
                j = e
            else:
                j += 1
    if not rects:
        return None
    poly = unary_union(rects).simplify(tol)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return [[round(x, 2), round(y, 2)] for x, y in poly.exterior.coords]


ALIGN_MAX_M = 6.0             # a click further than this along the wall from any aerial door is not "that door"


def align_to_click(property_id, model_id, x, z):
    """One-click along-wall alignment: a person clicked the middle of a REAL dock door in the scan (Showcase x, z).
    The nearest aerial dock door is moved onto it by sliding the scan registration ALONG that wall (registration.shift,
    ENU), and the alignment is marked confirmed by a person. Returns {ok, door, moved_m, why}."""
    from proinspect import docks
    store = docks.load(property_id)
    reg = dict(store.get("registration") or {})
    if reg.get("model_id") != model_id or reg.get("phi") is None:
        return {"ok": False, "why": "this scan has no registration to the aerial footprint"}
    _, basis = from_dock_store(model_id, property_id)
    flip = int(basis.get("flip") or reg.get("flip") or 0)
    phi, t = (reg["phi_flipped"], reg["t_flipped"]) if flip == 180 and reg.get("t_flipped") else (reg["phi"], reg["t"])
    shift = list(reg.get("shift") or [0.0, 0.0]) if reg.get("confirmed_by_user") else [0.0, 0.0]
    G = _grid(model_id)
    if G is None:
        return {"ok": False, "why": f"scan {model_id} has no interior twin"}
    rx, ry = showcase_to_rot(x, z, G["theta"])
    c, s = math.cos(phi), math.sin(phi)
    cx, cy = c * rx - s * ry + t[0] + shift[0], s * rx + c * ry + t[1] + shift[1]       # the click in ENU
    cand = [d for d in store.get("doors") or [] if d.get("status") != "rejected" and d.get("kind") != "personnel"]
    if not cand:
        return {"ok": False, "why": "no aerial doors to align"}
    d = min(cand, key=lambda d: math.hypot(d["x"] - cx, d["y"] - cy))
    nx, ny = d["normal"][:2]
    ax, ay = -ny, nx                                               # along the wall
    off = (d["x"] - cx) * ax + (d["y"] - cy) * ay
    perp = abs((d["x"] - cx) * nx + (d["y"] - cy) * ny)
    if perp > 8.0:
        return {"ok": False, "why": f"that point is {perp:.0f} m from the dock wall: click on a dock door"}
    if abs(off) > ALIGN_MAX_M:
        return {"ok": False, "why": f"the nearest aerial door is {abs(off):.1f} m along the wall from your click: "
                                    "click a dock door nearer the boxes"}
    shift = [round(shift[0] + ax * off, 2), round(shift[1] + ay * off, 2)]
    docks.save(property_id, {"doors": store["doors"], "registration": {
        **reg, "flip": flip, "confirmed_by_user": True, "shift": shift}})
    return {"ok": True, "door": d.get("label") or d["id"], "door_id": d["id"], "moved_m": round(off, 2),
            "shift": shift}


def _scan_pose(model_id, store):
    """(phi, t) of the CONFIRMED scan->ENU registration in the dock store (ENU = R(phi) p_rot + t), or None."""
    reg = (store or {}).get("registration") or {}
    if reg.get("model_id") != model_id or not reg.get("confirmed_by_user") or reg.get("phi") is None:
        return None
    flip = int(reg.get("flip") or 0)
    phi, t = (reg["phi_flipped"], reg["t_flipped"]) if flip == 180 and reg.get("t_flipped") else (reg["phi"], reg["t"])
    sh = reg.get("shift") or [0.0, 0.0]
    return phi, [t[0] + sh[0], t[1] + sh[1]]


def sync_marked_to_dock_store(model_id, property_id, snap_m=2.0):
    """Doors a person marked in the scan -> the aerial dock store (so the map, yard test and rack plan see them).
    Needs the scan alignment confirmed by a person (else the outside position would be a guess). An aerial door
    within `snap_m` along the same wall is confirmed and moved onto the marked door; other marked doors are added."""
    from proinspect import docks
    store = docks.load(property_id)
    pose = _scan_pose(model_id, store)
    marked = [d for d in doors_of(model_id) if d.get("kind") in ("dock_high", "drive_in")]
    if not marked:
        return {"synced": 0}
    if pose is None:
        return {"synced": 0, "why": "align the doors in the scan first (one click on a real door)"}
    G = _grid(model_id)
    phi, t = pose
    c, s = math.cos(phi), math.sin(phi)
    eidx = docks.edge_index(store)
    out = [d for d in store["doors"] if not str(d.get("id", "")).startswith("m-")]
    added = confirmed = 0
    for m in marked:
        rx, ry = showcase_to_rot(m["center"]["x"], m["center"]["z"], G["theta"])
        ex, ey = c * rx - s * ry + t[0], s * rx + c * ry + t[1]
        best = None
        for eid, (bid, e) in eidx.items():
            ax, ay = e["a"]
            sv = max(0.0, min(e["len_m"], (ex - ax) * e["t"][0] + (ey - ay) * e["t"][1]))
            px, py = ax + e["t"][0] * sv, ay + e["t"][1] * sv
            dist = math.hypot(ex - px, ey - py)
            if best is None or dist < best[0]:
                best = (dist, eid, sv, px, py)
        if best is None or best[0] > 3.0:
            continue
        _, eid, sv, px, py = best
        near = [d for d in out if d.get("edge") == eid and d.get("status") != "rejected"
                and abs(d.get("s_m", 1e9) - sv) <= snap_m]
        width_ft = round(m["width_m"] / 0.3048, 1)
        height_ft = round(m["height_m"] / 0.3048, 1) if m.get("height_m") else None
        ev = {"type": "user", "conf": 1.0, "ref": f"scan {model_id} door {m['id']}", "note": "marked by a person in the 3D scan"}
        if near:
            d = min(near, key=lambda d: abs(d["s_m"] - sv))
            d.update(x=round(px, 2), y=round(py, 2), s_m=round(sv, 2), status="confirmed", kind=m["kind"],
                     width_ft=width_ft, height_ft=height_ft or d.get("height_ft"),
                     evidence=[*(d.get("evidence") or []), ev])
            confirmed += 1
        else:
            out.append({"id": f"m-{m['id']}", "edge": eid, "building": eidx[eid][0], "x": round(px, 2), "y": round(py, 2),
                        "kind": m["kind"], "status": "user_added", "width_ft": width_ft, "height_ft": height_ft,
                        "evidence": [ev]})
            added += 1
    docks.save(property_id, {"doors": out})
    return {"synced": confirmed + added, "confirmed": confirmed, "added": added}


def doors_for_scan(model_id, property_id):
    """Doors to use inside a scan: the ones a person marked in 3D win; otherwise the aerial dock store's doors mapped
    into the scan (from_dock_store). Returns (doors, basis)."""
    if property_id is not None:
        try:
            from proinspect import docks
            if _scan_pose(model_id, docks.load(property_id)) is not None:
                # aligned by a person: aerial + marked doors live together in the dock store (sync_marked_to_dock_store)
                return from_dock_store(model_id, property_id)
        except Exception:                                         # noqa: BLE001
            pass
    marked = doors_of(model_id)
    if marked:
        return marked, {"registration": "marked in the scan", "in_scan": len(marked)}
    if property_id is None:
        return [], {"why": "no property id"}
    return from_dock_store(model_id, property_id)


def to_enu(model_id, property_id):
    """Doors in the exterior twin's local ENU metres [{id, x, y, normal, width_m, kind, ...}], or None when there
    are no marked doors or the scan-to-footprint registration isn't trustworthy (to_enu_report says why)."""
    return to_enu_report(model_id, property_id)[0]


COURT_CLOSE = 0.20            # court depths within 20% of each other: the flip stays unresolved
COURT_MAX_M = 80.0


def _court_depth(ext_dir, property_id, x, y, nx, ny):
    """Paved truck-court depth (m) straight out of a door at ENU (x, y) along (nx, ny): march the exterior twin's
    paved mask until a building, or >= 3 m of unpaved (non-vehicle) ground, like twin.exterior._court."""
    import numpy as np
    ext = json.loads((ext_dir / f"{property_id}.json").read_text())
    g = np.load(ext_dir / f"{property_id}.npz")
    paved, vehicle, others = g["paved"], g["vehicle"], g["others"]
    half, res = ext["frame"]["half_m"], ext["frame"]["res_m"]
    n = paved.shape[0]
    run = 0.0
    for k in range(2, int(COURT_MAX_M / 0.25)):
        d = k * 0.25
        r, c = int((half - (y + ny * d)) / res), int((x + nx * d + half) / res)
        if not (0 <= r < n and 0 <= c < n):
            return d
        if others[r, c]:
            return d
        if not paved[r, c] and not vehicle[r, c]:
            run += 0.25
            if run >= 3.0:
                return d - run
        else:
            run = 0.0
    return COURT_MAX_M


def _doors_enu(ds, th, phi, t):
    import numpy as np
    out = []
    R = np.array([[math.cos(phi), -math.sin(phi)], [math.sin(phi), math.cos(phi)]])
    for d in ds:
        cx, cz = d["center"]["x"], d["center"]["z"]
        p0 = np.array(showcase_to_rot(cx, cz, th))
        p1 = np.array(showcase_to_rot(cx + d["normal"]["x"], cz + d["normal"]["z"], th))
        e0 = R @ p0 + np.array(t)
        nv = R @ (p1 - p0)
        nv = nv / (np.linalg.norm(nv) or 1.0)
        out.append((d, float(e0[0]), float(e0[1]), float(nv[0]), float(nv[1])))
    return out


def to_enu_report(model_id, property_id, doors=None):
    """(doors in the exterior twin's local ENU metres, why). None + reason when registration isn't trustworthy.

    When the scan-to-footprint registration is only ambiguous in ROTATION (near-tied hypotheses on one building, e.g.
    a rectangular scan that fits as well flipped 180 deg), the marked dock doors decide it: each tied hypothesis
    puts the marked truck doors on some facade, and the one whose doors face the deepest paved truck court wins
    (doors are where the court is). Too close (< 20% apart) -> None."""
    ds = doors if doors is not None else doors_of(model_id)
    if not ds:
        return None, "no doors marked in this scan"
    G = _grid(model_id)
    if G is None:
        return None, f"scan {model_id} has no interior twin"
    ext_dir = BUILD / "twins" / "exterior"
    try:
        from proinspect.twin.fuse import MAX_AMBIGUITY_M, MIN_INSIDE, MIN_MARGIN, register
        reg = register(property_id, model_id)
    except FileNotFoundError:
        return None, f"property {property_id} has no exterior twin"
    except Exception as e:                                        # registration is best-effort
        return None, f"registration failed: {e}"
    th = G["theta"]
    basis = {"registration": "footprint fit", "confidence": reg.get("confidence")}
    if reg.get("ok"):
        phi, t = reg["phi"], reg["t"]
    else:
        hyps = reg.get("all") or []
        if not hyps:
            return None, f"scan-to-footprint registration failed: {reg.get('why')}"
        best = hyps[0]
        tied = [h for h in hyps if h["building"] == best["building"] and best["score"] - h["score"] < MIN_MARGIN
                and h["inside"] >= MIN_INSIDE and h["ambiguity_m"] <= MAX_AMBIGUITY_M]
        others = [h for h in hyps if h["building"] != best["building"] and best["score"] - h["score"] < MIN_MARGIN]
        if len(tied) < 2 or others:
            return None, f"scan-to-footprint registration not trustworthy: {reg.get('why')}"
        truck = [d for d in ds if d["kind"] != "personnel"] or ds
        rows = []
        for h in tied:
            placed = _doors_enu(truck, th, h["phi"], h["t"])
            deps = [_court_depth(ext_dir, property_id, x + nx * 0.5, y + ny * 0.5, nx, ny) for _, x, y, nx, ny in placed]
            import statistics
            rows.append((statistics.median(deps), h))
        rows.sort(key=lambda r: -r[0])
        (d0, h0), (d1, _) = rows[0], rows[1]
        rel = (d0 - d1) / d0 if d0 > 0 else 0.0
        if rel < COURT_CLOSE:
            return None, (f"registration rotation ambiguous and the marked doors don't decide it: truck-court depth "
                          f"{d0:.0f} m vs {d1:.0f} m (< {COURT_CLOSE:.0%} apart)")
        phi, t = h0["phi"], h0["t"]
        basis = {"registration": "footprint fit; 180-deg flip resolved by the marked dock doors facing the deepest "
                                 "paved truck court",
                 "court_depth_m": round(d0, 1), "runner_up_court_depth_m": round(d1, 1),
                 "court_margin": round(rel, 2),
                 "confidence": round(min(1.0, rel / 0.5) * h0["inside"] * (1.0 if h0["ambiguity_m"] <= 2 else 0.7), 2)}
    out = []
    for d, x, y, nx, ny in _doors_enu(ds, th, phi, t):
        out.append({"id": d["id"], "x": round(x, 2), "y": round(y, 2),
                    "normal": {"x": round(nx, 4), "y": round(ny, 4)}, "width_m": d["width_m"],
                    "height_m": d.get("height_m"), "kind": d["kind"], "source": d.get("source"),
                    "facing": "NESW"[int(((math.degrees(math.atan2(nx, ny)) + 45) % 360) // 90)],
                    "registration_confidence": basis["confidence"], "registration": basis})
    return out, None
