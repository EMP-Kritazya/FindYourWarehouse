import { Suspense, lazy, useCallback, useEffect } from 'react'
import { APP_NAME, APP_TAGLINE } from './lib/brand'
import { Warehouse as WarehouseIcon, X } from 'lucide-react'
import MapView from './components/MapView'
import { MarketTabs, SearchBar } from './components/TopBar'
import ResultsRail from './components/ResultsRail'
import ResultsCarousel from './components/ResultsCarousel'
import DraggableDock from './components/DraggableDock'
import WarehouseCard, { bestScan, loadDetail, loadTwin } from './components/WarehouseCard'
import CompareView from './components/CompareView'
import CompareDuel from './components/CompareDuel'
import CostPanel from './economics/CostPanel'
import FloodPanel from './flood/FloodPanel'
import MapControls from './components/MapControls'
import Stage from './components/Stage'
import ErrorBoundary from './components/ErrorBoundary'
import { DEV_SIM, useStore } from './lib/store'
import { mapApi } from './lib/mapApi'
import { api2, type Market } from './lib/api'
import { markAgent, sendEvent, guideLog } from './lib/guide'
import { recordFit, recordYard, rerunManeuver, runManeuverTest } from './components/TestPanel'
import { isManeuver, trucksFromArgs } from './yard/maneuver'

// Stream D owns VoiceOrb; lazy + boundary so voice problems never break the map.
const VoiceOrb = lazy(() => import('./voice/VoiceOrb'))
// S3 owns the 3D yard replay; S5 mounts it over the map.
const YardView = lazy(() => import('./yard/YardView'))
const InteriorWorkspace = lazy(() => import('./racks/InteriorWorkspace'))
// v3: the multi-truck maneuver player (default), and the 2D dock editor (map overlay + right-column panel)
const ManeuverView = lazy(() => import('./yard/ManeuverView'))
const DockMap = lazy(() => import('./docks/DockMap'))
const DockEditor = lazy(() => import('./docks/DockEditor'))

const toIds = (v: unknown): number[] => (Array.isArray(v) ? v : v == null ? [] : [v]).map(Number).filter(Number.isFinite)
const normMarket = (m: unknown): Market | undefined => {
  const t = String(m ?? '').toLowerCase()
  if (!t) return undefined
  if (/hou/.test(t)) return 'Houston'
  if (/^la$|lax|los/.test(t)) return 'Los Angeles'
  return undefined
}
const scanFor = (id: number) => Promise.all([loadDetail(id), loadTwin(id).catch(() => null)]).then(([w, t]) => ({ w, scan: bestScan(w, t) }))

/** Executes UI commands from the voice guide (docs/CONTRACTS.md v2, "Agent UI commands"). */
export function useUiCommands() {
  return useCallback((cmd: string, args: any) => {
    const s = useStore.getState()
    const a = args ?? {}
    const id = Number(a.id)
    switch (cmd) {
      case 'search':
        markAgent('searchResults')
        if (Array.isArray(a.chips)) s.runSearch({ chips: a.chips })
        else s.runSearch({ query: typeof a.query === 'string' ? a.query : '' })
        break
      case 'flyTo': {
        const p = s.pinsById.get(id)
        if (p) { if (p.market !== s.market) s.setMarket(p.market, false); mapApi.flyToPin(p); s.setHighlight([p.id]) }
        else if (Number.isFinite(Number(a.lat)) && Number.isFinite(Number(a.lon))) mapApi.flyToPoint(Number(a.lon), Number(a.lat))
        break
      }
      case 'openCard':
        markAgent('openCard', id)
        s.select(id, { tab: a.tab === 'test' ? 'test' : 'overview' })
        break
      case 'results': {
        const ids = toIds(a.ids).filter((i) => s.pinsById.has(i))
        if (ids.length) useStore.setState({ curated: { title: String(a.title ?? 'Results'), ids: ids.slice(0, 10), values: a.values ?? undefined }, ranking: null })
        break
      }
      case 'showFlood':
        if (Number.isFinite(id)) { if (s.selectedId !== id) s.select(id); useStore.setState({ floodOpen: id, costOpen: null }) }
        break
      case 'closeCompare':
        s.showCompare(false)
        break
      case 'profile':
        if (a.profile && typeof a.profile === 'object') useStore.setState({ buyerProfile: a.profile })
        break
      case 'compare': {
        const ids = toIds(a.ids ?? (a.from === 'shortlist' ? s.shortlist : undefined))
        s.setCompare(ids.length ? ids : s.shortlist, typeof a.profile === 'string' ? a.profile : undefined)
        break
      }
      case 'showInside':
        markAgent('openScan', id)
        scanFor(id).then(({ w, scan }) => {
          if (scan) useStore.getState().openStage({ modelId: scan.model_id, title: s.pinsById.get(id)?.address ?? w.address, subtitle: 'Matterport 3D interior', propertyId: id, mode: 'view' })
        }).catch(() => {})
        break
      case 'highlight': {
        const ids = toIds(a.ids)
        s.setHighlight(ids)
        const pts = ids.map((i) => s.pinsById.get(i)).filter(Boolean).map((p) => [p!.lon, p!.lat] as [number, number])
        if (pts.length) mapApi.fitTo(pts)
        break
      }
      case 'yardTest': {
        // v3: the maneuver test. Accepts {id, trucks:[{type, count}]}; a v2 per_day maps to count = min(per_day, 12).
        markAgent('openCard', id)
        s.openTest(id, 'yard')
        useStore.setState({ docksOpen: null })
        const trucks = trucksFromArgs(a.trucks)
        const run = trucks.length ? runManeuverTest({ property_id: id, trucks, dwell_s: a.dwell_s, door_ids: a.door_ids, seed: a.seed })
          : a.run_id ? fetch(`/api/yard/${encodeURIComponent(a.run_id)}`).then((r) => (r.ok ? r.json() : Promise.reject(r.status))).then((r) => { recordYard(id, r); return r }) : null
        run?.then(() => markAgent('testResult', id)).catch(() => console.warn('[ProInspect] yard test failed'))
        break
      }
      case 'storageTest': {
        if (!Number.isFinite(id)) break
        markAgent('openCard', id)
        s.openTest(id, 'storage')
        scanFor(id).then(({ w, scan }) => { if (scan) useStore.setState({ interiorOpen: { pid: id, modelId: scan.model_id, title: w.address } }) }).catch(() => {})
        break
      }
      case 'showDocks': {
        if (!Number.isFinite(id)) break
        markAgent('openCard', id)
        if (s.selectedId !== id) s.select(id, { tab: 'overview' })
        s.setYard(null)
        useStore.setState({ docksOpen: id })
        break
      }
      case 'equipmentTest': {
        // the equipment checks live in the interior planner now (forklift reach, trips, fleet size on a real layout)
        markAgent('openCard', id)
        s.openTest(id, 'storage')
        scanFor(id).then(({ w, scan }) => { if (scan) useStore.setState({ interiorOpen: { pid: id, modelId: scan.model_id, title: w.address } }) }).catch(() => {})
        break
      }
      case 'showPlace': {
        const lat = Number(a.lat), lon = Number(a.lon)
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) break
        const nearId = Number.isFinite(Number(a.near_id ?? a.id)) ? Number(a.near_id ?? a.id) : s.selectedId ?? undefined
        s.setPlace({ lat, lon, label: String(a.label ?? 'Place'), thumb_url: a.thumb_url, likelihood: a.likelihood, score: a.score != null ? Number(a.score) : undefined,
          distance_m: a.distance_m != null ? Number(a.distance_m) : undefined, nearId })
        break
      }
      case 'rankBy':
        if (a.metric) s.rankBy(String(a.metric), normMarket(a.market))
        break
      case 'rankAttribute':
        if (a.attr) s.rankBy(`attr:${String(a.attr)}${a.order === 'asc' || a.order === 'desc' ? `:${a.order}` : ''}`, normMarket(a.market))
        break
      case 'showAerial':
        markAgent('openAerial', id)
        markAgent('openCard', id)
        s.showAerial(id, a.view, a.year)
        break
      case 'shortlist': {
        const act = String(a.action ?? 'show') as 'add' | 'remove' | 'show'
        s.shortlistDo(['add', 'remove', 'show', 'hide', 'clear', 'toggle'].includes(act) ? act : 'show', Number.isFinite(id) ? id : s.selectedId ?? undefined)
        break
      }
      case 'simulate':
        // the full shift is hidden: route the old command to the Yard test unless the dev flag is on
        if (a.id != null && DEV_SIM && a.profile) s.requestSim({ id, profile: String(a.profile), volume_scale: a.volume_scale, run_id: a.run_id ? String(a.run_id) : undefined })
        else if (a.id != null) s.openTest(id, 'yard')
        break
      default:
        console.warn('[ProInspect] unknown UI command', cmd, args)
    }
  }, [])
}

export default function App() {
  const loadPins = useStore((s) => s.loadPins)
  const selectedId = useStore((s) => s.selectedId)
  const chips = useStore((s) => s.search?.chips)
  const visibleIds = useStore((s) => s.visibleIds)
  const market = useStore((s) => s.market)
  const stage = useStore((s) => s.stage)
  const yard = useStore((s) => s.yard)
  const setYard = useStore((s) => s.setYard)
  const docksOpen = useStore((s) => s.docksOpen)
  const interiorOpen = useStore((s) => s.interiorOpen)
  const listOpen = useStore((s) => s.listOpen)
  useEffect(() => { mapApi.setPadding({ left: listOpen ? 380 : 40, bottom: listOpen ? 110 : 190 }) }, [listOpen])
  const closeDocks = useCallback(() => useStore.setState({ docksOpen: null }), [])
  const onUi = useUiCommands()

  useEffect(() => { loadPins() }, [loadPins])
  // dev-only hooks so screenshot scripts can drive the same UI commands the voice agent uses
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const w = window as unknown as Record<string, unknown>
    w.__proinspectUi = onUi
    w.__proinspectStore = useStore
    w.__proinspectGuideLog = guideLog
  }, [onUi])
  // the stage opening (any path) is an "openScan" moment for the guide
  useEffect(() => {
    if (stage) sendEvent('openScan', { id: stage.propertyId, model_id: stage.modelId, mode: stage.mode ?? 'view', title: stage.title })
  }, [stage?.modelId, stage?.mode])
  // interaction lock: while the dock editor or the yard view covers the map, the main map can't rotate, pan or zoom
  useEffect(() => { mapApi.setLocked(docksOpen != null || !!yard || !!interiorOpen) }, [docksOpen, yard, interiorOpen])
  // fly onto the parcel when a yard replay starts
  useEffect(() => {
    if (!yard) return
    const p = useStore.getState().pinsById.get(yard.propertyId)
    if (p) mapApi.flyToPin(p)
  }, [yard?.propertyId, yard?.result])
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const s = useStore.getState()
      if (s.stage) s.openStage(null)
      else if (s.docksOpen != null) useStore.setState({ docksOpen: null })
      else if (s.yard) s.setYard(null)
      else if (s.place) s.setPlace(null)
      else if (s.compareOpen) s.showCompare(false)
      else if (s.selectedId) s.select(null)
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])

  // The orb host is one element (one websocket); it moves between the map bottom-centre and the stage sidebar.
  // A transform makes it the containing block, so even position:fixed content stays inside the reserved box.
  // On the map it sits centred in the visible map area (between the rail and the card), with room above it for the
  // guide's suggestion chips (S2 renders them inside the orb host).
  const mapCentre = selectedId ? 'calc((376px + 100vw - 464px) / 2)' : 'calc((376px + 100vw - 16px) / 2)'
  // While the yard replay runs, the guide docks over the bottom of the rail (scaled to the rail's width) so its
  // bubble never covers the parcel, the swept paths or the replay's time scrubber.
  const orbBox: React.CSSProperties = stage
    ? { right: 32, bottom: 34, width: 380, transform: 'translateZ(0)' }
    : yard || docksOpen != null
      ? { left: 16, bottom: 96, width: 380, transform: 'scale(0.905)', transformOrigin: 'bottom left' }
      : listOpen
        ? { left: mapCentre, bottom: 20, width: 380, transform: 'translateX(-50%)', transition: 'left 260ms cubic-bezier(.2,.8,.2,1)' }
        : { right: selectedId != null ? 436 : 16, bottom: 158, width: 380, display: 'flex', justifyContent: 'flex-end', transition: 'right 260ms cubic-bezier(.2,.8,.2,1)' }

  return (
    <div className="relative h-full w-full overflow-clip bg-bg">
      <MapView />
      {listOpen ? <ResultsRail /> : <ResultsCarousel />}
      <Brand />
      <MapControls />

      <div className="pointer-events-none absolute inset-x-0 top-4 z-20 flex flex-col items-center gap-3">
        <div className="pointer-events-auto"><MarketTabs /></div>
        <div className="pointer-events-auto w-[500px] max-w-[calc(100vw-840px)] min-w-[360px]"><SearchBar /></div>
      </div>

      {docksOpen != null && (
        <div className="pi-docks glass fade-up absolute bottom-4 left-[376px] top-[132px] z-[15] overflow-hidden rounded-2xl" style={{ right: 464 }}>
          <button onClick={closeDocks} title="Close the dock review (Esc)" aria-label="Close the dock review (Esc)"
            className="glass absolute left-3 top-3 z-20 flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] text-ink hover:text-accent">
            <X size={14} /> Close dock review
          </button>
          <ErrorBoundary fallback={<YardFallback onClose={closeDocks} />}>
            <Suspense fallback={null}><DockMap propertyId={docksOpen} /></Suspense>
          </ErrorBoundary>
        </div>
      )}

      {yard && docksOpen == null && (
        // S3's replay is framed in the visible map area: right of the rail, left of the card, under the search bar,
        // so its camera (centred on the parcel) puts the whole yard in view. Its own side panel is hidden (index.css):
        // the card's Test tab shows the same verdicts.
        <div className="pi-yard glass fade-up absolute bottom-4 left-[376px] top-[132px] z-[15] overflow-hidden rounded-2xl"
          style={{ right: selectedId ? 464 : 16 }}>
          <button onClick={() => setYard(null)} title="Close the yard replay (Esc)" aria-label="Close the yard replay (Esc)"
            className="glass absolute left-3 top-3 z-20 flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] text-ink hover:text-accent">
            <X size={14} /> Close yard replay
          </button>
          <ErrorBoundary fallback={<YardFallback onClose={() => setYard(null)} />}>
            <Suspense fallback={null}>
              {DEV_SIM && yard.result && !isManeuver(yard.result)
                ? <YardView propertyId={yard.propertyId} result={yard.result ?? undefined} onClose={() => setYard(null)} />
                : <ManeuverView propertyId={yard.propertyId} result={yard.result} onRerun={rerunManeuver} />}
            </Suspense>
          </ErrorBoundary>
        </div>
      )}

      {docksOpen != null ? (
        <ErrorBoundary fallback={null}>
          <Suspense fallback={null}><DockEditor propertyId={docksOpen} /></Suspense>
        </ErrorBoundary>
      ) : <WarehouseCard key={selectedId ?? 'none'} />}
      <CompareView />
      <CompareDuel />
      <CostPanel />
      <FloodPanel />
      <Stage />
      {interiorOpen && (
        <ErrorBoundary fallback={null}>
          <Suspense fallback={null}><InteriorWorkspace open={interiorOpen} onClose={() => useStore.setState({ interiorOpen: null })} /></Suspense>
        </ErrorBoundary>
      )}

      <DraggableDock defaultStyle={orbBox} hidden={!!interiorOpen}>
        <ErrorBoundary fallback={null}>
          <Suspense fallback={null}>
            <VoiceOrb inline onUi={onUi} context={{ selectedId: selectedId ?? undefined, chips, visible: visibleIds, market }} />
          </Suspense>
        </ErrorBoundary>
      </DraggableDock>
    </div>
  )
}

function YardFallback({ onClose }: { onClose: () => void }) {
  return (
    <div className="glass pointer-events-auto absolute left-[376px] top-[132px] flex items-center gap-2 rounded-xl px-3 py-2 text-[12px] text-ink-2">
      The yard replay could not be shown. <button onClick={onClose} className="rounded p-0.5 hover:bg-white/10"><X size={13} /></button>
    </div>
  )
}

/** The product mark, top-left (the full list used to carry it). */
function Brand() {
  const ranked = useStore((s) => s.search?.mode === 'ranked' || !!s.ranking)
  const listOpen = useStore((s) => s.listOpen)
  const dot = (c: string) => <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: c }} />
  return (
    <>
      <div className="glass pointer-events-none absolute left-4 top-4 z-20 flex h-11 items-center gap-2 rounded-xl px-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/15 text-accent" aria-hidden="true"><WarehouseIcon size={16} /></span>
        <span className="text-[15px] font-semibold tracking-tight" title={APP_TAGLINE}>{APP_NAME}</span>
      </div>
      {/* marker legend: colour = availability (or rank when results are ranked), ring = 3D scan, size = building size */}
      <div className={`glass pointer-events-none absolute left-4 z-10 flex h-9 items-center gap-3 rounded-xl px-3 text-[11.5px] text-ink-2 ${listOpen ? 'hidden' : 'bottom-[156px]'}`} style={{ transform: 'translate(var(--strip-dx, 0px), var(--strip-dy, 0px))' }} data-testid="map-legend">
        {ranked ? <><span className="flex items-center gap-1.5">{dot('#4fd1c5')} Top 5</span><span className="flex items-center gap-1.5">{dot('#389691')} Match</span></>
          : <><span className="flex items-center gap-1.5">{dot('#4fd1c5')} Available</span><span className="flex items-center gap-1.5">{dot('#94a3b8')} Not listed</span><span className="flex items-center gap-1.5">{dot('#64748b')} Off market</span></>}
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-2.5 rounded-full ring-2 ring-white/90" /> 3D scan</span>
        <span className="text-ink-3">size = building SF</span>
      </div>
    </>
  )
}
