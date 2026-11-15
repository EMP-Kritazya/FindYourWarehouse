// Storage capacity planner (Test tab). Auto-lays selective racks on the real scanned floor, shows the numbers a buyer
// decides on, compares truck choices, lets the user edit rack rows, and opens the plan inside the Matterport scan.
import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { Box, ChevronLeft, ChevronRight, Info, Loader2, RotateCcw, RotateCw, Trash2, Undo2 } from 'lucide-react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import EquipmentGlyph, { type EquipItem } from '../components/EquipmentGlyph'
import { SourceChip } from '../components/ui'
import RackPlan from './RackPlan'
import { racksApi, type RackEdits, type RackPlan as Plan, type TruckOption } from './racksApi'

const fmt = (n: number | null | undefined) => (n == null ? '?' : Math.round(n).toLocaleString())

export default function RackPanel({ id, modelId, address }: { id: number; modelId: string; address: string }) {
  const [equipment, setEquipment] = useState('reach_truck')
  const [orientation, setOrientation] = useState<'perpendicular' | 'parallel'>('perpendicular')
  const [edits, setEdits] = useState<RackEdits>({})
  const [plan, setPlan] = useState<Plan | null>(null)
  const [trucks, setTrucks] = useState<TruckOption[] | null>(null)
  const [lib, setLib] = useState<Record<string, EquipItem> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const openStage = useStore((s) => s.openStage)

  useEffect(() => { api.library().then((l) => setLib(l.equipment as Record<string, EquipItem>)).catch(() => {}) }, [])
  useEffect(() => {
    let live = true
    setBusy(true); setErr('')
    racksApi.plan(id, equipment, orientation, edits)
      .then((p) => { if (live) setPlan(p) })
      .catch((e) => { if (live) setErr(String(e.message ?? e)) })
      .finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [id, equipment, orientation, edits])
  useEffect(() => {
    let live = true
    setTrucks(null)
    racksApi.trucks(id, orientation).then((t) => { if (live) setTrucks(t.trucks) }).catch(() => {})
    return () => { live = false }
  }, [id, orientation])

  const changeLayout = (fn: () => void) => { setEdits({}); setSel(null); fn() }
  const move = useCallback((rid: string, dx: number) => setEdits((e) => ({ ...e, moved: { ...(e.moved ?? {}), [rid]: Math.round(((e.moved?.[rid] ?? 0) + dx) * 100) / 100 } })), [])
  const toggleDelete = (rid: string) => setEdits((e) => {
    const d = new Set(e.deleted ?? [])
    if (d.has(rid)) d.delete(rid); else d.add(rid)
    return { ...e, deleted: [...d] }
  })
  const row = plan?.rows.find((r) => r.id === sel) ?? null
  const nEdits = (edits.deleted?.length ?? 0) + Object.keys(edits.moved ?? {}).length
  const invalid = plan?.rows.filter((r) => !r.deleted && !r.ok) ?? []
  const base = useMemo(() => trucks?.find((t) => t.equipment === 'counterbalance_electric')?.positions, [trucks])

  return (
    <div className="space-y-3" data-testid="rack-panel">
      {/* headline numbers */}
      <div className="grid grid-cols-2 gap-2">
        <Stat big label="Pallet positions" value={plan ? fmt(plan.positions) : '…'} sub={plan ? `in the scanned ${fmt(plan.scanned_sqft)} SF` : ''} src="matterport" />
        <Stat big label="Whole building (est.)" value={plan?.building_estimate ? `~${fmt(plan.building_estimate.value)}` : '?'}
          sub={plan?.building_estimate ? `scaled ×${plan.building_estimate.ratio} to CoStar's size` : 'no building size'} src="costar" title={plan?.building_estimate?.basis} est />
        <Stat label="Rack levels" value={plan ? String(plan.levels) : '…'} sub={plan ? `${plan.aisle_ft} ft aisles` : ''} title={plan?.levels_basis} />
        <Stat label="SF per position" value={plan?.sqft_per_position != null ? String(plan.sqft_per_position) : '?'} sub="scanned floor" />
        <Stat label="$ per position / yr" value={plan?.cost_per_position_yr ? `$${fmt(plan.cost_per_position_yr.value)}` : '—'}
          sub={plan?.cost_per_position_yr ? 'CoStar asking rent' : 'rent not listed in CoStar'} title={plan?.cost_per_position_yr?.basis} />
        <Stat label="Lost to columns" value={plan?.positions_lost_to_columns != null ? fmt(plan.positions_lost_to_columns) : '?'}
          sub="positions (scan column grid)" tone={plan?.positions_lost_to_columns ? 'warn' : undefined} />
      </div>
      {plan && <div className="flex gap-1.5 text-[11px] leading-snug text-ink-3"><Info size={12} className="mt-px shrink-0" />{plan.levels_basis}. {plan.sprinkler}.</div>}

      {/* equipment trade-off */}
      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Same floor, different trucks</div>
        <div className="grid grid-cols-3 gap-1.5">
          {(trucks ?? [{ equipment: 'counterbalance_electric' }, { equipment: 'reach_truck' }, { equipment: 'vna_turret' }] as TruckOption[]).map((t) => (
            <button key={t.equipment} onClick={() => changeLayout(() => setEquipment(t.equipment))} data-testid={`truck-${t.equipment}`}
              className={clsx('rounded-xl p-2 text-left ring-1 ring-inset transition', t.equipment === equipment ? 'bg-accent/10 ring-accent/50' : 'bg-white/[0.03] ring-line hover:bg-white/[0.06]')}>
              {lib?.[t.equipment] && <EquipmentGlyph item={lib[t.equipment]} itemKey={t.equipment} frameFt={14} frameHFt={16} className="h-10 w-full" />}
              <div className="mt-1 truncate text-[11.5px] font-medium text-ink">{lib?.[t.equipment]?.name ?? t.name ?? t.equipment}</div>
              <div className="text-[15px] font-semibold tabular-nums text-ink">{t.positions != null ? fmt(t.positions) : <Loader2 size={12} className="animate-spin" />}</div>
              <div className="text-[11px] tabular-nums text-ink-3">
                {t.levels != null && `${t.levels} lvls · ${t.aisle_ft} ft aisle`}
                {base && t.positions != null && t.equipment !== 'counterbalance_electric' && <span className="ml-1 font-semibold text-good">+{t.vs_counterbalance_pct}%</span>}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* plan editor */}
      <div>
        <div className="mb-1.5 flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Rack plan</span>
          <span className="text-[11px] text-ink-3">{plan?.rows.filter((r) => r.slots_kept).length ?? 0} rows · {plan?.staging_lanes ?? 0} staging lanes</span>
          {busy && <Loader2 size={12} className="animate-spin text-ink-3" />}
          <div className="ml-auto flex overflow-hidden rounded-lg ring-1 ring-inset ring-line text-[11px]">
            {(['perpendicular', 'parallel'] as const).map((o) => (
              <button key={o} onClick={() => changeLayout(() => setOrientation(o))}
                className={clsx('px-2 py-1', orientation === o ? 'bg-accent/15 text-accent' : 'text-ink-3 hover:bg-white/5')}>
                {o === 'perpendicular' ? '⟂ dock wall' : '∥ dock wall'}
              </button>
            ))}
          </div>
        </div>
        {err && <div className="mb-2 rounded-lg bg-bad/10 px-2.5 py-1.5 text-[12px] text-bad">{err}</div>}
        {plan && <RackPlan plan={plan} selected={sel} onSelect={setSel} onMove={move} />}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px]">
          {row ? (
            <>
              <span className="font-mono text-ink-2">{row.id}</span>
              <span className="text-ink-3">{row.bays} bays · {row.slots} positions</span>
              <button title="Move 1 ft left" aria-label="Move 1 ft left" onClick={() => move(row.id, -0.3)} className="rounded-md p-1 ring-1 ring-inset ring-line hover:bg-white/5"><ChevronLeft size={13} /></button>
              <button title="Move 1 ft right" aria-label="Move 1 ft right" onClick={() => move(row.id, 0.3)} className="rounded-md p-1 ring-1 ring-inset ring-line hover:bg-white/5"><ChevronRight size={13} /></button>
              <button onClick={() => toggleDelete(row.id)} className="flex items-center gap-1 rounded-md px-1.5 py-1 text-bad ring-1 ring-inset ring-bad/30 hover:bg-bad/10">
                {row.deleted ? <><Undo2 size={12} /> Restore</> : <><Trash2 size={12} /> Remove row</>}
              </button>
              {!row.deleted && !row.ok && <span className="text-bad">{row.why}</span>}
            </>
          ) : <span className="text-ink-3">Click a rack row to select it; drag it sideways or remove it. Columns are the white dots; amber = staging lanes at the dock doors.</span>}
          {nEdits > 0 && (
            <button onClick={() => { setEdits({}); setSel(null) }} className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-1 text-ink-3 ring-1 ring-inset ring-line hover:bg-white/5">
              <RotateCcw size={12} /> Auto layout
            </button>
          )}
        </div>
        {invalid.length > 0 && <div className="mt-1 text-[11.5px] text-bad">{invalid.length} row{invalid.length > 1 ? 's' : ''} not counted: {invalid[0].why}</div>}
      </div>

      <button disabled={!plan} onClick={() => plan && openStage({ modelId, sceneUrl: plan.scene_url, title: address, subtitle: `Storage plan · ${fmt(plan.positions)} positions`, mode: 'view', propertyId: id })}
        className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-violet-500/25 text-[13px] font-semibold text-violet-50 ring-1 ring-inset ring-violet-300/40 transition hover:bg-violet-500/35 disabled:opacity-50">
        <Box size={15} /> See the racks inside the 3D scan
      </button>
      {plan && (
        <details className="text-[11px] text-ink-3">
          <summary className="cursor-pointer select-none">Assumptions ({plan.assumptions.length})</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">{plan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </details>
      )}
      {orientation === 'parallel' && <div className="flex items-center gap-1 text-[11px] text-ink-3"><RotateCw size={11} />Rows run parallel to the dock wall (left edge in the plan).</div>}
    </div>
  )
}

function Stat({ label, value, sub, src, title, est, big, tone }: {
  label: string; value: string; sub?: string; src?: string; title?: string; est?: boolean; big?: boolean; tone?: 'warn'
}) {
  return (
    <div title={title} className={clsx('rounded-xl bg-white/[0.03] px-3 py-2 ring-1 ring-inset', est ? 'ring-dashed ring-line-2' : 'ring-line')}>
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.06em] text-ink-3">{label}{src && <SourceChip source={src} />}</div>
      <div className={clsx('font-semibold tabular-nums', big ? 'text-[20px]' : 'text-[16px]', tone === 'warn' ? 'text-warn' : 'text-ink')}>{value}</div>
      {sub && <div className="truncate text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}
