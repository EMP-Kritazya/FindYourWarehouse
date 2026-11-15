// Dock editor map (plan B1, user correction surface): aerial + Vexcel footprint, doors snapped to footprint edges.
// Explicit tools (keys 1/2/3): Select (default) / Add door / Move gate. In Select a click selects; a door moves only
// once selected, by its centre handle, after a 6 px threshold, and moving never changes its status. Add places exactly
// one door on the hovered wall, then returns to Select. Empty-space drags pan; nothing rotates, double-click does nothing.
// Hit-testing is done in screen space against the DRAWN rectangles (+10 px), on MapLibre's own mouse events.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { DoorOpen, MapPin, MousePointer2 } from 'lucide-react'
import type { MapLayerMouseEvent, MapRef } from '@vis.gl/react-maplibre'
import { BitmapLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import { SiteMap, dashed, lonLatBounds } from '../yard/SiteMap'
import { enuToLonLat, lonLatToEnu, type DockDoor, type DockStore } from '../lib/docksApi'
import { loadTwin } from '../components/WarehouseCard'
import { edgeKey, findEdge, projectOnEdge, useDockEd, type DockTool, type EdgeKey } from './state'

type RGB = [number, number, number]
export const DOOR_KIND_RGB: Record<string, RGB> = { dock_high: [79, 209, 197], drive_in: [251, 191, 36], unknown: [148, 163, 184] }
const FT = 0.3048
const DOOR_DEPTH_M = 3.0

/** door rectangle on the facade: width along the edge, depth outward along the normal (lon/lat ring) */
export function doorRing(store: { frame: DockStore['frame']; buildings: DockStore['buildings'] }, d: DockDoor): [number, number][] {
  const f = findEdge(store, d.building, d.edge)
  const w = (d.width_ft ?? 9) * FT
  let ux = 1, uy = 0
  if (f) { const L = Math.hypot(f.edge.b[0] - f.edge.a[0], f.edge.b[1] - f.edge.a[1]) || 1; ux = (f.edge.b[0] - f.edge.a[0]) / L; uy = (f.edge.b[1] - f.edge.a[1]) / L }
  const [nx, ny] = d.normal ?? [0, -1]
  const p = (s: number, o: number) => enuToLonLat(store.frame, d.x + ux * s + nx * o, d.y + uy * s + ny * o)
  return [p(-w / 2, 0.15), p(w / 2, 0.15), p(w / 2, DOOR_DEPTH_M), p(-w / 2, DOOR_DEPTH_M), p(-w / 2, 0.15)]
}

type AnyHit = { kind: 'door'; id: string } | { kind: 'handle'; id: string } | { kind: 'gate' } | { kind: 'edge'; b: number; e: number; s: number } | null
const DRAG_PX = 6          // a press must travel this far before a drag starts
const CLICK_PX = 4         // a release within this distance of the press is a click
const PAD_PX = 10          // hit pad around a drawn door rectangle
const HANDLE_R = 8         // 16 px handle at the rectangle centre

/** distance (px) from a point to a polygon: 0 inside */
function distToPoly(px: number, py: number, poly: { x: number; y: number }[]) {
  let inside = false, best = Infinity
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j]
    if ((a.y > py) !== (b.y > py) && px < ((b.x - a.x) * (py - a.y)) / (b.y - a.y || 1e-9) + a.x) inside = !inside
    const vx = b.x - a.x, vy = b.y - a.y, L2 = vx * vx + vy * vy || 1
    const f = Math.max(0, Math.min(1, ((px - a.x) * vx + (py - a.y) * vy) / L2))
    best = Math.min(best, Math.hypot(a.x + vx * f - px, a.y + vy * f - py))
  }
  return inside ? 0 : best
}

export default function DockMap({ propertyId }: { propertyId: number }) {
  const base = useDockEd((s) => s.base)
  const doors = useDockEd((s) => s.doors)
  const gate = useDockEd((s) => s.gate)
  const sel = useDockEd((s) => s.sel)
  const hoverEdge = useDockEd((s) => s.hoverEdge)
  const showRejected = useDockEd((s) => s.showRejected)
  const reg = useDockEd((s) => s.reg)
  const tool = useDockEd((s) => s.tool)
  const focus = useDockEd((s) => s.focus)
  const mapRef = useRef<MapRef>(null)
  const drag = useRef<{ what: 'door' | 'gate'; id?: string; moved: boolean } | null>(null)
  const down = useRef<{ x: number; y: number; hit: AnyHit } | null>(null)
  const [ghost, setGhost] = useState<[number, number] | null>(null)

  // the precomputed ortho (same imagery the footprint came from), drawn under the overlay when available
  const [ortho, setOrtho] = useState<{ url: string; bounds: [number, number, number, number] } | null>(null)
  useEffect(() => {
    let live = true
    loadTwin(propertyId).then((t) => {
      const fr = t?.exterior?.frame as { lat0: number; lon0: number; half_m: number } | undefined
      const url = t?.media?.ortho
      if (!live || !fr || !url) return
      const kx = 111320 * Math.cos((fr.lat0 * Math.PI) / 180)
      setOrtho({ url, bounds: [fr.lon0 - fr.half_m / kx, fr.lat0 - fr.half_m / 110574, fr.lon0 + fr.half_m / kx, fr.lat0 + fr.half_m / 110574] })
    }).catch(() => {})
    return () => { live = false }
  }, [propertyId])

  const visible = useMemo(() => doors.filter((d) => showRejected || d.status !== 'rejected'), [doors, showRejected])

  const bounds = useMemo(() => {
    if (!base) return undefined
    const pts: [number, number][] = []
    base.buildings.forEach((b) => b.outline.forEach(([x, y]) => pts.push(enuToLonLat(base.frame, x, y))))
    return lonLatBounds(pts, 7)
  }, [base])

  const edgesLL = useMemo(() => {
    if (!base) return []
    return base.buildings.flatMap((b) => b.edges.map((e) => ({ key: edgeKey(b.id, e.id), b: b.id, e: e.id, facing: e.facing,
      path: [enuToLonLat(base.frame, e.a[0], e.a[1]), enuToLonLat(base.frame, e.b[0], e.b[1])] as [number, number][] })))
  }, [base])

  // ---- screen-space hit testing (against the drawn rectangles) ----
  const ringsFor = (ds: DockDoor[]) => (base ? ds.map((d) => ({ d, ring: doorRing(base, d) })) : [])
  const hitDoor = (ev: MapLayerMouseEvent): AnyHit => {
    const map = ev.target
    const { x, y } = ev.point
    const st = useDockEd.getState()
    // the selected door's handle wins over everything
    const sd = st.doors.find((d) => d.id === st.sel)
    if (sd && base) {
      const r = doorRing(base, sd), c = map.project(midRing(r))
      if (Math.hypot(c.x - x, c.y - y) <= HANDLE_R + 3) return { kind: 'handle', id: sd.id }
    }
    let best: AnyHit = null, bd = Infinity
    for (const { d, ring } of ringsFor(visible)) {
      const pts = ring.map((q) => map.project(q))
      const dist = distToPoly(x, y, pts)
      if (dist > PAD_PX) continue
      const c = map.project(midRing(ring))
      // inside/pad ties: the selected door first, then the nearest centre
      const score = (d.id === st.sel ? -1e6 : 0) + dist * 100 + Math.hypot(c.x - x, c.y - y)
      if (score < bd) { bd = score; best = { kind: 'door', id: d.id } }
    }
    return best
  }
  const hitGate = (ev: MapLayerMouseEvent): boolean => {
    if (!gate) return false
    const p = ev.target.project([gate.lon, gate.lat])
    return Math.hypot(p.x - ev.point.x, p.y - ev.point.y) < 13
  }
  const hitEdge = (ev: MapLayerMouseEvent): AnyHit => {
    if (!base) return null
    const map = ev.target
    const { x, y } = ev.point
    let eb: AnyHit = null, ed = 10
    for (const e of edgesLL) {
      const a = map.project(e.path[0]), b = map.project(e.path[1])
      const vx = b.x - a.x, vy = b.y - a.y, L2 = vx * vx + vy * vy || 1
      const f = Math.max(0, Math.min(1, ((x - a.x) * vx + (y - a.y) * vy) / L2))
      const dist = Math.hypot(a.x + vx * f - x, a.y + vy * f - y)
      if (dist < ed) {
        const fe = findEdge(base, e.b, e.e)
        if (!fe) continue
        const [ex, ey] = lonLatToEnu(base.frame, ev.lngLat.lng, ev.lngLat.lat)
        ed = dist; eb = { kind: 'edge', b: e.b, e: e.e, s: projectOnEdge(fe.edge, ex, ey).s }
      }
    }
    return eb
  }
  const setCursor = (ev: MapLayerMouseEvent, c: string) => { ev.target.getCanvas().style.cursor = c }

  const onMouseDown = (ev: MapLayerMouseEvent) => {
    const st = useDockEd.getState()
    let hit: AnyHit = null
    if (st.tool === 'select') hit = hitDoor(ev)
    else if (st.tool === 'add') hit = hitEdge(ev)
    else if (st.tool === 'gate' && hitGate(ev)) hit = { kind: 'gate' }
    down.current = { x: ev.point.x, y: ev.point.y, hit }
    // only a press on the selected door's handle, or on the gate in the gate tool, can drag; everything else pans
    if (hit?.kind === 'handle' || hit?.kind === 'gate') {
      ev.preventDefault()
      ev.target.dragPan.disable()
      drag.current = { what: hit.kind === 'gate' ? 'gate' : 'door', id: hit.kind === 'handle' ? hit.id : undefined, moved: false }
    }
  }
  const onMouseMove = (ev: MapLayerMouseEvent) => {
    const st = useDockEd.getState()
    if (drag.current && base) {
      const dr = drag.current
      if (!dr.moved && down.current && Math.hypot(ev.point.x - down.current.x, ev.point.y - down.current.y) >= DRAG_PX) dr.moved = true
      if (!dr.moved) return
      if (dr.what === 'gate') { st.setGate(ev.lngLat.lng, ev.lngLat.lat); setCursor(ev, 'grabbing'); return }
      const d = st.doors.find((x) => x.id === dr.id)
      const fe = d && findEdge(base, d.building, d.edge)
      if (!d || !fe) return
      const [ex, ey] = lonLatToEnu(base.frame, ev.lngLat.lng, ev.lngLat.lat)
      st.moveDoor(d.id, projectOnEdge(fe.edge, ex, ey).s)               // snap: pointer projected onto the door's edge, clamped to [0, len]
      setCursor(ev, 'grabbing')
      return
    }
    if (down.current) return                                              // panning
    if (st.tool === 'add') {
      const hit = hitEdge(ev)
      if (hit?.kind === 'edge') {
        st.setHoverEdge(edgeKey(hit.b, hit.e) as EdgeKey)
        setGhost(placeGhost(base!, hit.b, hit.e, hit.s))
        setCursor(ev, 'copy')
      } else { st.setHoverEdge(null); if (ghost) setGhost(null); setCursor(ev, 'crosshair') }
      return
    }
    if (st.tool === 'gate') { setCursor(ev, hitGate(ev) ? 'grab' : 'crosshair'); return }
    const hit = hitDoor(ev)
    setCursor(ev, hit?.kind === 'handle' ? 'grab' : hit ? 'pointer' : '')
  }
  const onMouseUp = (ev: MapLayerMouseEvent) => {
    const st = useDockEd.getState()
    const dn = down.current
    down.current = null
    if (drag.current) {
      const dr = drag.current
      drag.current = null
      ev.target.dragPan.enable()
      if (dr.moved) return
      // a press on the handle without a real drag is just a click on the selected door: nothing changes
      if (dr.what === 'gate') return
    }
    if (!dn || Math.hypot(ev.point.x - dn.x, ev.point.y - dn.y) > CLICK_PX) return   // it was a pan
    const hit = dn.hit
    if (st.tool === 'add') {
      if (hit?.kind === 'edge') { st.addDoor(hit.b, hit.e, hit.s); st.setHoverEdge(null); setGhost(null) }     // exactly one, then back to Select
      return
    }
    if (st.tool === 'gate') {
      if (!hitGate(ev)) st.setGate(ev.lngLat.lng, ev.lngLat.lat)                                                  // click places the gate
      return
    }
    if (hit?.kind === 'door' || hit?.kind === 'handle') st.select(hit.id)
    else st.select(null)
  }
  // keyboard: 1/2/3 tools, arrows step, C confirm, R reject, K kind; Esc leaves a tool before closing the editor
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const st = useDockEd.getState()
      const key = e.key.toLowerCase()
      if (key === '1') st.setTool('select')
      else if (key === '2') st.setTool('add')
      else if (key === '3') st.setTool('gate')
      else if (e.key === 'ArrowRight') st.step(1)
      else if (e.key === 'ArrowLeft') st.step(-1)
      else if (key === 'c' && st.sel) st.confirm(st.sel)
      else if (key === 'r' && st.sel) st.reject(st.sel)
      else if (key === 'k' && st.sel) st.cycleKind(st.sel)
      else if (e.key === 'Escape' && st.tool !== 'select') { st.setTool('select'); e.stopImmediatePropagation() }
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', k, true)
    return () => window.removeEventListener('keydown', k, true)
  }, [])
  // list click -> fly to the door
  useEffect(() => {
    if (!focus || !base) return
    const d = useDockEd.getState().doors.find((x) => x.id === focus.id)
    const m = mapRef.current?.getMap()
    if (!d || !m) return
    m.flyTo({ center: midRing(doorRing(base, d)), zoom: Math.max(19.5, Math.min(m.getZoom(), 20)), duration: 700, essential: true })
  }, [focus?.nonce])                                                  // eslint-disable-line react-hooks/exhaustive-deps
  // dev hook so the screenshot script can drive real mouse events at map coordinates
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const w = window as unknown as Record<string, unknown>
    w.__dockMap = {
      project: (lon: number, lat: number) => { const m = mapRef.current?.getMap(); if (!m) return null; const p = m.project([lon, lat]); const r = m.getCanvas().getBoundingClientRect(); return [r.left + p.x, r.top + p.y] },
      view: () => { const m = mapRef.current?.getMap(); return m ? { bearing: m.getBearing(), pitch: m.getPitch(), zoom: m.getZoom(), moving: m.isMoving() } : null },
      jump: (lon: number, lat: number, zoom: number) => mapRef.current?.getMap().jumpTo({ center: [lon, lat], zoom }),
      ring: (id: string) => { const st = useDockEd.getState(); const d = st.doors.find((x) => x.id === id); return d && st.base ? doorRing(st.base, d) : null },
    }
  })
  // a drag that leaves the canvas still ends cleanly
  useEffect(() => {
    const up = () => { if (drag.current) { drag.current = null; mapRef.current?.getMap().dragPan.enable() } }
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [])

  const layers = useMemo(() => {
    if (!base) return []
    const selDoor = doors.find((d) => d.id === sel)
    const selEdge = selDoor ? edgeKey(selDoor.building, selDoor.edge) : null
    const solid = visible.filter((d) => d.status === 'confirmed' || d.status === 'user_added')
    const inferred = visible.filter((d) => d.status === 'inferred')
    const proposed = visible.filter((d) => d.status === 'proposed')
    const rejected = visible.filter((d) => d.status === 'rejected')
    const rings = new Map(visible.map((d) => [d.id, doorRing(base, d)]))
    const kc = (d: DockDoor) => DOOR_KIND_RGB[d.kind] ?? DOOR_KIND_RGB.unknown
    // the registered scan floor outline (only when the store carries it), turned by the current flip
    const scan = (() => {
      if (!reg?.scan_outline?.length) return null
      const flipped = reg.flip === 180
      const phi = flipped ? reg.phi_flipped ?? (reg.phi ?? 0) + Math.PI : reg.phi ?? 0
      const t0 = (flipped ? reg.t_flipped : reg.t) ?? [0, 0]
      const t = [t0[0] + (reg.shift?.[0] ?? 0), t0[1] + (reg.shift?.[1] ?? 0)]
      const c = Math.cos(phi), sn = Math.sin(phi)
      return reg.scan_outline.map(([x, y]) => enuToLonLat(base.frame, c * x - sn * y + t[0], sn * x + c * y + t[1]))
    })()
    return [
      ortho && new BitmapLayer({ id: 'dk-ortho', image: ortho.url, bounds: ortho.bounds, opacity: 0.92 }),
      new PolygonLayer({
        id: 'dk-footprint', data: base.buildings, getPolygon: (b: DockStore['buildings'][number]) => b.outline.map(([x, y]) => enuToLonLat(base.frame, x, y)),
        filled: true, getFillColor: [79, 209, 197, 14], stroked: false,
      }),
      new PathLayer({
        id: 'dk-edges', data: edgesLL, getPath: (e: { path: [number, number][] }) => e.path, widthUnits: 'pixels', capRounded: true,
        getColor: (e: { key: string }) => (e.key === hoverEdge ? [79, 209, 197, 255] : e.key === selEdge ? [79, 209, 197, 170] : [226, 232, 240, 120]),
        getWidth: (e: { key: string }) => (e.key === hoverEdge ? 5 : e.key === selEdge ? 3 : 1.5), updateTriggers: { getColor: [hoverEdge, selEdge], getWidth: [hoverEdge, selEdge] },
      }),
      scan && new PathLayer({
        id: 'dk-scan', data: dashed([...scan, scan[0]], 1.2, 0.8), getPath: (p: [number, number][]) => p, widthUnits: 'pixels', getWidth: 1.6,
        getColor: reg?.confirmed_by_user ? [167, 139, 250, 230] : [167, 139, 250, 160],
      }),
      // rejected (toggle): faint red, dashed
      new PathLayer({
        id: 'dk-rejected', data: rejected.flatMap((d) => dashed(rings.get(d.id)!, 0.6, 0.5).map((p) => ({ p, id: d.id }))), getPath: (x: { p: [number, number][] }) => x.p,
        widthUnits: 'pixels', getWidth: 1.5, getColor: [248, 113, 113, 200],
      }),
      // inferred: lighter fill, no outline
      new PolygonLayer({
        id: 'dk-inferred', data: inferred, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: false,
        getFillColor: (d: DockDoor) => [...kc(d), 95] as [number, number, number, number], updateTriggers: { getFillColor: [doors] },
      }),
      // proposed: dark fill + dashed outline in the kind colour
      new PolygonLayer({ id: 'dk-proposed-fill', data: proposed, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: false, getFillColor: [10, 13, 18, 150] }),
      new PathLayer({
        id: 'dk-proposed', data: proposed.flatMap((d) => dashed(rings.get(d.id)!, 0.55, 0.4).map((p) => ({ p, d }))), getPath: (x: { p: [number, number][] }) => x.p,
        widthUnits: 'pixels', getWidth: 1.6, getColor: (x: { d: DockDoor }) => [...kc(x.d), 255] as [number, number, number, number],
      }),
      // confirmed / added by you: solid
      new PolygonLayer({
        id: 'dk-solid', data: solid, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1,
        getFillColor: (d: DockDoor) => [...kc(d), 235] as [number, number, number, number], getLineColor: [10, 13, 18, 255],
      }),
      selDoor && rings.get(selDoor.id) && new PathLayer({
        id: 'dk-sel', data: [rings.get(selDoor.id)!], getPath: (p: [number, number][]) => p, widthUnits: 'pixels', getWidth: 2.5, getColor: [255, 255, 255, 255],
      }),
      selDoor && tool === 'select' && new ScatterplotLayer({
        id: 'dk-handle', data: [midRing(rings.get(selDoor.id) ?? doorRing(base, selDoor))], getPosition: (p: [number, number]) => p,
        radiusUnits: 'pixels', getRadius: HANDLE_R, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
        getFillColor: [255, 255, 255, 235], getLineColor: [79, 209, 197, 255],
      }),
      new TextLayer({
        id: 'dk-unknown', data: visible.filter((d) => d.kind === 'unknown' && d.status !== 'rejected'), getPosition: (d: DockDoor) => midRing(rings.get(d.id)!),
        getText: () => '?', getSize: 11, getColor: [226, 232, 240, 255], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 700,
      }),
      selDoor && new TextLayer({
        id: 'dk-sel-label', data: [selDoor], getPosition: (d: DockDoor) => enuToLonLat(base.frame, d.x + d.normal[0] * 5.5, d.y + d.normal[1] * 5.5),
        getText: (d: DockDoor) => d.id, getSize: 11, getColor: [232, 236, 242, 255], fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,
        background: true, getBackgroundColor: [10, 13, 18, 220], backgroundPadding: [4, 2, 4, 2],
      }),
      ghost && new ScatterplotLayer({
        id: 'dk-ghost', data: [ghost], getPosition: (p: [number, number]) => p, radiusUnits: 'pixels', getRadius: 6, stroked: true, filled: true,
        getFillColor: [79, 209, 197, 90], getLineColor: [79, 209, 197, 255], lineWidthUnits: 'pixels', getLineWidth: 1.5,
      }),
      gate && new ScatterplotLayer({
        id: 'dk-gate', data: [gate], getPosition: (g: { lon: number; lat: number }) => [g.lon, g.lat], radiusUnits: 'pixels', getRadius: 8,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: [10, 13, 18, 230], getLineColor: [226, 232, 240, 255],
      }),
      gate && new TextLayer({
        id: 'dk-gate-label', data: [gate], getPosition: (g: { lon: number; lat: number }) => [g.lon, g.lat],
        getText: (g: { source: string }) => (g.source === 'user' ? 'gate' : 'gate (inferred)'), getSize: 11, getColor: [226, 232, 240, 255],
        getPixelOffset: [0, -17], fontFamily: 'Inter, system-ui, sans-serif', fontWeight: 600, background: true, getBackgroundColor: [10, 13, 18, 210], backgroundPadding: [4, 2, 4, 2],
      }),
    ]
  }, [base, doors, visible, sel, hoverEdge, edgesLL, ghost, gate, ortho, reg, tool])

  if (!base) return null
  return (
    <div className="absolute inset-0 overflow-hidden bg-bg" data-testid="dock-map">
      <SiteMap ref={mapRef} bounds={bounds} overlay={{ layers }} padding={{ top: 56, bottom: 84, left: 16, right: 16 }}
        mapProps={{ onMouseDown, onMouseMove, onMouseUp, onMouseOut: () => { useDockEd.getState().setHoverEdge(null); setGhost(null) },
          doubleClickZoom: false, keyboard: false, dragRotate: false, touchPitch: false, touchZoomRotate: true, pitch: 0, bearing: 0, maxPitch: 0 }} />
      <Toolbar tool={tool} />
      <div className="glass pointer-events-none absolute bottom-3 left-3 right-3 z-10 rounded-xl px-3 py-2 text-[11.5px] text-ink-2">
        <div>{tool === 'add' ? <><b className="font-semibold text-ink">Click a wall</b> to add one door (it snaps to the wall) · Esc cancels</>
          : tool === 'gate' ? <><b className="font-semibold text-ink">Click</b> where trucks enter, or drag the gate marker · Esc returns to Select</>
          : <><b className="font-semibold text-ink">Click</b> a door to select · drag its <b className="font-semibold text-ink">white handle</b> to slide it along the wall · <span className="font-mono">←/→</span> step · <span className="font-mono">C</span> confirm · <span className="font-mono">R</span> reject · <span className="font-mono">K</span> kind</>}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-ink-3">
          <span className="flex items-center gap-1"><Swatch c="ink-2" solid />confirmed</span>
          <span className="flex items-center gap-1"><Swatch c="ink-2" />proposed</span>
          <span className="flex items-center gap-1"><Swatch c="ink-2" light />inferred</span>
          <span className="h-3 w-px bg-line-2" /><span className="flex items-center gap-1"><Swatch c="accent" solid />dock-high</span>
          <span className="flex items-center gap-1"><Swatch c="warn" solid />drive-in</span>
          <span className="flex items-center gap-1"><Swatch c="ink-3" solid />unknown (?)</span>
        </div>
      </div>
    </div>
  )
}

function Toolbar({ tool }: { tool: DockTool }) {
  const set = useDockEd((s) => s.setTool)
  const items: [DockTool, string, string, ReactNode][] = [
    ['select', '1', 'Select', <MousePointer2 key="s" size={13} />], ['add', '2', 'Add door', <DoorOpen key="a" size={13} />], ['gate', '3', 'Move gate', <MapPin key="g" size={13} />],
  ]
  return (
    <div className="glass absolute right-3 top-3 z-20 flex gap-0.5 whitespace-nowrap rounded-xl p-1" data-testid="dock-tools">
      {items.map(([k, key, label, icon]) => (
        <button key={k} type="button" onClick={() => set(k)} data-tool={k} title={`${label} (${key})`}
          className={clsx('flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium transition',
            tool === k ? 'bg-accent/20 text-accent ring-1 ring-inset ring-accent/40' : 'text-ink-2 hover:bg-white/[0.06] hover:text-ink')}>
          {icon}{label}<span className="ml-0.5 rounded bg-white/[0.06] px-1 font-mono text-[10px] text-ink-3">{key}</span>
        </button>
      ))}
    </div>
  )
}

function placeGhost(base: DockStore, b: number, e: number, s: number): [number, number] | null {
  const f = findEdge(base, b, e)
  if (!f) return null
  const L = Math.hypot(f.edge.b[0] - f.edge.a[0], f.edge.b[1] - f.edge.a[1]) || 1
  return enuToLonLat(base.frame, f.edge.a[0] + ((f.edge.b[0] - f.edge.a[0]) / L) * s, f.edge.a[1] + ((f.edge.b[1] - f.edge.a[1]) / L) * s)
}
const midRing = (r: [number, number][]): [number, number] => [(r[0][0] + r[2][0]) / 2, (r[0][1] + r[2][1]) / 2]

function Swatch({ c, solid, light }: { c: 'accent' | 'warn' | 'ink-2' | 'ink-3'; solid?: boolean; light?: boolean }) {
  const col = `var(--color-${c})`
  return <span className="inline-block h-2.5 w-3.5 rounded-[2px]" style={solid ? { background: col } : light ? { background: `color-mix(in oklab, ${col} 40%, transparent)` } : { border: `1.5px dashed ${col}` }} />
}
