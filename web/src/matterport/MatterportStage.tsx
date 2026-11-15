// Stream A: the real Matterport scan (self-hosted Bundle SDK at /bundle/showcase.html) with the sim replay
// rendered INSIDE it. Props contract: docs/CONTRACTS.md. No npm deps beyond React.
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { StageController } from './stage';
import { VEHICLE_COLORS, vehicleColor, vehicleLabel } from './sceneRenderer';
import DoorMarker from './DoorMarker';
import FitSummary from './FitSummary';
import { fitBus } from './fitBus';
import type { SimScene, FitResult, DoorStore } from './types';

export interface MatterportStageProps {
  modelId: string;
  sdkKey: string;
  sceneUrl?: string;
  playing?: boolean;
  speed?: number;
  onReady?: () => void;
  onTime?: (t_s: number) => void;
  focus?: { x: number; z: number } | null;
  /** optional: property name shown on the loading panel */
  title?: string;
  /** optional: pass an already-loaded scene object instead of a URL */
  scene?: SimScene | null;
  /** "view" = scan + sim replay (default); "markDoors" = click two door edges to mark doors; "fit" = equipment test */
  mode?: 'view' | 'markDoors' | 'fit';
  /** property id (door store + fit endpoints); falls back to scene.property_id */
  propertyId?: number;
  /** equipment test result to draw in mode="fit" */
  fitResult?: FitResult | null;
  /** called after the marked doors were saved (PUT) or loaded */
  onDoorsChanged?: (store: DoorStore) => void;
  /** mode="fit": the user clicked a target point on the floor */
  onPickTarget?: (p: { x: number; z: number }) => void;
  /** markDoors: source written with new doors ('dev_fixture' for test doors that must be deleted later) */
  markSource?: 'marked_in_scan' | 'dev_fixture';
}

const SPEEDS = [1, 5, 10, 30, 60];

function fmt(t: number) {
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
const hex = (c: number) => '#' + c.toString(16).padStart(6, '0');

export default function MatterportStage(props: MatterportStageProps) {
  const { modelId, sdkKey, sceneUrl, onReady, onTime, focus } = props;
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const ctlRef = useRef<StageController | null>(null);
  const cbRef = useRef({ onReady, onTime });
  cbRef.current = { onReady, onTime };

  const [ready, setReady] = useState(false);
  // opaque loading cover: stays until the scene is built and the Overview camera has arrived, then fades (300 ms)
  const [cover, setCover] = useState<'on' | 'fading' | 'off'>('on');
  const reveal = () => setCover((c) => (c === 'on' ? 'fading' : c));
  useEffect(() => { if (cover !== 'fading') return; const id = setTimeout(() => setCover('off'), 320); return () => clearTimeout(id); }, [cover]);
  const [error, setError] = useState<string | null>(null);
  const [scene, setScene] = useState<SimScene | null>(null);
  // loading-panel title: props.title, else the property's name/address from the catalog
  const [propName, setPropName] = useState<string | null>(null);
  const mode = props.mode ?? 'view';
  const pid = props.propertyId ?? (scene?.property_id != null ? Number(scene.property_id) : undefined);
  useEffect(() => {
    if (props.title || pid == null) return;
    let off = false;
    fetch(`/api/warehouses/${pid}`).then((r) => (r.ok ? r.json() : null))
      .then((w) => { if (!off && w) setPropName([w.name ?? w.address, w.city].filter(Boolean).join(', ') || null); })
      .catch(() => {});
    return () => { off = true; };
  }, [pid, props.title]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(props.playing ?? true) // autoplay at 10x once loaded;
  const [speed, setSpeed] = useState(props.speed ?? 10);
  const [follow, setFollow] = useState<string>('');
  const [labels, setLabels] = useState(true);

  // controlled overrides from the parent
  useEffect(() => { if (props.playing !== undefined) setPlaying(props.playing); }, [props.playing]);
  useEffect(() => { if (props.speed !== undefined) setSpeed(props.speed); }, [props.speed]);

  // iframe + SDK lifecycle (one per model/key)
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe || !sdkKey || !modelId) return;
    setReady(false); setError(null); setCover('on');
    const ctl = new StageController();
    ctlRef.current = ctl;
    ctl.onTime = (tt) => { setT(tt); cbRef.current.onTime?.(tt); };
    const onLoad = () => {
      ctl.connect(iframe).then(() => {
        if (ctlRef.current !== ctl) return;
        setReady(true);
        cbRef.current.onReady?.();
      }).catch((e) => setError(String(e?.message ?? e)));
    };
    iframe.addEventListener('load', onLoad);
    // Showcase's dollhouse "Ctrl + click or right-click and drag to pan/rotate" prompt is shown until this
    // localStorage counter reaches 3 (bundle 26.10.4, key read via useLocalStorage). The iframe is same-origin.
    try {
      localStorage.setItem('peekaboo_dh_prompt_dismissed', '3');
      localStorage.setItem('peekaboo_dh_prompt_dismissed_rotate', '3');
    } catch { /* storage blocked: prompt may show */ }
    // help=0: no help overlay; qs=1: quickstart; title=0: no model title; brand=0: no presenter branding
    // (the Matterport logo stays, attribution); hl=0 no highlight reel; tour=0 no guided-tour bar.
    const q = new URLSearchParams({ m: modelId, play: '1', qs: '1', title: '0', brand: '0', help: '0', hl: '0', tour: '0', applicationKey: sdkKey });
    iframe.src = `/bundle/showcase.html?${q}`;
    return () => {
      iframe.removeEventListener('load', onLoad);
      ctl.dispose();
      if (ctlRef.current === ctl) ctlRef.current = null;
      iframe.src = 'about:blank';
    };
  }, [modelId, sdkKey]);

  // scene: from prop object or URL
  useEffect(() => {
    if (props.scene !== undefined) { setScene(props.scene); return; }
    if (!sceneUrl) { setScene(null); return; }
    let cancelled = false;
    fetch(sceneUrl).then((r) => { if (!r.ok) throw new Error(`scene ${r.status}`); return r.json(); })
      .then((j) => { if (!cancelled) setScene(j); })
      .catch((e) => { if (!cancelled) setError(`Could not load sim scene: ${e.message}`); });
    return () => { cancelled = true; };
  }, [sceneUrl, props.scene]);

  useEffect(() => {
    const ctl = ctlRef.current;
    if (!ctl || !ready) return;
    const sc = mode === 'view' ? scene : null;
    ctl.setScene(sc);
    setFollow('');
    if (sc) {
      // instant move while covered; give the overview at most 8 s before revealing anyway
      let done = false;
      const finish = () => { if (!done) { done = true; reveal(); } };
      const guard = setTimeout(finish, 8000);
      ctl.overview(true).catch(() => {}).finally(() => { clearTimeout(guard); setTimeout(finish, 150); });
      return () => clearTimeout(guard);
    }
    // no scene expected: reveal the plain scan as soon as it's ready
    if (mode !== 'view' || (!sceneUrl && props.scene === undefined)) reveal();
  }, [scene, ready, mode]);
  // never trap the user behind the cover: on error, or 30 s after the SDK is ready
  useEffect(() => { if (error) reveal(); }, [error]);
  useEffect(() => { if (!ready) return; const id = setTimeout(reveal, 30000); return () => clearTimeout(id); }, [ready]);

  useEffect(() => { if (ctlRef.current) ctlRef.current.playing = playing; }, [playing, ready]);
  useEffect(() => { if (ctlRef.current) ctlRef.current.speed = speed; }, [speed, ready]);
  useEffect(() => { ctlRef.current?.renderer?.setLabels(labels); }, [labels, ready, scene]);

  useEffect(() => {
    const ctl = ctlRef.current;
    if (!ctl || !ready || !focus) return;
    ctl.stopFollow(); setFollow('');
    ctl.lookAt(focus.x, focus.z, 24).catch(() => {});
  }, [focus?.x, focus?.z, ready]);

  // ---------------- doors a person marked in the scan (all modes draw them; nothing else is drawn as a door)
  const [doorStore, setDoorStore] = useState<DoorStore | null>(null);
  const doorsCb = useRef(props.onDoorsChanged); doorsCb.current = props.onDoorsChanged;
  const applyStore = (st: DoorStore) => { setDoorStore(st); ctlRef.current?.setDoors(st.doors); fitBus.emit('doors', st); doorsCb.current?.(st); };
  useEffect(() => {
    if (!ready || pid == null) return;
    let off = false;
    fetch(`/api/warehouses/${pid}/doors?model_id=${encodeURIComponent(modelId)}`).then((r) => (r.ok ? r.json() : null))
      .then((st) => { if (!off && st) applyStore(st); }).catch(() => {});
    return () => { off = true; };
  }, [ready, pid, modelId]);

  // ---------------- equipment test
  const [busResult, setBusResult] = useState<FitResult | null>(fitBus.last('result') ?? null);
  useEffect(() => fitBus.on('result', setBusResult), []);
  const fit = mode === 'fit' ? (props.fitResult !== undefined ? props.fitResult : busResult) : null;
  const [heatOn, setHeatOn] = useState(true);
  const [fitPlaying, setFitPlaying] = useState(true);
  const [fitT, setFitT] = useState(0);
  const [picking, setPicking] = useState<boolean>(!!fitBus.last('pick'));
  useEffect(() => fitBus.on('pick', setPicking), []);
  const pickCb = useRef(props.onPickTarget); pickCb.current = props.onPickTarget;
  useEffect(() => {
    const c = ctlRef.current;
    if (!c || !ready) return;
    c.onFitTime = setFitT;
    c.setFit(fit, heatOn);
    c.fitPlaying = fitPlaying;
    c.fitSpeed = 1;
    if (fit) {
      c.setTarget(null);
      const tgt = (fit.route as any)?.to;
      if (tgt && typeof tgt === 'object') c.setTarget(tgt);
      c.overview().catch(() => {});
    }
  }, [fit, ready]);
  useEffect(() => { ctlRef.current?.setHeat(heatOn); }, [heatOn, ready]);
  useEffect(() => { if (ctlRef.current) ctlRef.current.fitPlaying = fitPlaying; }, [fitPlaying, ready]);
  useEffect(() => {
    const c = ctlRef.current;
    if (!c || !ready || mode !== 'fit') return;
    if (!picking) { c.onClick = undefined; return; }
    c.onClick = (h) => {
      const fy = c.floorY();
      if (h.normal ? h.normal.y < 0.6 : Math.abs(h.position.y - fy) > 0.35) return;   // floor only
      const p = { x: +h.position.x.toFixed(2), z: +h.position.z.toFixed(2) };
      c.setTarget(p);
      fitBus.emit('target', p);
      fitBus.emit('pick', false);
      pickCb.current?.(p);
    };
    return () => { if (c) c.onClick = undefined; };
  }, [picking, ready, mode]);
  useEffect(() => { if (mode !== 'fit') ctlRef.current?.setTarget(null); }, [mode, ready]);

  const ctl = ctlRef.current;
  const duration = ctl?.duration || scene?.duration_s || 0;
  const vehicles = scene?.vehicles ?? [];
  const kinds = Array.from(new Set(vehicles.map((v) => vehicleColor(v.kind))));

  const camBtn = (label: string, fn: () => unknown, active = false) => (
    <button style={{ ...S.btn, ...(active ? S.btnOn : null) }} onClick={() => { try { Promise.resolve(fn()).catch(() => {}); } catch { /* */ } }} disabled={!ready}>{label}</button>
  );

  return (
    <div style={S.wrap}>
      <iframe ref={iframeRef} title="Matterport scan" style={S.iframe} allow="xr-spatial-tracking; fullscreen" />
      {cover !== 'off' && (
        <div style={{ ...S.cover, opacity: cover === 'fading' ? 0 : 1 }} aria-busy={cover === 'on'}>
          <style>{`@keyframes piShimmer{0%{transform:translateX(-100%)}100%{transform:translateX(250%)}}`}</style>
          <div style={S.coverTitle}>{props.title ?? propName ?? (pid != null ? `Property ${pid}` : '\u00a0')}</div>
          <div style={S.coverSub}>{sdkKey ? (ready ? 'Placing the simulation in the scan…' : 'Loading the Matterport scan…') : 'Waiting for Matterport key…'}</div>
          <div style={S.track}><div style={S.shimmer} /></div>
        </div>
      )}
      {error && <div style={{ ...S.center, color: '#ffb4a8', zIndex: 3 }}>{error}</div>}

      {ready && mode !== 'view' && (
        <div style={S.topRight}>
          {camBtn('Overview', () => ctl?.overview())}
          {camBtn('Floor plan', () => ctl?.floorplan())}
          {camBtn(mode === 'markDoors' && doorStore?.doors.length ? 'Walk to doors' : 'Walk', () => ctl?.inside())}
          {mode === 'fit' && fit?.path?.length ? camBtn(follow === '__fit' ? 'Stop following' : 'Follow vehicle', () => {
            if (follow === '__fit') { setFollow(''); ctl?.stopFollow(); } else { setFollow('__fit'); ctl?.startFollow('__fit'); }
          }, follow === '__fit') : null}
        </div>
      )}
      {ready && mode === 'markDoors' && (
        <DoorMarker ctl={ctl} ready={ready} propertyId={pid} modelId={modelId} store={doorStore}
          source={props.markSource} onSaved={applyStore} />
      )}
      {ready && mode === 'fit' && picking && <div style={S.hint}>Click a spot on the floor to set the target</div>}
      {ready && mode === 'fit' && fit && (
        <FitSummary result={fit} heatOn={heatOn} onHeat={setHeatOn} playing={fitPlaying} onPlay={setFitPlaying}
          t={fitT} onReplay={() => { ctl?.seekFit(0); setFitPlaying(true); }}
          onFocus={(p) => { setFollow(''); ctl?.stopFollow(); ctl?.lookAt(p.x, p.z, 14).catch(() => {}); }} />
      )}
      {ready && mode === 'view' && scene && (
        <>
          <div style={S.topRight}>
            {camBtn('Overview', () => { setFollow(''); return ctl?.overview(); })}
            {camBtn('Floor plan', () => { setFollow(''); return ctl?.floorplan(); })}
            {camBtn('Walk', () => { setFollow(''); return ctl?.inside(); })}
            <select style={S.select} value={follow} disabled={!vehicles.length}
              onChange={(e) => { const id = e.target.value; setFollow(id); if (id) ctl?.startFollow(id); else ctl?.stopFollow(); }}>
              <option value="">Follow vehicle…</option>
              {vehicles.map((v) => <option key={String(v.id)} value={String(v.id)}>{vehicleLabel(v.id, v.kind)}</option>)}
            </select>
          </div>

          <div style={S.legend}>
            {VEHICLE_COLORS.filter((c) => kinds.includes(c.color)).map((c) => (
              <span key={c.label} style={S.legendItem}><i style={{ ...S.swatch, background: hex(c.color) }} />{c.label}</span>
            ))}
            {!!scene.racks?.length && <span style={S.legendItem}><i style={{ ...S.swatch, background: '#3b6fd8' }} />Racks</span>}
            {!!scene.lanes?.length && <span style={S.legendItem}><i style={{ ...S.swatch, background: 'linear-gradient(90deg,#19c39a 50%,#b36bff 50%)' }} />Staging in/out</span>}
            {!!scene.doors?.length && <span style={S.legendItem}><i style={{ ...S.swatch, background: scene.doors.some((d) => d.source === 'vexcel') ? 'linear-gradient(90deg,#2ad4ff 50%,#ffc23a 50%)' : '#ffc23a' }} />Dock doors{scene.doors.some((d) => d.source !== 'vexcel') ? ' (? = assumed)' : ''}</span>}
            {!!scene.heat?.cells?.length && <span style={S.legendItem}><i style={{ ...S.swatch, background: 'linear-gradient(90deg,#2ecc40,#ffdc00,#ff4136)' }} />Congestion</span>}
            {scene.synthetic && <span style={{ ...S.legendItem, color: '#ffc23a' }}>SYNTHETIC</span>}
          </div>

          <div style={S.bar}>
            <button style={S.play} onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? '❚❚' : '▶'}
            </button>
            <input type="range" min={0} max={Math.max(1, duration)} step={1} value={Math.min(t, duration || t)}
              style={S.range} onChange={(e) => ctl?.seek(Number(e.target.value))} />
            <span style={S.time}>{fmt(t)} / {fmt(duration)}</span>
            <select style={S.select} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
            <label style={S.chk}><input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} /> labels</label>
          </div>
        </>
      )}
    </div>
  );
}

const glass: CSSProperties = { background: 'rgba(14,16,20,0.78)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 10, color: '#e9edf2' };
const S: Record<string, CSSProperties> = {
  wrap: { position: 'relative', width: '100%', height: '100%', minHeight: 360, background: '#0b0d10', overflow: 'hidden', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 13 },
  iframe: { position: 'absolute', inset: 0, width: '100%', height: '100%', border: 0 },
  cover: { position: 'absolute', inset: 0, zIndex: 2, background: 'radial-gradient(ellipse at 50% 40%, #161a21 0%, #0b0d10 70%)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, transition: 'opacity 300ms ease', color: '#e9edf2' },
  coverTitle: { fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em', textAlign: 'center', padding: '0 16px' },
  coverSub: { fontSize: 13, color: '#9aa3ad' },
  track: { width: 220, height: 3, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden', marginTop: 6 },
  shimmer: { width: '40%', height: '100%', borderRadius: 3, background: 'linear-gradient(90deg, transparent, #ff8a1f, transparent)', animation: 'piShimmer 1.4s ease-in-out infinite' },
  center: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#aab3bf', pointerEvents: 'none' },
  topRight: { ...glass, position: 'absolute', top: 12, right: 12, display: 'flex', gap: 6, padding: 6, flexWrap: 'wrap', maxWidth: 'calc(100% - 24px)' },
  // bottom-left, just above the control bar: clear of Matterport's top-left logo/UI; one line, scrolls if too narrow
  legend: { ...glass, position: 'absolute', bottom: 60, left: 12, display: 'flex', gap: 12, padding: '5px 10px', flexWrap: 'nowrap', whiteSpace: 'nowrap', maxWidth: 'calc(100% - 24px)', overflowX: 'auto', fontSize: 12 },
  legendItem: { display: 'inline-flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' },
  swatch: { width: 12, height: 12, borderRadius: 3, display: 'inline-block' },
  bar: { ...glass, position: 'absolute', left: 12, right: 12, bottom: 12, display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px' },
  play: { width: 34, height: 30, borderRadius: 8, border: 0, background: '#ff8a1f', color: '#111', fontWeight: 700, cursor: 'pointer' },
  range: { flex: 1, minWidth: 80, accentColor: '#ff8a1f' },
  time: { fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 12, whiteSpace: 'nowrap' },
  select: { background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 7, padding: '4px 6px', fontSize: 12 },
  btn: { background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 7, padding: '4px 10px', fontSize: 12, cursor: 'pointer' },
  btnOn: { background: '#ff8a1f', color: '#111' },
  hint: { ...glass, position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', padding: '7px 14px', fontSize: 13, color: '#ffe14d', pointerEvents: 'none' },
  chk: { display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap', fontSize: 12 },
};
