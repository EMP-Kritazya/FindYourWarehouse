import { useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { Car, Truck, ParkingSquare, Home, Sun, Trees, Ruler, History, MapPin, Plane, Container } from 'lucide-react'
import type { NearbyHit, Twin, VexcelProfile } from '../lib/api'
import { fmtFt, fmtInt, fmtSF, isNum } from '../lib/format'
import { fmtDist, loadVexcel, pv, yearOf, type Val } from '../lib/vexcel'
import { placeLabel } from '../lib/metrics'
import { useStore } from '../lib/store'
import { SectionTitle, Skeleton, useAsync } from './ui'

const UNKNOWN = 'Not measured from the Vexcel aerial for this property yet.'

/** "From the air (Vexcel)": parking, vehicles, roof, solar, trees, height, history and nearby places. */
export default function VexcelSection({ id, twin }: { id: number; twin: () => Promise<Twin> }) {
  const prof = useAsync(() => loadVexcel(id, twin), [id])
  if (prof.loading) return <div><SectionTitle>From the air · Vexcel</SectionTitle><div className="grid grid-cols-3 gap-2">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[64px]" />)}</div></div>
  const p = prof.data
  if (!p) {
    return (
      <div>
        <SectionTitle>From the air · Vexcel</SectionTitle>
        <div className="flex h-16 items-center justify-center gap-2 rounded-xl bg-white/[0.03] text-[12px] text-ink-3 ring-1 ring-inset ring-line">
          <Plane size={14} /> {prof.error ? 'Vexcel profile could not be loaded' : 'No Vexcel profile for this property yet'}
        </div>
      </div>
    )
  }
  return <Profile p={p} />
}

function Profile({ p }: { p: VexcelProfile }) {
  const d = p.captured?.ortho ?? p.captured?.parcel_attrs
  const S = (p.sources ?? {}) as Record<string, unknown>
  const g = (grp: Record<string, unknown> | undefined, k: string, date?: string) => pv((grp?.[k] ?? null) as never, date, S[k])
  const pk = p.parking as Record<string, unknown> | undefined
  const bd = p.building as Record<string, unknown> | undefined
  const st = p.site as Record<string, unknown> | undefined
  const parking = g(pk, 'parking_lot_area_sqft', d)
  const paved = g(pk, 'paved_area_sqft', p.captured?.parcel_attrs ?? d)
  const stalls = g(pk, 'car_stalls_est', d)
  const court = g(pk, 'truck_court_ft', d)
  const cars = g(pk, 'cars_seen', d), trucks = g(pk, 'trucks_seen', d), trailers = g(pk, 'trailers_seen', d)
  const roof = g(bd, 'roof_condition', p.captured?.parcel_attrs ?? d)
  const material = g(bd, 'roof_material')
  const defects = (Array.isArray(bd?.roof_defects) ? bd!.roof_defects : (pv(bd?.roof_defects as never)?.v as string[] | undefined)) ?? []
  const solar = g(bd, 'solar', p.captured?.parcel_attrs ?? d)
  const height = g(bd, 'height_ft_dsm', p.captured?.dsm)
  const trees = g(st, 'tree_cover_pct', p.captured?.parcel_attrs ?? d)
  const sky = g(bd, 'skylights', d), ac = g(bd, 'ac_units', d)

  return (
    <div>
      <SectionTitle right={<span className="flex items-center gap-1.5 text-[11px] text-ink-3"><VChip />{d ? `captured ${d}` : ''}</span>}>From the air · Vexcel</SectionTitle>
      {p._fallback && <div className="mb-2 rounded-lg bg-warn/[0.07] px-2.5 py-1.5 text-[11px] leading-snug text-warn/90 ring-1 ring-inset ring-warn/20">{p._fallback}</div>}

      <Group label="Yard & parking">
        {parking || !paved
          ? <Cell icon={<ParkingSquare size={12} />} label="Parking area" val={parking} fmt={fmtSF} alt={paved ? `paved ${fmtSF(paved.v)}` : undefined} />
          : <Cell icon={<ParkingSquare size={12} />} label="Paved area" val={paved} fmt={fmtSF} sub="parking lot not split out yet" />}
        <Cell icon={<Car size={12} />} label="Car stalls" val={stalls} fmt={(v) => `≤ ${fmtInt(v)}`} forceEstimate caveat="upper bound: mask can include truck courts" />
        <Cell icon={<Ruler size={12} />} label="Truck court" val={court} fmt={fmtFt} />
        <Cell icon={<Car size={12} />} label="Cars seen" val={cars} fmt={fmtInt} />
        <Cell icon={<Truck size={12} />} label="Trucks seen" val={trucks} fmt={fmtInt} />
        <Cell icon={<Container size={12} />} label="Trailers seen" val={trailers} fmt={fmtInt} />
      </Group>

      <Group label="Building & site">
        <Cell icon={<Home size={12} />} label="Roof" val={roof} fmt={(v) => (isNum(v) ? `${v}/5` : String(v))} sub={material ? String(material.v) : undefined} />
        <Cell icon={<Sun size={12} />} label="Solar" val={solar} fmt={(v) => (v === true || v === 1 ? 'Yes' : v === false || v === 0 ? 'None seen' : String(v))} />
        <Cell icon={<Ruler size={12} className="rotate-90" />} label="Height (DSM)" val={height} fmt={fmtFt} caveat="to roof top, not clear height" />
        <Cell icon={<Trees size={12} />} label="Tree cover" val={trees} fmt={(v) => (isNum(v) ? `${v}%` : String(v))} />
        <Cell icon={<Sun size={12} />} label="Skylights" val={sky} fmt={fmtInt} />
        <Cell icon={<Home size={12} />} label="Rooftop AC" val={ac} fmt={fmtInt} />
      </Group>
      {defects.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {defects.map((x) => <span key={String(x)} className="rounded bg-bad/10 px-1.5 py-0.5 text-[11px] text-bad ring-1 ring-inset ring-bad/20">{String(x).replace(/_/g, ' ')}</span>)}
        </div>
      )}

      <HistoryRow p={p} />
      <Nearby p={p} />
    </div>
  )
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-2">
      <div className="mb-1 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-3/80">{label}</div>
      <div className="grid grid-cols-3 gap-1.5">{children}</div>
    </div>
  )
}

function VChip({ date, estimate }: { date?: string; estimate?: boolean }) {
  return (
    <span className="inline-flex h-[16px] items-center gap-1 rounded px-1 text-[11px] font-medium uppercase tracking-wide text-amber-300 ring-1 ring-inset ring-amber-400/25">
      Vexcel{date ? <span className="normal-case tracking-normal text-amber-200/70">{yearOf(date) === date ? date : date.slice(0, 7)}</span> : null}
      {estimate && <span className="normal-case tracking-normal text-warn">est.</span>}
    </span>
  )
}

function Cell({ icon, label, val, fmt, sub, alt, forceEstimate, caveat }: {
  icon: ReactNode; label: string; val: Val | null; fmt: (v: unknown) => string; sub?: string; alt?: string; forceEstimate?: boolean; caveat?: string
}) {
  const has = val && val.v !== null && val.v !== undefined
  const est = !!has && (val!.estimate || !!forceEstimate)
  const title = has ? [val!.method, val!.date && `captured ${val!.date}`, est && 'Estimate, not a count', val!.note].filter(Boolean).join(' · ') : val?.note ?? UNKNOWN
  return (
    <div className="min-w-0 rounded-lg bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line" title={title}>
      <div className="flex items-center gap-1 truncate text-[11px] text-ink-3">{icon}{label}</div>
      <div className="mt-0.5 truncate text-[14px] font-semibold tabular-nums">{has ? fmt(val!.v) : <span className="cursor-help text-ink-3">?</span>}</div>
      {(sub || alt) && <div className="truncate text-[11px] text-ink-2">{sub ?? alt}</div>}
      {has && caveat && <div className="truncate text-[11px] text-warn/80" title={caveat}>{caveat}</div>}
      {has && <div className="mt-1"><VChip date={val!.date} estimate={est} /></div>}
    </div>
  )
}

function HistoryRow({ p }: { p: VexcelProfile }) {
  const dates = [...new Set((p.history?.ortho_dates ?? []).filter(Boolean))].sort()
  const ch = p.history?.change
  if (!dates.length && !ch) return null
  const years = [...new Set(dates.map(yearOf))]
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center gap-1 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-3/80"><History size={11} /> History</div>
      <div className="rounded-lg bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line">
        <div className="flex flex-wrap items-center gap-1">
          {years.map((y, i) => (
            <span key={y} className="flex items-center gap-1">
              <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[11px] tabular-nums text-ink-2" title={dates.filter((d) => yearOf(d) === y).join(', ')}>{y}</span>
              {i < years.length - 1 && <span className="h-px w-2 bg-line-2" />}
            </span>
          ))}
        </div>
        <div className="mt-1.5 text-[11px] leading-snug text-ink-2" title={ch?.note ?? undefined}>
          {ch && (isNum(ch.new_structure_m2) || isNum(ch.removed_m2))
            ? <>Surface change {ch.years?.join(' → ')}: <span className="font-medium text-ink">{isNum(ch.new_structure_m2) && ch.new_structure_m2 > 0 ? `+${Math.round(ch.new_structure_m2 * 10.764).toLocaleString()} SF new` : 'nothing new'}</span>
                {isNum(ch.removed_m2) && ch.removed_m2 > 0 ? `, −${Math.round(ch.removed_m2 * 10.764).toLocaleString()} SF removed` : ', nothing removed'}
                <span className="text-ink-3"> · DSM difference, &gt;2.5 m, may include parked trailers</span></>
            : ch?.note ?? (dates.length > 1 ? 'Change between captures not analysed yet.' : 'One capture on file.')}
        </div>
      </div>
    </div>
  )
}

const ORDER = ['grocery', 'highway_ramp', 'rail_yard', 'truck_stop', 'port_container', 'residential']

function Nearby({ p }: { p: VexcelProfile }) {
  const setPlace = useStore((s) => s.setPlace)
  const pin = useStore((s) => s.pinsById.get(p.id))
  type Wrapped = { found?: boolean; label?: string; nearest?: NearbyHit | null; best?: NearbyHit | null; caveat?: string | null; note?: string | null }
  const entries = Object.entries(p.nearby ?? {})
    .map(([k, raw]) => {
      const v = (Array.isArray(raw) ? raw[0] : raw) as (NearbyHit & Wrapped) | null | undefined
      if (!v) return [k, null] as [string, null]
      const isWrapped = 'found' in v || 'nearest' in v || 'best' in v
      const hit = isWrapped ? (v.nearest ?? v.best ?? null) : v
      return [k, { ...(hit ?? {}), label: v.label ?? hit?.label, caveat: v.caveat ?? undefined, found: isWrapped ? !!v.found && !!hit : true }] as [string, NearbyHit & { caveat?: string; found: boolean }]
    })
    .filter((e): e is [string, NearbyHit & { caveat?: string; found: boolean }] => !!e[1])
    .sort((a, b) => (ORDER.indexOf(a[0]) + 99) % 99 - (ORDER.indexOf(b[0]) + 99) % 99)
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center gap-1 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-3/80"><MapPin size={11} /> Nearby (image search)</div>
      {entries.length === 0 ? (
        <div className="rounded-lg bg-white/[0.03] px-2.5 py-2 text-[11px] text-ink-3 ring-1 ring-inset ring-line">Nearby places are not computed for this property yet.</div>
      ) : (
        <div className="divide-y divide-line overflow-hidden rounded-lg ring-1 ring-inset ring-line">
          {entries.map(([k, h]) => <NearbyRow key={k} k={k} h={h} onShow={() => isNum(h.lat) && isNum(h.lon) && setPlace({
            lat: h.lat, lon: h.lon, label: `${(h.label ?? placeLabel(k)).replace(/^\w/, (m) => m.toUpperCase())}${pin ? ` · near ${pin.address}` : ''}`,
            thumb_url: h.thumb_url, likelihood: h.likelihood, score: h.score, distance_m: h.distance_m, nearId: p.id })} />)}
        </div>
      )}
      <div className="mt-1 text-[11px] leading-snug text-ink-3">Matched by Vexcel image embeddings within ~3 km. "Likely" and "possible" are match strengths, not confirmed places.</div>
    </div>
  )
}

function NearbyRow({ k, h, onShow }: { k: string; h: NearbyHit & { caveat?: string; found?: boolean }; onShow: () => void }) {
  const [ok, setOk] = useState(true)
  if (h.found === false) {
    return (
      <div className="flex items-center gap-2.5 bg-white/[0.02] px-2 py-1.5" title={h.label}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-panel-3 text-ink-3"><MapPin size={13} /></div>
        <div className="min-w-0 flex-1"><div className="truncate text-[12px] capitalize text-ink-2">{placeLabel(k)}</div><div className="text-[11px] text-ink-3">none found within 3 km</div></div>
      </div>
    )
  }
  return (
    <button onClick={onShow} title={[h.label, h.caveat].filter(Boolean).join(' · ') + ' · show on the map'} aria-label={[h.label, h.caveat].filter(Boolean).join(' · ') + ' · show on the map'} className="flex w-full items-center gap-2.5 bg-white/[0.02] px-2 py-1.5 text-left transition hover:bg-white/[0.05]" >
      {h.thumb_url && ok ? <img src={h.thumb_url} alt="" onError={() => setOk(false)} className="h-9 w-9 shrink-0 rounded object-cover ring-1 ring-white/10" />
        : <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-panel-3 text-ink-3"><MapPin size={13} /></div>}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] capitalize text-ink">{placeLabel(k)}</div>
        <div className="truncate text-[11px] tabular-nums text-ink-3">{fmtDist(h.distance_m)}{isNum(h.score) ? ` · score ${h.score.toFixed(2)}` : ''}{h.label && h.label.toLowerCase() !== placeLabel(k) ? ` · ${h.label}` : ''}</div>
      </div>
      {h.likelihood && <span className={clsx('rounded px-1.5 py-0.5 text-[11px] font-medium', h.likelihood === 'likely' ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn')}>{h.likelihood}</span>}
    </button>
  )
}
