// Parametric side-profile glyphs for the equipment library (plan B4). Every glyph is built in FEET from the item's
// dims_ft, facing right (front at x = length), ground at y = 0, so the picture is the size the sim uses.
// `glyphGeometry(item)` is pure: it returns the primitives and their measured bounding box, and `checkGlyphs()`
// asserts bbox length/height == dims_ft for every library item (run in dev, and by the screenshot script).
import { useMemo } from 'react'

export interface EquipItem {
  name?: string
  category?: string
  dims_ft?: { length?: number; width?: number; height_mast_lowered?: number; height?: number }
  trailer_ft?: number
  lift_height_ft?: number
  turn_radius_ft?: number
  min_aisle_ft?: number
  capacity_lb?: number
}

export type GlyphKind =
  | 'counterbalance' | 'reach' | 'order_picker' | 'vna' | 'pallet_jack' | 'stacker' | 'tugger' | 'amr' | 'agv'
  | 'conveyor' | 'yard_truck' | 'semi' | 'box_truck' | 'van' | 'generic'

/** body = main mass, dark = tyres / chassis, line = mast, forks, guards, glass = windows, accent = load-handling */
type Tone = 'body' | 'dark' | 'line' | 'glass' | 'accent'
export type Prim =
  | { t: 'rect'; x: number; y: number; w: number; h: number; r?: number; tone: Tone }
  | { t: 'poly'; pts: [number, number][]; tone: Tone }
  | { t: 'wheel'; cx: number; r: number }
  | { t: 'seg'; a: [number, number]; b: [number, number]; tone: Tone; w?: number }

export interface GlyphGeometry {
  kind: GlyphKind
  /** the dims the glyph was built from (ft) */
  length_ft: number
  height_ft: number
  width_ft: number | null
  prims: Prim[]
  /** measured extent of the primitives (ft) */
  bbox: { x0: number; y0: number; x1: number; y1: number }
}

const KIND_BY_KEY: Record<string, GlyphKind> = {
  counterbalance_lpg: 'counterbalance', counterbalance_electric: 'counterbalance', reach_truck: 'reach', order_picker: 'order_picker',
  vna_turret: 'vna', pallet_jack_electric: 'pallet_jack', walkie_stacker: 'stacker', tugger_train: 'tugger',
  amr_goods_to_person: 'amr', autonomous_forklift: 'agv', conveyor_sorter: 'conveyor', yard_truck: 'yard_truck',
  semi_53: 'semi', box_truck_26: 'box_truck', last_mile_van: 'van',
}
const KIND_BY_CATEGORY: Record<string, GlyphKind> = {
  forklift: 'counterbalance', narrow_aisle: 'reach', horizontal: 'pallet_jack', light_lift: 'stacker', robot: 'amr',
  fixed: 'conveyor', yard: 'yard_truck', road_vehicle: 'box_truck',
}
export const glyphKind = (key: string | undefined, item: EquipItem): GlyphKind =>
  (key && KIND_BY_KEY[key]) || (item.category && KIND_BY_CATEGORY[item.category]) || 'generic'

export const itemHeight = (it: EquipItem) => it.dims_ft?.height_mast_lowered ?? it.dims_ft?.height ?? null

/** Pure geometry in feet. Every builder touches x = 0, x = L, y = 0 and y = H exactly once or more. */
export function glyphGeometry(item: EquipItem, key?: string): GlyphGeometry {
  const L = Number(item.dims_ft?.length) || 8
  const H = Number(itemHeight(item)) || 6
  const W = item.dims_ft?.width ?? null
  const kind = glyphKind(key, item)
  const p: Prim[] = []
  const rect = (x: number, y: number, w: number, h: number, tone: Tone = 'body', r = 0) => p.push({ t: 'rect', x, y, w, h, r, tone })
  const poly = (pts: [number, number][], tone: Tone = 'body') => p.push({ t: 'poly', pts, tone })
  const wheel = (cx: number, r: number) => p.push({ t: 'wheel', cx, r })
  const seg = (a: [number, number], b: [number, number], tone: Tone = 'line', w?: number) => p.push({ t: 'seg', a, b, tone, w })

  switch (kind) {
    case 'counterbalance': {
      const fork = Math.min(3.6, 0.32 * L), mastX = L - fork - 0.45, bodyH = 0.47 * H, rw = Math.min(1.1, 0.15 * H)
      poly([[0, 0.12 * H], [0, bodyH * 0.92], [0.12 * L, bodyH], [mastX, bodyH], [mastX, 0.12 * H]])          // chassis + counterweight
      rect(0.28 * L, bodyH, 0.12 * L, 0.12 * H, 'dark', 0.15)                                                // seat
      seg([0.2 * L, bodyH], [0.24 * L, 0.93 * H]); seg([mastX - 0.25, bodyH], [mastX - 0.25, 0.93 * H])        // overhead guard posts
      seg([0.2 * L, 0.93 * H], [mastX - 0.25, 0.93 * H], 'line', 0.22)
      rect(mastX, 0.04 * H, 0.45, 0.96 * H, 'line')                                                         // mast (full lowered height)
      rect(mastX + 0.45, 0.06 * H, fork, 0.06 * H, 'accent')                                                 // forks
      rect(mastX + 0.45, 0.06 * H, 0.18, 0.4 * H, 'accent')                                                  // carriage
      wheel(0.2 * L, rw * 0.9); wheel(mastX - rw * 1.05, rw)
      break
    }
    case 'reach': {
      const bodyW = 0.44 * L, mastX = 0.52 * L, legH = 0.07 * H
      rect(0, 0.06 * H, bodyW, 0.42 * H, 'body', 0.3)
      seg([0.05 * L, 0.48 * H], [0.05 * L, 0.8 * H]); seg([bodyW - 0.2, 0.48 * H], [bodyW - 0.2, 0.8 * H]); seg([0.05 * L, 0.8 * H], [bodyW - 0.2, 0.8 * H], 'line', 0.2)
      rect(bodyW, 0, L - bodyW, legH, 'dark')                                                               // baselegs (outriggers)
      rect(mastX, legH, 0.4, H - legH, 'line')                                                              // mast
      rect(mastX + 0.4, legH + 0.04 * H, L - mastX - 0.4, 0.05 * H, 'accent')                               // reach forks
      wheel(0.2 * L, 0.06 * H); wheel(0.9 * L, 0.035 * H)
      break
    }
    case 'order_picker': {
      const bodyW = 0.4 * L, mastX = 0.42 * L
      rect(0, 0.05 * H, bodyW, 0.4 * H, 'body', 0.3)
      rect(mastX, 0, 0.45, H, 'line')
      rect(mastX + 0.45, 0.12 * H, 0.26 * L, 0.06 * H, 'dark')                                               // operator platform
      seg([mastX + 0.45 + 0.26 * L, 0.18 * H], [mastX + 0.45 + 0.26 * L, 0.62 * H]); seg([mastX + 0.45, 0.62 * H], [mastX + 0.45 + 0.26 * L, 0.62 * H], 'line', 0.2)
      rect(mastX + 0.45, 0.02 * H, L - mastX - 0.45, 0.05 * H, 'accent')
      wheel(0.18 * L, 0.05 * H)
      break
    }
    case 'vna': {
      const chH = 0.28 * H, mastX = 0.6 * L
      rect(0, 0.05 * H, 0.72 * L, chH, 'body', 0.3)
      rect(0.3 * L, chH + 0.05 * H, 0.26 * L, 0.24 * H, 'glass', 0.2)                                        // operator cab
      rect(mastX, 0.05 * H, 0.5, 0.95 * H, 'line')
      rect(0.78 * L, 0.08 * H, 0.06 * L, 0.24 * H, 'line')                                                  // turret head
      rect(0.84 * L, 0.1 * H, L - 0.84 * L, 0.04 * H, 'accent')
      wheel(0.12 * L, 0.05 * H); wheel(0.62 * L, 0.05 * H)
      break
    }
    case 'pallet_jack': {
      const headW = 0.34 * L
      rect(0, 0.04 * H, headW, 0.5 * H, 'body', 0.25)
      seg([0.26 * L, 0.54 * H], [0.08 * L, H], 'line', 0.25); seg([0.03 * L, H], [0.14 * L, H], 'line', 0.25) // tiller + grip
      rect(headW, 0.03 * H, L - headW, 0.09 * H, 'accent')                                                   // forks
      wheel(0.16 * L, 0.06 * H); wheel(0.93 * L, 0.03 * H)
      break
    }
    case 'stacker': {
      const headW = 0.3 * L, mastX = 0.31 * L
      rect(0, 0.04 * H, headW, 0.42 * H, 'body', 0.25)
      seg([0.18 * L, 0.46 * H], [0.03 * L, 0.68 * H], 'line', 0.22)
      rect(mastX, 0.05 * H, 0.35, 0.95 * H, 'line')
      rect(mastX + 0.35, 0, L - mastX - 0.35, 0.05 * H, 'dark')                                             // baselegs
      rect(mastX + 0.35, 0.12 * H, 0.88 * L - mastX, 0.05 * H, 'accent')
      wheel(0.14 * L, 0.05 * H)
      break
    }
    case 'tugger': {
      const cart = 0.21 * L, gap = 0.04 * L, tr = 0.21 * L, r = 0.09 * H
      rect(L - tr, 0.12 * H, tr, 0.5 * H, 'body', 0.3)                                                      // tractor
      seg([L - tr + 0.25 * tr, 0.62 * H], [L - tr + 0.25 * tr, H]); seg([L - 0.15 * tr, 0.62 * H], [L - 0.15 * tr, H]); seg([L - tr + 0.25 * tr, H], [L - 0.15 * tr, H], 'line', 0.22)
      wheel(L - tr + 0.25 * tr, r); wheel(L - 0.2 * tr, r)
      for (let i = 0; i < 3; i++) {
        const x = i * (cart + gap)
        rect(x, 0.2 * H, cart, 0.4 * H, 'accent', 0.15)
        wheel(x + 0.2 * cart, r * 0.8); wheel(x + 0.8 * cart, r * 0.8)
        seg([x + cart, 0.3 * H], [x + cart + gap, 0.3 * H], 'dark', 0.2)                                    // drawbar
      }
      break
    }
    case 'amr': {
      const r = 0.12 * H
      rect(0, 0.16 * H, L, 0.62 * H, 'body', 0.35 * H)
      rect(0.08 * L, 0.78 * H, 0.84 * L, 0.22 * H, 'accent', 0.08)                                           // lift plate
      rect(0.42 * L, 0.36 * H, 0.16 * L, 0.14 * H, 'glass', 0.05)                                            // sensor window
      wheel(0.25 * L, r * 0.8); wheel(0.75 * L, r * 0.8)
      break
    }
    case 'agv': {
      const bodyW = 0.46 * L, mastX = 0.48 * L
      rect(0, 0.05 * H, bodyW, 0.46 * H, 'body', 0.3)
      rect(0.1 * L, 0.51 * H, 0.12 * L, 0.06 * H, 'glass', 0.05)                                             // lidar
      rect(mastX, 0.04 * H, 0.4, 0.96 * H, 'line')
      rect(mastX + 0.4, 0, L - mastX - 0.4, 0.05 * H, 'dark')
      rect(mastX + 0.4, 0.1 * H, L - mastX - 0.6, 0.05 * H, 'accent')
      wheel(0.2 * L, 0.06 * H)
      break
    }
    case 'conveyor': {
      rect(0, 0.72 * H, L, 0.28 * H, 'body', 0.1)
      for (let x = 0.02 * L; x < L; x += Math.max(8, L / 10)) seg([x, 0], [x, 0.72 * H], 'line', 0.35)
      seg([L - 0.02 * L, 0], [L - 0.02 * L, 0.72 * H], 'line', 0.35)
      break
    }
    case 'yard_truck': {
      const r = 0.15 * H
      rect(0, 0.2 * H, L, 0.14 * H, 'dark')                                                                 // chassis
      rect(0.08 * L, 0.34 * H, 0.32 * L, 0.06 * H, 'accent')                                                 // fifth-wheel deck
      poly([[0.55 * L, 0.34 * H], [0.55 * L, H], [0.92 * L, H], [L, 0.62 * H], [L, 0.34 * H]])              // cab
      rect(0.62 * L, 0.62 * H, 0.26 * L, 0.28 * H, 'glass', 0.1)
      seg([0.5 * L, 0.34 * H], [0.5 * L, 0.96 * H], 'line', 0.3)                                            // exhaust stack
      wheel(0.2 * L, r); wheel(0.82 * L, r)
      break
    }
    case 'semi': {
      const T = Math.min(item.trailer_ft ?? 53, L - 10), r = Math.min(1.75, 0.13 * H), floor = 0.3 * H
      rect(0, floor, T, H - floor, 'body', 0.2)                                                              // 53' trailer box
      rect(0.68 * T, 0.05 * H, 0.03 * T, floor - 0.05 * H, 'dark')                                           // landing gear
      wheel(0.06 * T, r); wheel(0.06 * T + 2 * r + 0.3, r)
      const tx = T - 3.5                                                                                     // tractor under the trailer nose
      rect(tx, 0.16 * H, L - tx, 0.11 * H, 'dark')
      poly([[L - 12, 0.27 * H], [L - 12, 0.88 * H], [L - 4.2, 0.88 * H], [L - 2.4, 0.56 * H], [L, 0.52 * H], [L, 0.27 * H]], 'accent')
      rect(L - 5.4, 0.6 * H, 2.4, 0.2 * H, 'glass', 0.15)
      wheel(tx + 2.4, r); wheel(tx + 2.4 + 2 * r + 0.3, r); wheel(L - 3.4, r)
      break
    }
    case 'box_truck': {
      const box = 0.74 * L, r = 0.12 * H
      rect(0, 0.24 * H, box, 0.76 * H, 'body', 0.15)
      rect(box - 1, 0.18 * H, L - box + 1, 0.08 * H, 'dark')
      poly([[box + 0.4, 0.26 * H], [box + 0.4, 0.74 * H], [L - 2.6, 0.74 * H], [L - 0.8, 0.46 * H], [L, 0.44 * H], [L, 0.26 * H]], 'accent')
      rect(L - 4.4, 0.5 * H, 2.2, 0.18 * H, 'glass', 0.1)
      wheel(0.22 * L, r); wheel(L - 3.6, r)
      break
    }
    case 'van': {
      const r = 0.13 * H
      poly([[0, 0.14 * H], [0, 0.94 * H], [0.06 * L, H], [0.7 * L, H], [0.86 * L, 0.58 * H], [L, 0.48 * H], [L, 0.14 * H]], 'accent')
      poly([[0.73 * L, 0.88 * H], [0.84 * L, 0.6 * H], [0.73 * L, 0.6 * H]], 'glass')
      wheel(0.19 * L, r); wheel(0.8 * L, r)
      break
    }
    default:
      rect(0, 0.15 * H, L, 0.85 * H, 'body', 0.3)
      wheel(0.2 * L, 0.08 * H); wheel(0.8 * L, 0.08 * H)
  }
  return { kind, length_ft: L, height_ft: H, width_ft: W, prims: p, bbox: bboxOf(p) }
}

export function bboxOf(prims: Prim[]) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  const add = (x: number, y: number) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y) }
  for (const q of prims) {
    if (q.t === 'rect') { add(q.x, q.y); add(q.x + q.w, q.y + q.h) }
    else if (q.t === 'poly') q.pts.forEach(([x, y]) => add(x, y))
    else if (q.t === 'wheel') { add(q.cx - q.r, 0); add(q.cx + q.r, 2 * q.r) }
    else { add(...q.a); add(...q.b) }
  }
  return { x0, y0, x1, y1 }
}

/** Unit-style check: drawn length and height equal dims_ft (to 0.1%). Returns the mismatches (empty = pass). */
export function checkGlyphs(lib: Record<string, EquipItem>) {
  const bad: string[] = []
  for (const [k, it] of Object.entries(lib)) {
    const g = glyphGeometry(it, k)
    const L = it.dims_ft?.length, H = itemHeight(it)
    if (L == null || H == null) continue
    const { x0, y0, x1, y1 } = g.bbox
    const tol = (v: number) => Math.max(1e-3 * v, 1e-6)
    if (Math.abs(x0) > tol(L) || Math.abs(y0) > tol(H) || Math.abs(x1 - L) > tol(L) || Math.abs(y1 - H) > tol(H)
      || Math.abs((x1 - x0) / (y1 - y0) - L / H) > 1e-3 * (L / H)) {
      bad.push(`${k}: drawn ${(x1 - x0).toFixed(2)}x${(y1 - y0).toFixed(2)} ft (from ${x0.toFixed(2)},${y0.toFixed(2)}) vs dims ${L}x${H}`)
    }
  }
  return bad
}

let checked = false
/** run checkGlyphs once per page in dev builds and shout if a glyph is off-scale */
export function devCheckGlyphs(lib: Record<string, EquipItem>) {
  if (!import.meta.env.DEV || checked || !Object.keys(lib).length) return
  checked = true
  const bad = checkGlyphs(lib)
  if (bad.length) console.error('[EquipmentGlyph] off-scale glyphs:\n' + bad.join('\n'))
  ;(window as unknown as Record<string, unknown>).__glyphCheck = { items: Object.keys(lib).length, bad }
}

const TONE: Record<Tone, { fill: string; stroke: string }> = {
  body: { fill: 'color-mix(in oklab, var(--color-ink-2) 26%, transparent)', stroke: 'var(--color-ink-2)' },
  dark: { fill: 'var(--color-panel-3)', stroke: 'var(--color-ink-3)' },
  line: { fill: 'var(--color-ink-3)', stroke: 'var(--color-ink-2)' },
  glass: { fill: 'color-mix(in oklab, var(--color-accent) 22%, transparent)', stroke: 'var(--color-accent-2)' },
  accent: { fill: 'color-mix(in oklab, var(--color-accent) 30%, transparent)', stroke: 'var(--color-accent)' },
}

interface GlyphProps {
  item: EquipItem
  itemKey?: string
  /** minimum frame width in ft, so a set of glyphs shares one scale (e.g. 14 for indoor trucks, 74 for road) */
  frameFt?: number
  /** minimum frame height in ft */
  frameHFt?: number
  /** draw a 0..N ft scale bar under the glyph (true = 20 ft) */
  scaleBar?: boolean | number
  className?: string
  title?: string
}

export default function EquipmentGlyph({ item, itemKey, frameFt = 0, frameHFt = 0, scaleBar, className, title }: GlyphProps) {
  const g = useMemo(() => glyphGeometry(item, itemKey), [item, itemKey])
  const bar = scaleBar === true ? 20 : typeof scaleBar === 'number' ? scaleBar : 0
  const fw = Math.max(g.length_ft, frameFt, bar)
  const fh = Math.max(g.height_ft, frameHFt)
  const unit = Math.max(fh, fw / 3) * 0.16          // scale-bar row height, in ft of drawing
  const pad = Math.max(fw, fh) * 0.03
  const barH = bar ? unit * 1.7 : 0
  const textW = bar ? unit * 3.6 : 0
  const vbW = fw + 2 * pad + textW, vbH = fh + pad + barH + pad * 0.5
  const sw = Math.max(fw, fh) / 120          // ~1 px stroke at typical card sizes
  const X = (x: number) => pad + (fw - g.length_ft) / 2 + x
  const Y = (y: number) => pad + fh - y
  return (
    <svg viewBox={`0 0 ${vbW} ${vbH}`} className={className} preserveAspectRatio="xMidYMax meet" role="img"
      aria-label={title ?? `${item.name ?? 'equipment'}, ${g.length_ft} ft long, ${g.height_ft} ft high`}>
      <line x1={pad * 0.5} x2={vbW - pad * 0.5} y1={Y(0)} y2={Y(0)} stroke="var(--color-line-2)" strokeWidth={sw} />
      {g.prims.map((q, i) => {
        if (q.t === 'wheel') return <circle key={i} cx={X(q.cx)} cy={Y(q.r)} r={q.r} fill="var(--color-bg)" stroke="var(--color-ink-2)" strokeWidth={sw} />
        const tn = TONE[q.tone]
        if (q.t === 'rect') return <rect key={i} x={X(q.x)} y={Y(q.y + q.h)} width={q.w} height={q.h} rx={q.r} fill={tn.fill} stroke={tn.stroke} strokeWidth={sw} strokeLinejoin="round" />
        if (q.t === 'poly') return <polygon key={i} points={q.pts.map(([x, y]) => `${X(x)},${Y(y)}`).join(' ')} fill={tn.fill} stroke={tn.stroke} strokeWidth={sw} strokeLinejoin="round" />
        return <line key={i} x1={X(q.a[0])} y1={Y(q.a[1])} x2={X(q.b[0])} y2={Y(q.b[1])} stroke={tn.stroke} strokeWidth={q.w ?? sw * 1.4} strokeLinecap="round" />
      })}
      {bar > 0 && (
        <g transform={`translate(${pad}, ${Y(0) + unit * 0.45})`} fontFamily="JetBrains Mono, monospace" fontSize={unit * 1.05} fill="var(--color-ink-3)">
          {[0, 1, 2, 3, 4].map((i) => (
            <rect key={i} x={(bar / 4) * i} y={0} width={bar / 4} height={unit * 0.22} fill={i % 2 ? 'var(--color-ink-3)' : 'var(--color-line-2)'} />
          ))}
          <text x={bar + unit * 0.3} y={unit * 0.15} dominantBaseline="middle">{bar} ft</text>
        </g>
      )}
    </svg>
  )
}
