// Overlays drawn inside Showcase next to the sim scene (S4):
//   doors     doors a PERSON marked in the scan (door store), never assumed ones
//   preview   the door being marked: first edge + live segment to the pointer, with its width
//   fit       equipment test result: reachable heat tiles, the route strip, the vehicle box at true size (animated),
//             pinch-point posts with "needs X ft, has Y ft" labels, the start point and a picked target
// THREE is Showcase's own copy (iframe realm): plain arrays only, canvases from the iframe document.
import { boxEdges, instancedBoxes, makeLabel, vehicleColor } from './sceneRenderer';
import type { FitResult, MarkedDoor, Pt3 } from './types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type T3 = any;

export const DOOR_COLORS: Record<string, number> = { marked_in_scan: 0x2ad4ff, dev_fixture: 0xff5ad1 };
export const KIND_LABEL: Record<string, string> = { dock_high: 'dock-high', drive_in: 'drive-in', personnel: 'personnel' };
const FT = 0.3048;
const LABEL_FADE_NEAR = 45, LABEL_FADE_FAR = 70;

function dispose(g: any) {
  g.traverse((o: any) => {
    o.geometry?.dispose?.();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) { m.map?.dispose?.(); m.dispose?.(); }
  });
}

export function heatRGB(v: number): number {
  // 0 red -> 0.5 amber -> 1 green
  const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);
  const c0 = [0xff, 0x41, 0x36], c1 = [0xff, 0xb0, 0x20], c2 = [0x2e, 0xcc, 0x40];
  const [a, b, t] = v < 0.5 ? [c0, c1, v / 0.5] : [c1, c2, (v - 0.5) / 0.5];
  return (lerp(a[0], b[0], t) << 16) | (lerp(a[1], b[1], t) << 8) | lerp(a[2], b[2], t);
}

export class OverlayRenderer {
  T: T3; root: any; doc: Document;
  floorY = 0;
  doorsG: any = null; previewG: any = null; fitG: any = null; targetG: any = null;
  labels: any[] = [];
  doors: MarkedDoor[] = [];
  fit: FitResult | null = null;
  heatMesh: any = null;
  vehicle: any = null;
  fitDuration = 0;
  showLabels = true;
  private tmpV: any = null;

  constructor(T: T3, root: any, doc: Document) { this.T = T; this.root = root; this.doc = doc; }

  private drop(key: 'doorsG' | 'previewG' | 'fitG' | 'targetG') {
    const g = this[key];
    if (!g) return;
    this.root.remove(g);
    const gone = new Set<any>();
    g.traverse((o: any) => gone.add(o));
    this.labels = this.labels.filter((l) => !gone.has(l));
    dispose(g);
    this[key] = null;
  }

  private label(text: string, color: number, k = 1) {
    const l = makeLabel(this.T, this.doc, text, color);
    l.userData.k = k;
    this.labels.push(l);
    return l;
  }

  private bar(x0: number, y0: number, z0: number, x1: number, y1: number, z1: number, thick: number, mat: any) {
    const T = this.T;
    const len = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 0.001;
    const m = new T.Mesh(new T.BoxGeometry(thick, len, thick), mat);
    m.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
    const dir = new T.Vector3(x1 - x0, y1 - y0, z1 - z0).normalize();
    m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dir);
    return m;
  }

  /** Marked doors: two posts at the clicked edges, a sill bar, a top bar when the height is known, a floor strip
   * just inside, and a label. Pulled 0.25 m inside the wall so the scan mesh doesn't hide them. */
  setDoors(doors: MarkedDoor[], highlight?: string | null) {
    this.drop('doorsG');
    this.doors = doors;
    if (!doors.length) return;
    const T = this.T, fy = this.floorY;
    const g = new T.Group(); g.name = 'pi.doors';
    doors.forEach((d, i) => {
      const color = DOOR_COLORS[d.source] ?? 0x2ad4ff;
      const hi = highlight === d.id;
      const mat = new T.MeshBasicMaterial({ color: hi ? 0xffffff : color, transparent: true, opacity: 0.95, depthTest: false });
      const nx = d.normal.x, nz = d.normal.z, inset = 0.25;
      const A = { x: d.a.x - nx * inset, z: d.a.z - nz * inset }, B = { x: d.b.x - nx * inset, z: d.b.z - nz * inset };
      const H = d.height_m ?? Math.max(2.4, d.a.y - fy + 0.3, d.b.y - fy + 0.3);
      const sill = fy + (d.sill_m ?? 0);
      g.add(this.bar(A.x, sill, A.z, A.x, fy + H, A.z, 0.12, mat));
      g.add(this.bar(B.x, sill, B.z, B.x, fy + H, B.z, 0.12, mat));
      g.add(this.bar(A.x, sill + 0.05, A.z, B.x, sill + 0.05, B.z, 0.1, mat));
      if (d.height_m != null) g.add(this.bar(A.x, fy + H, A.z, B.x, fy + H, B.z, 0.1, mat));
      // translucent door face so it reads in the walk view, and a floor strip for overview / floor plan
      const face = new T.Mesh(new T.PlaneGeometry(d.width_m, H - (d.sill_m ?? 0)),
        new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, depthWrite: false, side: T.DoubleSide }));
      face.position.set((A.x + B.x) / 2, (sill + fy + H) / 2, (A.z + B.z) / 2);
      face.rotation.y = Math.atan2(-(B.z - A.z), B.x - A.x);
      g.add(face);
      const strip = new T.Mesh(new T.BoxGeometry(d.width_m, 0.04, 0.6),
        new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.6, depthWrite: false }));
      strip.position.set((A.x + B.x) / 2 - nx * 0.3, fy + 0.03, (A.z + B.z) / 2 - nz * 0.3);
      strip.rotation.y = face.rotation.y;
      g.add(strip);
      const txt = `${/^new/.test(d.id) ? `D${i + 1}` : d.id.toUpperCase()} ${KIND_LABEL[d.kind] ?? d.kind} · ${(d.width_m / FT).toFixed(1)} ft${d.height_m == null ? ' · h ?' : ''}${d.source === 'dev_fixture' ? ' (dev)' : ''}`;
      const lab = this.label(txt, color, 0.75);
      lab.position.set((A.x + B.x) / 2, fy + H + 0.6 + (i % 2) * 0.5, (A.z + B.z) / 2);
      g.add(lab);
    });
    this.doorsG = g; this.root.add(g);
  }

  /** The door being marked: edge a (fixed) and the live end b (pointer), or a ghost door of fixed width. */
  setPreview(a: Pt3 | null, b: Pt3 | null, ok = true) {
    this.drop('previewG');
    if (!a) return;
    const T = this.T, fy = this.floorY;
    const g = new T.Group(); g.name = 'pi.preview';
    const col = ok ? 0xffe14d : 0xff4136;
    const mat = new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.95, depthTest: false });
    g.add(this.bar(a.x, fy, a.z, a.x, Math.max(fy + 2.4, a.y + 0.3), a.z, 0.12, mat));
    const dot = new T.Mesh(new T.SphereGeometry(0.12, 12, 8), mat);
    dot.position.set(a.x, a.y, a.z); g.add(dot);
    if (b) {
      g.add(this.bar(b.x, fy, b.z, b.x, Math.max(fy + 2.4, b.y + 0.3), b.z, 0.12, mat));
      g.add(this.bar(a.x, a.y, a.z, b.x, a.y, b.z, 0.06, mat));
      g.add(this.bar(a.x, fy + 0.05, a.z, b.x, fy + 0.05, b.z, 0.08, mat));
      const w = Math.hypot(b.x - a.x, b.z - a.z);
      const lab = this.label(`${w.toFixed(2)} m · ${(w / FT).toFixed(1)} ft${ok ? '' : ' (1–6 m)'}`, col, 0.7);
      lab.position.set((a.x + b.x) / 2, Math.max(a.y, b.y) + 0.7, (a.z + b.z) / 2);
      g.add(lab);
    }
    this.previewG = g; this.root.add(g);
  }

  setTarget(p: { x: number; z: number } | null) {
    this.drop('targetG');
    if (!p) return;
    const T = this.T, fy = this.floorY;
    const g = new T.Group();
    const mat = new T.MeshBasicMaterial({ color: 0xffe14d, transparent: true, opacity: 0.95, depthTest: false });
    const ring = new T.Mesh(new T.RingGeometry(0.45, 0.65, 32), mat);
    ring.rotation.x = -Math.PI / 2; ring.position.set(p.x, fy + 0.05, p.z); g.add(ring);
    g.add(this.bar(p.x, fy, p.z, p.x, fy + 2.5, p.z, 0.1, mat));
    const lab = this.label('Target', 0xffe14d, 0.7); lab.position.set(p.x, fy + 3.1, p.z); g.add(lab);
    this.targetG = g; this.root.add(g);
  }

  setFit(r: FitResult | null, heatOn = true) {
    this.drop('fitG');
    this.fit = r; this.vehicle = null; this.heatMesh = null; this.fitDuration = 0;
    if (!r) return;
    const T = this.T, fy = r.floor_y ?? this.floorY;
    this.floorY = fy;
    const g = new T.Group(); g.name = 'pi.fit';
    // heat: reachable tiles (green = room to work, amber = drive-through only, red = out of reach)
    const cells = r.heat?.cells ?? [];
    if (cells.length) {
      const cell = r.heat.cell || 1;
      const yaw = (r.heat as any).yaw ?? 0;
      const mat = new T.MeshBasicMaterial({ transparent: true, opacity: 0.38, depthWrite: false });
      const m = instancedBoxes(T, cells.map(([x, z, v]) => ({ x, y: fy + 0.02, z, w: cell * 0.94, h: 0.01, d: cell * 0.94, yaw, color: new T.Color(heatRGB(v)) })), mat);
      m.name = 'fit.heat'; m.renderOrder = 1; m.visible = heatOn;
      this.heatMesh = m; g.add(m);
    }
    const eq = r.equipment;
    const L = (eq.length_ft ?? 8) * FT, W = (eq.width_ft ?? 4) * FT, H = (eq.height_lowered_ft ?? 7) * FT;
    const color = vehicleColor(eq.key ?? eq.name ?? '');
    const ok = r.ok;
    // route strip: the vehicle's width along the path
    const path = r.path ?? [];
    if (path.length > 1) {
      const pathCol = ok === false ? 0xff6b5e : 0x2ad4ff;
      const boxes = [] as any[];
      for (let i = 1; i < path.length; i++) {
        const [, x0, z0] = path[i - 1], [, x1, z1] = path[i];
        const len = Math.hypot(x1 - x0, z1 - z0);
        if (len < 0.05) continue;
        boxes.push({ x: (x0 + x1) / 2, y: fy + 0.035, z: (z0 + z1) / 2, w: len + W, h: 0.02, d: W, yaw: Math.atan2(-(z1 - z0), x1 - x0) });
      }
      if (boxes.length) {
        const m = instancedBoxes(T, boxes, new T.MeshBasicMaterial({ color: pathCol, transparent: true, opacity: 0.35, depthWrite: false }));
        m.renderOrder = 2; g.add(m);
        const pos: number[] = [];
        for (let i = 1; i < path.length; i++) pos.push(path[i - 1][1], fy + 0.08, path[i - 1][2], path[i][1], fy + 0.08, path[i][2]);
        const geo = new T.BufferGeometry(); geo.setAttribute('position', new T.Float32BufferAttribute(pos, 3));
        g.add(new T.LineSegments(geo, new T.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, depthTest: false })));
      }
      this.fitDuration = path[path.length - 1][0];
      // the vehicle at true size: body + mast (front = local +x) + edges + label
      const vg = new T.Group();
      const body = new T.Mesh(new T.BoxGeometry(L, H * 0.55, W), new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 }));
      body.position.y = H * 0.275; vg.add(body);
      const mast = new T.Mesh(new T.BoxGeometry(0.15, H, W * 0.8), new T.MeshBasicMaterial({ color: 0x23262b }));
      mast.position.set(L / 2 - 0.1, H / 2, 0); vg.add(mast);
      vg.add(boxEdges(T, [{ x: 0, y: H / 2, z: 0, w: L, h: H, d: W, yaw: 0 }], 0xffffff, 0.9));
      const lab = this.label(`${eq.name ?? 'Vehicle'} ${eq.width_ft} × ${eq.length_ft} ft`, color, 0.75);
      lab.position.y = H + 0.8; vg.add(lab);
      g.add(vg);
      this.vehicle = vg;
    }
    // start points (doors / chosen start)
    for (const s of (r as any).starts ?? []) {
      const ring = new T.Mesh(new T.RingGeometry(0.35, 0.5, 24), new T.MeshBasicMaterial({ color: 0x2ad4ff, depthTest: false, transparent: true }));
      ring.rotation.x = -Math.PI / 2; ring.position.set(s.x, fy + 0.05, s.z); g.add(ring);
    }
    // pinch points: red posts (fail) / white (narrowest, ok) with labels
    for (const p of r.pinch_points ?? []) {
      const bad = p.ok === false;
      const col = bad ? 0xff4136 : 0xe9edf2;
      const mat = new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.95, depthTest: false });
      g.add(this.bar(p.x, fy, p.z, p.x, fy + 3.2, p.z, 0.14, mat));
      const ring = new T.Mesh(new T.RingGeometry(Math.max(0.3, p.width_m / 2 - 0.08), Math.max(0.42, p.width_m / 2), 40), mat);
      ring.rotation.x = -Math.PI / 2; ring.position.set(p.x, fy + 0.06, p.z); g.add(ring);
      const lab = this.label(p.label ?? `needs ${(p.need_m / FT).toFixed(1)} ft, has ${(p.width_m / FT).toFixed(1)} ft`, col, bad ? 0.85 : 0.6);
      lab.position.set(p.x, fy + 3.8, p.z); g.add(lab);
    }
    // failing checks that carry a location and aren't pinch points already (doors, turns)
    for (const c of r.checks ?? []) {
      if (c.ok !== false || !c.at || c.kind === 'corridor' || c.kind === 'aisle' || c.kind === 'turn') continue;
      const lab = this.label(`${c.label}: needs ${c.need} ${c.unit}, has ${c.have ?? '?'} ${c.unit}`, 0xff4136, 0.8);
      lab.position.set(c.at.x, fy + 4.6, c.at.z); g.add(lab);
    }
    this.fitG = g; this.root.add(g);
    this.updateFit(0);
  }

  setHeat(on: boolean) { if (this.heatMesh) this.heatMesh.visible = on; }

  /** Vehicle pose at time t along the fit path (linear, shortest-way yaw). */
  updateFit(t: number) {
    const r = this.fit, v = this.vehicle;
    if (!r || !v || !r.path?.length) return;
    const k = r.path;
    let i = 0;
    while (i + 1 < k.length && k[i + 1][0] <= t) i++;
    const a = k[i], b = k[Math.min(i + 1, k.length - 1)];
    let x = a[1], z = a[2], yaw = a[3];
    if (b !== a && b[0] > a[0] && t > a[0]) {
      const f = Math.min(1, (t - a[0]) / (b[0] - a[0]));
      x = a[1] + (b[1] - a[1]) * f; z = a[2] + (b[2] - a[2]) * f;
      let dy = b[3] - a[3];
      while (dy > Math.PI) dy -= 2 * Math.PI;
      while (dy < -Math.PI) dy += 2 * Math.PI;
      yaw = a[3] + dy * f;
    }
    v.position.set(x, this.floorY, z);
    v.rotation.y = yaw;
  }

  vehiclePos() { return this.vehicle ? { x: this.vehicle.position.x, z: this.vehicle.position.z } : null; }

  updateLabels(camera: any) {
    if (!camera || !this.labels.length) return;
    const T = this.T;
    const v = this.tmpV ?? (this.tmpV = new T.Vector3());
    const cam = camera.getWorldPosition ? camera.getWorldPosition(new T.Vector3()) : camera.position;
    for (const l of this.labels) {
      l.getWorldPosition(v);
      const d = v.distanceTo(cam);
      const h = Math.min(1.6, Math.max(0.15, d * 0.036)) * (l.userData.k ?? 1);
      l.scale.set(h * (l.userData.aspect ?? 3), h, 1);
      const fade = d <= LABEL_FADE_NEAR ? 1 : d >= LABEL_FADE_FAR ? 0 : 1 - (d - LABEL_FADE_NEAR) / (LABEL_FADE_FAR - LABEL_FADE_NEAR);
      l.material.opacity = fade;
      l.visible = this.showLabels && fade > 0.01;
    }
  }

  /** Bounds of what the overlays show (for camera framing). */
  bounds() {
    const pts: [number, number][] = [];
    for (const d of this.doors) pts.push([d.center.x, d.center.z]);
    const r = this.fit;
    if (r) {
      for (const c of r.heat?.cells ?? []) pts.push([c[0], c[1]]);
      for (const p of r.path ?? []) pts.push([p[1], p[2]]);
    }
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]), zs = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
    return { minX, maxX, minZ, maxZ, cx: (minX + maxX) / 2, cz: (minZ + maxZ) / 2, size: Math.max(maxX - minX, maxZ - minZ, 10) };
  }

  clear() { for (const k of ['doorsG', 'previewG', 'fitG', 'targetG'] as const) this.drop(k); }
}
