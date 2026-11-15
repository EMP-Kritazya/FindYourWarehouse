// Connects to the self-hosted Bundle SDK inside an iframe, mounts one custom component ('pi.simstage') whose
// objectRoot Group holds the whole sim scene, and drives playback from the component's onTick (Showcase's own
// render loop: verified that onTick runs every frame and that mutating three.js objects there re-renders).
import { SceneRenderer } from './sceneRenderer';
import { OverlayRenderer } from './overlays';
import type { FitResult, MarkedDoor, MpSdk, Pt3, SimScene } from './types';

export interface Hit { position: Pt3; normal?: Pt3 | null; object?: string }

const DEG = 180 / Math.PI;

export class StageController {
  sdk: MpSdk = null;
  renderer: SceneRenderer | null = null;
  sceneObject: any = null;
  t = 0;
  playing = false;
  speed = 10;
  follow: string | null = null;
  sweeps: { id: string; x: number; y: number; z: number }[] = [];
  camera: any = null;
  onTime?: (t: number) => void;
  onError?: (msg: string) => void;
  onFitTime?: (t: number) => void;
  private pendingScene: SimScene | null = null;
  private lastTimeEmit = 0;
  private followTimer: number | null = null;
  private disposed = false;
  private registration: any = null;
  // ---- S4 overlays (doors, marking preview, equipment test)
  overlay: OverlayRenderer | null = null;
  private pendingOverlay: ((o: OverlayRenderer) => void)[] = [];
  lastHit: Hit | null = null;
  onHover?: (h: Hit | null) => void;
  /** set to receive clicks on the scan (Showcase's own click-to-move is suppressed while set) */
  onClick?: (h: Hit) => void;
  fitT = 0;
  fitPlaying = false;
  fitSpeed = 1;
  private unsubs: (() => void)[] = [];

  async connect(iframe: HTMLIFrameElement): Promise<void> {
    const w = iframe.contentWindow as any;
    if (!w?.MP_SDK) throw new Error('Bundle SDK not found in iframe (is /bundle/showcase.html served?)');
    const sdk = await w.MP_SDK.connect(w);
    if (this.disposed) return;
    this.sdk = sdk;
    (window as any).__piStage = this; // debugging handle
    await sdk.App.state.waitUntil((s: any) => s.phase === sdk.App.Phase.PLAYING);
    sdk.Sweep.data.subscribe({
      onCollectionUpdated: (c: Record<string, any>) => {
        this.sweeps = Object.values(c).filter((s: any) => s.enabled !== false && s.position)
          .map((s: any) => ({ id: s.sid ?? s.id, x: s.position.x, y: s.position.y, z: s.position.z }));
      },
    });
    const self = this;
    this.registration = await sdk.Scene.register('pi.simstage', () => ({
      // NOTE: empty inputs + addComponent() without initial inputs => onInit never runs ('t.onChanged is not a function').
      inputs: { enabled: true },
      outputs: {},
      onInit(this: any) {
        const T = this.context.three;
        const root = new T.Group();
        root.name = 'pi.root';
        this.outputs.objectRoot = root;
        self.renderer = new SceneRenderer(T, root, this.context.renderer?.domElement?.ownerDocument);
        self.camera = this.context.camera;
        self.overlay = new OverlayRenderer(T, root, this.context.renderer?.domElement?.ownerDocument ?? document);
        self.overlay.floorY = self.guessFloorY();
        if (self.pendingScene) { self.renderer.setScene(self.pendingScene); self.pendingScene = null; }
        for (const f of self.pendingOverlay) f(self.overlay);
        self.pendingOverlay = [];
      },
      onTick(dt: number) { self.tick(dt); },
      onDestroy() { self.renderer?.clear(); self.renderer = null; self.overlay?.clear(); self.overlay = null; },
    }));
    const [obj] = await sdk.Scene.createObjects(1);
    obj.addNode().addComponent('pi.simstage', { enabled: true });
    obj.start();
    this.sceneObject = obj;
    const sub = sdk.Pointer.intersection.subscribe((h: any) => {
      this.lastHit = h?.position ? { position: { ...h.position }, normal: { ...h.normal }, object: h.object } : null;
      this.onHover?.(this.lastHit);
    });
    this.unsubs.push(() => sub?.cancel?.());
    this.attachClicks(iframe);
  }

  /** Clicks on the scan. A capture-phase listener in the (same-origin) Showcase window sees the pointer before
   * Showcase does; while `onClick` is set, a short, still click is consumed so Showcase doesn't fly to a sweep. */
  private attachClicks(iframe: HTMLIFrameElement) {
    const w = iframe.contentWindow as any;
    if (!w) return;
    let down: { x: number; y: number; t: number } | null = null;
    const isClick = (e: MouseEvent) => down && e.button === 0 && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6 && performance.now() - down.t < 600;
    const onDown = (e: PointerEvent) => { if (e.button === 0) down = { x: e.clientX, y: e.clientY, t: performance.now() }; };
    // the click ends on pointerup, or on mouseup when the browser cancelled the pointer (seen in headless Chromium)
    const onUp = (e: MouseEvent) => {
      if (!down) return;
      if (!this.onClick || !isClick(e)) { down = null; return; }
      down = null;
      e.stopImmediatePropagation(); e.preventDefault();
      // let Showcase end its own pointer gesture without treating it as a click
      if (e.type === 'pointerup') {
        try { e.target?.dispatchEvent(new w.PointerEvent('pointercancel', { pointerId: (e as PointerEvent).pointerId, bubbles: true, clientX: e.clientX, clientY: e.clientY })); } catch { /* */ }
      }
      const cb = this.onClick;
      // ray-cast at the exact click pixel (Pointer.intersection can lag behind, e.g. without real mouse motion)
      this.pick(e.clientX, e.clientY).then((h) => { const hit = h ?? this.lastHit; if (hit) cb(hit); });
    };
    let raf = 0, mx = 0, my = 0;
    const onMove = (e: PointerEvent) => {
      if (!this.onHover) return;
      mx = e.clientX; my = e.clientY;
      if (raf) return;
      raf = w.requestAnimationFrame(() => {
        raf = 0;
        this.pick(mx, my).then((h) => { if (h) { this.lastHit = h; this.onHover?.(h); } });
      });
    };
    w.addEventListener('pointermove', onMove, true);
    this.unsubs.push(() => { try { w.removeEventListener('pointermove', onMove, true); } catch { /* */ } });
    const swallow = (e: Event) => { if (this.onClick) { e.stopImmediatePropagation(); e.preventDefault(); } };
    w.addEventListener('pointerdown', onDown, true);
    w.addEventListener('pointerup', onUp, true);
    w.addEventListener('mouseup', onUp, true);
    w.addEventListener('click', swallow, true);
    w.addEventListener('dblclick', swallow, true);
    this.unsubs.push(() => {
      try { w.removeEventListener('pointerdown', onDown, true); w.removeEventListener('pointerup', onUp, true); w.removeEventListener('mouseup', onUp, true);
        w.removeEventListener('click', swallow, true); w.removeEventListener('dblclick', swallow, true); } catch { /* */ }
    });
  }

  /** World point under a screen pixel of the Showcase iframe (nearest model hit), or null. */
  async pick(x: number, y: number): Promise<Hit | null> {
    try {
      const d = await this.sdk.Renderer.getWorldPositionData({ x, y });
      if (!d?.position) return null;
      const near = this.lastHit && Math.hypot(this.lastHit.position.x - d.position.x, this.lastHit.position.y - d.position.y, this.lastHit.position.z - d.position.z) < 0.3;
      return { position: { x: d.position.x, y: d.position.y, z: d.position.z }, normal: near ? this.lastHit!.normal : null, object: 'model' };
    } catch { return null; }
  }

  /** Floor height without a scene: the sweep cameras stand ~1.3 m above the floor. */
  guessFloorY() {
    const ys = this.sweeps.map((s) => s.y).sort((a, b) => a - b);
    return ys.length ? ys[Math.floor(ys.length / 2)] - 1.3 : 0;
  }

  withOverlay(f: (o: OverlayRenderer) => void) {
    if (this.overlay) f(this.overlay); else this.pendingOverlay.push(f);
  }

  setDoors(doors: MarkedDoor[], highlight?: string | null) { this.withOverlay((o) => o.setDoors(doors, highlight)); }
  setPreview(a: Pt3 | null, b: Pt3 | null, ok = true) { this.withOverlay((o) => o.setPreview(a, b, ok)); }
  setTarget(p: { x: number; z: number } | null) { this.withOverlay((o) => o.setTarget(p)); }
  setFit(r: FitResult | null, heat = true) {
    this.fitT = 0;
    this.withOverlay((o) => { if (r) o.floorY = r.floor_y; o.setFit(r, heat); });
  }
  setHeat(on: boolean) { this.withOverlay((o) => o.setHeat(on)); }
  get fitDuration() { return this.overlay?.fitDuration ?? 0; }
  seekFit(t: number) { this.fitT = Math.max(0, Math.min(t, this.fitDuration)); this.overlay?.updateFit(this.fitT); }

  setScene(sc: SimScene | null) {
    this.t = 0;
    if (this.renderer) this.renderer.setScene(sc);
    else this.pendingScene = sc;
    this.onTime?.(0);
  }

  get duration() { return this.renderer?.duration ?? 0; }

  seek(t: number) {
    this.t = Math.max(0, Math.min(t, this.duration || t));
    this.renderer?.update(this.t);
    this.onTime?.(this.t);
  }

  private tick(dtMs: number) {
    this.overlay?.updateLabels(this.camera);
    if (this.overlay?.fit && this.fitPlaying) {
      const d = this.fitDuration;
      this.fitT += (Math.min(dtMs, 250) / 1000) * this.fitSpeed;
      if (d && this.fitT > d + 2) this.fitT = 0;            // hold 2 s at the target, then replay
      this.overlay.updateFit(Math.min(this.fitT, d));
      const now = performance.now();
      if (now - this.lastTimeEmit > 200) { this.lastTimeEmit = now; this.onFitTime?.(Math.min(this.fitT, d)); }
    }
    if (!this.renderer?.scene) return;
    this.renderer.updateLabels(this.camera);
    if (this.playing) {
      const d = this.duration;
      this.t += (Math.min(dtMs, 250) / 1000) * this.speed;
      if (d && this.t > d) this.t = 0; // loop the shift
      this.renderer.update(this.t);
      const now = performance.now();
      if (now - this.lastTimeEmit > 200) { this.lastTimeEmit = now; this.onTime?.(this.t); }
    }
  }

  // ---------- camera ----------
  private lookRotation(from: { x: number; y: number; z: number }, to: { x: number; y: number; z: number }) {
    const dx = to.x - from.x, dy = to.y - from.y, dz = to.z - from.z;
    // Showcase: yaw 0 looks along -z, positive yaw turns toward -x; pitch negative looks down.
    return { x: Math.atan2(dy, Math.hypot(dx, dz)) * DEG, y: Math.atan2(-dx, -dz) * DEG };
  }

  async overview(instant = false) {
    this.stopFollow();
    const tr = instant ? this.sdk.Camera.TransitionType.INSTANT : this.sdk.Camera.TransitionType.FLY;
    const b = this.bounds();
    const M = this.sdk.Mode;
    if (!b) return this.cam(() => M.moveTo(M.Mode.DOLLHOUSE, { transition: tr }));
    const fy = this.floorY();
    const target = { x: b.cx, y: fy, z: b.cz };
    const pos = { x: b.cx, y: fy + b.size * 0.62, z: b.cz + b.size * 0.55 };
    return this.cam(() => M.moveTo(M.Mode.DOLLHOUSE, { position: pos, rotation: this.lookRotation(pos, target), transition: tr }));
  }

  /** Scene bounds, else the overlays' (doors / fit result), else the sweeps'. */
  bounds() {
    const b = this.renderer?.bounds() ?? this.overlay?.bounds();
    if (b) return b;
    if (!this.sweeps.length) return null;
    const xs = this.sweeps.map((s) => s.x), zs = this.sweeps.map((s) => s.z);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
    return { minX, maxX, minZ, maxZ, cx: (minX + maxX) / 2, cz: (minZ + maxZ) / 2, size: Math.max(maxX - minX, maxZ - minZ) };
  }

  floorY() { return this.renderer?.scene?.floor_y ?? this.overlay?.fit?.floor_y ?? this.overlay?.floorY ?? 0; }

  async floorplan() {
    this.stopFollow();
    const b = this.bounds();
    const M = this.sdk.Mode;
    const opts: any = { transition: this.sdk.Camera.TransitionType.FLY };
    if (b) { opts.position = { x: b.cx, y: 0, z: b.cz }; opts.rotation = { x: -90, y: 0 }; }
    return this.cam(() => M.moveTo(M.Mode.FLOORPLAN, opts));
  }

  /**
   * Walk mode: fly to a panorama sweep standing ~8–12 m inside the dock doors (centre of the scene `doors`),
   * facing them, so the real overhead doors, our markers, lanes and unloading trucks are in view.
   * With an explicit target (or no doors) it uses the sweep nearest that point / the scene centre.
   */
  async inside(target?: { x: number; z: number }) {
    this.stopFollow();
    const TT = this.sdk.Camera.TransitionType;
    const sc = this.renderer?.scene;
    const fy = this.floorY();
    const fallback = () => this.cam(() => this.sdk.Mode.moveTo(this.sdk.Mode.Mode.INSIDE, { transition: TT.FLY }));
    if (!this.sweeps.length) return fallback();
    const racks = sc?.racks ?? [];
    const inRack = (s: { x: number; z: number }) => racks.some((r) => {
      const c = Math.cos(r.yaw ?? 0), sn = Math.sin(r.yaw ?? 0), dx = s.x - r.x, dz = s.z - r.z;
      const lx = c * dx - sn * dz, lz = sn * dx + c * dz; // inverse of rotation.y
      return Math.abs(lx) < r.w / 2 + 0.3 && Math.abs(lz) < r.d / 2 + 0.3;
    });
    const free = this.sweeps.filter((s) => !inRack(s));
    if (!free.length) return fallback();
    let look: { x: number; y: number; z: number };
    let pick: { id: string; x: number; y: number; z: number };
    // only doors a person marked in the scan (assumed sim doors are never used to aim the camera)
    const doors = (this.overlay?.doors ?? []).filter((d) => d.kind !== 'personnel');
    if (!target && doors.length) {
      const cx = doors.reduce((a, d) => a + d.center.x, 0) / doors.length, cz = doors.reduce((a, d) => a + d.center.z, 0) / doors.length;
      let nx = doors.reduce((a, d) => a + d.normal.x, 0), nz = doors.reduce((a, d) => a + d.normal.z, 0);
      const nl = Math.hypot(nx, nz) || 1; nx /= nl; nz /= nl;
      const want = { x: cx - nx * 10, z: cz - nz * 10 };
      // score: distance to the ideal standpoint, heavily penalising sweeps not 6–16 m inside the door line
      const score = (s: { x: number; z: number }) => {
        const inward = -((s.x - cx) * nx + (s.z - cz) * nz);
        const pen = inward < 6 ? (6 - inward) * 4 : inward > 16 ? (inward - 16) * 4 : 0;
        return Math.hypot(s.x - want.x, s.z - want.z) + pen;
      };
      pick = free.reduce((b, s) => (score(s) < score(b) ? s : b));
      // face the door wall straight on (look at the door line point opposite the sweep)
      const along = (pick.x - cx) * -nz + (pick.z - cz) * nx;
      look = { x: cx + -nz * along, y: fy + 2.2, z: cz + nx * along };
    } else {
      const b = this.bounds();
      const p = target ?? (b ? { x: b.cx, z: b.cz } : { x: free[0].x, z: free[0].z });
      const ranked = free.map((s) => ({ s, d: Math.hypot(s.x - p.x, s.z - p.z) })).sort((a, b2) => a.d - b2.d);
      pick = (ranked.find((r) => r.d >= 4) ?? ranked[0]).s;
      look = { x: p.x, y: fy + 1.0, z: p.z };
    }
    const rotation = this.lookRotation(pick, look);
    return this.cam(() => this.sdk.Sweep.moveTo(pick.id, { rotation, transition: TT.FLY, transitionTime: 1500 }));
  }

  /** Serialise camera operations (overlapping moveTo calls get rejected by Showcase). */
  private camChain: Promise<unknown> = Promise.resolve();
  private camBusy = false;
  private cam<R>(fn: () => Promise<R>): Promise<R> {
    const run = async () => { this.camBusy = true; try { return await fn(); } finally { this.camBusy = false; } };
    const next = this.camChain.then(run, run);
    this.camChain = next.catch(() => undefined);
    return next;
  }

  /** Dollhouse camera above and behind (+z) a point, looking down at it. */
  async lookAt(x: number, z: number, height = 22, transition?: string) {
    const fy = this.floorY();
    const target = { x, y: fy, z };
    const pos = { x, y: fy + height, z: z + height * 0.65 };
    return this.cam(() => this.sdk.Mode.moveTo(this.sdk.Mode.Mode.DOLLHOUSE, {
      position: pos, rotation: this.lookRotation(pos, target),
      transition: transition ?? this.sdk.Camera.TransitionType.FLY,
    }));
  }

  startFollow(id: string) {
    this.stopFollow();
    this.follow = id;
    const step = (first: boolean) => {
      if (this.camBusy || this.follow !== id) return;
      const p = id === '__fit' ? this.overlay?.vehiclePos() : this.renderer?.vehiclePos(id);
      if (!p) return;
      this.lookAt(p.x, p.z, 20, first ? undefined : this.sdk.Camera.TransitionType.INSTANT).catch(() => { /* camera busy */ });
    };
    step(true);
    this.followTimer = window.setInterval(() => step(false), 100);
  }

  stopFollow() {
    this.follow = null;
    if (this.followTimer != null) { clearInterval(this.followTimer); this.followTimer = null; }
  }

  dispose() {
    this.disposed = true;
    this.stopFollow();
    for (const u of this.unsubs) { try { u(); } catch { /* */ } }
    this.unsubs = [];
    try { this.overlay?.clear(); } catch { /* ignore */ }
    try { this.sceneObject?.stop(); } catch { /* ignore */ }
    try { this.renderer?.clear(); } catch { /* ignore */ }
    try { this.registration?.dispose?.(); } catch { /* ignore */ }
    try { this.sdk?.disconnect?.(); } catch { /* ignore */ }
    this.sdk = null; this.renderer = null; this.sceneObject = null;
  }
}
