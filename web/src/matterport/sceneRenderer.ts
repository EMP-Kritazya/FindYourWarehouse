// Builds the sim scene as plain three.js objects under one Group that lives inside Showcase's own scene graph
// (via a custom Bundle-SDK component whose outputs.objectRoot is that Group). THREE is Showcase's copy
// (this.context.three), so nothing here imports three.js.
import type { SimScene, VehicleKey } from './types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type T3 = any;

export const VEHICLE_COLORS: { match: RegExp; color: number; label: string }[] = [
  { match: /reach|order_picker|vna|turret|narrow/i, color: 0x2f7cf6, label: 'Reach / narrow-aisle' },
  { match: /pallet_jack|walkie|tugger|jack/i, color: 0x22b35e, label: 'Pallet jack' },
  { match: /amr|robot/i, color: 0xa25cf2, label: 'Robot' },
  { match: /fork|counterbalance|lift/i, color: 0xff8a1f, label: 'Forklift' },
];
export function vehicleColor(kind: string): number {
  for (const v of VEHICLE_COLORS) if (v.match.test(kind)) return v.color;
  return 0x9aa3ad;
}

/** Short on-screen name: ids may be numbers (stream B) or strings (fixture). */
export function vehicleLabel(id: string | number, kind: string) {
  const sid = String(id);
  if (/[a-z]/i.test(sid)) return sid;
  const k = /reach/i.test(kind) ? 'Reach' : /pallet_jack|jack/i.test(kind) ? 'Jack' : /order_picker/i.test(kind) ? 'Picker'
    : /vna|turret/i.test(kind) ? 'VNA' : /fork|counterbalance/i.test(kind) ? 'Forklift' : /amr|robot/i.test(kind) ? 'AMR' : 'Vehicle';
  return `${k} ${sid}`;
}

const LABEL_FADE_NEAR = 32, LABEL_FADE_FAR = 42;
const DEFAULT_DIMS = { l: 2.5, w: 1.1, h: 2.1 };

function heatColor(T: T3, v: number) {
  // green -> yellow -> red
  const c = new T.Color();
  const t = Math.max(0, Math.min(1, v));
  c.setHSL((1 - t) * 0.33, 0.9, 0.5);
  return c;
}

export function makeLabel(T: T3, doc: Document, text: string, color: number) {
  const canvas = doc.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  const font = '600 44px Inter, system-ui, sans-serif';
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 36;
  canvas.width = w; canvas.height = 68;
  ctx.font = font;
  ctx.fillStyle = 'rgba(12,14,18,0.82)';
  const r = 16;
  ctx.beginPath();
  ctx.moveTo(r, 0); ctx.lineTo(w - r, 0); ctx.quadraticCurveTo(w, 0, w, r); ctx.lineTo(w, 68 - r);
  ctx.quadraticCurveTo(w, 68, w - r, 68); ctx.lineTo(r, 68); ctx.quadraticCurveTo(0, 68, 0, 68 - r);
  ctx.lineTo(0, r); ctx.quadraticCurveTo(0, 0, r, 0); ctx.fill();
  ctx.fillStyle = '#' + color.toString(16).padStart(6, '0');
  ctx.fillRect(0, 0, 10, 68);
  ctx.fillStyle = '#fff';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 22, 36);
  const tex = new T.CanvasTexture(canvas);
  const mat = new T.SpriteMaterial({ map: tex, depthTest: false, depthWrite: false, transparent: true });
  const s = new T.Sprite(mat);
  const hgt = 0.9;
  s.scale.set((hgt * w) / 68, hgt, 1);
  s.userData.aspect = w / 68;
  s.userData.k = 1;
  s.renderOrder = 999;
  return s;
}

/** Unit-box edge segments for N oriented boxes, merged into one LineSegments (1 draw call). */
export function boxEdges(T: T3, boxes: { x: number; y: number; z: number; w: number; h: number; d: number; yaw: number }[], color: number, opacity = 0.9) {
  const E = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
  // plain array: typed arrays from this window fail three.js instanceof checks inside the Showcase iframe realm
  const pos: number[] = [];
  for (const b of boxes) {
    const c = Math.cos(b.yaw), s = Math.sin(b.yaw);
    const corners: number[][] = [];
    for (let i = 0; i < 8; i++) {
      const lx = (i & 1 ? 0.5 : -0.5) * b.w, ly = (i & 4 ? 0.5 : -0.5) * b.h, lz = (i & 2 ? 0.5 : -0.5) * b.d;
      // three.js rotation.y: x' = c*lx + s*lz, z' = -s*lx + c*lz
      corners.push([b.x + c * lx + s * lz, b.y + ly, b.z - s * lx + c * lz]);
    }
    for (const [a, bb] of E) pos.push(...corners[a], ...corners[bb]);
  }
  const g = new T.BufferGeometry();
  g.setAttribute('position', new T.Float32BufferAttribute(pos, 3));
  return new T.LineSegments(g, new T.LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
}

export function instancedBoxes(T: T3, boxes: { x: number; y: number; z: number; w: number; h: number; d: number; yaw: number; color?: any }[], mat: any) {
  const geo = new T.BoxGeometry(1, 1, 1);
  const mesh = new T.InstancedMesh(geo, mat, Math.max(1, boxes.length));
  mesh.count = boxes.length;
  const m = new T.Matrix4(), q = new T.Quaternion(), p = new T.Vector3(), s = new T.Vector3(), up = new T.Vector3(0, 1, 0);
  boxes.forEach((b, i) => {
    q.setFromAxisAngle(up, b.yaw); p.set(b.x, b.y, b.z); s.set(b.w, b.h, b.d);
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
    if (b.color) mesh.setColorAt(i, b.color);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.frustumCulled = false;
  return mesh;
}

interface VehicleObj {
  forks?: any;
  id: string;
  kind: string;
  keys: VehicleKey[];
  group: any;
  load: any;
  cursor: number;
  pos: { x: number; z: number };
}

export class SceneRenderer {
  T: T3;
  root: any;
  content: any = null;
  scene: SimScene | null = null;
  vehicles: VehicleObj[] = [];
  doorMats: any[] = [];
  labels: any[] = [];
  private tmpV: any = null;
  duration = 0;
  showLabels = true;

  doc: Document;
  // doc = the Showcase iframe document, so canvases belong to the same realm as its three.js
  constructor(T: T3, root: any, doc?: Document) { this.T = T; this.root = root; this.doc = doc ?? document; }

  clear() {
    if (!this.content) return;
    this.root.remove(this.content);
    this.content.traverse((o: any) => {
      o.geometry?.dispose?.();
      const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
      for (const m of mats) { m.map?.dispose?.(); m.dispose?.(); }
    });
    this.content = null; this.vehicles = []; this.doorMats = []; this.labels = []; this.scene = null;
  }

  setScene(sc: SimScene | null) {
    this.clear();
    if (!sc) return;
    const T = this.T;
    this.scene = sc;
    const fy = sc.floor_y ?? 0;
    const g = new T.Group();
    g.name = 'pi.sim';

    // scanned area outline
    if (sc.scanned_area && sc.scanned_area.length > 2) {
      const pts = sc.scanned_area.map(([x, z]) => new T.Vector3(x, fy + 0.04, z));
      const geo = new T.BufferGeometry().setFromPoints(pts);
      g.add(new T.LineLoop(geo, new T.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.8 })));
    }

    // heatmap tiles
    const heat = sc.heat?.cells?.filter((c) => c[2] > 0.02) ?? [];
    if (heat.length) {
      const cell = sc.heat!.cell || 1.25;
      const mat = new T.MeshBasicMaterial({ transparent: true, opacity: 0.42, depthWrite: false });
      const m = instancedBoxes(T, heat.map(([x, z, v]) => ({ x, y: fy + 0.015, z, w: cell * 0.96, h: 0.01, d: cell * 0.96, yaw: 0, color: heatColor(T, v) })), mat);
      m.name = 'heat'; m.renderOrder = 1;
      g.add(m);
    }

    // staging lanes
    const lanes = sc.lanes ?? [];
    if (lanes.length) {
      const inC = new T.Color(0x19c39a), outC = new T.Color(0xb36bff);
      const boxes = lanes.map((l) => ({ x: l.x, y: fy + 0.03, z: l.z, w: l.w, h: 0.04, d: l.d, yaw: l.yaw ?? 0, color: l.inbound === false ? outC : inC }));
      const m = instancedBoxes(T, boxes, new T.MeshBasicMaterial({ transparent: true, opacity: 0.55, depthWrite: false }));
      m.name = 'lanes'; m.renderOrder = 2;
      g.add(m);
      g.add(boxEdges(T, boxes, 0xffffff, 0.5));
    }

    // racks: real structure (uprights, beams per level, loaded pallets) when the plan gives bays/levels,
    // else the old translucent boxes
    const detailed = (sc.racks ?? []).filter((r) => r.bays && r.levels)
    if (detailed.length) {
      g.add(...this.rackStructure(T, detailed, fy));
      const amb = new T.AmbientLight(0xffffff, 0.55);
      const sun = new T.DirectionalLight(0xffffff, 0.75);
      sun.position.set(30, 60, 20);
      const hemi = new T.HemisphereLight(0xeef3ff, 0x5a4a3a, 0.35);
      g.add(amb, sun, hemi);
    }
    for (const rm of sc.removed ?? []) {
      if (rm.ring.length < 3) continue;
      const pts = [...rm.ring, rm.ring[0]].map(([x, z]) => new T.Vector3(x, fy + 0.05, z));
      const line = new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: 0xff4d4d, transparent: true, opacity: 0.9, depthTest: false }));
      line.renderOrder = 6;
      g.add(line);
    }
    const ZONE_RGB: Record<string, number> = { open: 0x22c55e, shelving: 0xf59e0b, selective: 0x4fd1c5, dense: 0xa78bfa, cantilever: 0xd97706 }
    for (const zn of sc.zones ?? []) {
      if (zn.ring.length < 3) continue
      const col = ZONE_RGB[zn.type] ?? 0x9ca3af
      const shape = new T.Shape(zn.ring.map(([x, z]) => new T.Vector2(x, -z)))
      const mesh = new T.Mesh(new T.ShapeGeometry(shape), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: zn.type === 'open' ? 0.22 : 0.08, depthWrite: false, side: T.DoubleSide }))
      mesh.rotation.x = -Math.PI / 2
      mesh.position.y = fy + 0.03
      mesh.renderOrder = 2
      g.add(mesh)
      const pts = [...zn.ring, zn.ring[0]].map(([x, z]) => new T.Vector3(x, fy + 0.04, z))
      g.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: col, transparent: true, opacity: 0.9 })))
      if (zn.type === 'open') {
        const cx = zn.ring.reduce((a, p) => a + p[0], 0) / zn.ring.length, cz = zn.ring.reduce((a, p) => a + p[1], 0) / zn.ring.length
        const lab = makeLabel(T, this.doc, zn.label, col)
        lab.position.set(cx, fy + 2.5, cz)
        this.labels.push(lab)
        g.add(lab)
      }
    }
    const racks = (sc.racks ?? []).filter((r) => !(r.bays && r.levels));
    if (racks.length) {
      const boxes = racks.map((r) => ({ x: r.x, y: fy + r.h / 2, z: r.z, w: r.w, h: r.h, d: r.d, yaw: r.yaw ?? 0 }));
      const m = instancedBoxes(T, boxes, new T.MeshBasicMaterial({ color: 0x3b6fd8, transparent: true, opacity: 0.28, depthWrite: false, side: T.DoubleSide }));
      m.name = 'racks'; m.renderOrder = 3;
      g.add(m);
      g.add(boxEdges(T, boxes, 0x9cc0ff, 0.95));
    }

    // doors: NOT drawn from sim scenes. Old runs carry ASSUMED doors (a CoStar count at 4 m pitch) that land on
    // blank wall; only doors a person marked in the scan are drawn (OverlayRenderer.setDoors, from the door store).
    this.doorMats = [];

    // vehicles
    this.duration = sc.duration_s ?? 0;
    // shared materials (one per colour) keep draw-state changes down with dozens of vehicles
    const matCache = new Map<number, any>();
    const mat = (c: number) => { let m = matCache.get(c); if (!m) { m = new T.MeshBasicMaterial({ color: c }); matCache.set(c, m); } return m; };
    for (const v of sc.vehicles ?? []) {
      if (!v.keys?.length) continue;
      const dims = v.dims ?? DEFAULT_DIMS;
      const color = vehicleColor(v.kind);
      const vg = new T.Group();
      const body = new T.Mesh(new T.BoxGeometry(dims.l, dims.h * 0.55, dims.w), mat(color));
      body.position.y = dims.h * 0.275;
      vg.add(body);
      // mast / overhead guard to show height and heading (at the front, local +x)
      const mast = new T.Mesh(new T.BoxGeometry(0.15, dims.h, dims.w * 0.8), mat(0x23262b));
      mast.position.set(dims.l / 2 - 0.1, dims.h / 2, 0);
      vg.add(mast);
      vg.add(boxEdges(T, [{ x: 0, y: dims.h * 0.275, z: 0, w: dims.l, h: dims.h * 0.55, d: dims.w, yaw: 0 }], 0x111111, 1));
      // forks (two tines on a carriage) that ride up the mast with key[5]
      const forks = new T.Group();
      for (const zz of [-dims.w * 0.22, dims.w * 0.22]) {
        const tine = new T.Mesh(new T.BoxGeometry(1.1, 0.05, 0.12), mat(0x23262b));
        tine.position.set(dims.l / 2 + 0.55, 0, zz);
        forks.add(tine);
      }
      const carriage = new T.Mesh(new T.BoxGeometry(0.08, 0.9, dims.w * 0.75), mat(0x30343a));
      carriage.position.set(dims.l / 2 + 0.02, 0.45, 0);
      forks.add(carriage);
      forks.position.y = 0.1;
      vg.add(forks);
      const load = new T.Mesh(new T.BoxGeometry(1.2, 1.1, 1.0), mat(0xc8955a));
      load.position.set(dims.l / 2 + 0.6, 0.65, 0);
      load.visible = false;
      vg.add(load);
      const lab = makeLabel(T, this.doc, vehicleLabel(v.id, v.kind), color);
      lab.position.y = dims.h + 0.7;
      lab.name = 'label';
      this.labels.push(lab);
      vg.add(lab);
      g.add(vg);
      const last = v.keys[v.keys.length - 1][0];
      if (last > this.duration) this.duration = last;
      this.vehicles.push({ id: String(v.id), kind: v.kind, keys: v.keys, group: vg, load, forks, cursor: 0, pos: { x: v.keys[0][1], z: v.keys[0][2] } });
    }
    for (const t of sc.trucks ?? []) if ((t.depart_s ?? 0) > this.duration) this.duration = t.depart_s!;

    this.content = g;
    this.root.add(g);
    this.update(0);
  }

  /**
   * Keep labels a roughly constant on-screen size (world height ∝ camera distance, clamped) and fade them out
   * between LABEL_FADE_NEAR and LABEL_FADE_FAR metres. Called every frame with Showcase's camera.
   */
  updateLabels(camera: any) {
    if (!camera || !this.labels.length) return;
    const T = this.T;
    const v = this.tmpV ?? (this.tmpV = new T.Vector3());
    const cam = camera.getWorldPosition ? camera.getWorldPosition(new T.Vector3()) : camera.position;
    for (const l of this.labels) {
      l.getWorldPosition(v);
      const d = v.distanceTo(cam);
      const h = Math.min(1.1, Math.max(0.15, d * 0.036)) * (l.userData.k ?? 1);
      l.scale.set(h * (l.userData.aspect ?? 3), h, 1);
      const fade = d <= LABEL_FADE_NEAR ? 1 : d >= LABEL_FADE_FAR ? 0 : 1 - (d - LABEL_FADE_NEAR) / (LABEL_FADE_FAR - LABEL_FADE_NEAR);
      l.material.opacity = fade;
      l.visible = this.showLabels && fade > 0.01;
    }
  }

  setLabels(on: boolean) {
    this.showLabels = on;
    for (const l of this.labels) l.visible = on;
  }

  /** Rack rows as real structure: blue uprights at every bay line on both faces, orange beams at every level, a
   *  loaded pallet on each beam pair (x deep). Unreachable rows (ok === false) are tinted red. Instanced: thousands
   *  of pallets stay cheap. Row frame: local x = w, local z = d; rows run along `along`. */
  rackStructure(T: any, rows: NonNullable<SimScene['racks']>, fy: number) {
    const uprights: any[] = [], beams: any[] = [], pallets: any[] = [], bad: any[] = [], lumber: any[] = [], bases: any[] = []
    const put = (r: any, lx: number, ly: number, lz: number, w: number, h: number, d: number, list: any[]) => {
      const yaw = r.yaw ?? 0, c = Math.cos(yaw), s = Math.sin(yaw)
      list.push({ x: r.x + c * lx + s * lz, y: fy + ly, z: r.z - s * lx + c * lz, w, h, d, yaw })
    }
    for (const r of rows) {
      const along = r.along ?? 'd'
      const L = along === 'd' ? r.d : r.w, D = along === 'd' ? r.w : r.d       // row length / rack depth
      const bays = r.bays!, levels = r.levels!, lh = r.level_h ?? 1.68, deep = r.deep ?? 1
      const H = levels * lh, bay = L / bays
      const shelf = r.type === 'shelving'
      // place in row frame (a = along the row, b = across) then map to local x/z
      const P = (a: number, y: number, b: number, sa: number, sh: number, sb: number, list: any[]) =>
        along === 'd' ? put(r, b, y, a, sb, sh, sa, list) : put(r, a, y, b, sa, sh, sb, list)
      const target = r.ok === false ? bad : null
      if (r.type === 'cantilever') {
        // a centre spine of columns, arms out to both faces at every level, a lumber bundle on each arm level
        for (let i = 0; i <= bays; i++) {
          const a = -L / 2 + i * bay
          P(a, H / 2, 0, 0.25, H, 0.3, target ?? uprights)
          for (let lv = 0; lv < levels; lv++) P(a, lv * lh + 0.25, 0, 0.12, 0.1, D, target ?? beams)
        }
        for (let lv = 0; lv < levels; lv++) {
          for (const b of [-D / 4, D / 4]) P(0, lv * lh + 0.55, b, L * 0.98, 0.5, D / 2 - 0.15, lumber)
        }
        continue
      }
      for (let i = 0; i <= bays; i++) {
        const a = -L / 2 + i * bay
        for (const b of [-D / 2 + 0.04, D / 2 - 0.04]) P(a, H / 2, b, 0.075, H, 0.075, target ?? uprights)
      }
      for (let lv = 1; lv <= levels; lv++) {
        const y = lv * lh - 0.06
        for (const b of [-D / 2 + 0.04, D / 2 - 0.04]) P(0, y, b, L, 0.11, 0.05, target ?? beams)
      }
      if (shelf) continue
      // pallets: 2 per bay per level on each face (x deep), sitting on the beam below each level
      const faces = deep > 1 ? [-D / 2 + D / 8, -D / 8, D / 8, D / 2 - D / 8] : [-D / 4, D / 4]
      const pd = deep > 1 ? D / 4 - 0.08 : D / 2 - 0.12
      for (let i = 0; i < bays; i++) {
        for (let k = 0; k < 2; k++) {
          const a = -L / 2 + i * bay + bay * (k === 0 ? 0.27 : 0.73)
          for (let lv = 0; lv < levels; lv++) {
            const base = lv * lh + (lv === 0 ? 0.0 : 0.02)
            if (((i * 7 + k * 3 + lv * 5) % 11) === 0) continue          // a few empty slots read as a working warehouse
            for (const b of faces) {
              P(a, base + 0.07, b, bay * 0.43, 0.14, pd, bases)          // the wooden pallet
              P(a, base + 0.14 + 0.55, b, bay * 0.40, 1.1, pd - 0.06, pallets)   // the shrink-wrapped load
            }
          }
        }
      }
    }
    const out: any[] = []
    const mk = (list: any[], color: number, opacity = 1, vary = 0) => {
      if (!list.length) return
      if (vary) {                                                   // per-instance shade so loads don't merge into a wall
        const base = new T.Color(color)
        list.forEach((b, i) => { const f = 1 - vary / 2 + ((i * 2654435761) % 1000) / 1000 * vary; b.color = base.clone().multiplyScalar(f) })
      }
      const m = instancedBoxes(T, list, new T.MeshLambertMaterial({ color: vary ? 0xffffff : color, transparent: opacity < 1, opacity }))
      m.renderOrder = 3
      out.push(m)
    }
    mk(uprights, 0x1f5fbf); mk(beams, 0xe8771c); mk(bases, 0x7a5a36); mk(pallets, 0xcfb38a, 1, 0.35); mk(lumber, 0xd8b077, 1, 0.25); mk(bad, 0xff4d4d, 0.85)
    return out
  }

  /** Place every vehicle at sim time t (linear interpolation of key-frames). */
  update(t: number) {
    const fy = this.scene?.floor_y ?? 0;
    for (const v of this.vehicles) {
      const k = v.keys;
      let i = v.cursor;
      if (i >= k.length || k[i][0] > t) i = 0; // scrubbed backwards
      while (i + 1 < k.length && k[i + 1][0] <= t) i++;
      v.cursor = i;
      const a = k[i], b = k[Math.min(i + 1, k.length - 1)];
      let x = a[1], z = a[2], yaw = a[3];
      if (b !== a && t > a[0] && b[0] > a[0]) {
        const f = Math.min(1, (t - a[0]) / (b[0] - a[0]));
        x = a[1] + (b[1] - a[1]) * f; z = a[2] + (b[2] - a[2]) * f;
        let dy = b[3] - a[3];
        while (dy > Math.PI) dy -= 2 * Math.PI;
        while (dy < -Math.PI) dy += 2 * Math.PI;
        yaw = a[3] + dy * f;
      }
      v.group.position.set(x, fy, z);
      v.group.rotation.y = yaw;
      v.load.visible = a[4] === 'loaded' || a[4] === 'drop';
      const fa = a[5] ?? 0, fb = b[5] ?? fa
      const fk = b !== a && b[0] > a[0] ? fa + (fb - fa) * Math.min(1, Math.max(0, (t - a[0]) / (b[0] - a[0]))) : fa
      v.load.position.y = 0.65 + fk
      if (v.forks) v.forks.position.y = 0.1 + fk
      v.pos.x = x; v.pos.z = z;
    }
    // doors light up while a truck is docked
    const trucks = this.scene?.trucks ?? [];
    if (trucks.length && this.doorMats.length) {
      const busy = new Set<number>();
      for (const tr of trucks) if (tr.door != null && t >= (tr.dock_s ?? tr.arrive_s ?? Infinity) && t < (tr.depart_s ?? -Infinity)) busy.add(tr.door);
      this.doorMats.forEach((m, i) => { if (m) m.opacity = busy.has(i) ? 0.95 : 0.4; });
    }
  }

  vehiclePos(id: string) { return this.vehicles.find((v) => v.id === id)?.pos ?? null; }

  bounds() {
    const sc = this.scene;
    if (!sc) return null;
    const pts: [number, number][] = [];
    for (const p of sc.scanned_area ?? []) pts.push(p);
    for (const r of sc.racks ?? []) pts.push([r.x, r.z]);
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]), zs = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
    return { minX, maxX, minZ, maxZ, cx: (minX + maxX) / 2, cz: (minZ + maxZ) / 2, size: Math.max(maxX - minX, maxZ - minZ) };
  }
}
