// S4: the compare panel (plan B3, CONTRACTS_v3 §2 + §4). Right column (432 px), covers the card while open.
// Everything comes from POST /api/compare: criteria picked for the operation, per-candidate values with their kind
// (verified = solid, estimated = outlined, unknown = "?"), a winner only beyond tolerance, tradeoffs, fit and a
// deterministic recommendation. "Show all details" expands the old full rows.
import { useEffect, useMemo, useState } from 'react'
import { X, ChevronDown, Star, AlertTriangle, Check, Minus, Box } from 'lucide-react'
import clsx from 'clsx'
import { useStore } from '../lib/store'
import { compareApi, type CmpRow, type CmpValue, type CompareResult, type Fit, type Operation } from '../lib/compareApi'
import type { WarehouseDetail, VexcelProfile } from '../lib/api'
import { AVAIL_LABEL, fmtFt, fmtInt, fmtKSF, isNum } from '../lib/format'
import { Skeleton, useAsync } from './ui'
import { loadDetail, loadTwin } from './WarehouseCard'
import { loadVexcel, pv } from '../lib/vexcel'

const LETTERS = ['A', 'B', 'C', 'D']
const FIT_STYLE: Record<Fit, string> = {
  pass: 'bg-good/15 text-good ring-good/30',
  flag: 'bg-warn/15 text-warn ring-warn/30',
  fail: 'bg-bad/15 text-bad ring-bad/30',
}
const FIT_LABEL: Record<Fit, string> = { pass: 'Fits', flag: 'Check', fail: 'Fails' }

let opsCache: Promise<Operation[]> | null = null

export default function CompareView() {
  const ids = useStore((s) => s.compareIds)
  const open = useStore((s) => s.compareOpen)
  const show = useStore((s) => s.showCompare)
  const profile = useStore((s) => s.compareProfile)
  const setProfile = useStore((s) => s.setCompareProfile)
  const select = useStore((s) => s.select)
  const [ops, setOps] = useState<Operation[]>([])
  const [details, setDetails] = useState(false)
  const key = ids.join(',')
  const res = useAsync<CompareResult>(open && ids.length >= 2 ? () => compareApi.compare(ids, profile) : null, [key, profile, open])

  useEffect(() => {
    if (!open) return
    ;(opsCache ??= compareApi.operations().catch(() => [])).then(setOps)
  }, [open])
  useEffect(() => setDetails(false), [key])

  if (!open || ids.length < 2) return null
  const c = res.data
  const letter = (id: number) => LETTERS[ids.indexOf(id)] ?? '?'
  const addr = (id: number) => c?.candidates.find((x) => x.id === id)?.address ?? String(id)

  return (
    <aside className="glass drawer-in absolute bottom-4 right-4 top-4 z-30 flex w-[432px] flex-col overflow-hidden rounded-2xl" aria-label="Compare warehouses">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">Compare {ids.length} warehouses</h2>
          <div className="text-[11px] text-ink-3">Solid = CoStar · outlined = estimate · ? = unknown</div>
        </div>
        <button onClick={() => show(false)} title="Close (Esc)" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.06] hover:bg-white/10"><X size={16} /></button>
      </header>

      <div className="scroll-thin flex-1 overflow-y-auto px-4 pb-4">
        {/* candidates */}
        <div className="grid gap-2 pt-3" style={{ gridTemplateColumns: `repeat(${ids.length}, minmax(0, 1fr))` }}>
          {ids.map((id) => {
            const cand = c?.candidates.find((x) => x.id === id)
            const fit = c?.fit[String(id)]
            const rec = c?.recommendation.id === id
            return (
              <button key={id} onClick={() => { show(false); select(id) }} title={`Open ${cand?.address ?? id}`}
                className={clsx('group min-w-0 rounded-xl bg-panel-2 p-1.5 text-left ring-1 transition hover:bg-panel-3', rec ? 'ring-accent/70' : 'ring-line')}>
                <div className="relative aspect-[16/10] overflow-hidden rounded-lg bg-panel-3">
                  <img src={cand?.thumb ?? `/media/twins/exterior/${id}_ortho.jpg`} alt="" className="h-full w-full object-cover" onError={(e) => (e.currentTarget.style.display = 'none')} />
                  <span className="absolute left-1 top-1 rounded bg-black/65 px-1 text-[10px] font-semibold text-ink">{letter(id)}</span>
                  {rec && <span className="absolute right-1 top-1 rounded bg-accent px-1 text-bg" title="Recommended"><Star size={10} className="my-[2px]" fill="currentColor" /></span>}
                </div>
                <div className="mt-1 truncate text-[11.5px] font-medium leading-tight">{cand?.address ?? <Skeleton className="h-3 w-16" />}</div>
                <div className="mt-1 flex items-center gap-1">
                  {fit ? <span title={(c?.fit_why[String(id)] ?? []).join('\n') || 'Passes every deal-breaker'}
                    className={clsx('inline-flex h-[17px] items-center rounded px-1.5 text-[10px] font-semibold ring-1 ring-inset', FIT_STYLE[fit])}>{FIT_LABEL[fit]}</span>
                    : <Skeleton className="h-3.5 w-10" />}
                  {cand?.inspectable && <Box size={11} className="text-ink-3" aria-label="3D scan" />}
                </div>
              </button>
            )
          })}
        </div>

        {/* operation */}
        <label className="mt-3 flex items-center gap-2 text-[12px] text-ink-3">
          <span className="shrink-0">Operation</span>
          <select value={profile ?? c?.profile ?? ''} onChange={(e) => setProfile(e.target.value || null)}
            className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-[13px] text-ink outline-none focus:border-accent">
            {!ops.length && c && <option value={c.profile}>{c.operation.name}</option>}
            {ops.map((o) => <option key={o.key} value={o.key}>{o.name}</option>)}
          </select>
        </label>
        {c?.operation.matched_by === 'default' && !profile && (
          <div className="mt-1 text-[11px] text-ink-3">Default operation: pick yours for the right criteria.</div>
        )}

        {res.loading && !c && <div className="mt-4 space-y-2"><Skeleton className="h-16" /><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>}
        {res.error != null && !c && <div className="mt-4 rounded-lg bg-bad/10 px-3 py-2 text-[12px] text-bad">The comparison could not be computed.</div>}

        {c && (
          <>
            {/* recommendation */}
            <section className="mt-3 rounded-xl bg-accent/[0.07] p-3 ring-1 ring-inset ring-accent/25">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-accent">
                <Star size={11} fill="currentColor" /> Recommendation · {letter(c.recommendation.id)}
              </div>
              <p className="text-[13px] leading-snug text-ink">{c.recommendation.text}</p>
              {c.recommendation.basis.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  <span className="text-[10.5px] text-ink-3">Why</span>
                  {c.recommendation.basis.map((k) => {
                    const r = c.rows.find((x) => x.key === k)
                    return <span key={k} title={c.criteria.find((x) => x.key === k)?.why}
                      className="rounded-full bg-accent/15 px-2 py-[1px] text-[10.5px] font-medium text-accent">{r?.label ?? k}</span>
                  })}
                </div>
              )}
            </section>

            {/* criteria */}
            <section className="mt-3">
              <div className="mb-1.5 flex items-baseline justify-between">
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">{c.operation.name} criteria</h3>
                <span className="text-[10.5px] text-ink-3">weight</span>
              </div>
              <div className="divide-y divide-line rounded-xl bg-panel-2/60 ring-1 ring-line">
                {c.rows.slice(0, 7).map((r) => <CriterionRow key={r.key} row={r} ids={ids} letter={letter}
                  why={c.criteria.find((x) => x.key === r.key)?.why} />)}
              </div>
            </section>

            {/* tradeoffs */}
            {c.tradeoffs.length > 0 && (
              <section className="mt-3">
                <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Tradeoffs</h3>
                <ul className="space-y-1.5">
                  {c.tradeoffs.map((t, i) => (
                    <li key={i} className="flex gap-2 text-[12.5px] leading-snug text-ink-2">
                      <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-3" />{t}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Object.values(c.fit).some((f) => f !== 'pass') && (
              <section className="mt-3 space-y-1">
                {ids.filter((id) => c.fit[String(id)] !== 'pass').map((id) => (
                  <div key={id} className="flex gap-1.5 text-[11.5px] text-ink-3">
                    <AlertTriangle size={12} className={clsx('mt-[2px] shrink-0', c.fit[String(id)] === 'fail' ? 'text-bad' : 'text-warn')} />
                    <span><b className="font-semibold text-ink-2">{letter(id)} {addr(id).split(',')[0]}:</b> {(c.fit_why[String(id)] ?? []).slice(0, 2).join('; ')}</span>
                  </div>
                ))}
              </section>
            )}
          </>
        )}

        <button onClick={() => setDetails((d) => !d)} className="mt-4 flex w-full items-center justify-between rounded-lg bg-white/[0.04] px-3 py-2 text-[12px] text-ink-2 hover:bg-white/[0.07]">
          Show all details <ChevronDown size={14} className={clsx('transition', details && 'rotate-180')} />
        </button>
        {details && <AllDetails ids={ids} letter={letter} />}
      </div>
    </aside>
  )
}

function WeightDots({ n }: { n: number }) {
  return (
    <span className="inline-flex items-center gap-[2px]" title={`Weight ${n} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => <span key={i} className={clsx('h-[4px] w-[4px] rounded-full', i <= n ? 'bg-ink-2' : 'bg-line-2')} />)}
    </span>
  )
}

function Verdict({ row, letter }: { row: CmpRow; letter: (id: number) => string }) {
  if (row.verdict === 'winner' && row.winner != null)
    return <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-accent" title={row.estimated ? 'Decided on an estimate' : 'Decided on CoStar values'}>
      <Check size={11} strokeWidth={3} />{letter(row.winner)}{row.estimated && <span className="font-normal text-ink-3">est.</span>}</span>
  if (row.verdict === 'tie') return <span className="inline-flex items-center gap-1 text-[11px] text-ink-2" title="Within the measurement tolerance"><Minus size={11} />tie</span>
  return <span className="text-[11px] text-ink-3" title={row.why ?? 'At least one value is unknown, or estimates overlap'}>? undecided</span>
}

function CriterionRow({ row, ids, letter, why }: { row: CmpRow; ids: number[]; letter: (id: number) => string; why?: string }) {
  const nums = row.values.map((v) => (typeof v.value === 'number' ? v.value : typeof v.value === 'boolean' ? (v.value ? 1 : 0) : typeof v.lo === 'number' ? v.lo : null))
  const finite = nums.filter((x): x is number => x != null)
  const max = finite.length ? Math.max(...finite.map(Math.abs)) : 0
  return (
    <div className="px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink" title={why}>
          {row.label}{row.level === 'D' && <span className="ml-1 text-[10px] font-semibold text-warn" title="Deal-breaker">must</span>}
        </span>
        <WeightDots n={row.weight} />
        <span className="w-[74px] text-right"><Verdict row={row} letter={letter} /></span>
      </div>
      <div className="mt-1.5 grid gap-1.5" style={{ gridTemplateColumns: `repeat(${ids.length}, minmax(0, 1fr))` }}>
        {ids.map((id, i) => {
          const v = row.values.find((x) => x.id === id)
          return <ValueCell key={id} v={v} letter={LETTERS[i]} win={row.winner === id} frac={max > 0 && nums[row.values.indexOf(v!)] != null ? Math.abs(nums[row.values.indexOf(v!)]!) / max : null} better={row.better} />
        })}
      </div>
    </div>
  )
}

function ValueCell({ v, letter, win, frac, better }: { v?: CmpValue; letter: string; win: boolean; frac: number | null; better: 'higher' | 'lower' }) {
  if (!v || v.kind === 'unknown')
    return (
      <div title={v?.basis ?? 'Unknown'} className="flex h-[38px] cursor-help flex-col justify-center rounded-md bg-white/[0.02] px-1.5 ring-1 ring-inset ring-white/[0.06]">
        <span className="text-[9.5px] font-semibold text-ink-3">{letter}</span>
        <span className="text-[12.5px] font-semibold text-ink-3">?</span>
      </div>
    )
  const est = v.kind === 'estimated'
  const tip = [v.basis, v.source && `Source: ${v.source}`, `Confidence ${Math.round((v.confidence ?? 0) * 100)}%`].filter(Boolean).join('\n')
  return (
    <div title={tip} className={clsx('relative flex h-[38px] flex-col justify-center overflow-hidden rounded-md px-1.5',
      est ? 'border border-dashed border-ink-3/60 bg-transparent' : 'bg-panel-3',
      win && 'ring-1 ring-inset ring-accent/80')}>
      <span className="flex items-center gap-1 text-[9.5px] font-semibold text-ink-3">{letter}{win && <span className="h-[5px] w-[5px] rounded-full bg-accent" />}{est && <span className="font-normal">est.</span>}</span>
      <span className={clsx('truncate text-[12.5px] font-semibold tabular-nums', win ? 'text-accent' : est ? 'text-ink-2' : 'text-ink')}>{v.display}</span>
      {frac != null && (
        <span className="absolute inset-x-1.5 bottom-[3px] h-[2px] rounded-full bg-white/[0.05]">
          <span className={clsx('absolute inset-y-0 left-0 rounded-full', win ? 'bg-accent' : 'bg-ink-3/60')}
            style={{ width: `${Math.max(4, frac * 100)}%` }} title={better === 'lower' ? 'Lower is better' : undefined} />
        </span>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ the old full rows, behind "Show all details"
type Row = { label: string; get: (w: WarehouseDetail) => { v: number | string | null; src?: string | null }; fmt: (v: number) => string }
const fact = (k: string) => (w: WarehouseDetail) => {
  const f = w.facts[k]
  return { v: f && (isNum(f.value) || typeof f.value === 'string') ? (f.value as number | string) : null, src: f?.source }
}
const ROWS: Row[] = [
  { label: 'Building size', get: fact('size_sqft'), fmt: fmtKSF },
  { label: 'Clear height', get: fact('clear_height_ft'), fmt: fmtFt },
  { label: 'Dock doors', get: fact('docks_total'), fmt: fmtInt },
  { label: 'Docks per 10k SF', get: fact('docks_per_10ksf'), fmt: (v) => v.toFixed(2) },
  { label: 'Drive-in doors', get: fact('drive_ins'), fmt: fmtInt },
  { label: 'Truck court', get: fact('truck_court_ft'), fmt: fmtFt },
  { label: 'Power', get: fact('power_amps'), fmt: (v) => `${fmtInt(v)} A` },
  { label: 'Year built', get: fact('year_built'), fmt: String },
  { label: 'Roof condition', get: fact('roof_condition'), fmt: (v) => `${v} / 5` },
  { label: 'Asking rent', get: fact('rent_psf_yr'), fmt: (v) => `$${v.toFixed(2)}` },
  { label: 'Availability', get: (w) => ({ v: AVAIL_LABEL[w.availability] ?? null, src: 'costar' }), fmt: String },
]
const vget = (grp: 'parking' | 'building' | 'site', k: string) => (p: VexcelProfile) => pv(((p[grp] ?? {}) as Record<string, never>)[k] ?? null)?.v ?? null
const VROWS: { label: string; get: (p: VexcelProfile) => unknown; fmt: (v: number) => string }[] = [
  { label: 'Parking area', get: vget('parking', 'parking_lot_area_sqft'), fmt: fmtKSF },
  { label: 'Paved area', get: vget('parking', 'paved_area_sqft'), fmt: fmtKSF },
  { label: 'Car stalls (est.)', get: vget('parking', 'car_stalls_est'), fmt: (v) => `≤ ${fmtInt(v)}` },
  { label: 'Trailers seen', get: vget('parking', 'trailers_seen'), fmt: fmtInt },
  { label: 'Building height (DSM)', get: vget('building', 'height_ft_dsm'), fmt: fmtFt },
]

function AllDetails({ ids, letter }: { ids: number[]; letter: (id: number) => string }) {
  const k = ids.join(',')
  const det = useAsync(() => Promise.all(ids.map(loadDetail)), [k])
  const vex = useAsync(() => Promise.all(ids.map((i) => loadVexcel(i, () => loadTwin(i)).catch(() => null))), [k])
  const ws = det.data ?? []
  const vs = vex.data ?? []
  const cols = { gridTemplateColumns: `112px repeat(${ids.length}, minmax(0, 1fr))` }
  const header = useMemo(() => ids.map((id) => <div key={id} className="text-[10.5px] font-semibold text-ink-3">{letter(id)}</div>), [k])
  return (
    <div className="mt-2 text-[12px]">
      <div className="grid gap-x-2 pb-1" style={cols}><div />{header}</div>
      {ROWS.map((row) => (
        <div key={row.label} className="grid items-center gap-x-2 border-t border-line py-1.5" style={cols}>
          <div className="truncate text-ink-3">{row.label}</div>
          {ids.map((id, i) => {
            const w = ws[i]
            if (!w) return <Skeleton key={id} className="h-3.5 w-12" />
            const x = row.get(w)
            const other = x.v != null && x.src && x.src !== 'costar'
            return <div key={id} className="min-w-0 truncate tabular-nums" title={other ? `Source: ${x.src} (not CoStar)` : undefined}>
              {x.v == null ? <span className="text-ink-3" title="Unknown: not reported or measured">?</span>
                : <span className={clsx(other && 'text-ink-2 underline decoration-dotted decoration-ink-3 underline-offset-2')}>{isNum(x.v) ? row.fmt(x.v) : x.v}</span>}
            </div>
          })}
        </div>
      ))}
      <div className="mt-1 text-[10.5px] text-ink-3">Dotted underline = measured or derived, not CoStar.</div>
      <div className="mt-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-amber-300/80">From the air · Vexcel</div>
      {VROWS.map((row) => (
        <div key={row.label} className="grid items-center gap-x-2 border-t border-line py-1.5" style={cols}>
          <div className="truncate text-ink-3">{row.label}</div>
          {ids.map((id, i) => {
            if (vex.loading) return <Skeleton key={id} className="h-3.5 w-12" />
            const v = vs[i] ? row.get(vs[i]!) : null
            return <div key={id} className="truncate tabular-nums">{isNum(v) ? row.fmt(v) : <span className="text-ink-3">?</span>}</div>
          })}
        </div>
      ))}
    </div>
  )
}
