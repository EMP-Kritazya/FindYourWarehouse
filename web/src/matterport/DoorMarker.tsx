// Door marking inside the Matterport scan (MatterportStage mode="markDoors", S4).
// First door: click its LEFT edge, then its RIGHT edge (2 clicks). After that, "same width" is on: one click on the
// next door's left edge places it (same width, same wall direction), and "+ next at same spacing" adds a door with
// zero clicks once two doors exist. Undo / delete / kind / optional height; Save -> PUT /api/warehouses/{id}/doors.
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { StageController, Hit } from './stage';
import type { DoorKind, DoorStore, MarkedDoor, Pt3 } from './types';
import { KIND_LABEL } from './overlays';

const FT = 0.3048;
const MIN_W = 1, MAX_W = 6;

type Draft = Omit<MarkedDoor, 'id' | 'center' | 'width_m' | 'normal'> & { id?: string; center?: MarkedDoor['center']; width_m?: number; normal?: MarkedDoor['normal']; normal_hint?: { x: number; z: number } };

function derive(d: Draft, i: number): MarkedDoor {
  const dx = d.b.x - d.a.x, dz = d.b.z - d.a.z, w = Math.hypot(dx, dz) || 1;
  // provisional normal for drawing (server recomputes from the scan floor): away from the camera that clicked
  let nx = -dz / w, nz = dx / w;
  const h = d.normal_hint ?? d.normal;
  if (h && nx * h.x + nz * h.z < 0) { nx = -nx; nz = -nz; }
  return {
    ...(d as any), id: d.id ?? `new${i + 1}`, center: { x: (d.a.x + d.b.x) / 2, z: (d.a.z + d.b.z) / 2 },
    width_m: Math.hypot(dx, dz), normal: d.normal ?? { x: nx, z: nz },
  };
}

export interface DoorMarkerProps {
  ctl: StageController | null;
  ready: boolean;
  propertyId?: number;
  modelId: string;
  store: DoorStore | null;
  source?: 'marked_in_scan' | 'dev_fixture';
  onSaved: (s: DoorStore) => void;
}

export default function DoorMarker({ ctl, ready, propertyId, modelId, store, source = 'marked_in_scan', onSaved }: DoorMarkerProps) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [kind, setKind] = useState<DoorKind>('dock_high');
  const [heightFt, setHeightFt] = useState('');
  const [first, setFirst] = useState<Pt3 | null>(null);
  const [sameWidth, setSameWidth] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const st = useRef({ drafts, kind, heightFt, first, sameWidth });
  st.current = { drafts, kind, heightFt, first, sameWidth };

  // load the saved doors into the draft list
  useEffect(() => { if (store) { setDrafts(store.doors.map((d) => ({ ...d }))); setDirty(false); } }, [store]);

  const doors = drafts.map(derive);
  const last = doors[doors.length - 1];
  const prev = doors[doors.length - 2];
  const lastDir = last ? { x: (last.b.x - last.a.x) / last.width_m, z: (last.b.z - last.a.z) / last.width_m } : null;
  const pitch = last && prev ? { x: last.center.x - prev.center.x, z: last.center.z - prev.center.z } : null;
  const oneClick = !!(sameWidth && last && lastDir);

  useEffect(() => { ctl?.setDoors(doors, selected); }, [ctl, ready, drafts, selected]);

  const cameraHint = (p: Pt3) => {
    // the person marking stands inside: outward = from the camera towards the door
    try {
      const T = ctl?.overlay?.T;
      const cam = ctl?.camera?.getWorldPosition ? ctl.camera.getWorldPosition(new T.Vector3()) : ctl?.camera?.position;
      if (cam) { const x = p.x - cam.x, z = p.z - cam.z, l = Math.hypot(x, z) || 1; return { x: x / l, z: z / l }; }
    } catch { /* */ }
    return undefined;
  };

  const height = () => { const h = parseFloat(st.current.heightFt); return h > 0 ? +(h * FT).toFixed(2) : null; };

  const addDoor = (a: Pt3, b: Pt3) => {
    const w = Math.hypot(b.x - a.x, b.z - a.z);
    if (w < MIN_W || w > MAX_W) { setMsg({ text: `That is ${w.toFixed(2)} m wide; a door is ${MIN_W}–${MAX_W} m. Click the two side edges of one door.`, bad: true }); return false; }
    const d: Draft = { kind: st.current.kind, a, b, height_m: height(), sill_m: null, source, normal_hint: cameraHint({ x: (a.x + b.x) / 2, y: a.y, z: (a.z + b.z) / 2 }) };
    setDrafts((ds) => [...ds, d]); setDirty(true);
    setMsg({ text: `Door ${st.current.drafts.length + 1} added (${(w / FT).toFixed(1)} ft). ${st.current.sameWidth ? 'Next: one click on the next door\'s left edge.' : 'Next: click the next door\'s left edge.'}` });
    return true;
  };

  // pointer: live preview, clicks
  useEffect(() => {
    if (!ctl || !ready) return;
    let raf = 0, pending: Hit | null = null;
    const draw = () => {
      raf = 0;
      const h = pending, s = st.current;
      if (!h) return;
      const ds = s.drafts.map(derive), l = ds[ds.length - 1];
      if (s.first) {
        const w = Math.hypot(h.position.x - s.first.x, h.position.z - s.first.z);
        ctl.setPreview(s.first, h.position, w >= MIN_W && w <= MAX_W);
      } else if (s.sameWidth && l) {
        const dir = { x: (l.b.x - l.a.x) / l.width_m, z: (l.b.z - l.a.z) / l.width_m };
        ctl.setPreview(h.position, { x: h.position.x + dir.x * l.width_m, y: h.position.y, z: h.position.z + dir.z * l.width_m }, true);
      } else ctl.setPreview(null, null);
    };
    ctl.onHover = (h) => { pending = h; if (!raf) raf = requestAnimationFrame(draw); };
    ctl.onClick = (h) => {
      const s = st.current, p = { ...h.position };
      const ds = s.drafts.map(derive), l = ds[ds.length - 1];
      if (s.first) {
        if (addDoor(s.first, p)) setFirst(null);
      } else if (s.sameWidth && l) {
        const dir = { x: (l.b.x - l.a.x) / l.width_m, z: (l.b.z - l.a.z) / l.width_m };
        addDoor(p, { x: p.x + dir.x * l.width_m, y: p.y, z: p.z + dir.z * l.width_m });
      } else {
        setFirst(p);
        setMsg({ text: 'Left edge set. Now click the door\'s right edge.' });
      }
    };
    return () => { ctl.onHover = undefined; ctl.onClick = undefined; ctl.setPreview(null, null); if (raf) cancelAnimationFrame(raf); };
  }, [ctl, ready]);

  const undo = () => {
    if (first) { setFirst(null); ctl?.setPreview(null, null); setMsg({ text: 'Edge cleared.' }); return; }
    if (drafts.length) { setDrafts((ds) => ds.slice(0, -1)); setDirty(true); setMsg({ text: 'Last door removed.' }); }
  };
  const del = (id: string) => { setDrafts((ds) => ds.filter((_, i) => derive(ds[i], i).id !== id)); setDirty(true); setSelected(null); };
  const setDoorKind = (id: string, k: DoorKind) => { setDrafts((ds) => ds.map((d, i) => (derive(d, i).id === id ? { ...d, kind: k } : d))); setDirty(true); };
  const nextAtPitch = () => {
    if (!last || !pitch) return;
    const sh = (p: Pt3) => ({ x: p.x + pitch.x, y: p.y, z: p.z + pitch.z });
    addDoor(sh(last.a), sh(last.b));
  };

  // keyboard: Esc / Ctrl+Z undo (the iframe usually has focus)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z')) { e.preventDefault(); undoRef.current(); }
    };
    const iw = (document.querySelector('iframe[title="Matterport scan"]') as HTMLIFrameElement | null)?.contentWindow;
    window.addEventListener('keydown', onKey); iw?.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); iw?.removeEventListener('keydown', onKey); };
  }, [ready]);
  const undoRef = useRef(undo); undoRef.current = undo;

  const save = async () => {
    if (propertyId == null) { setMsg({ text: 'No property id: cannot save.', bad: true }); return; }
    setBusy(true); setMsg(null);
    try {
      const body = { model_id: modelId, doors: drafts.map((d) => ({ id: d.id && !/^new/.test(d.id) ? d.id : undefined, kind: d.kind, a: d.a, b: d.b, height_m: d.height_m, sill_m: d.sill_m, source: d.source ?? source, normal_hint: d.normal_hint ?? d.normal, marked_at: d.marked_at })) };
      const r = await fetch(`/api/warehouses/${propertyId}/doors`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail));
      setDirty(false); onSaved(j);
      setMsg({ text: `Saved ${j.doors.length} door${j.doors.length === 1 ? '' : 's'}.` });
    } catch (e: any) { setMsg({ text: `Not saved: ${e.message ?? e}`, bad: true }); } finally { setBusy(false); }
  };

  if (collapsed) return <button style={{ ...S.panel, ...S.btn, top: 56, padding: '6px 12px' }} onClick={() => setCollapsed(false)}>Mark doors ({doors.length}) ▸</button>;

  return (
    <div style={S.panel} data-testid="door-marker">
      <div style={S.head}>
        <b>Mark the doors</b>
        <button style={S.x} onClick={() => setCollapsed(true)} aria-label="Collapse">‹</button>
      </div>
      <ol style={S.steps}>
        <li style={!first && !oneClick ? S.now : undefined}>Walk close to a door (click the floor to move, drag to look).</li>
        <li style={first ? S.now : !oneClick ? S.now : undefined}>
          {oneClick ? <>Click the next door's <b>left</b> edge: it gets the same width ({(last!.width_m / FT).toFixed(1)} ft).</>
            : first ? <>Now click its <b>right</b> edge.</> : <>Click the door's <b>left</b> edge, then its <b>right</b> edge.</>}
        </li>
        <li>Save. Only marked doors are used by the tests.</li>
      </ol>
      <div style={S.row}>
        {(['dock_high', 'drive_in', 'personnel'] as DoorKind[]).map((k) => (
          <button key={k} style={{ ...S.btn, ...(kind === k ? S.on : null) }} onClick={() => setKind(k)}>{KIND_LABEL[k]}</button>
        ))}
      </div>
      <div style={S.row}>
        <label style={S.lbl}>Height <input style={S.inp} inputMode="decimal" placeholder="? ft" value={heightFt} onChange={(e) => setHeightFt(e.target.value)} /> ft</label>
        {!!doors.length && <label style={S.lbl}><input type="checkbox" checked={sameWidth} onChange={(e) => setSameWidth(e.target.checked)} /> same width</label>}
      </div>
      <div style={S.row}>
        <button style={S.btn} onClick={undo} disabled={!first && !drafts.length} title="Esc / Ctrl+Z">Undo</button>
        {pitch && <button style={S.btn} onClick={nextAtPitch} title="Adds a door at the same spacing as the last two; check it lands on a real door">+ next at same spacing ({(Math.hypot(pitch.x, pitch.z) / FT).toFixed(1)} ft)</button>}
      </div>
      {!!doors.length && (
        <div style={S.list}>
          {doors.map((d) => (
            <div key={d.id} style={{ ...S.item, ...(selected === d.id ? S.itemOn : null) }} onMouseEnter={() => setSelected(d.id)} onMouseLeave={() => setSelected(null)}>
              <span style={{ width: 34, fontWeight: 600 }}>{/^new/.test(d.id) ? `D${doors.indexOf(d) + 1}` : d.id.toUpperCase()}</span>
              <select style={S.sel} value={d.kind} onChange={(e) => setDoorKind(d.id, e.target.value as DoorKind)}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <span style={S.mono}>{(d.width_m / FT).toFixed(1)} ft</span>
              <span style={S.mono}>{d.height_m != null ? `${(d.height_m / FT).toFixed(0)} ft h` : 'h ?'}</span>
              {d.source === 'dev_fixture' && <span style={{ color: '#ff5ad1', fontSize: 11 }}>dev</span>}
              <button style={S.del} onClick={() => del(d.id)} aria-label={`Delete ${d.id}`}>✕</button>
            </div>
          ))}
        </div>
      )}
      {msg && <div style={{ ...S.msg, color: msg.bad ? '#ffb4a8' : '#b8f5c8' }}>{msg.text}</div>}
      <div style={S.row}>
        <button style={{ ...S.btn, ...S.save }} onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : dirty ? `Save ${doors.length} door${doors.length === 1 ? '' : 's'}` : doors.length ? 'Saved' : 'No doors yet'}</button>
        {store?.updated_at && <span style={S.small}>last saved {new Date(store.updated_at).toLocaleString()}</span>}
      </div>
    </div>
  );
}

const S: Record<string, CSSProperties> = {
  panel: { position: 'absolute', top: 56, left: 12, width: 310, maxHeight: 'calc(100% - 130px)', overflowY: 'auto', background: 'rgba(14,16,20,0.86)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, color: '#e9edf2', padding: 12, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12.5, zIndex: 1 },
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 14 },
  x: { background: 'none', border: 0, color: '#9aa3ad', fontSize: 18, cursor: 'pointer' },
  steps: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 3, color: '#9aa3ad', lineHeight: 1.35 },
  now: { color: '#ffe14d' },
  row: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' },
  btn: { background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 7, padding: '4px 9px', fontSize: 12, cursor: 'pointer' },
  on: { background: '#2ad4ff', color: '#071318', borderColor: '#2ad4ff', fontWeight: 600 },
  save: { background: '#ff8a1f', color: '#111', border: 0, fontWeight: 700, padding: '6px 14px' },
  lbl: { display: 'inline-flex', alignItems: 'center', gap: 5, color: '#c4cbd3' },
  inp: { width: 52, background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '3px 5px', fontSize: 12 },
  list: { display: 'flex', flexDirection: 'column', gap: 3, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 6 },
  item: { display: 'flex', alignItems: 'center', gap: 7, padding: '2px 4px', borderRadius: 6 },
  itemOn: { background: 'rgba(42,212,255,0.12)' },
  sel: { background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, fontSize: 11.5, padding: '2px 3px' },
  mono: { fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 11.5, color: '#c4cbd3' },
  del: { marginLeft: 'auto', background: 'none', border: 0, color: '#ff8a7a', cursor: 'pointer', fontSize: 13 },
  msg: { fontSize: 12, lineHeight: 1.35 },
  small: { fontSize: 11, color: '#7d8691' },
};
