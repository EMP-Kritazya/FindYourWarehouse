// Interior planner: what is selected, its dimensions, and where each number comes from.
// Every value carries a source: MEASURED (Matterport scan / CoStar record), ASSUMED (standard spec or library),
// YOURS (the buyer's input) or CALCULATED (derived from the others). An assumed value is never shown as measured.
import clsx from 'clsx'
import { Trash2, Undo2, X } from 'lucide-react'
import type { Dim, DimSource, InteriorDims, InteriorPlan } from './racksApi'

export const M_TO_FT = 3.28084
export const ft = (m: number) => m * M_TO_FT
export const fmtFt = (v: number | null | undefined, d = 1) => (v == null ? '?' : `${v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: v < 10 && d ? 1 : 0 })} ft`)
export const fmtIn = (v: number | null | undefined) => (v == null ? '?' : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} in`)
export const fmtDim = (d: Dim | null | undefined) => (!d || d.value == null ? '?' : d.unit === 'in' ? fmtIn(d.value) : d.unit === 'ft' ? fmtFt(d.value, d.value < 10 ? 2 : 1) : d.unit === 'lb' ? `${d.value.toLocaleString()} lb` : String(d.value))

export const SRC: Record<DimSource, { label: string; cls: string; hint: string }> = {
  measured: { label: 'Measured', cls: 'bg-good/12 text-good ring-good/30', hint: 'Measured in the Matterport scan or recorded by CoStar' },
  assumed: { label: 'Assumed', cls: 'bg-warn/12 text-warn ring-warn/30', hint: 'A standard spec or library value, not a measurement. Change it if yours differs.' },
  yours: { label: 'Yours', cls: 'bg-accent/12 text-accent ring-accent/30', hint: 'What you entered' },
  calculated: { label: 'Calculated', cls: 'bg-indigo-400/12 text-indigo-300 ring-indigo-300/30', hint: 'Derived from the measured and assumed values' },
  recorded: { label: 'Recorded', cls: 'bg-good/12 text-good ring-good/30', hint: 'Recorded by CoStar (a listing or a signed lease)' },
  estimate: { label: 'Estimate', cls: 'bg-sky-400/12 text-sky-300 ring-sky-300/30', hint: "Estimated from comparable signed leases, not this building's own number" },
  unknown: { label: 'Unknown', cls: 'bg-white/[0.05] text-ink-3 ring-line', hint: 'No data: never counted as zero' },
}
export function SrcTag({ s }: { s: DimSource }) {
  const t = SRC[s]
  return <span title={t.hint} className={clsx('inline-flex h-[18px] items-center rounded px-1.5 text-[10.5px] font-semibold uppercase tracking-wide ring-1 ring-inset', t.cls)}>{t.label}</span>
}

/** What can be selected: a rack row (its id), or a prefixed id for everything else. */
export type SelKind = 'row' | 'obj' | 'zone' | 'lane' | 'col' | 'machine' | 'pallet' | 'bay' | 'building' | 'aisle'
export const selKind = (sel: string): SelKind => {
  const p = sel.split(':')[0]
  return (['obj', 'zone', 'lane', 'col', 'machine', 'pallet', 'bay', 'building', 'aisle'] as const).includes(p as never) ? (p as SelKind) : 'row'
}

interface Prop { label: string; value: string; src: DimSource; basis?: string }
interface Item { title: string; kind: string; props: Prop[]; note?: string; action?: { label: string; danger?: boolean; restore?: boolean } }

const OBJ_KIND: Record<string, string> = { rack_run: 'Existing rack run', stack: 'Stacked goods', room: 'Room / office', clutter: 'Equipment or clutter' }
const ZONE_TXT: Record<string, string> = { open: 'Open floor', shelving: 'Pick shelving', selective: 'Selective pallet rack', dense: 'Double-deep pallet rack', cantilever: 'Cantilever rack' }
const d = (label: string, x: Dim | null | undefined, basis?: string): Prop => ({ label, value: fmtDim(x), src: x?.source ?? 'assumed', basis: basis ?? x?.basis })

/** Plan geometry: rect in metres -> length (long side) and width (short side) in ft. */
export const sides = (x0: number, x1: number, y0: number, y1: number) => {
  const a = Math.abs(x1 - x0), b = Math.abs(y1 - y0)
  return { L: ft(Math.max(a, b)), W: ft(Math.min(a, b)) }
}

export function describe(sel: string, plan: InteriorPlan): Item | null {
  const D: InteriorDims = plan.dims
  const k = selKind(sel)
  const id = sel.includes(':') ? sel.slice(sel.indexOf(':') + 1) : sel
  if (k === 'row') {
    const r = plan.rows.find((x) => x.id === sel)
    if (!r) return null
    const { L, W } = sides(r.x0, r.x1, r.y0, r.y1)
    const lv = r.levels ?? plan.levels
    const pitch = r.level_ft ?? D.rack.level_pitch.value ?? 5.5
    const pallets = r.type === 'selective' || r.type === 'dense'
    return {
      title: `Rack row ${r.id}`, kind: ZONE_TXT[r.type ?? 'selective'] ?? 'Rack row',
      props: [
        { label: 'Length', value: fmtFt(L), src: 'calculated', basis: `${r.bays} bays × ${fmtFt(D.rack.bay_width.value, 2)}` },
        { label: 'Depth', value: fmtFt(W), src: 'calculated', basis: 'rack frame depth (from the pallet), back-to-back rows include the flue' },
        ...(pallets ? [{ label: 'Height (top load)', value: fmtFt(lv >= 1 ? (lv - 1) * pitch + (plan.pallet.load_in / 12) : null), src: 'calculated' as DimSource, basis: `${lv} levels × ${fmtFt(pitch, 2)} + ${fmtIn(plan.pallet.load_in)} load` }] : []),
        { label: 'Bays', value: String(r.bays), src: 'calculated' },
        { label: 'Levels', value: String(lv), src: 'calculated', basis: 'usable height ÷ level pitch' },
        { label: pallets ? 'Pallet positions' : 'Positions', value: (r.slots_kept ?? r.slots).toLocaleString(), src: 'calculated', basis: `${r.bays} bays × ${r.per_bay ?? 2} per bay × ${lv} levels${(r.deep ?? 1) > 1 ? ' × 2 deep' : ''}` },
        ...(r.trip_m != null ? [{ label: 'Trip from the docks', value: fmtFt(ft(r.trip_m), 0), src: 'calculated' as DimSource, basis: 'shortest drivable path on the scanned floor' }] : []),
      ],
      note: r.deleted ? 'Removed from the layout.' : r.ok === false ? r.why ?? undefined : r.reachable === false ? 'Your machine cannot reach this row.' : undefined,
      action: r.deleted ? { label: 'Restore row', restore: true } : { label: 'Remove row', danger: true },
    }
  }
  if (k === 'obj') {
    const o = plan.objects.find((x) => x.id === id)
    if (!o) return null
    const { L, W } = sides(o.x0, o.x1, o.y0, o.y1)
    const deck = D.building.roof_deck.value
    return {
      title: OBJ_KIND[o.kind] ?? 'Scanned object', kind: `Found in the scan · ${o.kept ? 'kept' : 'removed for the new layout'}`,
      props: [
        { label: 'Length', value: fmtFt(L), src: 'measured', basis: 'footprint of the object in the Matterport scan (bounding box)' },
        { label: 'Width', value: fmtFt(W), src: 'measured', basis: 'footprint of the object in the Matterport scan (bounding box)' },
        { label: 'Floor area', value: `${o.area_sqft.toLocaleString()} SF`, src: 'measured' },
        o.height === 'full'
          ? { label: 'Height', value: deck ? `full height (≥ ${fmtFt(deck - 4.9, 0)})` : 'full height', src: 'measured', basis: 'occupies the scan at 1.2–2.0 m AND near the roof deck; the exact top is not extracted' }
          : { label: 'Height', value: 'under 6.6 ft', src: 'measured', basis: 'occupies the scan only below 2.0 m (6.6 ft); the exact top is not extracted' },
        { label: 'Type', value: OBJ_KIND[o.kind] ? `${OBJ_KIND[o.kind].toLowerCase()} (guess)` : o.kind, src: 'calculated', basis: 'guessed from the shape; not identified' },
      ],
      action: o.kept ? { label: 'Remove for the new layout', danger: true } : { label: 'Keep it', restore: true },
    }
  }
  if (k === 'zone') {
    const z = plan.zones.find((x) => x.id === id)
    if (!z || !z.rect) return null
    const { L, W } = sides(z.rect[0], z.rect[1], z.rect[2], z.rect[3])
    return {
      title: ZONE_TXT[z.type] ?? 'Zone', kind: `Zone ${z.id} · ${Math.round(z.from * 100)}–${Math.round(z.to * 100)}% from the docks`,
      props: [
        { label: 'Extent', value: `${fmtFt(L, 0)} × ${fmtFt(W, 0)}`, src: 'calculated', basis: 'the zone band on the scanned floor' },
        { label: 'Floor area', value: `${z.area_sqft.toLocaleString()} SF`, src: 'calculated' },
        ...(z.type !== 'open' ? [{ label: 'Positions', value: z.positions.toLocaleString(), src: 'calculated' as DimSource }] : []),
        ...(z.levels ? [{ label: 'Levels', value: String(z.levels), src: 'calculated' as DimSource }] : []),
      ],
      note: z.why,
    }
  }
  if (k === 'lane') {
    const l = plan.lanes[Number(id)]
    if (!l) return null
    const { L, W } = sides(l.x0, l.x1, l.y0, l.y1)
    return {
      title: 'Staging lane', kind: 'In front of a dock door: pallets wait here to load or put away',
      props: [
        { label: 'Length', value: fmtFt(L), src: 'assumed', basis: 'about one trailer of pallets, shorter in shallow buildings' },
        { label: 'Width', value: fmtFt(W), src: 'assumed', basis: D.lane.width.basis },
        { label: 'Pallets (floor)', value: String(Math.max(0, Math.floor(L / (plan.pallet.depth_in / 12)))), src: 'calculated' },
      ],
    }
  }
  if (k === 'col') {
    const c = plan.plan.columns[Number(id)]
    if (!c) return null
    return {
      title: 'Structural column', kind: 'Found in the scan',
      props: [
        { label: 'Position', value: 'measured', src: 'measured', basis: 'a column seen in both a low and a high slice of the scan mesh' },
        ...(D.building.column_bay?.value ? [d('Column spacing', D.building.column_bay)] : []),
      ],
      note: 'Racks and aisles are laid out around it.',
    }
  }
  if (k === 'machine') {
    const m = D.machine
    return {
      title: m.name, kind: 'Your put-away machine (library spec)',
      props: [d('Length', m.length), d('Width', m.width), d('Height (mast down)', m.height), d('Lift height', m.lift_height), d('Capacity', m.capacity), d('Aisle it needs', D.aisle)],
      note: m.basis,
    }
  }
  if (k === 'pallet') {
    const p = D.pallet
    return {
      title: `Pallet · ${p.name}`, kind: p.note,
      props: [d('Width (along the beam)', p.width), d('Depth (into the rack)', p.depth), d('Load height', p.load_height)],
      note: 'Change the pallet above the plan; racks, levels and capacity recalculate.',
    }
  }
  if (k === 'bay') {
    const r = D.rack
    return {
      title: 'Rack bay', kind: `${plan.pallet.per_bay} pallets per level`,
      props: [d('Bay width', r.bay_width), d('Frame depth', r.frame_depth), d('Level pitch', r.level_pitch), d('Levels', r.levels), d('Top beam', r.top_beam), d('Top of top load', r.top_load), d('Usable height', D.building.usable_height)],
    }
  }
  if (k === 'aisle') {
    return {
      title: 'Rack aisle', kind: `Set by your machine: ${D.machine.name}`,
      props: [d('Aisle width', D.aisle), d('Machine width', D.machine.width), d('Machine length', D.machine.length)],
      note: 'The aisle a machine needs to turn into a rack face and pick up a pallet (manufacturer spec range).',
    }
  }
  if (k === 'building') {
    const b = D.building
    return {
      title: 'Building height', kind: 'What limits how high you can stack',
      props: [d('Clear height', b.clear_height), d('Roof deck', b.roof_deck), d('Sprinkler clearance', b.sprinkler_clearance), d('Usable height', b.usable_height)],
      note: b.sprinkler_clearance.basis,
    }
  }
  return null
}

/** The compact properties panel (overlays the plan's corner). */
export function PropertiesPanel({ sel, plan, onClose, onAction }: { sel: string; plan: InteriorPlan; onClose: () => void; onAction: () => void }) {
  const it = describe(sel, plan)
  if (!it) return null
  const k = selKind(sel)
  return (
    <div className="glass w-[262px] overflow-hidden rounded-xl text-[12px]" data-testid="plan-props" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
      <div className="flex items-start gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold text-ink">{it.title}</div>
          <div className="text-[11px] leading-snug text-ink-3">{it.kind}</div>
        </div>
        <button onClick={onClose} aria-label="Clear selection" title="Clear selection (Esc)" className="rounded p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink"><X size={13} /></button>
      </div>
      <dl className="divide-y divide-line/60 px-3">
        {it.props.map((p) => (
          <div key={p.label} className="flex items-center gap-2 py-1.5" title={p.basis}>
            <dt className="min-w-0 flex-1 truncate text-ink-3">{p.label}</dt>
            <dd className="font-semibold tabular-nums text-ink">{p.value}</dd>
            <SrcTag s={p.src} />
          </div>
        ))}
      </dl>
      {k === 'bay' && <BayElevation plan={plan} />}
      {it.note && <div className="border-t border-line px-3 py-2 text-[11px] leading-snug text-ink-3">{it.note}</div>}
      {it.action && (
        <div className="border-t border-line p-2">
          <button onClick={onAction} className={clsx('flex h-7 w-full items-center justify-center gap-1.5 rounded-lg text-[11.5px] font-medium ring-1 ring-inset',
            it.action.danger ? 'text-bad ring-bad/30 hover:bg-bad/10' : 'text-accent ring-accent/30 hover:bg-accent/10')}>
            {it.action.danger ? <Trash2 size={12} /> : <Undo2 size={12} />} {it.action.label}
          </button>
        </div>
      )}
    </div>
  )
}

/** Side view of one bay, to scale: uprights, beams at each level, pallets with their load, the sprinkler line. */
export function BayElevation({ plan }: { plan: InteriorPlan }) {
  const D = plan.dims
  const bayFt = D.rack.bay_width.value ?? 8.25
  const pitch = D.rack.level_pitch.value ?? 5.5
  const load = plan.pallet.load_in / 12
  const clear = D.building.clear_height.value ?? 30
  const usable = D.building.usable_height.value ?? clear - 3
  const beams = D.rack.beam_heights_ft
  const per = plan.pallet.per_bay
  const pw = plan.pallet.width_in / 12
  const gap = (bayFt - per * pw) / (per + 1)
  const Wd = bayFt + 9, Hd = clear + 2
  const X = (x: number) => 4 + x, Y = (y: number) => Hd - y
  return (
    <svg viewBox={`0 0 ${Wd} ${Hd}`} className="mx-3 mb-2 block w-[calc(100%-24px)] rounded-lg bg-[#0d1117]" style={{ aspectRatio: `${Wd} / ${Hd}`, maxHeight: 170 }} data-testid="bay-elevation" aria-label="Rack bay elevation">
      <line x1={0} x2={Wd} y1={Y(clear)} y2={Y(clear)} stroke="#e6fffb" strokeWidth={0.12} />
      <text x={0.2} y={Y(clear) - 0.3} fontSize={0.9} fill="#e6fffb">clear {clear.toFixed(1)} ft · measured</text>
      <line x1={0} x2={Wd} y1={Y(usable)} y2={Y(usable)} stroke="#fbbf24" strokeWidth={0.12} strokeDasharray="0.5 0.3" />
      <text x={0.2} y={Y(usable) + 1.1} fontSize={0.85} fill="#fbbf24">sprinkler line {usable.toFixed(1)} ft</text>
      <line x1={0} x2={Wd} y1={Y(0)} y2={Y(0)} stroke="#64748b" strokeWidth={0.15} />
      {[0, bayFt].map((x) => <rect key={x} x={X(x) - 0.15} y={Y(beams.length ? beams[beams.length - 1] + load + 0.5 : 6)} width={0.3} height={(beams.length ? beams[beams.length - 1] + load + 0.5 : 6)} fill="#2563eb" />)}
      {beams.map((b) => (
        <g key={b}>
          {b > 0 && <rect x={X(0)} y={Y(b) - 0.12} width={bayFt} height={0.35} fill="#ea580c" />}
          {Array.from({ length: per }, (_, i) => (
            <g key={i}>
              <rect x={X(gap + i * (pw + gap))} y={Y(b + 0.45 + load) + 0.05} width={pw} height={load - 0.05} fill="#c8a165" stroke="#7c5a2c" strokeWidth={0.06} />
              <rect x={X(gap + i * (pw + gap))} y={Y(b + 0.45)} width={pw} height={0.45} fill="#8b6b3e" />
            </g>
          ))}
          <text x={X(bayFt) + 0.6} y={Y(b) + 0.3} fontSize={0.8} fill="#94a3b8">{b.toFixed(1)}</text>
        </g>
      ))}
      <text x={X(bayFt / 2)} y={Hd - 0.2} fontSize={0.85} textAnchor="middle" fill="#94a3b8">{bayFt.toFixed(2)} ft bay · {per} × {plan.pallet.width_in}″ pallets</text>
    </svg>
  )
}
