import { create } from 'zustand'
import { api, ApiError, type Band, type Chip, type Market, type Pin, type SearchResponse } from './api'
import { mapApi } from './mapApi'
import { rank, type Ranking } from './metrics'
import { sendEvent } from './guide'

/** The old 12-hour shift UI is hidden; reachable only with ?devsim or localStorage proinspect.devSim = 1. */
export const DEV_SIM = (() => {
  try { return new URLSearchParams(location.search).has('devsim') || localStorage.getItem('proinspect.devSim') === '1' } catch { return false }
})()

const SHORTLIST_KEY = 'proinspect.shortlist'
const loadShortlist = (): number[] => {
  try { const v = JSON.parse(localStorage.getItem(SHORTLIST_KEY) ?? '[]'); return Array.isArray(v) ? v.map(Number).filter(Number.isFinite) : [] } catch { return [] }
}
const saveShortlist = (ids: number[]) => { try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify(ids)) } catch { /* private mode */ } }

export type StageMode = 'view' | 'markDoors' | 'fit' | 'align'
export type TestKind = 'yard' | 'equipment' | 'storage'
export interface PlaceMarker { lat: number; lon: number; label: string; thumb_url?: string; likelihood?: string; score?: number; distance_m?: number; nearId?: number }
export interface RankState { metric: string; market: string; loading: boolean; error: string | null; data: Ranking | null }

export const MARKETS: Market[] = ['Houston', 'Los Angeles']

export interface StageState { modelId: string; sceneUrl?: string; title: string; subtitle?: string; kpi?: Record<string, Band | null>; runId?: string; propertyId?: number; mode?: StageMode; fitResult?: unknown }

interface State {
  pins: Pin[]
  pinsById: Map<number, Pin>
  pinsError: string | null
  market: Market
  query: string
  search: SearchResponse | null
  searching: boolean
  searchError: string | null
  selectedId: number | null
  hoveredId: number | null
  highlightIds: number[]
  compareIds: number[]
  compareOpen: boolean
  /** operation key for the compare panel (library/criteria.yaml); null = the server default */
  compareProfile: string | null
  drawerTab: 'overview' | 'test' | 'simulate'
  testKind: TestKind | null
  /** the S3 yard replay mounted over the map */
  yard: { propertyId: number; result: unknown | null } | null
  /** S3: the property whose Dock editor is open (replaces the card in the right column) */
  docksOpen: number | null
  interiorOpen: { pid: number; modelId: string; title: string } | null
  listOpen: boolean
  /** the bottom strip's ids in on-screen order (the analyst reads it: "first" = [0]) */
  visibleIds: number[]
  /** what the buyer told the analyst (update_buyer_profile), shown as chips */
  buyerProfile: Record<string, unknown> | null
  /** an explicit list the analyst put on the strip (closest to X, comparables, a ranking within a set) */
  curated: { title: string; ids: number[]; values?: Record<string, string> } | null
  /** the True-cost panel beside the property card (property id), null = closed */
  costOpen: number | null
  /** the Flood-exposure panel beside the property card (property id), null = closed */
  floodOpen: number | null
  /** the full property card (evidence / tests) instead of the one-glance panel */
  cardDetail: boolean
  shortlist: number[]
  /** latest test results by `yard:<id>` / `fit:<id>` */
  lastTest: Record<string, unknown>
  shortlistOpen: boolean
  place: PlaceMarker | null
  ranking: RankState | null
  /** a showAerial command: the card scrolls to the aerial section and picks the view */
  aerialReq: { id: number; view?: string; year?: number; nonce: number } | null
  stage: StageState | null
  sdkKey: string
  /** a simulate request coming from the voice agent; the Simulate panel consumes it */
  simRequest: { id: number; profile: string; volume_scale?: number; run_id?: string; nonce: number } | null

  loadPins: () => Promise<void>
  setMarket: (m: Market, fly?: boolean) => void
  setQuery: (q: string) => void
  runSearch: (p?: { query?: string; chips?: Chip[] }) => Promise<void>
  clearSearch: () => void
  select: (id: number | null, opts?: { fly?: boolean; tab?: State['drawerTab'] }) => void
  hover: (id: number | null) => void
  setHighlight: (ids: number[]) => void
  setCompare: (ids: number[], profile?: string | null) => void
  setCompareProfile: (profile: string | null) => void
  showCompare: (open: boolean) => void
  toggleCompare: (id: number) => void
  setTab: (t: State['drawerTab']) => void
  openStage: (s: StageState | null) => void
  requestSim: (r: { id: number; profile: string; volume_scale?: number; run_id?: string }) => void
  openTest: (id: number, kind: TestKind | null) => void
  setYard: (y: State['yard']) => void
  shortlistDo: (action: 'add' | 'remove' | 'toggle' | 'show' | 'hide' | 'clear', id?: number) => void
  setPlace: (p: PlaceMarker | null) => void
  rankBy: (metric: string, market?: Market) => Promise<void>
  clearRanking: () => void
  showAerial: (id: number, view?: string, year?: number) => void
}

let searchSeq = 0

export const useStore = create<State>((set, get) => ({
  pins: [],
  pinsById: new Map(),
  pinsError: null,
  market: 'Houston',
  query: '',
  search: null,
  searching: false,
  searchError: null,
  selectedId: null,
  hoveredId: null,
  highlightIds: [],
  compareIds: [],
  compareOpen: false,
  compareProfile: null,
  drawerTab: 'overview',
  stage: null,
  sdkKey: '',
  simRequest: null,
  testKind: null,
  yard: null,
  docksOpen: null,
  interiorOpen: null,
  listOpen: false,
  visibleIds: [],
  buyerProfile: null,
  curated: null,
  costOpen: null,
  floodOpen: null,
  cardDetail: false,
  shortlist: loadShortlist(),
  lastTest: {},
  shortlistOpen: false,
  place: null,
  ranking: null,
  aerialReq: null,

  loadPins: async () => {
    try {
      const pins = await api.warehouses()
      set({ pins, pinsById: new Map(pins.map((p) => [p.id, p])), pinsError: null })
    } catch (e) {
      set({ pinsError: e instanceof Error ? e.message : 'Could not load warehouses' })
    }
    api.config().then((c) => set({ sdkKey: c.matterport_sdk_key ?? '' })).catch(() => {})
  },

  setMarket: (m, fly = true) => {
    if (m === get().market) { if (fly) mapApi.flyToMarket(m); return }
    const sel = get().selectedId
    const keepSel = sel != null && get().pinsById.get(sel)?.market === m
    set({ market: m, selectedId: keepSel ? sel : null, highlightIds: [], compareIds: [] })
    if (fly) mapApi.flyToMarket(m)
    const s = get().search
    // re-run the active search in the new market (chips only: no re-parse, no Claude call)
    if (s && s.mode === 'ranked') {
      get().runSearch({ chips: s.chips.filter((c) => c.field !== 'market') })
    }
  },

  setQuery: (q) => set({ query: q }),

  runSearch: async (p = {}) => {
    const seq = ++searchSeq
    const query = p.query ?? (p.chips ? undefined : get().query)
    if (p.query !== undefined) set({ query: p.query })
    if (!p.chips && !query?.trim()) { get().clearSearch(); return }
    set({ searching: true, searchError: null })
    try {
      const body: Parameters<typeof api.search>[0] = { requirements: { market: get().market } }
      if (p.chips) body.chips = p.chips
      else body.query = query
      let res = await api.search(body)
      if (seq !== searchSeq) return
      // the query named the other market ("in LA"): switch tabs and re-rank there with the same chips
      const mChip = res.chips.find((c) => c.field === 'market')
      const named = mChip && MARKETS.find((m) => m === mChip.value)
      if (named && named !== get().market) {
        set({ market: named, selectedId: null })
        mapApi.flyToMarket(named)
        res = await api.search({ chips: res.chips, requirements: { market: named } })
        if (seq !== searchSeq) return
      }
      set({ search: res, searching: false, highlightIds: [], ranking: null, curated: null })
      sendEvent('searchResults', {
        query: res.query, mode: res.mode, market: get().market, counts: res.counts,
        chips: res.chips.map((c) => ({ field: c.field, value: c.value, op: c.op })),
        top: res.results.slice(0, 5).map((r) => ({ id: r.id, address: r.address, score: r.score, rank: r.rank, fails: r.checks.filter((c) => c.status === 'fail').map((c) => c.label) })),
        excluded: res.excluded.slice(0, 8).map((e) => ({ id: e.id, why: e.why[0] })),
      })
      const top = res.results.slice(0, 12)
      if (res.mode === 'ranked' && top.length) mapApi.fitTo(top.map((r) => [r.lon, r.lat]))
    } catch (e) {
      if (seq !== searchSeq) return
      set({ searching: false, searchError: e instanceof ApiError && e.status === 0
        ? 'Search service is unreachable' : 'Search failed. Try rephrasing.' })
    }
  },

  clearSearch: () => { ++searchSeq; set({ search: null, query: '', searching: false, searchError: null }) },

  select: (id, opts = {}) => {
    const changed = id !== get().selectedId
    set({ selectedId: id, drawerTab: opts.tab ?? (changed ? 'overview' : get().drawerTab), testKind: changed ? null : get().testKind,
          cardDetail: opts.tab === 'test' ? true : changed ? false : get().cardDetail })
    mapApi.setPadding({ right: id == null ? 40 : 440 })
    if (id == null) return
    const pin = get().pinsById.get(id)
    if (pin && pin.market !== get().market) set({ market: pin.market })
    if (pin && opts.fly !== false) mapApi.flyToPin(pin)
    if (changed && pin) {
      const r = get().search?.results.find((x) => x.id === id)
      sendEvent('openCard', { id, address: pin.address, market: pin.market, rba_sqft: pin.rba_sqft, clear_height_ft: pin.clear_height_ft,
        availability: pin.availability, inspectable: pin.inspectable, rank: r?.rank, score: r?.score,
        shortlisted: get().shortlist.includes(id) })
    }
  },
  hover: (id) => { if (id !== get().hoveredId) set({ hoveredId: id }) },
  setHighlight: (ids) => set({ highlightIds: ids }),
  setCompare: (ids, profile) => set({ compareIds: ids.slice(0, 4), compareOpen: ids.length >= 2,
    ...(profile !== undefined ? { compareProfile: profile ? String(profile) : null } : {}) }),
  setCompareProfile: (profile) => set({ compareProfile: profile }),
  showCompare: (open) => set({ compareOpen: open }),
  toggleCompare: (id) => {
    const c = get().compareIds
    const next = c.includes(id) ? c.filter((x) => x !== id) : [...c, id].slice(-4)
    set({ compareIds: next, compareOpen: get().compareOpen && next.length >= 2 })
  },
  setTab: (t) => set({ drawerTab: t }),
  openStage: (s) => set({ stage: s }),
  requestSim: (r) => {
    if (!DEV_SIM) { get().openTest(r.id, 'yard'); return }
    set({ simRequest: { ...r, nonce: Date.now() } })
    get().select(r.id, { tab: 'simulate' })
  },
  openTest: (id, kind) => {
    get().select(id, { tab: 'test' })
    set({ drawerTab: 'test', testKind: kind, cardDetail: true })
  },
  setYard: (y) => set({ yard: y }),
  shortlistDo: (action, id) => {
    const cur = get().shortlist
    let next = cur
    if (action === 'add' && id != null && !cur.includes(id)) next = [...cur, id]
    else if (action === 'remove' && id != null) next = cur.filter((x) => x !== id)
    else if (action === 'toggle' && id != null) next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    else if (action === 'clear') next = []
    if (next !== cur) { saveShortlist(next); set({ shortlist: next }) }
    if (action === 'add' || action === 'show') set({ shortlistOpen: true })
    if (action === 'hide') set({ shortlistOpen: false })
    if (action === 'show') {
      const pts = next.map((i) => get().pinsById.get(i)).filter(Boolean).map((p) => [p!.lon, p!.lat] as [number, number])
      set({ highlightIds: next })
      if (pts.length) mapApi.fitTo(pts)
    }
  },
  setPlace: (p) => {
    set({ place: p })
    if (!p) return
    // frame the place together with the warehouse it was found for
    const near = p.nearId != null ? get().pinsById.get(p.nearId) : get().selectedId != null ? get().pinsById.get(get().selectedId!) : undefined
    if (near) mapApi.fitTo([[near.lon, near.lat], [p.lon, p.lat]], 15.5)
    else mapApi.flyToPoint(p.lon, p.lat)
  },
  rankBy: async (metric, market) => {
    const m = market ?? get().market
    if (m !== get().market) get().setMarket(m)
    set({ curated: null, ranking: { metric, market: m, loading: true, error: null, data: get().ranking?.metric === metric ? get().ranking!.data : null } })
    try {
      const data = await rank(metric, m, get().pins)
      if (get().ranking?.metric !== metric) return
      set({ ranking: { metric, market: m, loading: false, error: null, data } })
      const pts = data.rows.slice(0, 10).map((r) => get().pinsById.get(r.id)).filter(Boolean).map((p) => [p!.lon, p!.lat] as [number, number])
      if (pts.length) mapApi.fitTo(pts)
    } catch {
      set({ ranking: { metric, market: m, loading: false, error: 'Ranking could not be computed', data: null } })
    }
  },
  clearRanking: () => set({ ranking: null, curated: null }),
  showAerial: (id, view, year) => {
    if (get().selectedId !== id) get().select(id, { tab: 'overview' })
    else set({ drawerTab: 'overview' })
    set({ aerialReq: { id, view, year, nonce: Date.now() } })
  },
}))
