// Head to head: two warehouses side by side over the map (opened by "compare the first and second", or by ticking
// two cards in the strip). Everything is from POST /api/compare (criteria for the buyer's operation, a winner only
// beyond tolerance, unknown never counted) plus the same yard and interior tests both buildings can run.
// A criterion a building wins is its advantage; one it loses is its disadvantage; an unknown is listed as unknown.
import { useMemo } from 'react'
import clsx from 'clsx'
import { AlertTriangle, Box, Check, CircleHelp, GitCompareArrows, Minus, Star, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { compareApi, type CmpRow, type CmpValue, type CompareResult, type Fit } from '../lib/compareApi'
import { AVAIL_LABEL, fmtKSF, isNum } from '../lib/format'
import { Skeleton, useAsync } from './ui'
import { GradeChip, useYardVerdict } from './YardVerdict'
import { useStorage } from '../racks/StorageSummary'
import { money, useCost } from '../economics/useCost'
import { useCostOperation } from '../economics/CostPanel'
import { useFlood } from '../flood/FloodPanel'

const FIT: Record<Fit, { label: string; cls: string }> = {
  pass: { label: 'Fits your deal-breakers', cls: 'bg-good/15 text-good ring-good/30' },
  flag: { label: 'Deal-breaker unverified', cls: 'bg-warn/15 text-warn ring-warn/30' },
  fail: { label: 'Fails a deal-breaker', cls: 'bg-bad/15 text-bad ring-bad/30' },
}
const GRADE_RANK: Record<string, number> = { good: 2, limited: 1, poor: 0 }

interface Point { text: string; detail?: string; est?: boolean }

export default function CompareDuel() {
  const ids = useStore((s) => s.compareIds)
  const open = useStore((s) => s.compareOpen)
  const profile = useStore((s) => s.compareProfile)
  const buyer = useStore((s) => s.buyerProfile)
  const show = useStore((s) => s.showCompare)
  const on = open && ids.length === 2
  // curate to the buyer: their operation key if the analyst picked one, else their own words
  const opsText = useMemo(() => {
    if (profile || !buyer) return null
    const b = buyer as Record<string, unknown>
    const bits = [b.business, ...((b.must_haves as string[]) ?? []), ...((b.nice_to_haves as string[]) ?? []),
      ...(((b.trucks as string[]) ?? []).map((t) => t.replace(/_/g, ' ')))].filter((x) => typeof x === 'string' && x)
    return bits.length ? bits.join(', ') : null
  }, [profile, buyer])
  const key = ids.join(',')
  const res = useAsync<CompareResult>(on ? () => compareApi.compare(ids, profile, opsText) : null, [key, profile, opsText, on])
  if (!on) return null
  const [a, b] = ids
  const c = res.data ?? undefined

  return (
    <div className="pointer-events-none absolute bottom-[172px] left-4 right-4 top-4 z-40 flex items-start justify-center xl:right-[412px]" data-testid="compare-duel">
      <section className="glass drawer-in pointer-events-auto flex max-h-full w-full max-w-[1120px] flex-col overflow-hidden rounded-2xl" aria-label="Compare two warehouses">
        <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
          <GitCompareArrows size={16} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold tracking-tight">Head to head</h2>
            <div className="truncate text-[11.5px] text-ink-3">
              {c ? <>Judged for <span className="text-ink-2">{c.operation.name}</span>{opsText ? ` · from what you told me: ${opsText}` : ''} · only criteria the data can decide pick a winner</> : 'Scoring both buildings…'}
            </div>
          </div>
          <button onClick={() => show(false)} title="Close (Esc)" aria-label="Close the comparison (Esc)"
            className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.06] hover:bg-white/10"><X size={16} /></button>
        </header>

        {res.error ? <div className="p-6 text-[13px] text-bad">The comparison could not be computed ({String(res.error)}).</div> : (
          <div className="scroll-thin grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[1fr_minmax(300px,1.05fr)_1fr]">
            <Side id={a} other={b} letter="A" c={c} />
            <Scale c={c} a={a} b={b} />
            <Side id={b} other={a} letter="B" c={c} />
          </div>
        )}

        {c && (
          <footer className="flex items-start gap-2.5 border-t border-line bg-accent/[0.06] px-4 py-2.5">
            <Star size={15} className="mt-0.5 shrink-0 text-accent" fill="currentColor" />
            <div className="min-w-0 text-[12.5px] leading-snug">
              <span className="text-ink">{c.recommendation.text}</span>
              {c.tradeoffs[0] && <span className="ml-1 text-ink-3">{c.tradeoffs[0]}</span>}
            </div>
          </footer>
        )}
      </section>
    </div>
  )
}

/** One building: photo, headline facts, its advantages, disadvantages and unknowns, and its two test results. */
function Side({ id, other, letter, c }: { id: number; other: number; letter: string; c: CompareResult | undefined }) {
  const pin = useStore((s) => s.pinsById.get(id))
  const select = useStore((s) => s.select)
  const show = useStore((s) => s.showCompare)
  const openTest = useStore((s) => s.openTest)
  const yard = useYardVerdict(id), yardO = useYardVerdict(other)
  const inside = useStorage(id), insideO = useStorage(other)
  const costOp = useCostOperation()
  const cost = useCost(id, costOp), costO = useCost(other, costOp)
  const fl = useFlood(id).f, flO = useFlood(other).f
  const cand = c?.candidates.find((x) => x.id === id)
  const fit = c?.fit[String(id)]
  const rec = c?.recommendation.id === id

  const { pros, cons, unknown } = useMemo(() => {
    const pros: Point[] = [], cons: Point[] = [], unknown: Point[] = []
    if (!c) return { pros, cons, unknown }
    for (const r of c.rows) {
      const me = val(r, id), them = val(r, other)
      if (me?.kind === 'unknown') { unknown.push({ text: r.label, detail: r.level === 'D' ? 'deal-breaker' : undefined }); continue }
      if (r.winner === id) pros.push({ text: r.label, detail: `${me?.display} vs ${them?.display}`, est: r.estimated })
      else if (r.winner === other) cons.push({ text: r.label, detail: `${me?.display} vs ${them?.display}`, est: r.estimated })
    }
    // deal-breakers this building misses (an unknown one is already listed under Unknown)
    for (const w of (c.fit_why[String(id)] ?? []).filter((x) => !/unknown/i.test(x))) cons.push({ text: w.replace(/\s*\(deal-breaker\)/, ''), detail: 'deal-breaker' })
    // the same tests on both buildings: count a difference only when both have a result
    const g = yard.v?.status === 'computed' ? yard.v.grade : undefined, go = yardO.v?.status === 'computed' ? yardO.v.grade : undefined
    if (g && go && GRADE_RANK[g] !== GRADE_RANK[go]) {
      ;(GRADE_RANK[g] > GRADE_RANK[go] ? pros : cons).push({ text: 'Yard test (5 semis)', detail: `${g} vs ${go}` })
    }
    const r1 = inside?.status === 'computed' ? inside.reachable_pct : undefined, r2 = insideO?.status === 'computed' ? insideO.reachable_pct : undefined
    if (isNum(r1) && isNum(r2) && Math.abs(r1 - r2) >= 10) {
      ;(r1 > r2 ? pros : cons).push({ text: 'Forklift reach inside', detail: `${Math.round(r1)}% vs ${Math.round(r2)}% of racks`, est: true })
    }
    const m1 = inside?.status === 'computed' ? inside.minutes_per_pallet : undefined, m2 = insideO?.status === 'computed' ? insideO.minutes_per_pallet : undefined
    if (isNum(m1) && isNum(m2) && Math.abs(m1 - m2) / Math.max(m1, m2) >= 0.1) {
      ;(m1 < m2 ? pros : cons).push({ text: 'Dock-to-rack trip', detail: `${m1.toFixed(1)} vs ${m2.toFixed(1)} min/pallet`, est: true })
    }
    // true cost per pallet: only when both are known and differ by > 10% (an estimate stays marked est.)
    const p1 = cost?.per_position_month.value, p2 = costO?.per_position_month.value
    if (isNum(p1) && isNum(p2) && Math.abs(p1 - p2) / Math.max(p1, p2) > 0.1) {
      const est = [cost, costO].some((x) => !x || x.per_position_month.rough || !['asking', 'own'].includes(x.rent.tier))
      ;(p1 < p2 ? pros : cons).push({ text: 'Cost per pallet / month', detail: `${money(p1, 2)} vs ${money(p2, 2)}`, est })
    }
    // flood exposure: only when both are computed and neither is 'not enough data'
    const RANK: Record<string, number> = { lower: 0, moderate: 1, higher: 2 }
    const l1 = fl?.indicator?.level, l2 = flO?.indicator?.level
    if (l1 && l2 && l1 in RANK && l2 in RANK && RANK[l1] !== RANK[l2]) {
      ;(RANK[l1] < RANK[l2] ? pros : cons).push({ text: 'Flood exposure', detail: `${fl!.indicator!.label.toLowerCase()} vs ${flO!.indicator!.label.toLowerCase()}` })
    }
    return { pros, cons, unknown }
  }, [c, id, other, yard.v, yardO.v, inside, insideO, cost, costO, fl, flO])

  return (
    <article className={clsx('flex min-w-0 flex-col gap-2.5 rounded-xl bg-panel-2/70 p-2.5 ring-1', rec ? 'ring-accent/60' : 'ring-line')} data-testid={`duel-${letter}`}>
      <div className="relative aspect-[16/8] overflow-hidden rounded-lg bg-panel-3">
        <img src={cand?.thumb ?? `/media/twins/exterior/${id}_ortho.jpg`} alt={`Aerial of ${pin?.address ?? id}`} className="h-full w-full object-cover"
          onError={(e) => (e.currentTarget.style.display = 'none')} />
        <span className="absolute left-2 top-2 rounded-md bg-black/70 px-1.5 text-[12px] font-bold text-ink">{letter}</span>
        {rec && <span className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-accent px-1.5 py-0.5 text-[11px] font-semibold text-bg"><Star size={11} fill="currentColor" /> Recommended</span>}
      </div>
      <div>
        <div className="flex items-center gap-1.5 truncate text-[14px] font-semibold">
          <span className="truncate">{pin?.address ?? cand?.address ?? <Skeleton className="h-4 w-32" />}</span>
          {pin?.inspectable && <Box size={12} className="shrink-0 text-violet-300" aria-label="3D scan" />}
        </div>
        <div className="mt-0.5 text-[12px] tabular-nums text-ink-3">
          {fmtKSF(cand?.size_sqft ?? pin?.rba_sqft)} · {AVAIL_LABEL[pin?.availability ?? cand?.availability ?? ''] ?? 'Availability ?'}
          {pin?.clear_height_ft != null && ` · ${pin.clear_height_ft} ft clear`}
        </div>
        {fit ? <span title={(c?.fit_why[String(id)] ?? []).join('\n') || undefined}
          className={clsx('mt-1.5 inline-flex h-5 items-center rounded px-1.5 text-[11px] font-semibold ring-1 ring-inset', FIT[fit].cls)}>{FIT[fit].label}</span>
          : <Skeleton className="mt-1.5 h-5 w-28" />}
      </div>

      <List title="Advantages" tone="good" items={pros} empty={c ? 'Wins no criterion outright' : undefined} />
      <List title="Disadvantages" tone="bad" items={cons} empty={c ? 'Loses no criterion' : undefined} />
      {unknown.length > 0 && <List title="Unknown (no data)" tone="warn" items={unknown} />}

      <div className="mt-auto grid grid-cols-2 gap-1.5 text-[11.5px]">
        <div className="rounded-lg bg-white/[0.03] px-2 py-1.5 ring-1 ring-inset ring-line">
          <div className="text-[11px] uppercase tracking-[0.06em] text-ink-3">Yard</div>
          {yard.v?.status === 'computed'
            ? <div className="mt-0.5 flex items-center gap-1.5"><GradeChip grade={yard.v.grade} /><span className="truncate text-ink-2" title={yard.v.line}>{yard.v.completed}/{yard.v.trucks} semis</span></div>
            : <button onClick={yard.run} disabled={yard.running} className="mt-0.5 text-accent hover:underline disabled:text-ink-3">{yard.running ? 'testing… ~20 s' : 'Run test'}</button>}
        </div>
        <div className="rounded-lg bg-white/[0.03] px-2 py-1.5 ring-1 ring-inset ring-line">
          <div className="text-[11px] uppercase tracking-[0.06em] text-ink-3">Inside</div>
          {!inside ? <span className="text-ink-3">…</span>
            : inside.status !== 'computed' ? <span className="text-ink-3" title={inside.why}>no measured floor</span>
            : <div className="mt-0.5 truncate tabular-nums text-ink-2" title="Interior planner, scanned area">
                {Math.round(inside.positions ?? 0).toLocaleString()} pos · {isNum(inside.reachable_pct) ? `${Math.round(inside.reachable_pct)}% reach` : '? reach'}
              </div>}
        </div>
      </div>
      <div className="flex gap-1.5">
        <button onClick={() => { show(false); select(id) }} className="h-8 flex-1 rounded-lg bg-white/[0.06] text-[12px] font-medium hover:bg-white/10">Open</button>
        <button onClick={() => { show(false); openTest(id, 'yard') }} className="h-8 flex-1 rounded-lg bg-accent/15 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/25">Yard test</button>
      </div>
    </article>
  )
}

function List({ title, tone, items, empty }: { title: string; tone: 'good' | 'bad' | 'warn'; items: Point[]; empty?: string }) {
  const Icon = tone === 'good' ? Check : tone === 'bad' ? Minus : CircleHelp
  const cls = tone === 'good' ? 'text-good' : tone === 'bad' ? 'text-bad' : 'text-warn'
  return (
    <div>
      <div className={clsx('mb-1 text-[11px] font-semibold uppercase tracking-[0.08em]', cls)}>{title}</div>
      {items.length === 0 ? <div className="text-[12px] text-ink-3">{empty ?? '…'}</div> : (
        <ul className="space-y-1">
          {items.map((p) => (
            <li key={p.text + (p.detail ?? '')} className="flex items-start gap-1.5 text-[12.5px] leading-snug">
              <Icon size={13} className={clsx('mt-[3px] shrink-0', cls)} aria-hidden="true" />
              <span className="min-w-0">
                <span className="text-ink">{p.text}</span>
                {p.detail && <span className="ml-1 tabular-nums text-ink-3">{p.detail}</span>}
                {p.est && <span className="ml-1 text-[11px] text-amber-300/90">est.</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The middle: every criterion as a balance, A's value left, B's right; the winning side lights up. */
function Scale({ c, a, b }: { c: CompareResult | undefined; a: number; b: number }) {
  const pts = (id: number) => c?.candidates.find((x) => x.id === id)?.points ?? 0
  return (
    <div className="flex min-w-0 flex-col rounded-xl bg-white/[0.02] p-2.5 ring-1 ring-inset ring-line" data-testid="duel-scale">
      <div className="mb-2 flex items-center text-[12px]">
        <span className="font-bold text-ink">A <span className="font-semibold tabular-nums text-accent">{c ? pts(a) : ''}</span></span>
        <span className="mx-auto text-[11px] uppercase tracking-[0.08em] text-ink-3">criteria won (by weight)</span>
        <span className="font-bold text-ink"><span className="font-semibold tabular-nums text-accent">{c ? pts(b) : ''}</span> B</span>
      </div>
      {!c ? <div className="space-y-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-9 w-full" />)}</div> : (
        <ul className="space-y-1.5">
          {c.rows.map((r) => <ScaleRow key={r.key} r={r} a={a} b={b} />)}
        </ul>
      )}
      {c && <div className="mt-auto pt-2 text-[11px] leading-snug text-ink-3">Solid = CoStar · <span className="text-amber-300/90">est.</span> = from imagery or the scan · ? = no data, never counted as zero.</div>}
    </div>
  )
}

function ScaleRow({ r, a, b }: { r: CmpRow; a: number; b: number }) {
  const va = val(r, a), vb = val(r, b)
  const na = num(va), nb = num(vb)
  const max = Math.max(na ?? 0, nb ?? 0) || 1
  const side = (v: CmpValue | undefined, n: number | null, id: number, align: 'left' | 'right') => {
    const won = r.winner === id, unk = v?.kind === 'unknown'
    return (
      <div className={clsx('relative min-w-0 flex-1', align === 'left' ? 'text-right' : 'text-left')}>
        <div className={clsx('relative z-10 truncate px-1.5 text-[12.5px] tabular-nums', unk ? 'text-warn' : won ? 'font-semibold text-good' : 'text-ink-2',
          v?.kind === 'estimated' && 'italic')} title={v?.basis ?? v?.source ?? undefined}>
          {unk ? '?' : v?.display ?? '…'}{won && <Check size={11} className="ml-0.5 inline" aria-label="wins" />}
        </div>
        {n != null && !unk && (
          <div className={clsx('absolute inset-y-0 rounded', align === 'left' ? 'right-0' : 'left-0', won ? 'bg-good/20' : 'bg-white/[0.06]',
            v?.kind === 'estimated' && 'border border-dashed border-white/20 bg-transparent')}
            style={{ width: `${Math.max(6, (n / max) * 100)}%` }} aria-hidden="true" />
        )}
      </div>
    )
  }
  return (
    <li className="rounded-lg bg-white/[0.02] px-1 py-1">
      <div className="mb-0.5 flex items-center justify-center gap-1.5 text-[11.5px] text-ink-3">
        {r.level === 'D' && <span className="rounded bg-bad/15 px-1 text-[10.5px] font-semibold text-bad" title="Deal-breaker for this operation">must</span>}
        <span className="truncate" title={r.why}>{r.label}</span>
        {r.verdict === 'tie' && <span className="text-[11px]">· tie</span>}
        {r.verdict === 'unknown' && <AlertTriangle size={11} className="text-warn" aria-label="can't be decided" />}
      </div>
      <div className="flex items-stretch gap-1">
        {side(va, na, a, 'left')}
        <div className="w-px bg-line" aria-hidden="true" />
        {side(vb, nb, b, 'right')}
      </div>
    </li>
  )
}

const val = (r: CmpRow, id: number) => r.values.find((v) => v.id === id)
const num = (v: CmpValue | undefined) => (v && typeof v.value === 'number' && Number.isFinite(v.value) ? Math.abs(v.value) : isNum(v?.hi) ? v!.hi! : null)
