// Docks on the MAIN map for the selected property (plan C): footprint outline, the same door rectangles as the
// editor (kind colour; proposed dashed, inferred light, confirmed solid), the gate, a legend chip, and a proof popup
// per door (the Vexcel oblique crop + SAM3 boxes it was detected in, or an honest "no photo evidence").
import { useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { Check, Loader2, PencilRuler, X } from 'lucide-react'
import { PathLayer, PolygonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { create } from 'zustand'
import { useStore } from '../lib/store'
import { mapApi } from '../lib/mapApi'
import {
  docksApi, enuToLonLat, KIND_LABEL, proofApi, proofImageUrl, setDoorStatus, STATUS_LABEL,
  type DockDoor, type DockStore, type ProofIndex,
} from '../lib/docksApi'
import { dashed } from '../yard/SiteMap'
import { DOOR_KIND_RGB, doorRing } from './DockMap'
import { useDockEd } from './state'

interface OverlayUi {
  store: DockStore | null
  proof: ProofIndex | null
  popup: { pid: number; doorId: string } | null
  busy: boolean
  error: string | null
  setPopup: (p: OverlayUi['popup']) => void
}
export const useDockOverlay = create<OverlayUi>((set) => ({
  store: null, proof: null, popup: null, busy: false, error: null,
  setPopup: (p) => set({ popup: p, error: null }),
}))
/** set when a door was clicked, so the map's own click handler doesn't also deselect the property */
export let lastDockClick = 0

const EVIDENCE_LABEL: Record<string, string> = { oblique: 'Vexcel oblique · SAM3', trailer: 'Docked trailer (ortho)', well: 'Truck well (DTM)', costar_span: 'CoStar span fill', vision: 'Vision model', user: 'Added by a person' }

/** Loads the store for the selected property, frames the camera on the footprint, and returns the deck.gl layers. */
export function useDockLayers(selectedId: number | null) {
  const docksOpen = useStore((s) => s.docksOpen)
  const store = useDockOverlay((s) => s.store)
  const popup = useDockOverlay((s) => s.popup)
  const fitted = useRef<number | null>(null)
  useEffect(() => {
    useDockOverlay.setState({ store: null, proof: null, popup: null })
    if (selectedId == null) { fitted.current = null; return }
    let live = true
    docksApi.get(selectedId).then((s) => {
      if (!live) return
      useDockOverlay.setState({ store: s })
      proofApi.get(selectedId).then((p) => live && useDockOverlay.setState({ proof: p })).catch(() => {})
      if (fitted.current === selectedId) return
      fitted.current = selectedId
      const pts = s.buildings.flatMap((b) => b.outline.map(([x, y]) => enuToLonLat(s.frame, x, y)))
      const m = mapApi.get()
      const go = () => { if (live && !mapApi.isLocked()) mapApi.fitFootprint(pts, 25) }
      if (m?.isMoving()) m.once('moveend', go); else go()
    }).catch(() => {})
    return () => { live = false }
  }, [selectedId])
  // refresh after the editor saves (its save updates the docksApi cache)
  const savedAt = useDockEd((s) => s.savedAt)
  useEffect(() => { if (selectedId != null && savedAt) docksApi.get(selectedId).then((s) => useDockOverlay.setState({ store: s })).catch(() => {}) }, [savedAt, selectedId])

  const layers = useMemo(() => {
    if (!store || store.property_id !== selectedId || docksOpen != null) return []
    const doors = store.doors.filter((d) => d.status !== 'rejected')
    const rings = new Map(doors.map((d) => [d.id, doorRing(store, d)]))
    const kc = (d: DockDoor) => DOOR_KIND_RGB[d.kind] ?? DOOR_KIND_RGB.unknown
    const outline = store.buildings.map((b) => b.outline.map(([x, y]) => enuToLonLat(store.frame, x, y)))
    const solid = doors.filter((d) => d.status === 'confirmed' || d.status === 'user_added')
    const proposed = doors.filter((d) => d.status === 'proposed')
    const inferred = doors.filter((d) => d.status === 'inferred')
    const sel = popup?.pid === selectedId ? popup.doorId : null
    return [
      new PathLayer({ id: 'dko-outline', data: outline, getPath: (p: [number, number][]) => p, widthUnits: 'pixels', getWidth: 2, getColor: [79, 209, 197, 230], jointRounded: true }),
      new PolygonLayer({ id: 'dko-proposed-fill', data: proposed, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: false, getFillColor: [10, 13, 18, 160] }),
      new PathLayer({
        id: 'dko-proposed', data: proposed.flatMap((d) => dashed(rings.get(d.id)!, 0.55, 0.4).map((p) => ({ p, d }))), getPath: (x: { p: [number, number][] }) => x.p,
        widthUnits: 'pixels', getWidth: 1.6, getColor: (x: { d: DockDoor }) => [...kc(x.d), 255] as [number, number, number, number],
      }),
      new PolygonLayer({ id: 'dko-inferred', data: inferred, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: false,
        getFillColor: (d: DockDoor) => [...kc(d), 100] as [number, number, number, number] }),
      new PolygonLayer({ id: 'dko-solid', data: solid, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1,
        getFillColor: (d: DockDoor) => [...kc(d), 235] as [number, number, number, number], getLineColor: [10, 13, 18, 255] }),
      sel && rings.get(sel) ? new PathLayer({ id: 'dko-sel', data: [rings.get(sel)!], getPath: (p: [number, number][]) => p, widthUnits: 'pixels', getWidth: 2.5, getColor: [255, 255, 255, 255] }) : null,
      // invisible, generous click targets over every door
      new PolygonLayer({
        id: 'dko-pick', data: doors, getPolygon: (d: DockDoor) => rings.get(d.id)!, filled: true, stroked: true, pickable: true,
        getFillColor: [0, 0, 0, 1], getLineColor: [0, 0, 0, 1], lineWidthUnits: 'pixels', getLineWidth: 10,
        onClick: (i: { object?: DockDoor }) => { if (!i.object) return false; lastDockClick = Date.now(); useDockOverlay.getState().setPopup({ pid: store.property_id, doorId: i.object.id }); return true },
        onHover: (i: { object?: DockDoor }) => { const c = mapApi.get()?.getCanvas(); if (c && i.object) c.style.cursor = 'pointer' },
      }),
      store.gate ? new ScatterplotLayer({
        id: 'dko-gate', data: [store.gate], getPosition: (g: { lon: number; lat: number }) => [g.lon, g.lat], radiusUnits: 'pixels', getRadius: 6,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: [10, 13, 18, 230], getLineColor: [226, 232, 240, 255],
      }) : null,
    ]
  }, [store, selectedId, docksOpen, popup])
  const hideTowerId = layers.length ? selectedId : null
  return { layers, hideTowerId }
}

/** "Docks: 10 seen in Vexcel obliques (SAM3) · 1 inferred · CoStar 23" */
export function DockLegend() {
  const store = useDockOverlay((s) => s.store)
  const selectedId = useStore((s) => s.selectedId)
  const docksOpen = useStore((s) => s.docksOpen)
  const yard = useStore((s) => s.yard)
  if (!store || store.property_id !== selectedId || docksOpen != null || yard) return null
  const doors = store.doors.filter((d) => d.status !== 'rejected')
  const seen = doors.filter((d) => d.evidence.some((e) => e.type === 'oblique')).length
  const trailers = doors.filter((d) => d.evidence.some((e) => e.type === 'trailer')).length
  const inferred = doors.filter((d) => d.status === 'inferred').length
  const conf = doors.filter((d) => d.status === 'confirmed' || d.status === 'user_added').length
  const cs = store.summary?.costar_docks
  return (
    <div className="glass pointer-events-auto absolute left-[392px] top-[132px] z-[12] flex items-center gap-2 rounded-xl px-3 py-1.5 text-[12px] text-ink-2 fade-up" data-testid="dock-legend">
      <span className="font-semibold text-ink">Docks:</span>
      <span><b className="font-semibold tabular-nums text-ink">{seen}</b> seen in Vexcel obliques (SAM3)</span>
      {trailers > 0 && <span>· <b className="tabular-nums text-ink">{trailers}</b> from docked trailers</span>}
      <span>· <b className="tabular-nums text-ink">{inferred}</b> inferred</span>
      {conf > 0 && <span className="text-good">· {conf} confirmed</span>}
      <span>· CoStar <b className="tabular-nums text-ink">{cs ?? '?'}</b></span>
      <button onClick={() => useStore.setState({ docksOpen: store.property_id })} className="ml-1 rounded-md px-1.5 py-0.5 text-[11.5px] font-medium text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/10">Edit</button>
    </div>
  )
}

/** Proof popup glued to the clicked door. */
export function DockProofPopup() {
  const popup = useDockOverlay((s) => s.popup)
  const store = useDockOverlay((s) => s.store)
  const proof = useDockOverlay((s) => s.proof)
  const busy = useDockOverlay((s) => s.busy)
  const error = useDockOverlay((s) => s.error)
  const selectedId = useStore((s) => s.selectedId)
  const docksOpen = useStore((s) => s.docksOpen)
  const ref = useRef<HTMLDivElement>(null)
  const door = popup && store?.property_id === popup.pid ? store.doors.find((d) => d.id === popup.doorId) ?? null : null
  const anchor = useMemo(() => (door && store ? doorRing(store, door) : null), [door, store])

  useEffect(() => {
    if (!anchor) return
    const m = mapApi.get()
    const place = () => {
      const el = ref.current, map = mapApi.get()
      if (!el || !map) return
      const c = anchor.reduce((a, p) => [a[0] + p[0] / anchor.length, a[1] + p[1] / anchor.length], [0, 0])
      const p = map.project(c as [number, number])
      const cw = map.getContainer().clientWidth
      const lo = 376 + 8 + 150, hi = cw - 456 - 8 - 150
      const x = Math.min(Math.max(p.x, lo), Math.max(lo, hi))
      // above the door when there is room under the search bar, else below it
      const below = p.y - el.offsetHeight - 14 < 140
      el.style.transform = `translate(${Math.round(x)}px, ${Math.round(p.y)}px) translate(-50%, ${below ? '18px' : 'calc(-100% - 14px)'})`
      const stem = el.querySelector<HTMLElement>('[data-stem]')
      if (stem) stem.style.display = below ? 'none' : ''
      el.style.opacity = '1'
    }
    place()
    m?.on('move', place); m?.on('resize', place)
    return () => { m?.off('move', place); m?.off('resize', place) }
  }, [anchor])
  useEffect(() => { if (popup && popup.pid !== selectedId) useDockOverlay.getState().setPopup(null) }, [selectedId, popup])

  if (!popup || !door || !store || docksOpen != null) return null
  const pr = proof?.doors[door.id]
  const act = async (status: 'confirmed' | 'rejected') => {
    useDockOverlay.setState({ busy: true, error: null })
    try {
      const s = await setDoorStatus(store.property_id, door.id, status)
      const ed = useDockEd.getState()
      if (ed.pid === store.property_id && !ed.dirty) useDockEd.setState({ pid: null, base: null })   // editor reloads the saved store
      useDockOverlay.setState({ store: s, busy: false, popup: status === 'rejected' ? null : popup })
    } catch (e) {
      useDockOverlay.setState({ busy: false, error: e instanceof Error ? e.message : String(e) })
    }
  }
  const conf = Math.max(0, ...door.evidence.map((e) => e.conf ?? 0))
  return (
    <div ref={ref} className="pointer-events-auto absolute left-0 top-0 z-[19] w-[300px] opacity-0" data-testid="dock-proof">
      <div className="glass fade-up overflow-hidden rounded-xl">
        <div className="flex items-center gap-2 px-3 pb-2 pt-2.5">
          <span className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: `rgb(${(DOOR_KIND_RGB[door.kind] ?? DOOR_KIND_RGB.unknown).join(' ')})` }} />
          <span className="font-mono text-[13px] font-semibold">{door.id}</span>
          <span className="text-[12px] text-ink-2">{KIND_LABEL[door.kind]}</span>
          <span className="rounded bg-white/[0.06] px-1.5 text-[10px] uppercase tracking-wide text-ink-3">{STATUS_LABEL[door.status]}</span>
          <button onClick={() => useDockOverlay.getState().setPopup(null)} className="ml-auto rounded p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink" aria-label="Close"><X size={13} /></button>
        </div>
        {!proof ? <div className="skeleton mx-3 h-[120px]" />
          : pr && !pr.no_photo && pr.crops.length ? (
            <div className="space-y-1.5 px-3">
              {pr.crops.slice(0, 2).map((c) => <ProofImage key={c.crop_id} src={proofImageUrl(store.property_id, door.id, c)} crop={c} fixture={!!proof._fixture} />)}
            </div>
          ) : (
            <div className="mx-3 rounded-lg bg-white/[0.03] px-3 py-3 text-[12px] leading-snug text-ink-2 ring-1 ring-inset ring-line">
              {pr?.no_photo ? pr.why : 'No photo evidence on file for this door.'}
            </div>
          )}
        <div className="flex flex-wrap gap-1 px-3 pt-2">
          {door.evidence.length ? door.evidence.map((e, i) => (
            <span key={i} title={e.note} className="inline-flex h-[18px] items-center gap-1 rounded px-1.5 text-[10px] font-medium text-amber-200 ring-1 ring-inset ring-amber-400/25">
              {EVIDENCE_LABEL[e.type] ?? e.type}<span className="tabular-nums text-ink-3">{Math.round((e.conf ?? 0) * 100)}%</span>
            </span>
          )) : <span className="text-[11px] text-ink-3">No evidence recorded</span>}
          {conf > 0 && <span className="ml-auto text-[10.5px] tabular-nums text-ink-3">confidence {Math.round(conf * 100)}%</span>}
        </div>
        {error && <div className="mx-3 mt-2 rounded bg-bad/10 px-2 py-1 text-[11px] text-bad">Not saved: {error}</div>}
        <div className="mt-2.5 flex gap-1.5 border-t border-line p-2">
          <button disabled={busy || door.status === 'confirmed' || door.status === 'user_added'} onClick={() => act('confirmed')}
            className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-[12px] font-medium text-good ring-1 ring-inset ring-good/30 hover:bg-good/10 disabled:opacity-40">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Confirm
          </button>
          <button disabled={busy} onClick={() => act('rejected')}
            className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-[12px] font-medium text-bad ring-1 ring-inset ring-bad/30 hover:bg-bad/10 disabled:opacity-40"><X size={13} /> Reject</button>
          <button onClick={() => { useDockEd.getState().select(door.id); useStore.setState({ docksOpen: store.property_id }); useDockOverlay.getState().setPopup(null) }}
            className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-[12px] font-medium text-ink ring-1 ring-inset ring-line-2 hover:bg-white/[0.06]"><PencilRuler size={13} /> Edit docks</button>
        </div>
      </div>
      <div data-stem className="mx-auto h-3 w-px bg-accent/80" />
    </div>
  )
}

function ProofImage({ src, crop, fixture }: { src: string; crop: import('../lib/docksApi').ProofCrop; fixture: boolean }) {
  const [st, setSt] = useState<'loading' | 'ok' | 'err'>('loading')
  useEffect(() => setSt('loading'), [src])
  const nHits = Array.isArray(crop.hits) ? crop.hits.length : typeof crop.hits === 'number' ? crop.hits : null
  return (
    <figure className="overflow-hidden rounded-lg bg-panel-3 ring-1 ring-inset ring-line">
      <div className={clsx('relative', st === 'ok' ? '' : 'h-[120px]')}>
        {st !== 'ok' && (
          <div className={clsx('absolute inset-0 flex items-center justify-center gap-2 text-[11.5px] text-ink-3', st === 'loading' && 'skeleton')}>
            {st === 'loading' ? <><Loader2 size={13} className="animate-spin" />Fetching the Vexcel crop…</> : 'The proof image could not be loaded.'}
          </div>
        )}
        <img src={src} alt={`Vexcel ${crop.view} crop`} onLoad={() => setSt('ok')} onError={() => setSt('err')}
          className={clsx('block max-h-[200px] w-full bg-black object-contain transition-opacity', st === 'ok' ? 'opacity-100' : 'absolute inset-0 h-full opacity-0')} />
        {fixture && st === 'ok' && <span className="absolute left-1.5 top-1.5 rounded bg-bg/80 px-1.5 text-[10px] text-warn">dev: whole-site oblique, not the crop</span>}
      </div>
      <figcaption className="px-2 py-1 text-[10.5px] leading-snug text-ink-3">
        Vexcel oblique · camera looking {crop.camera_dir ?? crop.view.replace('oblique-', '')}{crop.date ? ` · captured ${crop.date}` : ''}
        {nHits != null ? ` · ${nHits} SAM3 box${nHits === 1 ? '' : 'es'}` : ''}{crop.conf != null ? ` · ${crop.conf.toFixed(2)}` : ''}
      </figcaption>
    </figure>
  )
}

// dev hooks for the screenshot / mouse-test scripts (module instances can differ under HMR)
if (import.meta.env.DEV && typeof window !== 'undefined') {
  const w = window as unknown as Record<string, unknown>
  w.__dockOverlay = useDockOverlay
  w.__dockEd = useDockEd
  w.__mapApi = mapApi
  w.__doorRing = doorRing
}
