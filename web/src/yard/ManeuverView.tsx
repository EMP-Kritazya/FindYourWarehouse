// Yard maneuver player (plan B5): N trucks on the real site at once, replayed from the planner's kinematic keys.
// Planned path per truck (forward solid, reverse dashed), tractor and trailer drawn separately from their own headings,
// door markers by kind/status, the gate, issue pins coloured by class. Controls: Start/Pause, Reset, Rerun, 1-8x, scrubber.
import { useEffect, useMemo, useRef, useState } from 'react'
import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import clsx from 'clsx'
import { Loader2, Pause, Play, RefreshCw, RotateCcw, X } from 'lucide-react'
import { SiteMap, dashed, lonLatBounds } from './SiteMap'
import {
  ISSUE_STYLE, PHASE_LABEL, boxPoly, issueRows, isManeuver, samplePose, truckColor, rgb, useYardUi,
  type ManDoor, type ManeuverResult,
} from './maneuver'
import { docksApi, usableDoors } from '../lib/docksApi'

const SPEEDS = [1, 2, 4, 8]
const KIND_COLOR: Record<string, [number, number, number]> = { dock_high: [79, 209, 197], drive_in: [251, 191, 36], unknown: [148, 163, 184] }
const mmss = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.floor(Math.max(0, s) % 60)).padStart(2, '0')}`

interface Props { propertyId: number; result?: unknown; onRerun?: () => void }

export default function ManeuverView({ propertyId, result, onRerun }: Props) {
  const res: ManeuverResult | null = isManeuver(result) ? result : null
  const running = useYardUi((s) => s.running)
  const selIssue = useYardUi((s) => s.selectedIssue)
  const selTruck = useYardUi((s) => s.selectedTruck)
  const selectIssue = useYardUi((s) => s.selectIssue)
  const selectTruck = useYardUi((s) => s.selectTruck)

  // without a result yet: show the site with the store's doors and gate
  const [preview, setPreview] = useState<{ doors: ManDoor[]; gate: [number, number] | null; center: [number, number] } | null>(null)
  useEffect(() => {
    if (res) return
    let live = true
    docksApi.get(propertyId).then((s) => {
      if (!live) return
      setPreview({
        doors: usableDoors(s).map((d) => ({ id: d.id, lon: d.lon, lat: d.lat, heading_deg: 0, kind: d.kind, status: d.status, source: d.status === 'confirmed' || d.status === 'user_added' ? 'confirmed' : 'proposed' })),
        gate: s.gate ? [s.gate.lon, s.gate.lat] : null, center: [s.frame.lon0, s.frame.lat0],
      })
    }).catch(() => fetch(`/api/warehouses/${propertyId}`).then((r) => (r.ok ? r.json() : null)).then((w) => live && w && setPreview({ doors: [], gate: null, center: [w.lon, w.lat] })).catch(() => {}))
    return () => { live = false }
  }, [propertyId, res])

  const [t0, t1] = useMemo(() => {
    if (!res?.vehicles.length) return [0, 0]
    let a = Infinity, b = -Infinity
    for (const v of res.vehicles) if (v.keys.length) { a = Math.min(a, v.keys[0][0]); b = Math.max(b, v.keys[v.keys.length - 1][0]) }
    return Number.isFinite(a) ? [a, b] : [0, 0]
  }, [res])
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(2)
  const started = useRef(false)
  // a new result starts playing from the beginning
  useEffect(() => { setT(t0); started.current = false; setPlaying(!!res); if (res) started.current = true }, [res?.run_id, t0])
  const raf = useRef(0)
  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000); last = now
      setT((x) => { const nx = x + dt * speed; if (nx >= t1) { setPlaying(false); return t1 } return nx })
      raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [playing, speed, t1])
  // dev hook so screenshot scripts can scrub deterministically
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const w = window as unknown as Record<string, unknown>
    w.__yardPlayer = { seek: (x: number) => { setPlaying(false); setT(x) }, range: [t0, t1] }
  }, [t0, t1])

  const live = useMemo(() => {
    const polys: { poly: [number, number][]; fill: [number, number, number, number]; id: string; vid: string }[] = []
    const labels: { pos: [number, number]; text: string; vid: string }[] = []
    let moving = 0, docked = 0, waiting = 0, done = 0
    if (!res) return { polys, labels, moving, docked, waiting, done }
    const lat0 = res.frame.lat0
    res.vehicles.forEach((v) => {
      const last = v.keys[v.keys.length - 1]
      if (last && t > last[0]) { done++; return }
      const p = samplePose(v.keys, t)
      if (!p) return
      const g = res.vehicle_geometry[v.type]
      const c = truckColor(v.color_idx)
      const dim = selTruck && selTruck !== v.id ? 0.45 : 1
      if (g?.trailer) polys.push({ id: v.id + ':r', vid: v.id, fill: [c[0], c[1], c[2], Math.round(150 * dim)], poly: boxPoly(p.lon, p.lat, p.ht, g.trailer.rear_m, g.trailer.front_m, g.trailer.width_m, lat0) })
      const tr = g?.tractor ?? { rear_m: -1, front_m: 5, width_m: 2.4 }
      polys.push({ id: v.id + ':t', vid: v.id, fill: [Math.min(255, c[0] + 40), Math.min(255, c[1] + 40), Math.min(255, c[2] + 40), Math.round(250 * dim)],
        poly: boxPoly(p.lon, p.lat, p.h, tr.rear_m, tr.front_m, tr.width_m * (g?.trailer ? 0.94 : 1), lat0) })
      labels.push({ pos: [p.lon, p.lat], text: v.id, vid: v.id })
      if (p.phase === 'docked') docked++
      else if (p.phase === 'wait' || p.phase === 'staging' || p.gear === 0) waiting++
      else moving++
    })
    return { polys, labels, moving, docked, waiting, done }
  }, [res, t, selTruck])

  const paths = useMemo(() => {
    const fwd: { path: [number, number][]; vid: string; c: [number, number, number] }[] = []
    const rev: { path: [number, number][]; vid: string; c: [number, number, number] }[] = []
    if (!res) return { fwd, rev }
    for (const v of res.vehicles) {
      const c = truckColor(v.color_idx)
      let run: [number, number][] = [], g = 0
      const flush = () => {
        if (run.length > 1) { if (g < 0) dashed(run, 1.6, 1.4).forEach((p) => rev.push({ path: p, vid: v.id, c })); else fwd.push({ path: run, vid: v.id, c }) }
      }
      for (const [lon, lat, gear] of v.path) {
        const gg = gear === 0 ? g : gear
        if (gg !== g && run.length) { const lastPt = run[run.length - 1]; flush(); run = [lastPt] }
        g = gg; run.push([lon, lat])
      }
      flush()
    }
    return { fwd, rev }
  }, [res])

  const doors: ManDoor[] = res?.doors_used ?? preview?.doors ?? []
  const gate: [number, number] | null = res?.gate ? [res.gate.lon, res.gate.lat] : preview?.gate ?? null
  const issues = res?.issues ?? []
  // failed trucks: a ghost at their closest reached pose (or the reported point), with a red ✕ and the specific reason
  const failedTrucks = useMemo(() => (res?.vehicles ?? []).filter((v) => v.status === 'failed').map((v, k) => {
    const own = issues.find((x) => x.trucks?.includes(v.id) && x.where)
    const pos = (v.fail?.pose ? [v.fail.pose[0], v.fail.pose[1]] : v.fail?.where ?? own?.where ?? (res?.gate ? [res.gate.lon, res.gate.lat] : null)) as [number, number] | null
    return { v, pos, k, exact: !!(v.fail?.pose || v.fail?.where) }
  }).filter((x): x is { v: typeof x.v; pos: [number, number]; k: number; exact: boolean } => !!x.pos).map((x, j, all) => {
    // several failures reported at one point (the gate): fan them out 7 m apart so each ✕ stays clickable
    const same = all.filter((y) => !y.exact && y.pos[0] === x.pos[0] && y.pos[1] === x.pos[1])
    const idx = same.indexOf(x)
    if (x.exact || same.length < 2 || !res) return x
    const kx = 111320 * Math.cos((res.frame.lat0 * Math.PI) / 180)
    return { ...x, pos: [x.pos[0] + ((idx - (same.length - 1) / 2) * 7) / kx, x.pos[1]] as [number, number] }
  }), [res, issues])
  const failedIds = useMemo(() => new Set(failedTrucks.map((f) => f.v.id)), [failedTrucks])
  // issue pins: truck failures are already shown as ✕ pins
  const pinIssues = useMemo(() => issueRows(issues).filter((x) => x.where && !(x.trucks?.length && x.trucks.every((t) => failedIds.has(t)))), [issues, failedIds])
  const activeEvents = useMemo(() => (res?.events ?? []).filter((e) => e.t_s <= t && t - e.t_s < 6 && (e.kind === 'wait' || e.kind === 'conflict')), [res, t])

  const bounds = useMemo(() => {
    const pts: [number, number][] = []
    res?.vehicles.forEach((v) => v.path.forEach((p) => pts.push([p[0], p[1]])))
    doors.forEach((d) => pts.push([d.lon, d.lat]))
    if (gate) pts.push(gate)
    return lonLatBounds(pts, 12)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [res?.run_id, preview])

  const layers = [
    new PathLayer({
      id: 'man-fwd', data: paths.fwd, getPath: (d: { path: [number, number][] }) => d.path, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      getColor: (d: { vid: string; c: [number, number, number] }) => [...d.c, selTruck ? (d.vid === selTruck ? 235 : 45) : 130] as [number, number, number, number],
      getWidth: (d: { vid: string }) => (d.vid === selTruck ? 3.5 : 2), updateTriggers: { getColor: [selTruck], getWidth: [selTruck] },
    }),
    new PathLayer({
      id: 'man-rev', data: paths.rev, getPath: (d: { path: [number, number][] }) => d.path, widthUnits: 'pixels',
      getColor: (d: { vid: string; c: [number, number, number] }) => [...d.c, selTruck ? (d.vid === selTruck ? 255 : 55) : 190] as [number, number, number, number],
      getWidth: (d: { vid: string }) => (d.vid === selTruck ? 3.5 : 2.2), updateTriggers: { getColor: [selTruck], getWidth: [selTruck] },
    }),
    new ScatterplotLayer({
      id: 'man-doors', data: doors, getPosition: (d: ManDoor) => [d.lon, d.lat], radiusUnits: 'pixels', getRadius: 4.5,
      stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1.5, pickable: true,
      getFillColor: (d: ManDoor) => (d.source === 'confirmed' ? [...(KIND_COLOR[d.kind] ?? KIND_COLOR.unknown), 255] : [10, 13, 18, 200]) as [number, number, number, number],
      getLineColor: (d: ManDoor) => [...(KIND_COLOR[d.kind] ?? KIND_COLOR.unknown), 255] as [number, number, number, number],
    }),
    gate && new ScatterplotLayer({
      id: 'man-gate', data: [gate], getPosition: (d: [number, number]) => d, radiusUnits: 'pixels', getRadius: 8,
      stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: [10, 13, 18, 230], getLineColor: [226, 232, 240, 255],
    }),
    gate && new TextLayer({
      id: 'man-gate-label', data: [gate], getPosition: (d: [number, number]) => d, getText: () => { const src = res?.gate?.source ?? 'inferred'; return `gate${src === 'user' ? '' : src === 'osm' ? ' (OpenStreetMap driveway)' : ' (inferred)'}` },
      getSize: 11, getColor: [226, 232, 240, 255], getPixelOffset: [0, -17], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 600,
      background: true, getBackgroundColor: [10, 13, 18, 210], backgroundPadding: [4, 2, 4, 2],
    }),
    new ScatterplotLayer({
      id: 'man-events', data: activeEvents, getPosition: (e: { where: [number, number] }) => e.where, radiusUnits: 'pixels',
      getRadius: (e: { t_s: number }) => 10 + (t - e.t_s) * 4, stroked: true, filled: false, lineWidthUnits: 'pixels', getLineWidth: 2,
      getLineColor: (e: { t_s: number }) => [251, 146, 60, Math.max(0, 230 - (t - e.t_s) * 38)], updateTriggers: { getRadius: [t], getLineColor: [t] },
    }),
    new PolygonLayer({
      id: 'man-trucks', data: live.polys, getPolygon: (d: { poly: [number, number][] }) => d.poly, pickable: true,
      getFillColor: (d: { fill: [number, number, number, number] }) => d.fill, getLineColor: [10, 13, 18, 255], lineWidthUnits: 'pixels', getLineWidth: 1,
      stroked: true, filled: true, onClick: (i: { object?: { vid: string } }) => { if (i.object) selectTruck(i.object.vid === selTruck ? null : i.object.vid) },
    }),
    new PolygonLayer({
      id: 'man-ghosts', data: failedTrucks.filter((f) => f.v.fail?.pose).flatMap((f) => {
        const g = res!.vehicle_geometry[f.v.type], P = f.v.fail!.pose!, lat0 = res!.frame.lat0
        const out = [{ poly: boxPoly(P[0], P[1], P[2], g.tractor.rear_m, g.tractor.front_m, g.tractor.width_m, lat0), vid: f.v.id }]
        if (g.trailer) out.push({ poly: boxPoly(P[0], P[1], P[3], g.trailer.rear_m, g.trailer.front_m, g.trailer.width_m, lat0), vid: f.v.id })
        return out
      }),
      getPolygon: (d: { poly: [number, number][] }) => d.poly, filled: true, stroked: true, getFillColor: [248, 113, 113, 45], getLineColor: [248, 113, 113, 200],
      lineWidthUnits: 'pixels', getLineWidth: 1.2,
    }),
    new ScatterplotLayer({
      // spread failures that share one point (e.g. the gate) so each ✕ stays clickable
      id: 'man-fail', data: failedTrucks, pickable: true, getPosition: (f: { pos: [number, number] }) => f.pos,
      radiusUnits: 'pixels', getRadius: (f: { v: { id: string } }) => (f.v.id === selTruck ? 11 : 9),
      stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1.5, getFillColor: [248, 113, 113, 245], getLineColor: [10, 13, 18, 255],
      onClick: (i: { object?: { v: { id: string } } }) => { if (i.object) selectTruck(i.object.v.id === selTruck ? null : i.object.v.id) },
      updateTriggers: { getRadius: [selTruck] },
    }),
    new TextLayer({
      id: 'man-fail-x', data: failedTrucks, getPosition: (f: { pos: [number, number] }) => f.pos, getText: () => '✕', getSize: 11,
      getColor: [10, 13, 18, 255], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 800, characterSet: ['✕'],
    }),
    new TextLayer({
      id: 'man-fail-label', data: failedTrucks, getPosition: (f: { pos: [number, number] }) => f.pos, getText: (f: { v: { id: string } }) => f.v.id,
      getSize: 10, getColor: [254, 202, 202, 255], getPixelOffset: [0, 17],
      fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, background: true, getBackgroundColor: [10, 13, 18, 200], backgroundPadding: [3, 1, 3, 1],
    }),
    new TextLayer({
      id: 'man-truck-labels', data: live.labels, getPosition: (d: { pos: [number, number] }) => d.pos, getText: (d: { text: string }) => d.text,
      getSize: 10, getColor: [232, 236, 242, 255], getPixelOffset: [0, -14], fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,
      background: true, getBackgroundColor: [10, 13, 18, 180], backgroundPadding: [3, 1, 3, 1],
    }),
    new ScatterplotLayer({
      id: 'man-issues', data: pinIssues, pickable: true,
      getPosition: (x: { where: [number, number] }) => x.where, radiusUnits: 'pixels', getRadius: (x: { i: number }) => (x.i === selIssue ? 11 : 8.5),
      stroked: true, lineWidthUnits: 'pixels', getLineWidth: (x: { i: number }) => (x.i === selIssue ? 2.5 : 1.5),
      getFillColor: (x: { class: keyof typeof ISSUE_STYLE }) => [...(ISSUE_STYLE[x.class]?.color ?? [200, 200, 200]), 240] as [number, number, number, number],
      getLineColor: [10, 13, 18, 255], onClick: (i: { object?: { i: number } }) => { if (i.object) selectIssue(i.object.i === selIssue ? null : i.object.i) },
      updateTriggers: { getRadius: [selIssue], getLineWidth: [selIssue] },
    }),
    new TextLayer({
      id: 'man-issue-num', data: pinIssues, getPosition: (x: { where: [number, number] }) => x.where,
      getText: (x: { n: number }) => String(x.n), getSize: 10.5, getColor: [10, 13, 18, 255], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 700,
    }),
  ]

  const center: [number, number] | undefined = res ? [res.frame.lon0, res.frame.lat0] : preview?.center
  const sel = selIssue != null ? issues[selIssue] : null
  const selT = selTruck ? res?.vehicles.find((v) => v.id === selTruck) : null
  const selPose = selT ? samplePose(selT.keys, t) : null
  const atEnd = res != null && t >= t1 - 1e-6

  return (
    <div className="absolute inset-0 overflow-hidden bg-bg" data-testid="maneuver-view">
      <SiteMap bounds={bounds} center={center} zoom={17.8}
        overlay={{ layers, getTooltip: (i: { object?: unknown; layer?: { id: string } | null }) => {
          const o = i.object as Record<string, unknown> | undefined
          if (!o || !i.layer) return null
          if (i.layer.id === 'man-doors') return { text: `Door ${o.id} · ${String(o.kind).replace('_', '-')} · ${o.source}` }
          if (i.layer.id === 'man-issues') return { text: String(o.text) }
          if (i.layer.id === 'man-trucks') return { text: `${o.vid}` }
          if (i.layer.id === 'man-fail') { const v = (o as { v: { id: string; fail?: { text: string } } }).v; return { text: `${v.id} not completed: ${v.fail?.text ?? ''}` } }
          return null
        } }} />

      {/* event ticker + selected issue / truck (top-left, under the close button) */}
      <div className="pointer-events-none absolute left-3 top-14 z-10 flex w-[300px] flex-col gap-1.5">
        {sel && (
          <div className="glass pointer-events-auto rounded-xl p-3 fade-up" data-testid="issue-pop">
            <div className="flex items-center gap-2">
              <span className={clsx('inline-flex h-[18px] items-center rounded px-1.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset', ISSUE_STYLE[sel.class]?.cls)}>{ISSUE_STYLE[sel.class]?.label ?? sel.class}</span>
              <span className="text-[11px] text-ink-3">#{pinIssues.find((x) => x.i === selIssue)?.n ?? issueRows(issues).find((x) => x.i === selIssue)?.n} · {sel.severity}</span>
              <button onClick={() => selectIssue(null)} className="ml-auto rounded p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink" aria-label="Close"><X size={13} /></button>
            </div>
            <div className="mt-1.5 text-[12.5px] leading-snug text-ink">{sel.text}</div>
            <div className="mt-1 text-[11px] leading-snug text-ink-3">{ISSUE_STYLE[sel.class]?.explain}</div>
          </div>
        )}
        {selT && (
          <div className="glass pointer-events-auto flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-[12px]" data-testid="truck-pop">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: rgb(truckColor(selT.color_idx)) }} />
            <span className="font-mono font-semibold">{selT.id}</span>
            <span className="text-ink-2">{selT.target?.kind === 'door' ? `door ${selT.target.id}` : selT.target?.kind ?? ''}</span>
            <span className="text-ink-3">{selPose ? PHASE_LABEL[selPose.phase] ?? selPose.phase : selT.status === 'failed' ? 'not completed' : selT.status}</span>
            <button onClick={() => selectTruck(null)} className="ml-auto rounded p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink" aria-label="Clear"><X size={12} /></button>
            {selT.status === 'failed' && selT.fail?.text && (
              <div className="basis-full text-[12px] leading-snug text-ink-2"><span className="mr-1 font-semibold text-bad">✕</span>{selT.fail.text.charAt(0).toUpperCase() + selT.fail.text.slice(1)}
                {!failedTrucks.find((f) => f.v.id === selT.id)?.exact && <span className="text-ink-3"> (pinned at the gate: the planner did not report where it got stuck)</span>}</div>
            )}
          </div>
        )}
        {activeEvents.slice(-2).map((e, i) => (
          <div key={`${e.t_s}-${i}`} className="glass rounded-lg px-2.5 py-1.5 text-[11.5px] leading-snug text-orange-200 fade-up">{e.text}</div>
        ))}
      </div>

      {/* live counters */}
      {res && t1 > t0 && (
        <div className="glass absolute right-3 top-3 z-10 rounded-xl px-3.5 py-2.5" data-testid="maneuver-clock">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[20px] font-semibold tabular-nums">{mmss(t - t0)}</span>
            <span className="text-[11px] text-ink-3">of {mmss(t1 - t0)}</span>
          </div>
          <div className="mt-1 grid grid-cols-4 gap-2.5 text-center">
            <Counter n={live.moving} label="moving" cls="text-ink" />
            <Counter n={live.docked} label="docked" cls="text-accent" />
            <Counter n={live.waiting} label="waiting" cls={live.waiting ? 'text-warn' : 'text-ink-3'} />
            <Counter n={live.done} label="done" cls="text-ink-3" />
          </div>
        </div>
      )}

      {/* legend */}
      {res && (
        <div className="glass absolute bottom-[62px] right-3 z-10 space-y-1 rounded-xl px-3 py-2 text-[11px] text-ink-2">
          <div className="flex items-center gap-2"><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--color-ink-2)" strokeWidth="2" /></svg>forward</div>
          <div className="flex items-center gap-2"><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--color-ink-2)" strokeWidth="2" strokeDasharray="4 3" /></svg>reverse (back-in)</div>
          <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-accent" />dock-high · <span className="h-2.5 w-2.5 rounded-full bg-warn" />drive-in · <span className="h-2.5 w-2.5 rounded-full bg-ink-3" />?</div>
          <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-accent" />proposed door · filled = confirmed</div>
        </div>
      )}

      {res?._fixture && (
        <div className="absolute bottom-[62px] left-3 z-10 rounded-md bg-bg/80 px-2 py-1 text-[11px] font-medium text-warn ring-1 ring-inset ring-warn/30">
          Dev fixture: sample data until /api/yard/maneuver is live
        </div>
      )}

      {/* transport */}
      <div className="glass absolute bottom-3 left-3 right-3 z-10 flex items-center gap-2.5 rounded-xl px-3 py-2" data-testid="maneuver-controls">
        {res && !res.vehicles.some((v) => v.keys.length) ? (
          <div className="flex h-8 items-center gap-2 text-[12px] text-ink-2">
            <span className="h-2 w-2 rounded-full bg-violet-400" />No truck could be planned on this run. The pins show why; see the issue list in the Test tab.
            {onRerun && <button onClick={onRerun} disabled={running} className="ml-2 flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/10"><RefreshCw size={12} /> Rerun</button>}
          </div>
        ) : !res ? (
          <div className="flex h-8 items-center gap-2 text-[12px] text-ink-2">
            {running ? <><Loader2 size={14} className="animate-spin text-accent" />Planning every truck's moves…</> : 'Pick your trucks in the Test tab and run the yard test.'}
          </div>
        ) : (
          <>
            <button type="button" onClick={() => { if (atEnd) setT(t0); setPlaying((p) => !p) }} aria-label={playing ? 'Pause' : 'Start'}
              className="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12px] font-semibold text-bg hover:brightness-110">
              {playing ? <><Pause size={14} />Pause</> : <><Play size={14} />{t > t0 && !atEnd ? 'Resume' : 'Start'}</>}
            </button>
            <IconBtn title="Reset to the start" onClick={() => { setPlaying(false); setT(t0) }}><RotateCcw size={14} /></IconBtn>
            <IconBtn title="Rerun the test (picks up saved doors)" onClick={() => onRerun?.()} disabled={!onRerun || running}>
              {running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </IconBtn>
            <span className="w-10 text-right font-mono text-[12px] tabular-nums text-ink">{mmss(t - t0)}</span>
            <input aria-label="time" type="range" min={t0} max={t1} step={0.2} value={Math.min(t1, Math.max(t0, t))}
              onChange={(e) => setT(Number(e.target.value))} className="min-w-0 flex-1 accent-[#4fd1c5]" />
            <div className="flex overflow-hidden rounded-lg ring-1 ring-inset ring-line">
              {SPEEDS.map((s) => (
                <button type="button" key={s} onClick={() => setSpeed(s)}
                  className={clsx('px-2 py-1 font-mono text-[11px]', s === speed ? 'bg-accent/20 text-accent' : 'text-ink-3 hover:text-ink')}>{s}×</button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Counter({ n, label, cls }: { n: number; label: string; cls: string }) {
  return <div><div className={clsx('text-[16px] font-semibold tabular-nums leading-tight', cls)}>{n}</div><div className="text-[11px] text-ink-3">{label}</div></div>
}
function IconBtn({ children, title, onClick, disabled }: { children: React.ReactNode; title: string; onClick?: () => void; disabled?: boolean }) {
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} disabled={disabled}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 ring-1 ring-inset ring-line hover:bg-white/[0.06] hover:text-ink disabled:opacity-40">
      {children}
    </button>
  )
}
