// Flood exposure: measured Vexcel elevation + the FEMA zone. A slim line on the property card, a panel beside it
// (indicator, the rule that fired, facts with their source, a terrain cross-section) and, while the panel is open,
// the low spots and the nearest bayou drawn on the map. Never "safe" on missing data; never an insurance call.
import { useCallback, useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { Droplets, Loader2, X } from 'lucide-react'
import { PathLayer, PolygonLayer } from '@deck.gl/layers'
import { useStore } from '../lib/store'
import { SrcTag } from '../racks/PlanProps'
import type { DimSource } from '../racks/racksApi'

interface Fact { value: unknown; unit: string; source: DimSource; basis: string }
export interface Flood {
  status: 'computed' | 'not_run'; property_id: number; address?: string; note?: string
  indicator?: { level: 'higher' | 'moderate' | 'lower' | 'unclear'; partial: boolean; why: string[]; label: string }
  fema?: { label?: string; zone?: string; sfha?: boolean; level?: string; source_name?: string; error?: string }
  facts?: Record<string, Fact>; missing?: string[]
  waterway?: { name: string; distance_mi: number } | null
  layers?: { ponding: { area_m2: number; max_depth_m: number; polygon: [number, number][] }[]; waterway: [number, number][] | null }
  profile?: { points: [number, number][]; building: [number, number] | null; pad_ft: number; channel: string; note: string }
}

const cache = new Map<number, Flood | null>()
export function useFlood(id: number | null) {
  const [f, setF] = useState<Flood | null | undefined>(id == null ? null : cache.get(id))
  const [running, setRunning] = useState(false)
  const load = useCallback(async (run: boolean) => {
    if (id == null) return
    if (run) setRunning(true)
    try {
      const r = await fetch(`/api/flood/${id}${run ? '?run=true' : ''}`)
      const d = r.ok ? ((await r.json()) as Flood) : null
      cache.set(id, d); setF(d)
    } catch { cache.set(id, null); setF(null) } finally { setRunning(false) }
  }, [id])
  useEffect(() => { if (id == null) setF(null); else if (cache.has(id)) setF(cache.get(id)); else { setF(undefined); load(false) } }, [id, load])
  return { f, running, run: () => load(true) }
}

const LEVEL: Record<string, { cls: string; dot: string }> = {
  higher: { cls: 'bg-bad/12 text-bad ring-bad/35', dot: 'bg-bad' },
  moderate: { cls: 'bg-warn/12 text-warn ring-warn/35', dot: 'bg-warn' },
  lower: { cls: 'bg-good/12 text-good ring-good/35', dot: 'bg-good' },
  unclear: { cls: 'bg-white/[0.05] text-ink-3 ring-line', dot: 'bg-ink-3' },
}
const ft = (v: unknown) => (typeof v === 'number' ? `${v > 0 ? '' : ''}${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} ft` : '?')

/** The slim line under the property card's tiles. */
export function FloodLineHud({ id }: { id: number }) {
  const { f, running, run } = useFlood(id)
  const open = useStore((s) => s.floodOpen === id)
  if (f === null || f === undefined) return null
  const computed = f.status === 'computed' && f.indicator
  const click = () => {
    if (!computed) { run(); return }
    useStore.setState({ floodOpen: open ? null : id, costOpen: null })
  }
  return (
    <button onClick={click} disabled={running} data-testid="hud-flood"
      className={clsx('flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] ring-1 ring-inset transition disabled:opacity-70',
        open ? 'bg-sky-400/10 ring-sky-300/40' : 'bg-white/[0.03] ring-line hover:bg-white/[0.06]')}>
      <Droplets size={13} className="shrink-0 text-sky-300" />
      <span className="text-ink-3">Water</span>
      {computed ? (
        <>
          <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full', LEVEL[f.indicator!.level].dot)} />
          <span className="font-semibold text-ink">{f.indicator!.label}</span>
          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-3">· {f.fema?.label ?? 'FEMA zone ?'}{f.indicator!.partial ? ' · partial' : ''}</span>
        </>
      ) : <span className="min-w-0 flex-1 text-ink-2">{running ? <span className="flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" /> checking elevation + FEMA (~30 s)</span> : 'Check flood exposure'}</span>}
      <span className="text-ink-3">›</span>
    </button>
  )
}

export default function FloodPanel() {
  const id = useStore((s) => s.floodOpen)
  const selectedId = useStore((s) => s.selectedId)
  const { f } = useFlood(id)
  useEffect(() => { if (id != null && selectedId !== id) useStore.setState({ floodOpen: null }) }, [id, selectedId])
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape' && id != null) useStore.setState({ floodOpen: null }) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [id])
  if (id == null || !f || f.status !== 'computed' || !f.indicator) return null
  const fx = f.facts ?? {}
  const pond = fx.ponding?.value as { spots: number; area_sqft: number; max_depth_ft: number } | undefined
  const rows: [string, string, Fact | undefined][] = [
    ['Pad above the surroundings', ft(fx.pad_ft?.value), fx.pad_ft],
    [f.waterway ? `Above ${f.waterway.name} (${f.waterway.distance_mi} mi)` : 'Above the nearest waterway', fx.hand_ft?.value != null ? ft(fx.hand_ft.value) : 'unknown', fx.hand_ft],
    ['Lowest ground at the walls, vs pad', typeof fx.lowest_ft?.value === 'number' ? `−${ft(Math.abs(fx.lowest_ft.value as number))}` : '?', fx.lowest_ft],
    [pond?.spots ? `Low spots nearby (${pond.spots}), deepest` : 'Low spots nearby', pond ? (pond.spots ? ft(pond.max_depth_ft) : 'none') : '?', fx.ponding],
    ['Dock truck wells below the yard', fx.dock_wells_ft?.value != null ? ft(fx.dock_wells_ft.value) : 'not resolved', fx.dock_wells_ft],
  ]
  return (
    <aside className="glass drawer-in absolute right-[436px] top-4 z-30 flex max-h-[calc(100%-250px)] w-[340px] flex-col overflow-hidden rounded-2xl" aria-label="Flood exposure" data-testid="flood-panel">
      <header className="flex items-start gap-2 border-b border-line px-4 py-2.5">
        <Droplets size={15} className="mt-0.5 shrink-0 text-sky-300" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold">Flood exposure</h2>
          <div className="truncate text-[11px] text-ink-3">{f.address} · Vexcel elevation + FEMA map</div>
        </div>
        <button onClick={() => useStore.setState({ floodOpen: null })} aria-label="Close (Esc)" title="Close (Esc)" className="rounded p-1 text-ink-3 hover:bg-white/10 hover:text-ink"><X size={14} /></button>
      </header>
      <div className="scroll-thin space-y-3 overflow-y-auto px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={clsx('inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[13px] font-semibold ring-1 ring-inset', LEVEL[f.indicator.level].cls)} data-testid="flood-indicator">
            <span className={clsx('h-2 w-2 rounded-full', LEVEL[f.indicator.level].dot)} />{f.indicator.label}
          </span>
          {f.fema?.label && <span className={clsx('rounded-lg px-2 py-1 text-[12px] font-medium ring-1 ring-inset', f.fema.sfha ? 'bg-bad/10 text-bad ring-bad/30' : f.fema.level === '0.2pct' ? 'bg-warn/10 text-warn ring-warn/30' : 'bg-white/[0.04] text-ink-2 ring-line')}
            title={f.fema.source_name}>FEMA: {f.fema.label}</span>}
          {f.indicator.partial && <span className="text-[11px] text-ink-3" title={(f.missing ?? []).join(', ')}>partial: {(f.missing ?? []).join(', ')} unknown</span>}
        </div>
        {f.indicator.why.length > 0 && (
          <ul className="space-y-0.5 text-[12px] leading-snug text-ink-2">{f.indicator.why.map((w) => <li key={w}>· {w}</li>)}</ul>
        )}
        {f.profile && f.profile.points.length > 5 && <Profile p={f.profile} />}
        <ul className="divide-y divide-line/60 rounded-xl bg-white/[0.02] px-3 ring-1 ring-inset ring-line" data-testid="flood-facts">
          {rows.map(([label, value, fact]) => (
            <li key={label} className="flex items-center gap-2 py-1.5 text-[12px]" title={fact?.basis}>
              <span className="min-w-0 flex-1 text-ink-3">{label}</span>
              <span className="text-right font-semibold tabular-nums text-ink">{value}</span>
              <SrcTag s={fact?.source ?? 'unknown'} />
            </li>
          ))}
        </ul>
        {pond && pond.spots > 0 && <div className="flex items-center gap-1.5 text-[11px] text-ink-3"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-sky-400/60" /> Low spots are shaded on the map; the bayou is the blue line.</div>}
        <p className="text-[11px] leading-snug text-ink-3">{f.note} Heights are relative (pad vs surroundings, pad vs channel) from the Vexcel ground model; storm drains are not modelled.</p>
      </div>
    </aside>
  )
}

/** Terrain from the channel through the building (heights above the channel's low line). */
function Profile({ p }: { p: NonNullable<Flood['profile']> }) {
  const W = 300, H = 110, PADL = 6, PADB = 16
  const xs = p.points.map((q) => q[0]), ys = p.points.map((q) => q[1])
  const x0 = Math.min(...xs), x1 = Math.max(...xs)
  const yMax = Math.max(4, ...ys) * 1.25, yMin = Math.min(0, ...ys) - 1
  const X = (x: number) => PADL + ((x - x0) / (x1 - x0)) * (W - 2 * PADL)
  const Y = (y: number) => H - PADB - ((y - yMin) / (yMax - yMin)) * (H - PADB - 8)
  const line = p.points.map((q) => `${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(' ')
  const area = `${X(x0)},${H - PADB} ${line} ${X(x1)},${H - PADB}`
  const b = p.building
  return (
    <figure className="rounded-xl bg-[#0d1117] p-2 ring-1 ring-inset ring-line" data-testid="flood-profile">
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" aria-label={`Terrain cross-section from ${p.channel} through the building`}>
        <polygon points={area} fill="#334155" />
        <polyline points={line} fill="none" stroke="#94a3b8" strokeWidth={1.2} />
        <line x1={X(x0)} x2={X(x1)} y1={Y(0)} y2={Y(0)} stroke="#38bdf8" strokeWidth={1} strokeDasharray="3 2" />
        <text x={X(0) + 3} y={Y(0) - 3} fontSize={8} fill="#38bdf8">{p.channel} (low line)</text>
        {b && (
          <g>
            <rect x={X(b[0])} y={Y(p.pad_ft) - 22} width={Math.max(4, X(b[1]) - X(b[0]))} height={22} fill="#4fd1c533" stroke="#4fd1c5" strokeWidth={0.8} />
            <text x={(X(b[0]) + X(b[1])) / 2} y={Y(p.pad_ft) - 25} fontSize={8} textAnchor="middle" fill="#e6fffb">building · pad {p.pad_ft} ft above</text>
          </g>
        )}
        <text x={PADL} y={H - 4} fontSize={7.5} fill="#64748b">{Math.round(x1 - x0).toLocaleString()} m section · height exaggerated</text>
      </svg>
      <figcaption className="mt-1 text-[10.5px] leading-snug text-ink-3">{p.note}</figcaption>
    </figure>
  )
}

/** Map layers while the panel is open: low spots (blue) and the nearest waterway. Nothing otherwise. */
export function useFloodLayers() {
  const id = useStore((s) => s.floodOpen)
  const { f } = useFlood(id)
  return useMemo(() => {
    if (id == null || !f || f.status !== 'computed' || !f.layers) return []
    const L = f.layers
    return [
      new PolygonLayer({
        id: 'flood-ponding', data: L.ponding, getPolygon: (d: { polygon: [number, number][] }) => d.polygon,
        getFillColor: [56, 189, 248, 105], getLineColor: [125, 211, 252, 230], getLineWidth: 1.5, lineWidthUnits: 'pixels', stroked: true,
        parameters: { depthCompare: 'always', depthWriteEnabled: false },
      }),
      L.waterway ? new PathLayer({
        id: 'flood-waterway', data: [{ path: L.waterway }], getPath: (d: { path: [number, number][] }) => d.path,
        getColor: [56, 189, 248, 230], getWidth: 4, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        parameters: { depthCompare: 'always', depthWriteEnabled: false },
      }) : null,
    ]
  }, [id, f])
}
