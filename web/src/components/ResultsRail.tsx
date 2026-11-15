import { useMemo, useState } from 'react'
import clsx from 'clsx'
import { Box, ChevronDown, Warehouse, ListOrdered, X, Info } from 'lucide-react'
import { useStore } from '../lib/store'
import type { Pin, Result } from '../lib/api'
import { AVAIL_LABEL, fmtFt, fmtKSF, prettyWhy } from '../lib/format'
import { ScoreBand, Skeleton } from './ui'
import HeroesMenu from './HeroesMenu'
import ShortlistTray from './ShortlistTray'
import { fmtInt, isNum } from '../lib/format'

function Thumb({ id, photo }: { id: number; photo: string | null }) {
  // Vexcel ortho first (consistent top-down look), CoStar photo second, icon last.
  const cands = [`/media/twins/exterior/${id}_ortho.jpg`, photo].filter(Boolean) as string[]
  const [i, setI] = useState(0)
  return i < cands.length ? (
    <img key={cands[i]} src={cands[i]} alt="" loading="lazy" onError={() => setI(i + 1)}
      className="h-12 w-12 shrink-0 rounded-lg object-cover ring-1 ring-white/10" />
  ) : (
    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-panel-3 text-ink-3 ring-1 ring-white/5">
      <Warehouse size={18} />
    </div>
  )
}

function Counts({ c }: { c: Result['counts'] }) {
  const items: [string, number, string][] = [['✓', c.pass, 'text-good'], ['~', c.near, 'text-warn'], ['✗', c.fail, 'text-bad'], ['?', c.unknown, 'text-ink-3']]
  return (
    <span className="flex items-center gap-2 text-[11px] tabular-nums" title="requirements met / close / not met / unknown">
      {items.map(([s, n, cls]) => (
        <span key={s} className={clsx(n ? cls : 'text-ink-3/40')}>{s} {n}</span>
      ))}
    </span>
  )
}

function Row({ pin, result, metric }: { pin: Pin; result?: Result; metric?: { rank: number; value: string; date?: string; tag?: string; est?: boolean } }) {
  const selected = useStore((s) => s.selectedId === pin.id)
  const hovered = useStore((s) => s.hoveredId === pin.id)
  const select = useStore((s) => s.select)
  const hover = useStore((s) => s.hover)
  const unverified = result?.unverified_dealbreakers ?? []
  return (
    <button onClick={() => select(pin.id)} onMouseEnter={() => hover(pin.id)} onMouseLeave={() => hover(null)}
      className={clsx('group flex w-full gap-3 rounded-xl p-2.5 text-left transition',
        selected ? 'bg-accent/10 ring-1 ring-inset ring-accent/40' : hovered ? 'bg-white/[0.05]' : 'hover:bg-white/[0.04]')}>
      <div className="relative">
        <Thumb id={pin.id} photo={pin.photo} />
        {metric && (
          <span className={clsx('absolute -left-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] font-semibold tabular-nums',
            metric.rank <= 5 ? 'bg-accent text-bg' : 'bg-panel-3 text-ink ring-1 ring-line-2')}>{metric.rank}</span>
        )}
        {!metric && result && result.mode === 'ranked' && (
          <span className={clsx('absolute -left-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] font-semibold tabular-nums',
            result.rank <= 5 ? 'bg-accent text-bg' : 'bg-panel-3 text-ink ring-1 ring-line-2')}>
            {result.rank}
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[13px] font-medium text-ink">{pin.address}</span>
          {pin.inspectable && <span title="3D interior scan available"><Box size={13} className="shrink-0 text-ink-2" /></span>}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-ink-2">
          <span className="tabular-nums">{fmtKSF(pin.rba_sqft)}</span>
          <span className="text-ink-3">·</span>
          <span className="tabular-nums">{fmtFt(pin.clear_height_ft)} clear</span>
          <span className="text-ink-3">·</span>
          <span className={pin.availability === 'available' ? 'text-accent' : 'text-ink-3'}>{AVAIL_LABEL[pin.availability] ?? '?'}</span>
        </div>
        {metric && (
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className={clsx('text-[15px] font-semibold tabular-nums', metric.est ? 'text-ink-2' : 'text-accent')}>{metric.value}</span>
            {metric.tag ? <span className={clsx('text-[10px]', metric.est ? 'rounded border border-dashed border-amber-300/50 px-1 text-amber-300/90' : 'text-ink-3')}>{metric.tag}</span>
              : metric.date && <span className="text-[10px] text-amber-300/80">Vexcel {metric.date.slice(0, 7)}</span>}
          </div>
        )}
        {!metric && result && result.mode === 'ranked' && (
          <div className="mt-2 flex items-center gap-3">
            {result.counts.fail + result.counts.near + result.counts.unknown === 0 ? (
              <span className="flex items-center gap-1.5 text-[12px] font-medium text-good">
                <span className="flex h-4 w-4 items-center justify-center rounded-full bg-good/15 text-[10px]">✓</span>
                Meets all {result.counts.pass}
              </span>
            ) : (
              <>
                <span className="w-7 text-[12px] font-semibold tabular-nums text-ink" title="Fit score (0–100)">{result.score}</span>
                <div className="flex-1">
                  {result.band[1] > result.band[0] ? <ScoreBand score={result.score} band={result.band} compact />
                    : <div className="h-1.5" />}
                </div>
                <Counts c={result.counts} />
              </>
            )}
          </div>
        )}
        {unverified.length > 0 && (
          <div className="mt-1 truncate text-[11px] text-ink-3" title={`Must-haves not yet verified: ${unverified.join(', ')}`}>
            Unverified: {unverified.join(', ')}
          </div>
        )}
      </div>
    </button>
  )
}

export default function ResultsRail() {
  const pins = useStore((s) => s.pins)
  const pinsById = useStore((s) => s.pinsById)
  const pinsError = useStore((s) => s.pinsError)
  const market = useStore((s) => s.market)
  const search = useStore((s) => s.search)
  const searching = useStore((s) => s.searching)
  const [showExcluded, setShowExcluded] = useState(false)
  const ranking = useStore((s) => s.ranking)
  const clearRanking = useStore((s) => s.clearRanking)

  const browse = useMemo(() => {
    const rank = (p: Pin) => (p.availability === 'available' ? 0 : p.availability === 'off_market_listing' ? 2 : 1) - (p.inspectable ? 0.5 : 0)
    return pins.filter((p) => p.market === market).sort((a, b) => rank(a) - rank(b) || (b.rba_sqft ?? 0) - (a.rba_sqft ?? 0))
  }, [pins, market])

  const ranked = search?.mode === 'ranked'
  const results = ranked ? search!.results.filter((r) => pinsById.get(r.id)?.market === market) : []
  const excluded = ranked ? search!.excluded.filter((e) => pinsById.get(e.id)?.market === market) : []
  const loading = !pins.length && !pinsError

  return (
    <aside className="glass absolute bottom-4 left-4 top-4 z-20 flex w-[344px] flex-col overflow-hidden rounded-2xl">
      <header className="border-b border-line px-4 pb-3 pt-4">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/15 text-accent"><Warehouse size={16} /></div>
          <span className="text-[15px] font-semibold tracking-tight">ProInspect</span>
          <span className="ml-auto"><HeroesMenu /></span>
        </div>
        <div className="mt-3 flex items-baseline justify-between">
          <h2 className="text-[13px] font-medium text-ink">{ranking ? 'Ranked' : ranked ? 'Best matches' : 'Warehouses'} <span className="text-ink-3">in {market}</span></h2>
          <span className="text-[12px] tabular-nums text-ink-3">
            {loading ? '' : ranking ? `${ranking.data?.rows.length ?? ''}` : ranked ? `${results.length} of ${results.length + excluded.length}` : `${browse.length}`}
          </span>
        </div>
      </header>
      {ranking ? <RankedList /> : <>

      <div className={clsx('scroll-thin flex-1 overflow-y-auto p-2 transition-opacity', searching && 'opacity-50')}>
        {loading && Array.from({ length: 7 }, (_, i) => (
          <div key={i} className="flex gap-3 p-2.5"><Skeleton className="h-12 w-12" /><div className="flex-1 space-y-2 pt-1"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/2" /></div></div>
        ))}
        {pinsError && <div className="m-2 rounded-lg bg-bad/10 p-3 text-[12px] text-bad">Warehouses could not be loaded. Is the API running?</div>}
        {!ranked && browse.map((p) => <Row key={p.id} pin={p} />)}
        {ranked && results.map((r) => { const p = pinsById.get(r.id); return p ? <Row key={r.id} pin={p} result={r} /> : null })}
        {ranked && results.length === 0 && (
          <div className="m-2 rounded-xl bg-white/[0.03] p-4 text-[13px] text-ink-2">
            No warehouse in {market} meets every must-have. Loosen a requirement above, or see why below.
          </div>
        )}
        {ranked && excluded.length > 0 && (
          <div className="mt-2 border-t border-line pt-2">
            <button onClick={() => setShowExcluded(!showExcluded)} className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-[12px] text-ink-3 hover:bg-white/5 hover:text-ink-2">
              <span>{excluded.length} excluded</span>
              <ChevronDown size={14} className={clsx('transition', showExcluded && 'rotate-180')} />
            </button>
            {showExcluded && excluded.map((e) => (
              <ExcludedRow key={e.id} id={e.id} address={e.address} why={e.why} />
            ))}
          </div>
        )}
      </div>

      </>}
      <ShortlistTray />
      <footer className="flex items-center gap-3 border-t border-line px-4 py-2.5 text-[11px] text-ink-3">
        {ranked ? (
          <>
            <Legend color="bg-accent" label="Top 5" /><Legend color="bg-accent-2" label="Top 15" /><Legend color="bg-slate-600" label="Others" />
          </>
        ) : (
          <>
            <Legend color="bg-accent" label="Available" /><Legend color="bg-slate-400" label="Not listed" /><Legend color="bg-slate-600" label="Off market" />
          </>
        )}
        <span className="ml-auto flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full ring-[1.5px] ring-ink" />3D scan</span>
      </footer>
    </aside>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="flex items-center gap-1.5"><span className={clsx('h-2 w-2 rounded-full', color)} />{label}</span>
}

function ExcludedRow({ id, address, why }: { id: number; address: string; why: string[] }) {
  const select = useStore((s) => s.select)
  const hover = useStore((s) => s.hover)
  return (
    <button onClick={() => select(id)} onMouseEnter={() => hover(id)} onMouseLeave={() => hover(null)}
      title={`Excluded because: ${why.map(prettyWhy).join('; ')}`}
      className="block w-full rounded-lg px-2.5 py-1.5 text-left hover:bg-white/[0.04]">
      <div className="truncate text-[12px] text-ink-2">{address}</div>
      <div className="truncate text-[11px] text-bad/80">{prettyWhy(why[0] ?? '')}</div>
    </button>
  )
}

export function fmtMetric(v: unknown, unit?: string) {
  if (v == null) return '?'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (!isNum(v)) return String(v)
  switch (unit) {
    case 'SF': return `${fmtInt(v)} SF`
    case 'ft': return `${Math.round(v)} ft`
    case '%': return `${Math.round(v * 10) / 10}%`
    case '/5': return `${v}/5`
    case 'mi': return `${v.toFixed(1)} mi`
    case 'm': return v >= 1609 ? `${(v / 1609.34).toFixed(1)} mi` : `${Math.round(v * 3.281)} ft`
    case '': case undefined: return fmtInt(v)
    default: return `${fmtInt(v)} ${unit}`
  }
}

function RankedList() {
  const ranking = useStore((s) => s.ranking)!
  const clearRanking = useStore((s) => s.clearRanking)
  const rankBy = useStore((s) => s.rankBy)
  const pinsById = useStore((s) => s.pinsById)
  const def = ranking.data?.metric
  const rows = ranking.data?.rows ?? []
  const alt = def?.alt
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line bg-accent/[0.05] px-4 py-3">
        <div className="flex items-start gap-2">
          <ListOrdered size={15} className="mt-0.5 shrink-0 text-accent" />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-ink">By {def?.label ?? ranking.metric.replace(/_/g, ' ')}
              <span className="ml-1.5 text-[11px] font-normal text-ink-3">{def ? (def.better === 'max' ? 'highest first' : 'lowest first') : ''}</span></div>
            {def && <div className="mt-1 text-[11px] leading-snug text-ink-2">{def.definition}</div>}
            {alt && <button onClick={() => rankBy(alt, ranking.market as never)} className="mt-1.5 text-[11px] text-accent hover:underline">Rank by {alt.replace(/_/g, ' ').replace(/ sqft| est$/g, '')} instead</button>}
          </div>
          <button onClick={clearRanking} title="Back to all warehouses" className="rounded-md p-1 text-ink-3 hover:bg-white/5 hover:text-ink"><X size={14} /></button>
        </div>
        {ranking.data?.note && <div className="mt-2 flex gap-1.5 text-[10px] leading-snug text-warn/85"><Info size={11} className="mt-px shrink-0" />{ranking.data.note}</div>}
      </div>
      <div className={clsx('scroll-thin flex-1 overflow-y-auto p-2', ranking.loading && 'opacity-50')}>
        {ranking.loading && !rows.length && Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex gap-3 p-2.5"><Skeleton className="h-12 w-12" /><div className="flex-1 space-y-2 pt-1"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/2" /></div></div>
        ))}
        {ranking.error && <div className="m-2 rounded-lg bg-bad/10 p-3 text-[12px] text-bad">{ranking.error}</div>}
        {!ranking.loading && !ranking.error && rows.length === 0 && <div className="m-2 rounded-xl bg-white/[0.03] p-4 text-[12px] text-ink-2">No warehouse in {ranking.market} has this metric measured yet.</div>}
        {rows.map((r, i) => {
          const p = pinsById.get(r.id)
          return p ? <Row key={r.id} pin={p} metric={{ rank: i + 1, value: fmtMetric(r.value, r.unit ?? def?.unit), date: r.date, tag: r.tag, est: r.est }} /> : null
        })}
      </div>
    </div>
  )
}
