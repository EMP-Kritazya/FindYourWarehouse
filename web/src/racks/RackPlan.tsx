// Top-down plan of the scanned floor with the rack rows (SVG). Same pointer rules as the dock editor:
// a click only selects; a selected row moves only after a deliberate drag (6 px), across its aisle direction;
// nothing changes on mousedown. Edits are sent to the backend, which validates them (columns, walls, aisle width).
import { useMemo, useRef, useState } from 'react'
import type { InteriorObject, RackPlan as Plan } from './racksApi'

const PAD = 8
const ZONE_BG: Record<string, string> = { open: '#22c55e1f', shelving: '#f59e0b0d', selective: '#4fd1c50a', dense: '#a78bfa0d', cantilever: '#d977060f' }
const ZONE_LINE: Record<string, string> = { open: '#22c55e', shelving: '#f59e0b', selective: '#4fd1c5', dense: '#a78bfa', cantilever: '#d97706' }
const ZONE_TXT: Record<string, string> = { open: 'Open floor', shelving: 'Pick shelving', selective: 'Selective racks', dense: 'Double-deep racks', cantilever: 'Cantilever (lumber, pipe)' }
const DRAG_PX = 6

const TYPE_FILL: Record<string, [string, string]> = {          // [normal, selected]
  selective: ['#4fd1c599', '#4fd1c5'], dense: ['#a78bfa99', '#a78bfa'], shelving: ['#f59e0b88', '#f59e0b'], cantilever: ['#d9770699', '#d97706'],
}

export default function RackPlan({ plan, selected, onSelect, onMove, objects, onToggleObject, zones }: {
  plan: Plan | (Omit<Plan, 'rows'> & { rows: (Plan['rows'][number] & { type?: string; reachable?: boolean })[] })
  selected: string | null
  onSelect: (id: string | null) => void
  onMove: (id: string, dx: number) => void
  objects?: InteriorObject[]
  onToggleObject?: (id: string) => void
  zones?: { id: string; type: string; rect?: number[]; area_sqft: number }[]
}) {
  const W = plan.plan.width_m, H = plan.plan.depth_m
  const svgRef = useRef<SVGSVGElement>(null)
  const drag = useRef<{ id: string; x0: number; px0: number; started: boolean } | null>(null)
  const [dx, setDx] = useState(0)
  // generator frame: x right, y up (dock wall at y = 0 for perpendicular, x = 0 for parallel). SVG y is down.
  const sx = (x: number) => PAD + x
  const sy = (y: number) => PAD + (H - y)
  const view = `0 0 ${W + 2 * PAD} ${H + 2 * PAD}`
  const outline = useMemo(() => plan.plan.outline.map((ring) => ring.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')), [plan])

  const toM = (clientX: number) => {
    const el = svgRef.current
    if (!el) return 0
    const r = el.getBoundingClientRect()
    return (clientX - r.left) / r.width * (W + 2 * PAD)
  }
  const down = (e: React.PointerEvent, id: string) => {
    e.stopPropagation()
    if (selected !== id) { onSelect(id); return }                // first click: select only
    drag.current = { id, x0: toM(e.clientX), px0: e.clientX, started: false }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  const move = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    if (!d.started && Math.abs(e.clientX - d.px0) < DRAG_PX) return
    d.started = true
    setDx(Math.round((toM(e.clientX) - d.x0) / 0.25) * 0.25)
  }
  const up = () => {
    const d = drag.current
    drag.current = null
    if (d?.started && dx !== 0) onMove(d.id, dx)
    setDx(0)
  }

  return (
    <svg ref={svgRef} viewBox={view} className="w-full touch-none select-none rounded-xl bg-[#0d1117] ring-1 ring-inset ring-line"
      style={{ aspectRatio: `${W + 2 * PAD} / ${H + 2 * PAD}`, maxHeight: 460 }}
      onPointerMove={move} onPointerUp={up} onPointerLeave={up} onClick={() => !drag.current && onSelect(null)} data-testid="rack-plan">
      {outline.map((pts, i) => <polygon key={i} points={pts} fill="#1a2029" stroke="#3a4454" strokeWidth={0.35} />)}
      {zones?.map((z) => z.rect && (
        <g key={z.id}>
          <rect x={sx(z.rect[0])} y={sy(z.rect[3])} width={z.rect[1] - z.rect[0]} height={z.rect[3] - z.rect[2]}
            fill={ZONE_BG[z.type] ?? 'transparent'} stroke={ZONE_LINE[z.type] ?? '#64748b'} strokeWidth={0.3} strokeDasharray="1.2 0.8" />
          <text x={sx(z.rect[0]) + 1.5} y={sy(z.rect[3]) + 3.5} fontSize={2.6} fill={ZONE_LINE[z.type] ?? '#94a3b8'} fontWeight={600}>
            {ZONE_TXT[z.type] ?? z.type}{z.type === 'open' ? ` · ${z.area_sqft.toLocaleString()} SF` : ''}
          </text>
        </g>
      ))}
      {plan.plan.obstructions.map(([x0, x1, y0, y1], i) => (
        <rect key={i} x={sx(x0)} y={sy(y1)} width={x1 - x0} height={y1 - y0} fill="#2b3240" />
      ))}
      {plan.lanes.map((l, i) => (
        <rect key={i} x={sx(l.x0)} y={sy(l.y1)} width={l.x1 - l.x0} height={l.y1 - l.y0} fill="#f59e0b22" stroke="#f59e0b88" strokeWidth={0.2} strokeDasharray="0.8 0.6" />
      ))}
      {plan.rows.map((r) => {
        const sel = r.id === selected
        const off = sel && dx ? dx : 0
        const bad = !r.deleted && (!r.ok || (r as { reachable?: boolean }).reachable === false)
        const tf = TYPE_FILL[(r as { type?: string }).type ?? 'selective'] ?? TYPE_FILL.selective
        const x = plan.transposed ? r.x0 : r.x0
        return (
          <g key={r.id} onPointerDown={(e) => down(e, r.id)} onClick={(e) => e.stopPropagation()} style={{ cursor: sel ? 'ew-resize' : 'pointer' }}>
            <rect x={sx(x + off)} y={sy(r.y1)} width={r.x1 - r.x0} height={r.y1 - r.y0}
              fill={r.deleted ? 'transparent' : bad ? '#ef444466' : sel ? tf[1] : tf[0]}
              stroke={r.deleted ? '#4fd1c555' : bad ? '#ef4444' : sel ? '#e6fffb' : 'none'}
              strokeWidth={r.deleted || bad || sel ? 0.3 : 0} strokeDasharray={r.deleted ? '0.6 0.5' : undefined} />
            {/* a fat invisible hit area so thin rows are easy to click */}
            <rect x={sx(x + off) - 0.8} y={sy(r.y1)} width={r.x1 - r.x0 + 1.6} height={r.y1 - r.y0} fill="transparent" />
          </g>
        )
      })}
      {plan.plan.columns.map(([x, y], i) => <circle key={i} cx={sx(x)} cy={sy(y)} r={0.45} fill="#e5e7eb" />)}
      {/* today's scanned contents: click to keep (grey) or remove (red dashed) */}
      {objects?.map((o) => (
        <g key={o.id} onPointerDown={(e) => { e.stopPropagation(); onToggleObject?.(o.id) }} onClick={(e) => e.stopPropagation()} style={{ cursor: 'pointer' }}>
          <rect x={sx(o.x0)} y={sy(o.y1)} width={o.x1 - o.x0} height={o.y1 - o.y0}
            fill={o.kept ? '#9ca3af88' : 'transparent'} stroke={o.kept ? '#d1d5db' : '#ef4444'} strokeWidth={0.35}
            strokeDasharray={o.kept ? undefined : '0.8 0.5'} />
          <title>{`${o.kind.replace('_', ' ')} · ${o.area_sqft} SF · ${o.kept ? 'kept (click to remove)' : 'removed (click to keep)'}`}</title>
        </g>
      ))}
      {/* dock wall marker */}
      {plan.plan.dock_wall === 'bottom'
        ? <line x1={sx(0)} x2={sx(W)} y1={sy(0) + 1.2} y2={sy(0) + 1.2} stroke="#4fd1c5" strokeWidth={0.6} strokeDasharray="2 1" />
        : <line x1={sx(0) - 1.2} x2={sx(0) - 1.2} y1={sy(0)} y2={sy(H)} stroke="#4fd1c5" strokeWidth={0.6} strokeDasharray="2 1" />}
    </svg>
  )
}
