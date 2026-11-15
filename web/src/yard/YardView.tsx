// Yard test replay: the buyer's trucks on the real site, seen from the air (deck.gl over MapLibre).
// Swept paths are green where the truck clears everything (with a 0.6 m buffer) and red where it does not, with the
// conflict points marked. Trucks are oriented boxes (tractor + trailer linked at the kingpin) moving along the
// key-frames of one representative day; the street spill-back is highlighted. The legend states the door source.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Map, useControl } from '@vis.gl/react-maplibre'
import { MapLibreOverlay, type MapLibreOverlayProps } from '@deck.gl/maplibre'
import { BitmapLayer, GeoJsonLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import type { StyleSpecification } from 'maplibre-gl'
import { AlertTriangle, CheckCircle2, ChevronDown, Pause, Play, X, XCircle } from 'lucide-react'
import 'maplibre-gl/dist/maplibre-gl.css'
import YardForm from './YardForm'
import { marginClass, marginText } from './format'
import { DOOR_SOURCE_LABEL, TRUCK_TYPES, type TruckType, type YardFit, type YardResult } from './types'

function DeckOverlay(props: MapLibreOverlayProps) {
  const overlay = useControl<MapLibreOverlay>(() => new MapLibreOverlay({ ...props, interleaved: true }))
  overlay.setProps(props)
  return null
}

// Vexcel aerial through the backend proxy on top of Esri World Imagery (keyless; shows through where Vexcel 4xx's).
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    esri: { type: 'raster', tileSize: 256, maxzoom: 19, attribution: 'Esri, Maxar, Earthstar Geographics',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'] },
    vexcel: { type: 'raster', tileSize: 256, minzoom: 12, maxzoom: 21, attribution: 'Vexcel Imaging', tiles: [`${typeof location !== 'undefined' ? location.origin : ''}/api/tiles/{z}/{x}/{y}.jpg`] },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#0a0d12' } },
    { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-brightness-max': 0.8, 'raster-saturation': -0.3 } },
    { id: 'vexcel', type: 'raster', source: 'vexcel', paint: { 'raster-brightness-max': 0.85, 'raster-saturation': -0.2, 'raster-fade-duration': 200 } },
  ],
}

type RGBA = [number, number, number, number]
const STATE_COLOR: Record<string, RGBA> = {
  approach: [226, 232, 240, 235], enter: [226, 232, 240, 235], to_yard: [226, 232, 240, 235], to_door: [226, 232, 240, 235],
  exit: [148, 163, 184, 220], street_queue: [248, 113, 113, 255], yard_queue: [251, 191, 36, 255],
  back_in: [79, 209, 197, 255], pull_out: [79, 209, 197, 235], docked: [42, 157, 147, 255],
}
const SPEEDS = [1, 5, 15, 30, 60]

const clock = (t: number) => {
  const h = Math.floor(t / 3600) % 24, m = Math.floor((t % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}
const angDiff = (a: number, b: number) => ((b - a + 540) % 360) - 180

type Key = YardResult['vehicles'][number]['keys'][number]
function sample(keys: Key[], t: number) {
  if (!keys.length || t < keys[0][0] || t > keys[keys.length - 1][0]) return null
  let lo = 0, hi = keys.length - 1
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (keys[mid][0] <= t) lo = mid; else hi = mid }
  const a = keys[lo], b = keys[hi]
  const f = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 0
  if (a[4] === 'gone') return null
  return {
    lon: a[1] + (b[1] - a[1]) * f, lat: a[2] + (b[2] - a[2]) * f,
    h: a[3] + angDiff(a[3], b[3]) * f, ht: a[5] + angDiff(a[5], b[5]) * f,
    state: f > 0.999 ? b[4] : a[4],
  }
}

/** oriented box from rear_m..front_m along a compass heading, at (lon, lat) */
function box(lon: number, lat: number, hDeg: number, rear: number, front: number, w: number, lat0: number): [number, number][] {
  const h = (hDeg * Math.PI) / 180, fx = Math.sin(h), fy = Math.cos(h), rx = fy, ry = -fx
  const kx = 1 / (111320 * Math.cos((lat0 * Math.PI) / 180)), ky = 1 / 110574
  const pt = (a: number, s: number): [number, number] => [lon + (fx * a + rx * s) * kx, lat + (fy * a + ry * s) * ky]
  return [pt(rear, -w / 2), pt(front, -w / 2), pt(front, w / 2), pt(rear, w / 2), pt(rear, -w / 2)]
}

// `result` is typed loosely so callers can pass the raw JSON of POST /api/yard/test (or GET /api/yard/{run_id})
interface Props { propertyId: number; result?: YardResult | unknown | null; onClose?: () => void; initialTime?: number; initialSpeed?: number }

export default function YardView({ propertyId, result, onClose, initialTime, initialSpeed }: Props) {
  const valid = (r: unknown): r is YardResult => !!r && typeof r === 'object' && 'fits' in r && 'frame' in r
  const [res, setRes] = useState<YardResult | null>(valid(result) ? result : null)
  useEffect(() => { if (valid(result)) setRes(result) }, [result])
  const [center, setCenter] = useState<[number, number] | null>(null)
  useEffect(() => {
    if (res || center) return
    fetch(`/api/warehouses/${propertyId}`).then((r) => (r.ok ? r.json() : null)).then((w) => w && setCenter([w.lon, w.lat])).catch(() => {})
  }, [propertyId, res, center])

  const types = useMemo(() => (res ? res.summary.map((s) => s.type) : []), [res])
  const [vtype, setVtype] = useState<TruckType | null>(null)
  const shownType = vtype && types.includes(vtype) ? vtype : types[0]
  const [door, setDoor] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)

  const [t0, t1] = useMemo(() => {
    if (!res?.vehicles.length) return [(res?.queue.hours?.[0] ?? 6) * 3600, (res?.queue.hours?.[1] ?? 16) * 3600]
    let a = Infinity, b = -Infinity
    res.vehicles.forEach((v) => { a = Math.min(a, v.keys[0][0]); b = Math.max(b, v.keys[v.keys.length - 1][0]) })
    return [Math.floor(a), Math.ceil(b)]
  }, [res])
  const [t, setT] = useState(initialTime ?? 0)
  const [playing, setPlaying] = useState(initialTime == null)
  const [speed, setSpeed] = useState(initialSpeed ?? 30)
  useEffect(() => { setT(initialTime ?? t0) }, [t0, initialTime])
  const raf = useRef(0)
  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000); last = now
      setT((x) => (x + dt * speed > t1 ? t0 : x + dt * speed))
      raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [playing, speed, t0, t1])

  const live = useMemo(() => {
    if (!res) return { trucks: [], polys: [], counts: { street: 0, yard: 0, door: 0 } }
    const lat0 = res.frame.lat0
    const trucks: { id: string; type: string; lon: number; lat: number; state: string; door: string }[] = []
    const polys: { poly: [number, number][]; color: RGBA; id: string }[] = []
    for (const v of res.vehicles) {
      const s = sample(v.keys, t)
      if (!s) continue
      const g = res.vehicle_geometry[v.type]
      const col = STATE_COLOR[s.state] ?? STATE_COLOR.approach
      if (g.trailer) {
        polys.push({ id: v.id + 'r', color: col, poly: box(s.lon, s.lat, s.ht, g.trailer.rear_m, g.trailer.front_m, g.trailer.width_m, lat0) })
      }
      polys.push({ id: v.id + 't', color: [Math.min(255, col[0] + 20), Math.min(255, col[1] + 20), Math.min(255, col[2] + 20), 255],
        poly: box(s.lon, s.lat, s.h, g.tractor.rear_m, g.tractor.front_m, g.tractor.width_m * (g.trailer ? 0.94 : 1), lat0) })
      trucks.push({ id: v.id, type: v.type, lon: s.lon, lat: s.lat, state: s.state, door: v.door_id })
    }
    const counts = {
      street: trucks.filter((x) => x.state === 'street_queue').length,
      yard: trucks.filter((x) => x.state === 'yard_queue').length,
      door: trucks.filter((x) => ['back_in', 'docked', 'pull_out'].includes(x.state)).length,
    }
    return { trucks, polys, counts }
  }, [res, t])

  const fitsShown = useMemo(() => (res ? res.fits.filter((f) => f.vehicle === shownType) : []), [res, shownType])
  const selFit: YardFit | undefined = fitsShown.find((f) => f.door_id === door) ?? undefined

  const layers = useMemo(() => {
    if (!res) return []
    const f = res.frame
    const lat0 = f.lat0
    const kx = 1 / (111320 * Math.cos((lat0 * Math.PI) / 180)), ky = 1 / 110574
    const [ex, ey] = res.entrance.lonlat
    const sd = res.entrance.street_dir
    const qLen = live.counts.street ? 12 + 22 * live.counts.street : 0
    const doorOk = new globalThis.Map(fitsShown.map((x) => [x.door_id, x.ok]))
    return [
      new BitmapLayer({ id: 'yard-ortho', image: f.ortho_url, bounds: f.bounds, opacity: 1 }),
      res.parcel && new GeoJsonLayer({
        id: 'yard-parcel', data: { type: 'Feature', geometry: res.parcel, properties: {} } as never, stroked: true, filled: false,
        getLineColor: [251, 191, 36, 170], lineWidthUnits: 'pixels', getLineWidth: 1.5,
      }),
      new GeoJsonLayer({
        id: 'yard-swept', data: { type: 'FeatureCollection', features: fitsShown.map((x) => ({ type: 'Feature', geometry: x.swept, properties: { ok: x.ok, door: x.door_id } })) } as never,
        stroked: true, filled: true, lineWidthUnits: 'pixels', pickable: true,
        getFillColor: (d: { properties: { ok: boolean; door: string } }) => (d.properties.ok ? [74, 222, 128, door && d.properties.door !== door ? 18 : 46] : [248, 113, 113, door && d.properties.door !== door ? 22 : 60]),
        getLineColor: (d: { properties: { ok: boolean; door: string } }) => (d.properties.ok ? [74, 222, 128, door && d.properties.door !== door ? 60 : 200] : [248, 113, 113, door && d.properties.door !== door ? 70 : 230]),
        getLineWidth: (d: { properties: { door: string } }) => (d.properties.door === door ? 2.5 : 1),
        onClick: (i: { object?: { properties: { door: string } } }) => i.object && setDoor(i.object.properties.door),
        updateTriggers: { getFillColor: [door], getLineColor: [door], getLineWidth: [door] },
      }),
      selFit && new PathLayer({
        id: 'yard-path', data: [selFit.path.map((p) => [p[1], p[2]])], getPath: (d: number[][]) => d as [number, number][],
        getColor: selFit.ok ? [74, 222, 128, 255] : [248, 113, 113, 255], getWidth: 2, widthUnits: 'pixels', getDashArray: [4, 3],
      }),
      new ScatterplotLayer({
        id: 'yard-conflicts', data: (selFit ? [selFit] : fitsShown).flatMap((x) => x.conflicts.slice(0, selFit ? 12 : 2)),
        getPosition: (c: { lon: number; lat: number }) => [c.lon, c.lat], radiusUnits: 'pixels', getRadius: 6,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: [248, 113, 113, 230], getLineColor: [255, 255, 255, 255],
        pickable: true,
      }),
      live.counts.street > 0 && new PathLayer({
        id: 'yard-spill', data: [[[ex, ey], [ex + sd[0] * qLen * kx, ey + sd[1] * qLen * ky]]],
        getPath: (d: number[][]) => d as [number, number][], getColor: [248, 113, 113, 140], getWidth: 14, widthUnits: 'meters', capRounded: true,
      }),
      new ScatterplotLayer({
        id: 'yard-entrance', data: [res.entrance], getPosition: () => [ex, ey], radiusUnits: 'pixels', getRadius: 8,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: [10, 13, 18, 220], getLineColor: [226, 232, 240, 255],
      }),
      new TextLayer({
        id: 'yard-entrance-label', data: [res.entrance], getPosition: () => [ex, ey], getText: () => 'street entrance', getSize: 12,
        getColor: [226, 232, 240, 255], getPixelOffset: [0, -16], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 600,
        background: true, getBackgroundColor: [10, 13, 18, 200], backgroundPadding: [4, 2, 4, 2],
      }),
      new PolygonLayer({
        id: 'yard-trucks', data: live.polys, getPolygon: (d: { poly: [number, number][] }) => d.poly,
        getFillColor: (d: { color: RGBA }) => d.color, getLineColor: [10, 13, 18, 255], lineWidthUnits: 'pixels', getLineWidth: 1,
        stroked: true, filled: true,
      }),
      new ScatterplotLayer({
        id: 'yard-doors', data: res.doors, pickable: true, getPosition: (d: { lonlat: [number, number] }) => d.lonlat,
        radiusUnits: 'pixels', getRadius: (d: { id: string }) => (d.id === door ? 7 : 5), stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1.5,
        getFillColor: (d: { id: string }) => (doorOk.get(d.id) ? [74, 222, 128, 255] : [248, 113, 113, 255]),
        getLineColor: res.door_source === 'assumed' ? [251, 191, 36, 255] : [10, 13, 18, 255],
        onClick: (i: { object?: { id: string } }) => i.object && setDoor(i.object.id === door ? null : i.object.id),
        updateTriggers: { getFillColor: [shownType], getRadius: [door] },
      }),
      new TextLayer({
        id: 'yard-door-labels', data: res.doors.filter((d, i) => d.id === door || (!door && (i === 0 || i === res.doors.length - 1))), getPosition: (d: { lonlat: [number, number] }) => d.lonlat,
        getText: (d: { id: string }) => (res.door_source === 'assumed' ? `${d.id}?` : d.id), getSize: 10, getColor: [232, 236, 242, 230],
        getPixelOffset: [0, 12], fontFamily: 'JetBrains Mono, monospace', background: true, getBackgroundColor: [10, 13, 18, 170],
        backgroundPadding: [2, 1, 2, 1],
      }),
    ]
  }, [res, fitsShown, selFit, door, live, shownType])

  const tl = res?.queue.timeline ?? []
  const peak = Math.max(1, ...tl.map((r) => r[1] + r[2]))
  const q = res?.queue
  const lon = res?.frame.lon0 ?? center?.[0]
  const lat = res?.frame.lat0 ?? center?.[1]

  return (
    <div className="absolute inset-0 overflow-hidden bg-bg" data-testid="yard-view">
      {lon != null && lat != null && (
        <Map key={`${lon},${lat}`} initialViewState={{ longitude: lon, latitude: lat, zoom: 17.6, pitch: 0, bearing: 0 }}
          mapStyle={STYLE} maxZoom={21} attributionControl={false} style={{ position: 'absolute', inset: 0 }}>
          <DeckOverlay layers={layers} getTooltip={(i: { object?: unknown; layer?: { id: string } | null }) => {
            const o = i.object as Record<string, unknown> | undefined
            if (!o || !i.layer) return null
            if (i.layer.id === 'yard-conflicts') return { text: `${o.what} (${o.intrusion_m} m into the buffer)` }
            if (i.layer.id === 'yard-doors') return { text: `Door ${o.id}: ${o.source}` }
            return null
          }} />
        </Map>
      )}

      {/* left panel */}
      <div className="glass scroll-thin absolute left-3 top-3 bottom-24 z-10 flex w-[340px] flex-col overflow-y-auto rounded-xl p-4">
        <div className="mb-3 flex items-start justify-between">
          <div>
            <div className="text-[15px] font-semibold text-ink">Yard test</div>
            <div className="text-[12px] text-ink-3">Can your trucks turn, back in and queue on this site?</div>
          </div>
          {onClose && <button type="button" aria-label="Close yard test" onClick={onClose} className="rounded-md p-1 text-ink-3 hover:bg-panel-3 hover:text-ink"><X size={16} /></button>}
        </div>

        {!res && <YardForm compact propertyId={propertyId} onResult={(r) => setRes(r)} />}

        {res && q && (
          <>
            <div className="space-y-1.5">
              {res.summary.map((s) => {
                const ok = s.doors_ok > 0
                return (
                  <button type="button" key={s.type} onClick={() => { setVtype(s.type); setDoor(null) }}
                    className={`flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left ${s.type === shownType ? 'border-line-2 bg-panel-2' : 'border-transparent hover:bg-panel-2/60'}`}>
                    {ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-good" /> : <XCircle size={16} className="mt-0.5 shrink-0 text-bad" />}
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium text-ink">{TRUCK_TYPES.find((x) => x.key === s.type)?.label ?? s.name}</div>
                      <div className={`text-[12px] ${ok ? 'text-good' : 'text-bad'}`}>{s.verdict}</div>
                      <div className="text-[11.5px] text-ink-3">needs ≥ {s.depth_needed_ft} ft of clear court at the best door</div>
                    </div>
                  </button>
                )
              })}
            </div>

            {selFit && (
              <div className="mt-2 rounded-lg border border-line bg-panel-2 px-2.5 py-2 text-[12px] text-ink-2">
                <div className="font-medium text-ink">Door {selFit.door_id}: <span className={marginClass(selFit.margin_m, selFit.geometric_ok)}>{marginText(selFit.margin_m, selFit.geometric_ok)}</span> <span className="font-normal text-ink-3">(beyond the 2 ft buffer)</span></div>
                <div>back-in maneuver {Math.round(selFit.maneuver_s)} s · {selFit.neighbors_parked} neighbouring door{selFit.neighbors_parked === 1 ? '' : 's'} occupied</div>
                {selFit.conflicts.slice(0, 3).map((c, i) => <div key={i} className="text-bad">hits {c.what} by {c.intrusion_m} m</div>)}
                {!selFit.reachable_from_gate && <div className="text-warn">no drivable route from the entrance found</div>}
                {selFit.crosses_parcel_line && <div className="text-warn">the swing crosses the parcel line</div>}
              </div>
            )}

            <div className="mt-4 grid grid-cols-2 gap-2">
              <Kpi label="Wait for a door" value={q.wait_p50_min == null ? '—' : `${q.wait_p50_min.toFixed(0)} min`} sub={q.wait_p90_min == null ? 'no truck served' : `P90 ${q.wait_p90_min.toFixed(0)} min`} />
              <Kpi label="Door utilization" value={`${Math.round(q.door_utilization * 100)}%`} sub={`${res.doors.length} doors`} />
              <Kpi label="Max waiting in yard" value={String(q.max_in_yard)} sub={`P90 ${q.max_in_yard_p90} · room ≈ ${Math.round(q.staging_semi_equivalents)}`} />
              <Kpi label="Max on the street" value={String(q.max_on_street)} sub={q.runs_with_street_spill ? `spills in ${q.runs_with_street_spill}/${q.runs} days` : `none in ${q.runs} days`} bad={q.max_on_street > 0} />
            </div>
            {q.unserved_per_day > 0 && (
              <div className="mt-2 flex gap-1.5 rounded-md border border-bad/40 bg-bad/10 px-2 py-1.5 text-[12px] text-bad">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />{q.unserved_per_day} trucks/day have no door they can back into.
              </div>
            )}

            <div className="mt-4">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-3">Trucks waiting (representative day)</div>
              <svg viewBox={`0 0 300 44`} className="h-11 w-full" preserveAspectRatio="none" aria-label="queue over the day">
                {tl.map((r, i) => {
                  const x = ((r[0] - t0) / Math.max(1, t1 - t0)) * 300
                  return <g key={i}>
                    <rect x={x} y={44 - (r[1] / peak) * 40} width={300 / Math.max(1, tl.length) + 0.6} height={(r[1] / peak) * 40} fill="#fbbf24" opacity={0.8} />
                    <rect x={x} y={44 - ((r[1] + r[2]) / peak) * 40} width={300 / Math.max(1, tl.length) + 0.6} height={(r[2] / peak) * 40} fill="#f87171" />
                  </g>
                })}
                {peak <= 1 && !tl.some((r) => r[1] + r[2] > 0) && <text x={150} y={26} textAnchor="middle" fontSize={11} fill="#6b7587">no truck waits for a door all day</text>}
                <line x1={((t - t0) / Math.max(1, t1 - t0)) * 300} x2={((t - t0) / Math.max(1, t1 - t0)) * 300} y1={0} y2={44} stroke="#e8ecf2" strokeWidth={1} />
              </svg>
            </div>

            <div className="mt-4 space-y-1 text-[12px] text-ink-2">
              <div className="text-[11px] uppercase tracking-wide text-ink-3">Legend</div>
              <Legend color="#4ade80" label="swept path fits (truck + 0.6 m buffer)" />
              <Legend color="#f87171" label="swept path conflicts; dots = where it hits" />
              <Legend color="#fbbf24" label="truck waiting in the yard" />
              <Legend color="#f87171" label="truck waiting on the street (spill-back)" solid />
              <Legend color="#4fd1c5" label="backing in / at the door" solid />
              <div className={`mt-1.5 rounded-md px-2 py-1.5 ${res.door_source === 'assumed' ? 'border border-warn/40 bg-warn/10 text-warn' : 'bg-panel-2 text-ink-2'}`}>
                {DOOR_SOURCE_LABEL[res.door_source] ?? res.door_source}{res.door_source === 'assumed' ? ': mark the real doors in the 3D scan to make this exact.' : ''}
              </div>
              <div className="text-ink-3">Street entrance: {res.entrance.method.replace(/^street entrance:?\s*/i, '')}</div>
            </div>

            <details className="mt-3 text-[12px] text-ink-2">
              <summary className="flex cursor-pointer items-center gap-1 text-ink-3"><ChevronDown size={12} />Assumptions ({res.assumptions.length})</summary>
              <ul className="mt-1 list-disc space-y-1 pl-4">{res.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
              <div className="mt-1 text-ink-3">{q.staging_note}</div>
            </details>

            <div className="mt-3 border-t border-line pt-3">
              <button type="button" onClick={() => setShowForm((x) => !x)} className="text-[12.5px] font-medium text-accent">{showForm ? 'Hide' : 'Change trucks or doors'}</button>
              {showForm && <div className="mt-2"><YardForm compact propertyId={propertyId} doors={res.doors} onResult={(r) => { setRes(r); setShowForm(false); setDoor(null) }} /></div>}
            </div>
          </>
        )}
      </div>

      {/* live counter */}
      {res && (
        <div className="glass absolute right-3 top-3 z-10 rounded-xl px-4 py-3">
          <div className="font-mono text-[22px] font-semibold tabular-nums text-ink">{clock(t)}</div>
          <div className="mt-1 grid grid-cols-3 gap-3 text-center">
            <Counter n={live.counts.door} label="at doors" color="text-accent" />
            <Counter n={live.counts.yard} label="in yard" color="text-warn" />
            <Counter n={live.counts.street} label="on street" color={live.counts.street ? 'text-bad' : 'text-ink-3'} />
          </div>
          {live.counts.street > 0 && <div className="mt-1.5 rounded bg-bad/15 px-2 py-0.5 text-center text-[11.5px] font-medium text-bad">Queue spills onto the street</div>}
        </div>
      )}

      {/* time scrubber */}
      {res && (
        <div className="glass absolute bottom-3 left-3 right-3 z-10 flex items-center gap-3 rounded-xl px-4 py-2.5">
          <button type="button" aria-label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying((p) => !p)}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-bg">{playing ? <Pause size={15} /> : <Play size={15} />}</button>
          <span className="w-12 font-mono text-[13px] tabular-nums text-ink">{clock(t)}</span>
          <input aria-label="time" type="range" min={t0} max={t1} step={10} value={Math.min(t1, Math.max(t0, t))}
            onChange={(e) => setT(Number(e.target.value))} className="flex-1 accent-[#4fd1c5]" />
          <div className="flex overflow-hidden rounded-md border border-line">
            {SPEEDS.map((s) => (
              <button type="button" key={s} onClick={() => setSpeed(s)}
                className={`px-2 py-1 font-mono text-[11.5px] ${s === speed ? 'bg-accent/20 text-accent' : 'text-ink-3 hover:text-ink'}`}>{s}×</button>
            ))}
          </div>
          <span className="hidden text-[11px] text-ink-3 md:block">Imagery © Vexcel · Esri</span>
        </div>
      )}
    </div>
  )
}

function Kpi({ label, value, sub, bad }: { label: string; value: string; sub?: string; bad?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-panel-2 px-2.5 py-2">
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className={`text-[18px] font-semibold tabular-nums ${bad ? 'text-bad' : 'text-ink'}`}>{value}</div>
      {sub && <div className="text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}
function Counter({ n, label, color }: { n: number; label: string; color: string }) {
  return <div><div className={`text-[18px] font-semibold tabular-nums ${color}`}>{n}</div><div className="text-[11px] text-ink-3">{label}</div></div>
}
function Legend({ color, label, solid }: { color: string; label: string; solid?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: solid ? color : `${color}55`, border: `1.5px solid ${color}` }} />
      <span>{label}</span>
    </div>
  )
}
