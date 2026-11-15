// Interior planner: the buyer's operation decides what goes on the REAL scanned floor; the 2D plan (left) is where
// they edit; the Matterport scan (right) shows the same plan live, with real-looking racks and a forklift trip.
import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { AlertTriangle, Check, Info, Loader2, Play, RotateCcw, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import EquipmentGlyph, { type EquipItem } from '../components/EquipmentGlyph'
import type { SimScene } from '../matterport/types'
import RackPlan from './RackPlan'
import { interiorApi, PALLET_PRESETS, type InteriorPlan, type Operation, type PalletInput, type RackEdits } from './racksApi'
import { fmtDim, fmtFt, PropertiesPanel, selKind, SrcTag } from './PlanProps'

const MatterportStage = lazy(() => import('../matterport/MatterportStage'))
const MACHINES = ['counterbalance_electric', 'reach_truck', 'vna_turret', 'order_picker']
const ZONE_LABEL: Record<string, string> = { selective: 'Selective racks', dense: 'Double-deep racks', shelving: 'Pick shelving', cantilever: 'Cantilever (lumber, pipe)', open: 'Open floor' }
const n = (v: number | null | undefined, d = 0) => (v == null ? '?' : v.toLocaleString(undefined, { maximumFractionDigits: d }))

export interface InteriorOpen { pid: number; modelId: string; title: string }

export default function InteriorWorkspace({ open, onClose }: { open: InteriorOpen; onClose: () => void }) {
  const sdkKey = useStore((s) => s.sdkKey)
  const [ops, setOps] = useState<Operation[]>([])
  const [lib, setLib] = useState<Record<string, EquipItem> | null>(null)
  const [operation, setOperation] = useState('pallet_3pl')
  const [machine, setMachine] = useState<string | undefined>(undefined)
  const [keep, setKeep] = useState<string[] | null>(null)
  const [zoneTypes, setZoneTypes] = useState<Record<string, string>>({})
  const [edits, setEdits] = useState<RackEdits>({})
  const [plan, setPlan] = useState<InteriorPlan | null>(null)
  const [scene, setScene] = useState<SimScene | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [trip, setTrip] = useState(false)
  // the buyer's pallet: null = the US standard (an ASSUMPTION, labelled as such); applied after a short pause
  const [pallet, setPallet] = useState<PalletInput | null>(null)
  const [palletDraft, setPalletDraft] = useState<PalletInput>({ preset: 'gma_48x40', width_in: 40, depth_in: 48, load_in: 54 })
  useEffect(() => {
    const t = setTimeout(() => {
      const d = palletDraft
      const isDefault = d.preset === 'gma_48x40' && d.width_in === 40 && d.depth_in === 48 && d.load_in === 54
      const valid = d.width_in >= 24 && d.width_in <= 60 && d.depth_in >= 24 && d.depth_in <= 60 && d.load_in >= 12 && d.load_in <= 96
      if (valid) setPallet((p) => (isDefault ? null : p && p.width_in === d.width_in && p.depth_in === d.depth_in && p.load_in === d.load_in ? p : { ...d }))
    }, 450)
    return () => clearTimeout(t)
  }, [palletDraft])
  useEffect(() => {                                     // Esc clears the selection
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape' && sel) { e.stopPropagation(); setSel(null) } }
    window.addEventListener('keydown', k, true)
    return () => window.removeEventListener('keydown', k, true)
  }, [sel])

  useEffect(() => { interiorApi.operations().then(setOps).catch(() => {}); api.library().then((l) => setLib(l.equipment as Record<string, EquipItem>)).catch(() => {}) }, [])
  useEffect(() => {
    let live = true
    setBusy(true); setErr('')
    interiorApi.plan(open.pid, { operation, machine, keep, zone_types: zoneTypes, edits, pallet })
      .then(async (p) => {
        if (!live) return
        setPlan(p)
        if (keep == null) setKeep(p.objects.filter((o) => o.kept).map((o) => o.id))
        const sc = await fetch(p.scene_url).then((r) => r.json())
        if (live) setScene(sc)
      })
      .catch((e) => { if (live) setErr(String(e.message ?? e)) })
      .finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [open.pid, operation, machine, keep, zoneTypes, edits, pallet])

  const reset = (fn: () => void) => { setEdits({}); setSel(null); fn() }
  const move = useCallback((rid: string, dx: number) => setEdits((e) => ({ ...e, moved: { ...(e.moved ?? {}), [rid]: Math.round(((e.moved?.[rid] ?? 0) + dx) * 100) / 100 } })), [])
  const toggleRow = (rid: string) => setEdits((e) => { const d = new Set(e.deleted ?? []); if (d.has(rid)) d.delete(rid); else d.add(rid); return { ...e, deleted: [...d] } })
  const toggleObj = (oid: string) => setKeep((k) => { const s = new Set(k ?? []); if (s.has(oid)) s.delete(oid); else s.add(oid); return [...s] })
  const pr = plan?.proofs
  const onAction = () => {
    if (!sel) return
    const k = selKind(sel)
    if (k === 'row') toggleRow(sel)
    else if (k === 'obj') toggleObj(sel.slice(4))
  }
  const fails = plan?.feasibility?.filter((f) => !f.ok) ?? []
  // the stage shows the plan; the forklift trip only when asked (so the racks read first)
  const shown = scene ? (trip ? scene : { ...scene, vehicles: [], duration_s: 0 }) : null

  return (
    <div className="fixed inset-3 z-40 flex flex-col overflow-hidden rounded-2xl bg-bg ring-1 ring-line-2" data-testid="interior-workspace">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-accent">Interior planner · real scanned floor</div>
          <div className="truncate text-[15px] font-semibold text-ink">{open.title}</div>
        </div>
        <label className="ml-4 flex items-center gap-2 text-[12px] text-ink-3">My operation
          <select value={operation} onChange={(e) => reset(() => { setOperation(e.target.value); setMachine(undefined); setZoneTypes({}) })} data-testid="operation"
            className="rounded-lg bg-panel-2 px-2 py-1.5 text-[13px] text-ink ring-1 ring-inset ring-line">
            {(ops.length ? ops : [{ key: 'pallet_3pl', name: '3PL pallet storage' } as Operation]).map((o) => <option key={o.key} value={o.key}>{o.name}</option>)}
          </select>
        </label>
        <div className="ml-2 flex items-center gap-1">
          {MACHINES.map((m) => (
            <button key={m} title={lib?.[m]?.name ?? m} aria-label={lib?.[m]?.name ?? m} onClick={() => reset(() => setMachine(m))}
              className={clsx('flex h-10 w-14 items-center justify-center rounded-lg ring-1 ring-inset transition', (machine ?? plan?.machine) === m ? 'bg-accent/15 ring-accent/50' : 'ring-line hover:bg-white/5')}>
              {lib?.[m] ? <EquipmentGlyph item={lib[m]} itemKey={m} frameFt={14} frameHFt={14} className="h-8 w-12" /> : m.slice(0, 4)}
            </button>
          ))}
        </div>
        {busy && <Loader2 size={15} className="animate-spin text-ink-3" />}
        <button onClick={onClose} className="ml-auto rounded-lg p-2 text-ink-3 hover:bg-white/5 hover:text-ink" aria-label="Close"><X size={16} /></button>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* left: numbers, why, zones, 2D plan */}
        <div className="scroll-thin flex w-[46%] min-w-[460px] flex-col gap-3 overflow-y-auto border-r border-line p-4">
          {err && <div className="rounded-lg bg-bad/10 px-3 py-2 text-[12px] text-bad">{err}</div>}
          {plan && pr && (
            <>
              <div className="grid grid-cols-5 gap-1.5" data-testid="interior-metrics">
                <M label="Pallet positions" v={n(plan.positions)} sub={[plan.pick_faces ? `+ ${n(plan.pick_faces)} pick faces` : '', plan.long_goods_positions ? `+ ${n(plan.long_goods_positions)} lumber bundles` : '', `${plan.levels} levels`].filter(Boolean).join(' · ')} />
                <M label="Reachable" v={`${n(pr.reachable_pct, 0)}%`} sub={pr.machine_name} tone={pr.reachable_pct < 100 ? 'warn' : 'good'} />
                <M label="Min / pallet" v={n(pr.minutes_per_pallet, 2)} sub={pr.avg_trip_m ? `avg trip ${n(pr.avg_trip_m)} m` : ''} />
                <M label="Pallets / hour" v={n(pr.pallets_per_hour, 0)} sub="per machine" />
                <M label="Machines needed" v={pr.machines_needed != null ? String(pr.machines_needed) : '—'} sub={pr.daily_pallet_moves ? `${n(pr.daily_pallet_moves)} moves / ${pr.shift_hours} h` : 'no pallet volume'} />
              </div>
              {plan.scan_coverage_pct != null && plan.scan_coverage_pct < 30 && (
                <div className="rounded-xl bg-warn/10 px-3 py-2 text-[12px] leading-snug text-warn ring-1 ring-inset ring-warn/25">
                  The scan covers only {Math.round(plan.scan_coverage_pct)}% of this building ({n(plan.scanned_sqft)} SF): these numbers describe that part only.
                </div>
              )}
              <div className="rounded-xl bg-accent/[0.06] p-3 text-[12.5px] leading-snug text-ink-2 ring-1 ring-inset ring-accent/20">
                <b className="text-ink">Why this layout:</b> {plan.why}
                <div className="mt-1 flex gap-1.5 text-[11px] text-ink-3"><Info size={12} className="mt-px shrink-0" />{plan.levels_basis}. {pr.lift_note}.</div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {plan.zones.map((z) => (
                  <div key={z.id} className="flex items-center gap-1.5 rounded-lg bg-white/[0.03] px-2 py-1 text-[11.5px] ring-1 ring-inset ring-line" title={z.why}>
                    <span className="text-ink-3">{Math.round(z.from * 100)}–{Math.round(z.to * 100)}% from the docks</span>
                    <select value={z.type} onChange={(e) => reset(() => setZoneTypes((t) => ({ ...t, [z.id]: e.target.value })))}
                      className="rounded bg-panel-2 px-1 py-0.5 text-[11.5px] text-ink ring-1 ring-inset ring-line">
                      {Object.entries(ZONE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                    <span className="tabular-nums text-ink-2">{z.type === 'open' ? `${n(z.area_sqft)} SF open` : n(z.positions)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          {plan?.dims && (
            <PalletBar draft={palletDraft} setDraft={setPalletDraft} plan={plan} busy={busy} onSelect={setSel} sel={sel} />
          )}
          {plan?.feasibility && (
            <details className="rounded-xl bg-white/[0.03] px-3 py-2 text-[12px] ring-1 ring-inset ring-line" open={fails.length > 0} data-testid="feasibility">
              <summary className="flex cursor-pointer list-none items-center gap-2">
                {fails.length ? <AlertTriangle size={14} className="text-warn" /> : <Check size={14} className="text-good" />}
                <span className="font-medium text-ink">{fails.length ? `${fails.length} layout check${fails.length > 1 ? 's' : ''} need attention` : `All ${plan.feasibility.length} layout checks pass`}</span>
                <span className="ml-auto text-[11px] text-ink-3">details</span>
              </summary>
              <ul className="mt-1.5 space-y-1">
                {plan.feasibility.map((f) => (
                  <li key={f.id} className="flex items-start gap-1.5 leading-snug">
                    {f.ok ? <Check size={12} className="mt-0.5 shrink-0 text-good" /> : <AlertTriangle size={12} className="mt-0.5 shrink-0 text-warn" />}
                    <span><span className="text-ink">{f.label}</span> <span className="text-ink-3">· {f.detail}</span></span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {plan && (
            <>
              <div className="relative">
                <RackPlan plan={plan as never} selected={sel} onSelect={setSel} onMove={move} objects={plan.objects} onToggleObject={toggleObj} zones={plan.zones}
                  dims={plan.dims} machinePos={plan.machine_pos} loadIn={plan.pallet?.load_in} />
                {sel && plan.dims && (
                  <div className="pointer-events-none absolute bottom-2 right-2 top-2 z-10 flex flex-col"><div className="scroll-thin pointer-events-auto min-h-0 overflow-y-auto rounded-xl"><PropertiesPanel sel={sel} plan={plan} onClose={() => setSel(null)} onAction={onAction} /></div></div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
                <span>Click anything on the plan for its dimensions. Drag a selected rack row sideways to move it.</span>
                <span className="flex items-center gap-1"><span className="inline-block h-0 w-4 border-t-2 border-[#e6fffb]" /> measured</span>
                <span className="flex items-center gap-1"><span className="inline-block h-0 w-4 border-t-2 border-dashed border-[#fbbf24]" /> ≈ assumed or calculated</span>
                {(Object.keys(edits.moved ?? {}).length + (edits.deleted?.length ?? 0)) > 0 && (
                  <button onClick={() => { setEdits({}); setSel(null) }} className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-1 text-ink-3 ring-1 ring-inset ring-line hover:bg-white/5"><RotateCcw size={12} /> Auto layout</button>
                )}
              </div>
              {pr?.basis && <details className="text-[11px] text-ink-3"><summary className="cursor-pointer">How the forklift numbers are computed</summary><p className="mt-1">{pr.basis}</p>
                <ul className="mt-1 list-disc pl-4">{plan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>}
            </>
          )}
        </div>

        {/* right: the same plan inside the Matterport scan */}
        <div className="relative min-w-0 flex-1 bg-black">
          {sdkKey && shown ? (
            <Suspense fallback={<div className="skeleton absolute inset-0" />}>
              <MatterportStage key={open.modelId} modelId={open.modelId} sdkKey={sdkKey} scene={shown} title={open.title} propertyId={open.pid} />
            </Suspense>
          ) : <div className="skeleton absolute inset-0" />}
          {plan?.proofs?.avg_trip_m && (
            <button onClick={() => setTrip((t) => !t)} data-testid="play-trip"
              className={clsx('absolute bottom-20 right-4 flex items-center gap-2 rounded-xl px-3 py-2 text-[12.5px] font-semibold ring-1 ring-inset',
                trip ? 'bg-white/10 text-ink ring-line-2' : 'bg-accent text-bg ring-accent')}>
              <Play size={13} /> {trip ? 'Hide the forklift trip' : `Play a dock-to-rack trip (${plan.machine_name})`}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function M({ label, v, sub, tone }: { label: string; v: string; sub?: string; tone?: 'good' | 'warn' }) {
  return (
    <div className="rounded-xl bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line">
      <div className="text-[11px] uppercase tracking-[0.06em] text-ink-3">{label}</div>
      <div className={clsx('text-[18px] font-semibold tabular-nums', tone === 'warn' ? 'text-warn' : tone === 'good' ? 'text-good' : 'text-ink')}>{v}</div>
      {sub && <div className="truncate text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}

/** The buyer's pallet + the rack spec it implies, as clickable chips (each opens its properties). */
function PalletBar({ draft, setDraft, plan, busy, onSelect, sel }: {
  draft: PalletInput; setDraft: (f: (d: PalletInput) => PalletInput) => void; plan: InteriorPlan; busy: boolean
  onSelect: (s: string) => void; sel: string | null
}) {
  const D = plan.dims
  const preset = PALLET_PRESETS.find((p) => p.key === draft.preset)
  const num = (k: 'width_in' | 'depth_in' | 'load_in', label: string, min: number, max: number) => (
    <label className="flex items-center gap-1 text-[11px] text-ink-3">{label}
      <input type="number" inputMode="decimal" min={min} max={max} step={1} value={draft[k]} aria-label={`Pallet ${label} in inches`}
        onChange={(e) => { const v = Number(e.target.value); setDraft((d) => ({ ...d, [k]: v, ...(k !== 'load_in' ? { preset: 'custom' } : {}) })) }}
        className={clsx('h-7 w-[58px] rounded-md bg-panel-2 px-1.5 text-right text-[12px] tabular-nums text-ink ring-1 ring-inset',
          draft[k] < min || draft[k] > max ? 'ring-bad/60' : 'ring-line')} />
      <span>in</span>
    </label>
  )
  const chip = (id: string, label: string, value: string, src: Parameters<typeof SrcTag>[0]['s']) => (
    <button key={id} onClick={() => onSelect(id)} data-testid={`spec-${id}`}
      className={clsx('flex items-center gap-1.5 rounded-lg px-2 py-1 text-left text-[11.5px] ring-1 ring-inset transition',
        sel === id ? 'bg-accent/12 ring-accent/50' : 'bg-white/[0.03] ring-line hover:bg-white/[0.06]')}>
      <span className="text-ink-3">{label}</span><span className="font-semibold tabular-nums text-ink">{value}</span><SrcTag s={src} />
    </button>
  )
  const isDefault = draft.preset === 'gma_48x40' && draft.width_in === 40 && draft.depth_in === 48 && draft.load_in === 54
  return (
    <div className="rounded-xl bg-white/[0.03] p-2.5 ring-1 ring-inset ring-line" data-testid="pallet-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Your pallet</span>
        <select value={preset ? draft.preset : 'custom'} aria-label="Pallet size"
          onChange={(e) => { const p = PALLET_PRESETS.find((x) => x.key === e.target.value); if (p) setDraft((d) => ({ ...d, preset: p.key, width_in: p.width_in, depth_in: p.depth_in })) }}
          className="h-7 rounded-md bg-panel-2 px-1.5 text-[12px] text-ink ring-1 ring-inset ring-line">
          {PALLET_PRESETS.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          <option value="custom">Custom size</option>
        </select>
        {num('width_in', 'W', 24, 60)}{num('depth_in', 'D', 24, 60)}{num('load_in', 'Load H', 12, 96)}
        {busy && <Loader2 size={13} className="animate-spin text-ink-3" />}
        {!isDefault && <button onClick={() => setDraft(() => ({ preset: 'gma_48x40', width_in: 40, depth_in: 48, load_in: 54 }))}
          className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-ink-3 ring-1 ring-inset ring-line hover:bg-white/5"><RotateCcw size={11} /> US standard</button>}
      </div>
      {plan.pallet.source === 'assumed' && <div className="mt-1 text-[11px] text-warn/90">Using the US standard pallet (48 × 40 in, 54 in load) as an assumption. Set yours to see your real capacity.</div>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {chip('pallet', 'Pallet', `${plan.pallet.depth_in}×${plan.pallet.width_in}×${plan.pallet.load_in} in`, plan.pallet.source === 'yours' ? 'yours' : 'assumed')}
        {chip('bay', 'Bay', `${fmtFt(D.rack.bay_width.value, 2)} · ${D.rack.levels.value} levels @ ${fmtFt(D.rack.level_pitch.value, 2)}`, 'calculated')}
        {chip('building', 'Clear height', fmtDim(D.building.clear_height), 'measured')}
        {chip('machine', D.machine.name, `${fmtDim(D.machine.length)} × ${fmtDim(D.machine.width)}`, 'assumed')}
        {chip('aisle', 'Aisle', fmtDim(D.aisle), 'assumed')}
      </div>
    </div>
  )
}
