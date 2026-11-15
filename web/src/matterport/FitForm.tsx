// Equipment test form (S4; restyled by S3 v3 with to-scale equipment cards): pick a machine (library card or custom dims), a route, a floor, then Run.
// POST /api/fit/test -> onResult(result). Target picking talks to MatterportStage through fitBus, so the parent only
// has to mount <FitForm/> next to <MatterportStage mode="fit"/> and pass the result into `fitResult`.
import { useEffect, useState } from 'react';
import clsx from 'clsx';
import EquipmentCard from '../components/EquipmentCard';
import { devCheckGlyphs } from '../components/EquipmentGlyph';
import { fitBus } from './fitBus';
import type { DoorStore, FitResult } from './types';

export interface FitFormProps {
  propertyId: number;
  modelId?: string;
  onResult?: (r: FitResult) => void;
  /** optional preset (e.g. from the voice guide): a library key */
  equipment?: string;
  /** optional: a target the parent got from MatterportStage.onPickTarget (the in-page fitBus does this too) */
  pickedTarget?: { x: number; z: number } | null;
  /** optional: called when the user asks to pick a target in the scan */
  onPickTarget?: () => void;
}

interface Equip { name: string; category: string; dims_ft: { length: number; width: number; height_mast_lowered?: number; height?: number }; lift_height_ft?: number; turn_radius_ft?: number; min_aisle_ft?: number; indoor_ok?: boolean }
const INDOOR = new Set(['forklift', 'narrow_aisle', 'horizontal', 'light_lift', 'robot']);
const ORDER = ['reach_truck', 'counterbalance_lpg', 'counterbalance_electric', 'order_picker', 'vna_turret', 'pallet_jack_electric', 'walkie_stacker', 'autonomous_forklift', 'amr_goods_to_person', 'tugger_train'];
type Custom = { length: string; width: string; height_lowered: string; lift_height: string; turn_radius: string; min_aisle: string };
const CUSTOM0: Custom = { length: '8', width: '4', height_lowered: '7', lift_height: '15', turn_radius: '6.5', min_aisle: '11' };

export default function FitForm({ propertyId, modelId, onResult, equipment: preset, pickedTarget, onPickTarget }: FitFormProps) {
  const [lib, setLib] = useState<Record<string, Equip>>({});
  const [eq, setEq] = useState<string>(preset ?? 'reach_truck');
  const [custom, setCustom] = useState<Custom>(CUSTOM0);
  const [routeKind, setRouteKind] = useState<'auto' | 'target'>('auto');
  const [store, setStore] = useState<DoorStore | null>(fitBus.last('doors') ?? null);
  const [fromDoor, setFromDoor] = useState<string>('');
  const [target, setTarget] = useState<{ x: number; z: number } | null>(fitBus.last('target') ?? null);
  const [picking, setPicking] = useState(false);
  const [floor, setFloor] = useState<'as_scanned' | 'emptied'>('as_scanned');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [last, setLast] = useState<FitResult | null>(null);

  useEffect(() => { if (preset) setEq(preset); }, [preset]);
  useEffect(() => { if (pickedTarget) { setTarget(pickedTarget); setPicking(false); setRouteKind('target'); } }, [pickedTarget?.x, pickedTarget?.z]);
  useEffect(() => {
    fetch('/api/library').then((r) => r.json()).then((j) => { setLib(j.equipment ?? {}); devCheckGlyphs(j.equipment ?? {}); }).catch(() => setErr('Could not load the equipment library'));
  }, []);
  useEffect(() => {
    const q = modelId ? `?model_id=${encodeURIComponent(modelId)}` : '';
    fetch(`/api/warehouses/${propertyId}/doors${q}`).then((r) => (r.ok ? r.json() : null)).then((s) => s && setStore(s)).catch(() => {});
  }, [propertyId, modelId]);
  useEffect(() => fitBus.on('doors', setStore), []);
  useEffect(() => fitBus.on('target', (t) => { setTarget(t); setPicking(false); if (t) setRouteKind('target'); }), []);
  useEffect(() => fitBus.on('pick', setPicking), []);

  const truckDoors = (store?.doors ?? []).filter((d) => d.kind !== 'personnel');
  const keys = ORDER.filter((k) => lib[k]).concat(Object.keys(lib).filter((k) => !ORDER.includes(k) && INDOOR.has(lib[k].category)));

  const pick = () => { const on = !picking; setPicking(on); fitBus.emit('pick', on); if (on) onPickTarget?.(); };

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const equipment = eq === 'custom'
        ? Object.fromEntries(Object.entries(custom).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)]))
        : eq;
      const route: any = routeKind === 'auto' ? { to: 'auto' } : { to: target };
      if (fromDoor) route.from_door = fromDoor;
      if (routeKind === 'target' && !target) throw new Error('Pick a target in the scan first');
      const r = await fetch('/api/fit/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ property_id: propertyId, model_id: modelId, equipment, route, floor }) });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail));
      setLast(j); fitBus.emit('result', j); onResult?.(j);
    } catch (e: any) { setErr(String(e.message ?? e)); } finally { setBusy(false); }
  };


  const shown = keys.filter((k) => lib[k]);
  const seg = (on: boolean) => clsx('h-8 rounded-lg px-3 text-[12px] font-medium ring-1 ring-inset transition',
    on ? 'bg-accent/15 text-accent ring-accent/40' : 'bg-white/[0.03] text-ink-2 ring-line hover:bg-white/[0.06] hover:text-ink');
  const sec = 'mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3';
  const inp = 'h-8 rounded-lg bg-panel-2 px-2 text-[12.5px] text-ink ring-1 ring-inset ring-line outline-none focus:ring-accent/50';

  return (
    <div className="flex min-w-[260px] flex-col gap-2 p-3.5 text-[13px] text-ink" data-testid="fit-form">
      <div>
        <div className="text-[15px] font-semibold">Equipment test</div>
        <div className="text-[12px] text-ink-3">Do your machines fit and move through this building?</div>
      </div>

      <div className={sec}>1 · Machine</div>
      <div className="grid grid-cols-2 gap-1.5">
        {shown.map((k) => (
          <EquipmentCard key={k} itemKey={k} item={lib[k]} selected={eq === k} onClick={() => setEq(k)} scaleBar={5} />
        ))}
        <button type="button" onClick={() => setEq('custom')} aria-pressed={eq === 'custom'}
          className={clsx('flex flex-col items-start justify-center rounded-xl p-2.5 text-left ring-1 ring-inset transition',
            eq === 'custom' ? 'bg-accent/10 ring-accent/40' : 'bg-white/[0.02] ring-line hover:bg-white/[0.04] hover:ring-line-2')}>
          <span className="text-[12.5px] font-semibold">Custom…</span>
          <span className="text-[11px] text-ink-3">your own dimensions (ft)</span>
        </button>
      </div>
      {!shown.length && !err && <div className="grid grid-cols-2 gap-1.5">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px]" />)}</div>}
      {eq === 'custom' && (
        <div className="grid grid-cols-2 gap-1.5">
          {(Object.keys(CUSTOM0) as (keyof Custom)[]).map((k) => (
            <label key={k} className="flex flex-col gap-1 text-[11px] capitalize text-ink-3">{k.replace('_', ' ')}
              <input className={clsx(inp, 'tabular-nums')} inputMode="decimal" value={custom[k]} onChange={(e) => setCustom({ ...custom, [k]: e.target.value })} />
            </label>
          ))}
        </div>
      )}

      <div className={sec}>2 · Route</div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={seg(routeKind === 'auto')} onClick={() => setRouteKind('auto')}>Whole floor (auto)</button>
        <button type="button" className={seg(routeKind === 'target')} onClick={() => setRouteKind('target')}>To a target</button>
      </div>
      <label className="flex flex-col gap-1 text-[11px] text-ink-3">From
        <select className={inp} value={fromDoor} onChange={(e) => setFromDoor(e.target.value)}>
          <option value="">{truckDoors.length ? (routeKind === 'auto' ? 'all marked doors' : 'nearest marked door') : 'no doors marked yet'}</option>
          {truckDoors.map((d) => <option key={d.id} value={d.id}>{d.id.toUpperCase()} {d.kind.replace('_', '-')} · {(d.width_m / 0.3048).toFixed(1)} ft{d.source === 'dev_fixture' ? ' (dev)' : ''}</option>)}
        </select>
      </label>
      {!truckDoors.length && <div className="text-[11.5px] leading-snug text-warn">Mark the doors in the scan first for door checks; without doors the test starts from the largest open floor area.</div>}
      {routeKind === 'target' && (
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" className={seg(picking)} onClick={pick}>{picking ? 'Click the floor in the scan…' : target ? 'Re-pick target' : 'Pick target in the scan'}</button>
          {target && <span className="font-mono text-[11.5px] tabular-nums text-ink-2">x {target.x.toFixed(1)}, z {target.z.toFixed(1)}</span>}
        </div>
      )}

      <div className={sec}>3 · Floor</div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={seg(floor === 'as_scanned')} onClick={() => setFloor('as_scanned')}>As scanned</button>
        <button type="button" className={seg(floor === 'emptied')} onClick={() => setFloor('emptied')}>Emptied</button>
      </div>
      <div className="text-[11.5px] text-ink-3">{floor === 'as_scanned' ? "Today's racks and pallets count as obstacles." : "Today's contents removed; walls, columns, full-height structure stay."}</div>

      <button type="button" onClick={run} disabled={busy || (routeKind === 'target' && !target)}
        className="mt-2 flex h-10 items-center justify-center gap-2 rounded-xl bg-accent text-[13px] font-semibold text-bg transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? 'Testing…' : 'Run test'}
      </button>
      {err && <div className="text-[12px] text-bad">{err}</div>}
      {last && <div className={clsx('text-[12.5px] leading-snug', last.ok === true ? 'text-good' : last.ok === false ? 'text-bad' : 'text-warn')}>{last.verdict}</div>}
    </div>
  );
}
