// Results as a slim strip of compact cards along the bottom of the map (the map stays full-screen). Same data as the
// full list (ResultsRail): an active ranking, else ranked search matches, else every warehouse in the market.
// "List" opens the full ranked list when it's wanted.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { Box, GitCompareArrows, GripVertical, List as ListIcon, ListOrdered, X } from 'lucide-react'
import { useStore } from '../lib/store'
import type { Pin } from '../lib/api'
import { Thumb, fmtMetric } from './ResultsRail'
import HeroesMenu from './HeroesMenu'

const sf = (v: number | null | undefined) => (v == null ? '?' : v >= 1000 ? `${Math.round(v / 1000)}k SF` : `${v} SF`)

interface Item { pin: Pin; rank?: number; value?: string; tag?: string; est?: boolean; excluded?: boolean }

export default function ResultsCarousel() {
  const pins = useStore((s) => s.pins)
  const pinsById = useStore((s) => s.pinsById)
  const market = useStore((s) => s.market)
  const search = useStore((s) => s.search)
  const searching = useStore((s) => s.searching)
  const ranking = useStore((s) => s.ranking)
  const clearRanking = useStore((s) => s.clearRanking)
  const selectedId = useStore((s) => s.selectedId)
  const hoveredId = useStore((s) => s.hoveredId)
  const select = useStore((s) => s.select)
  const hover = useStore((s) => s.hover)
  const listOpen = useStore((s) => s.listOpen)
  const strip = useRef<HTMLDivElement>(null)
  const mover = useStripMove()
  const compareIds = useStore((s) => s.compareIds)
  const toggleCompare = useStore((s) => s.toggleCompare)
  const setCompare = useStore((s) => s.setCompare)
  const profile = useStore((s) => s.buyerProfile)
  const curated = useStore((s) => s.curated)

  const items: Item[] = useMemo(() => {
    if (curated) {
      return curated.ids.map((id, i) => {
        const p = pinsById.get(id)
        return p ? { pin: p, rank: i + 1, value: curated.values?.[String(id)] } : null
      }).filter(Boolean) as Item[]
    }
    if (ranking?.data) {
      return ranking.data.rows.map((r, i) => {
        const p = pinsById.get(r.id)
        return p ? { pin: p, rank: i + 1, value: fmtMetric(r.value, r.unit ?? ranking.data!.metric.unit), tag: r.tag, est: r.est } : null
      }).filter(Boolean).slice(0, 10) as Item[]
    }
    if (search?.mode === 'ranked') {
      return search.results.map((r) => ({ pin: pinsById.get(r.id)!, rank: r.rank })).filter((x) => x.pin && x.pin.market === market)
        .slice(0, 10).map((x, i) => ({ ...x, rank: i + 1 }))
    }
    const order = (p: Pin) => (p.availability === 'available' ? 0 : p.availability === 'off_market_listing' ? 2 : 1) - (p.inspectable ? 0.5 : 0)
    return pins.filter((p) => p.market === market).sort((a, b) => order(a) - order(b) || (b.rba_sqft ?? 0) - (a.rba_sqft ?? 0)).map((p) => ({ pin: p }))
  }, [pins, pinsById, market, search, ranking, curated])

  // the analyst reads this order: "compare the first and second" = items[0], items[1]
  const order = items.slice(0, 10).map((x) => x.pin.id).join(',')
  useEffect(() => { useStore.setState({ visibleIds: order ? order.split(',').map(Number) : [] }) }, [order])

  // keep the selected building in view
  useEffect(() => {
    if (selectedId == null) return
    strip.current?.querySelector(`[data-id="${selectedId}"]`)?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [selectedId])

  const ranked = !!curated || !!ranking || search?.mode === 'ranked'
  const title = curated ? curated.title : ranking ? `Top ${items.length} by ${ranking.data?.metric.label ?? ranking.metric.replace(/_/g, ' ')}`
    : search?.mode === 'ranked' ? (profile ? `Top ${items.length} for you` : `Top ${items.length} matches`) : 'Warehouses'
  const pchips = profileChips(profile)
  const right = selectedId != null ? 'right-[420px]' : 'right-4'
  if (listOpen) return null
  return (
    <section ref={mover.box} className={clsx('glass absolute bottom-4 left-4 z-20 flex flex-col overflow-hidden rounded-2xl transition-[right] duration-300', right)}
      style={{ transform: 'translate(var(--strip-dx, 0px), var(--strip-dy, 0px))' }} data-testid="results-carousel">
      <header className="flex items-center gap-2 pl-1.5 pr-3.5 pt-2.5">
        <button type="button" {...mover.handle} data-testid="strip-grip"
          title="Drag to move · double-click to reset" aria-label="Move the results strip (drag, or arrow keys; Home resets)"
          className="flex h-7 w-5 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-ink-3 hover:bg-white/5 hover:text-ink active:cursor-grabbing">
          <GripVertical size={14} aria-hidden="true" />
        </button>
        {ranking ? <ListOrdered size={14} className="text-accent" /> : null}
        <h2 className="text-[13px] font-semibold text-ink">{title} <span className="font-normal text-ink-3">in {market}{ranked ? '' : ` · ${items.length}`}</span></h2>
        {pchips.length > 0 && (
          <span className="hidden min-w-0 items-center gap-1 truncate md:flex" title="What you told the analyst" data-testid="profile-chips">
            {pchips.map((c) => <span key={c} className="truncate rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent ring-1 ring-inset ring-accent/25">{c}</span>)}
          </span>
        )}
        {(ranking || curated) && <button onClick={clearRanking} title="Back to all warehouses" aria-label="Back to all warehouses" className="rounded-md p-1 text-ink-3 hover:bg-white/5 hover:text-ink"><X size={13} /></button>}
        {searching && <span className="text-[12px] text-ink-3">searching…</span>}
        <span className="ml-auto flex items-center gap-1.5">
          {compareIds.length >= 2 && (
            <button onClick={() => setCompare(compareIds)} data-testid="compare-picked"
              className="flex h-8 items-center gap-1.5 rounded-lg bg-accent/15 px-2.5 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/25">
              <GitCompareArrows size={13} /> Compare {compareIds.length}
            </button>
          )}
          <HeroesMenu />
          <button onClick={() => useStore.setState({ listOpen: true })} data-testid="open-list"
            className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] text-ink-2 ring-1 ring-inset ring-line hover:bg-white/5 hover:text-ink">
            <ListIcon size={13} /> Full list
          </button>
        </span>
      </header>
      <div ref={strip} className={clsx('scroll-thin flex snap-x gap-2 overflow-x-auto px-3 pb-3 pt-2 transition-opacity', searching && 'opacity-50')}>
        {items.length === 0 && <div className="py-6 text-[13px] text-ink-3">No warehouse in {market} matches every must-have. Loosen a requirement, or open the full list to see why.</div>}
        {items.map(({ pin, rank, value, tag, est }) => {
          const sel = pin.id === selectedId, hov = pin.id === hoveredId
          return (
            <div key={pin.id} data-id={pin.id} className="group relative flex-none snap-start">
            <button onClick={() => select(pin.id)} onMouseEnter={() => hover(pin.id)} onMouseLeave={() => hover(null)}
              className={clsx('relative flex w-[236px] flex-none snap-start items-center gap-2.5 rounded-xl p-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                sel ? 'bg-accent/12 ring-1 ring-inset ring-accent/50' : hov ? 'bg-white/[0.06]' : 'bg-white/[0.025] hover:bg-white/[0.05]')}>
              <div className="relative flex-none">
                <Thumb id={pin.id} photo={pin.photo} />
                {rank != null && (
                  <span className={clsx('absolute -left-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] font-semibold tabular-nums',
                    rank <= 3 ? 'bg-accent text-bg' : 'bg-panel-3 text-ink ring-1 ring-line-2')}>{rank}</span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1 truncate text-[13px] font-medium text-ink">
                  <span className="truncate">{pin.address}</span>
                  {pin.inspectable && <Box size={11} className="flex-none text-violet-300" aria-label="3D scan" />}
                </div>
                {value ? (
                  <div className="mt-0.5 flex items-center gap-1.5 text-[12px]">
                    <span className={clsx('font-semibold tabular-nums', est ? 'text-ink-2' : 'text-accent')}>{value}</span>
                    {tag && <span className={clsx('text-[11px]', est ? 'text-amber-300/90' : 'text-ink-3')}>{tag}</span>}
                  </div>
                ) : (
                  <div className="mt-0.5 text-[12px] tabular-nums text-ink-2">{sf(pin.rba_sqft)} · {pin.clear_height_ft != null ? `${pin.clear_height_ft} ft clear` : '? clear'}</div>
                )}
                <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-3">
                  <span className={clsx('h-1.5 w-1.5 rounded-full', pin.availability === 'available' ? 'bg-good' : pin.availability === 'off_market_listing' ? 'bg-ink-3' : 'bg-indigo-300')} />
                  {pin.availability === 'available' ? 'Available' : pin.availability === 'off_market_listing' ? 'Off market' : 'Not listed'}
                </div>
              </div>
            </button>
            <button onClick={() => toggleCompare(pin.id)} data-testid={`cmp-${pin.id}`}
              aria-pressed={compareIds.includes(pin.id)} aria-label={compareIds.includes(pin.id) ? 'Remove from comparison' : 'Add to comparison'}
              title={compareIds.includes(pin.id) ? 'Remove from comparison' : 'Add to comparison'}
              className={clsx('absolute right-1.5 top-1.5 flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-semibold ring-1 ring-inset transition',
                compareIds.includes(pin.id) ? 'bg-accent text-bg ring-accent' : 'bg-panel-2/90 text-ink-3 opacity-0 ring-line group-hover:opacity-100 focus-visible:opacity-100')}>
              <GitCompareArrows size={11} />{compareIds.includes(pin.id) ? String.fromCharCode(65 + compareIds.indexOf(pin.id)) : ''}
            </button>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/** short chips for what the buyer told the analyst (only what they said) */
function profileChips(p: Record<string, unknown> | null): string[] {
  if (!p) return []
  const out: string[] = []
  if (typeof p.business === 'string') out.push(p.business.replace(/_/g, ' '))
  if (typeof p.size_sqft === 'number') out.push(`~${Math.round(p.size_sqft / 1000)}k SF`)
  const TRUCK: Record<string, string> = { semi_53: '53-ft trailers', semi_48: '48-ft trailers', box_truck: 'box trucks', van: 'vans', sprinter_van: 'vans' }
  for (const t of (p.trucks as string[] | undefined) ?? []) out.push(TRUCK[t] ?? t.replace(/_/g, ' '))
  if (typeof p.market === 'string') out.push(p.market)
  if (typeof p.deal === 'string' && p.deal !== 'either') out.push(p.deal)
  for (const m of (p.must_haves as string[] | undefined) ?? []) out.push(m)
  return out.slice(0, 5)
}

/** Drag the strip anywhere (grip in its header); the map legend and dock summary follow via --strip-dx/--strip-dy.
 *  Keyboard: arrow keys (Shift = bigger), Home or double-click resets. Remembered per browser. */
function useStripMove() {
  const KEY = 'pi.strip.pos'
  const box = useRef<HTMLElement | null>(null)
  const [pos, setPos] = useState<{ dx: number; dy: number }>(() => {
    try { const v = JSON.parse(localStorage.getItem(KEY) || 'null'); return v && typeof v.dx === 'number' ? v : { dx: 0, dy: 0 } } catch { return { dx: 0, dy: 0 } }
  })
  const cur = useRef({ dx: 0, dy: 0 })
  const clamp = useCallback((p: { dx: number; dy: number }) => {
    const r = box.current?.getBoundingClientRect()
    if (!r) return p
    const left0 = r.left - cur.current.dx, right0 = r.right - cur.current.dx, top0 = r.top - cur.current.dy
    return { dx: Math.round(Math.max(8 - left0, Math.min(window.innerWidth - 8 - right0, p.dx))),   // fully on screen
             dy: Math.round(Math.min(0, Math.max(8 - top0, p.dy))) }
  }, [])
  useEffect(() => {
    cur.current = pos
    const el = document.documentElement
    el.style.setProperty('--strip-dx', `${pos.dx}px`); el.style.setProperty('--strip-dy', `${pos.dy}px`)
    try { localStorage.setItem(KEY, JSON.stringify(pos)) } catch { /* private mode */ }
  }, [pos])
  useEffect(() => {
    const on = () => setPos((p) => clamp(p))
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [clamp])
  const drag = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null)
  const handle = {
    onPointerDown: (e: React.PointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, dx: pos.dx, dy: pos.dy }; (e.currentTarget as Element).setPointerCapture(e.pointerId); e.preventDefault() },
    onPointerMove: (e: React.PointerEvent) => { const d = drag.current; if (d) setPos(clamp({ dx: d.dx + e.clientX - d.x, dy: d.dy + e.clientY - d.y })) },
    onPointerUp: () => { drag.current = null },
    onPointerCancel: () => { drag.current = null },
    onDoubleClick: () => setPos({ dx: 0, dy: 0 }),
    onKeyDown: (e: React.KeyboardEvent) => {
      const st = e.shiftKey ? 80 : 20
      const m: Record<string, [number, number]> = { ArrowLeft: [-st, 0], ArrowRight: [st, 0], ArrowUp: [0, -st], ArrowDown: [0, st] }
      if (e.key === 'Home') { e.preventDefault(); setPos({ dx: 0, dy: 0 }); return }
      const v = m[e.key]; if (!v) return
      e.preventDefault(); setPos((p) => clamp({ dx: p.dx + v[0], dy: p.dy + v[1] }))
    },
  }
  return { box, handle }
}
