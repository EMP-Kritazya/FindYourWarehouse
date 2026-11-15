// Top-down plan of the scanned floor with the rack rows (SVG). Same pointer rules as the dock editor:
// a click only selects; a selected row moves only after a deliberate drag (6 px), across its aisle direction;
// nothing changes on mousedown. Edits are sent to the backend, which validates them (columns, walls, aisle width).
import { useMemo, useRef, useState } from 'react'
import type { InteriorDims, InteriorObject, RackPlan as Plan } from './racksApi'
import { M_TO_FT } from './PlanProps'

const PAD = 8
const ZONE_BG: Record<string, string> = { open: '#22c55e1f', shelving: '#f59e0b0d', selective: '#4fd1c50a', dense: '#a78bfa0d', cantilever: '#d977060f' }
const ZONE_LINE: Record<string, string> = { open: '#22c55e', shelving: '#f59e0b', selective: '#4fd1c5', dense: '#a78bfa', cantilever: '#d97706' }
const ZONE_TXT: Record<string, string> = { open: 'Open floor', shelving: 'Pick shelving', selective: 'Selective racks', dense: 'Double-deep racks', cantilever: 'Cantilever (lumber, pipe)' }
const DRAG_PX = 6

const TYPE_FILL: Record<string, [string, string]> = {          // [normal, selected]
  selective: ['#4fd1c599', '#4fd1c5'], dense: ['#a78bfa99', '#a78bfa'], shelving: ['#f59e0b88', '#f59e0b'], cantilever: ['#d9770699', '#d97706'],
}

export default function RackPlan({ plan, selected, onSelect, onMove, objects, onToggleObject, zones, dims, machinePos, loadIn }: {
  plan: Plan | (Omit<Plan, 'rows'> & { rows: (Plan['rows'][number] & { type?: string; reachable?: boolean })[] })
  selected: string | null
  onSelect: (id: string | null) => void
  onMove: (id: string, dx: number) => void
  objects?: InteriorObject[]
  onToggleObject?: (id: string) => void
  zones?: { id: string; type: string; rect?: number[]; area_sqft: number }[]
  /** interior planner: dimensions for annotations, the machine's spot, the load height (in) */
  dims?: InteriorDims; machinePos?: { x: number; y: number; heading_deg: number } | null; loadIn?: number
}) {
  const fs = Math.max(W0(plan), H0(plan)) / 46                    // annotation text size (m), readable at any scale
  const selectable = !!dims                                       // interior planner: everything is selectable
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
        <g key={z.id} onClick={(e) => { if (!selectable) return; e.stopPropagation(); onSelect(`zone:${z.id}`) }} style={selectable ? { cursor: 'pointer' } : undefined}>
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
        <rect key={i} x={sx(l.x0)} y={sy(l.y1)} width={l.x1 - l.x0} height={l.y1 - l.y0} fill={selected === `lane:${i}` ? '#f59e0b55' : '#f59e0b22'}
          stroke={selected === `lane:${i}` ? '#fde68a' : '#f59e0b88'} strokeWidth={0.2} strokeDasharray="0.8 0.6"
          onClick={(e) => { if (!selectable) return; e.stopPropagation(); onSelect(`lane:${i}`) }} style={selectable ? { cursor: 'pointer' } : undefined} />
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
      {plan.plan.columns.map(([x, y], i) => (
        <g key={i} onClick={(e) => { if (!selectable) return; e.stopPropagation(); onSelect(`col:${i}`) }} style={selectable ? { cursor: 'pointer' } : undefined}>
          <circle cx={sx(x)} cy={sy(y)} r={selected === `col:${i}` ? 0.8 : 0.45} fill={selected === `col:${i}` ? '#4fd1c5' : '#e5e7eb'} />
          {selectable && <circle cx={sx(x)} cy={sy(y)} r={1.4} fill="transparent" />}
        </g>
      ))}
      {/* today's scanned contents: click to keep (grey) or remove (red dashed) */}
      {objects?.map((o) => (
        <g key={o.id} onPointerDown={(e) => { e.stopPropagation(); if (selectable) onSelect(`obj:${o.id}`); else onToggleObject?.(o.id) }} onClick={(e) => e.stopPropagation()} style={{ cursor: 'pointer' }}>
          <rect x={sx(o.x0)} y={sy(o.y1)} width={o.x1 - o.x0} height={o.y1 - o.y0}
            fill={o.kept ? '#9ca3af88' : 'transparent'} stroke={selected === `obj:${o.id}` ? '#e6fffb' : o.kept ? '#d1d5db' : '#ef4444'} strokeWidth={selected === `obj:${o.id}` ? 0.5 : 0.35}
            strokeDasharray={o.kept ? undefined : '0.8 0.5'} />
          <title>{`${o.kind.replace('_', ' ')} · ${o.area_sqft} SF · ${o.kept ? 'kept' : 'removed'}${selectable ? ' · click for dimensions' : o.kept ? ' (click to remove)' : ' (click to keep)'}`}</title>
        </g>
      ))}
      {dims && machinePos && <Machine p={machinePos} L={(dims.machine.length.value ?? 7) / M_TO_FT} Wd={(dims.machine.width.value ?? 4) / M_TO_FT}
        sel={selected === 'machine'} sx={sx} sy={sy} onSelect={() => onSelect('machine')} />}
      {dims && selected && <Annotations sel={selected} plan={plan} objects={objects} zones={zones} dims={dims} machinePos={machinePos} loadIn={loadIn} sx={sx} sy={sy} fs={fs} />}
      {/* dock wall marker */}
      {plan.plan.dock_wall === 'bottom'
        ? <line x1={sx(0)} x2={sx(W)} y1={sy(0) + 1.2} y2={sy(0) + 1.2} stroke="#4fd1c5" strokeWidth={0.6} strokeDasharray="2 1" />
        : <line x1={sx(0) - 1.2} x2={sx(0) - 1.2} y1={sy(0)} y2={sy(H)} stroke="#4fd1c5" strokeWidth={0.6} strokeDasharray="2 1" />}
    </svg>
  )
}

const W0 = (p: { plan: { width_m: number } }) => p.plan.width_m
const H0 = (p: { plan: { depth_m: number } }) => p.plan.depth_m
const fmt = (m: number) => `${(m * M_TO_FT).toLocaleString(undefined, { maximumFractionDigits: 1, minimumFractionDigits: m * M_TO_FT < 10 ? 1 : 0 })} ft`

function Machine({ p, L, Wd, sel, sx, sy, onSelect }: { p: { x: number; y: number; heading_deg: number }; L: number; Wd: number; sel: boolean
  sx: (x: number) => number; sy: (y: number) => number; onSelect: () => void }) {
  // heading is in the plan frame (y up); SVG y is down, so the rotation flips sign
  return (
    <g transform={`translate(${sx(p.x)} ${sy(p.y)}) rotate(${-p.heading_deg})`} onPointerDown={(e) => { e.stopPropagation(); onSelect() }}
      onClick={(e) => e.stopPropagation()} style={{ cursor: 'pointer' }} data-testid="plan-machine">
      <rect x={-L / 2} y={-Wd / 2} width={L} height={Wd} rx={0.25} fill={sel ? '#fbbf24' : '#fbbf24cc'} stroke={sel ? '#fff7d6' : '#7c5a10'} strokeWidth={0.2} />
      <rect x={L / 2} y={-Wd / 2 + 0.15} width={L * 0.32} height={0.18} fill="#e5e7eb" />
      <rect x={L / 2} y={Wd / 2 - 0.33} width={L * 0.32} height={0.18} fill="#e5e7eb" />
      <title>Forklift (to scale): click for its dimensions</title>
    </g>
  )
}

/** Dimension lines for the selected item: along the bottom and the right side, with a height badge when known.
 *  Solid = measured; dashed amber with ≈ = assumed or calculated (never shown as a measurement). */
function Annotations({ sel, plan, objects, zones, dims, machinePos, loadIn, sx, sy, fs }: {
  sel: string; plan: Plan | (Omit<Plan, 'rows'> & { rows: (Plan['rows'][number] & { type?: string; levels?: number; level_ft?: number })[] })
  objects?: InteriorObject[]; zones?: { id: string; rect?: number[] }[]; dims: InteriorDims
  machinePos?: { x: number; y: number; heading_deg: number } | null; loadIn?: number
  sx: (x: number) => number; sy: (y: number) => number; fs: number
}) {
  let box: [number, number, number, number] | null = null, measured = false, h: string | null = null
  const id = sel.includes(':') ? sel.slice(sel.indexOf(':') + 1) : sel
  if (sel.startsWith('obj:')) {
    const o = objects?.find((x) => x.id === id)
    if (o) { box = [o.x0, o.x1, o.y0, o.y1]; measured = true; h = o.height === 'full' ? 'H full height' : 'H < 6.6 ft' }
  } else if (sel.startsWith('zone:')) {
    const z = zones?.find((x) => x.id === id)
    if (z?.rect) box = [z.rect[0], z.rect[1], z.rect[2], z.rect[3]]
  } else if (sel.startsWith('lane:')) {
    const l = plan.lanes[Number(id)]
    if (l) box = [l.x0, l.x1, l.y0, l.y1]
  } else if (sel === 'machine' && machinePos) {
    const L = (dims.machine.length.value ?? 7) / M_TO_FT, W = (dims.machine.width.value ?? 4) / M_TO_FT
    const horiz = Math.abs(Math.cos(machinePos.heading_deg * Math.PI / 180)) > 0.7
    const [a, b] = horiz ? [L, W] : [W, L]
    box = [machinePos.x - a / 2, machinePos.x + a / 2, machinePos.y - b / 2, machinePos.y + b / 2]
    h = `H ≈ ${dims.machine.height.value ?? '?'} ft`
  } else if (!sel.includes(':')) {
    const r = plan.rows.find((x) => x.id === sel) as (Plan['rows'][number] & { levels?: number; level_ft?: number; type?: string }) | undefined
    if (r) {
      box = [r.x0, r.x1, r.y0, r.y1]
      const lv = r.levels ?? plan.levels, pitch = r.level_ft ?? 5.5
      if ((r.type ?? 'selective') === 'selective' || r.type === 'dense') h = `H ≈ ${((lv - 1) * pitch + (loadIn ?? 54) / 12).toFixed(1)} ft · ${lv} levels`
    }
  }
  if (!box) return null
  const [x0, x1, y0, y1] = box
  const L = sx(x0), R = sx(x1), T = sy(y1), B = sy(y0)
  const col = measured ? '#e6fffb' : '#fbbf24'
  const dash = measured ? undefined : `${fs * 0.5} ${fs * 0.3}`
  const pre = measured ? '' : '≈ '
  const off = fs * 1.4, tick = fs * 0.5, sw = fs * 0.09
  const label = (x: number, y: number, t: string, rot = 0) => {
    const w = t.length * fs * 0.56 + fs * 0.6
    return (
      <g transform={`translate(${x} ${y}) rotate(${rot})`}>
        <rect x={-w / 2} y={-fs * 0.72} width={w} height={fs * 1.3} rx={fs * 0.25} fill="#0d1117" stroke={col} strokeWidth={sw * 0.6} strokeDasharray={dash} />
        <text x={0} y={fs * 0.3} fontSize={fs} textAnchor="middle" fill={col} fontWeight={600} style={{ fontVariantNumeric: 'tabular-nums' }}>{t}</text>
      </g>
    )
  }
  return (
    <g pointerEvents="none" data-testid="plan-dims">
      <rect x={L} y={T} width={R - L} height={B - T} fill="none" stroke={col} strokeWidth={sw * 1.4} strokeDasharray={dash} />
      {/* along x (bottom) */}
      <line x1={L} x2={R} y1={B + off} y2={B + off} stroke={col} strokeWidth={sw} strokeDasharray={dash} />
      <line x1={L} x2={L} y1={B + off - tick} y2={B + off + tick} stroke={col} strokeWidth={sw} />
      <line x1={R} x2={R} y1={B + off - tick} y2={B + off + tick} stroke={col} strokeWidth={sw} />
      {label((L + R) / 2, B + off, pre + fmt(x1 - x0))}
      {/* along y (right side) */}
      <line x1={R + off} x2={R + off} y1={T} y2={B} stroke={col} strokeWidth={sw} strokeDasharray={dash} />
      <line x1={R + off - tick} x2={R + off + tick} y1={T} y2={T} stroke={col} strokeWidth={sw} />
      <line x1={R + off - tick} x2={R + off + tick} y1={B} y2={B} stroke={col} strokeWidth={sw} />
      {label(R + off, (T + B) / 2, pre + fmt(y1 - y0), -90)}
      {h && label(L + (h.length * fs * 0.56) / 2 + fs * 0.3, T - off, h.startsWith('H <') || h.startsWith('H full') ? h : h)}
    </g>
  )
}
