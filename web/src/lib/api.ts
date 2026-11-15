// Typed client for the ProInspect API (docs/CONTRACTS.md). Endpoints owned by other streams may 404 while they are
// being built: every caller handles `ApiError` with status 404 as "not available yet".

export type Market = 'Houston' | 'Los Angeles'
export type Source = 'costar' | 'matterport' | 'vexcel' | 'derived' | string

export interface Pin {
  id: number
  name: string | null
  address: string
  city: string
  market: Market
  lat: number
  lon: number
  subtype: string
  rba_sqft: number | null
  clear_height_ft: number | null
  year_built: number | null
  availability: 'available' | 'not_listed' | 'off_market_listing' | string
  inspectable: boolean
  photo: string | null
}

export interface Fact {
  value: number | string | boolean | null
  source: Source | null
  confidence: number | null
  detail: string | null
  alt: { value: number | string; source: Source }[]
  bound: unknown
}

export interface Scan { model_id: string; class: string; floor_sqft: number | null; height_ft: number | null; panos?: number; name?: string }

export interface Listing {
  listing_id: number; type: string; status: string; broker: string | null; kind: string
  available_sqft: number | null; rent_low_psf_yr: number | null; rent_high_psf_yr: number | null
}

export interface WarehouseDetail extends Pin {
  state?: string
  zip?: string
  building_class?: string | null
  rba_sqft: number | null
  lot_acres?: number | null
  docks_external?: number | null
  drive_ins?: number | null
  drive_in_w_ft?: number | null
  drive_in_h_ft?: number | null
  power_amps?: number | null
  power_volts?: number | null
  power_phases?: number | null
  sprinklers?: string | null
  scans: Scan[]
  listings: Listing[]
  tenants?: { current_count: number; known: { name: string; industry: string }[]; occupied_sqft: number | null }
  facts: Record<string, Fact>
}

export interface Chip {
  field: string
  label: string
  value: unknown
  said: string
  source: 'rules' | 'claude' | 'user' | string
  op: string
  conflict: { claude?: unknown; kept?: string } | null
  [k: string]: unknown
}

export type CheckStatus = 'pass' | 'near' | 'fail' | 'unknown'
export interface Check {
  fact: string; label: string; unit?: string; level: string; weight: number
  need: Record<string, unknown>; have: unknown; source: Source | null; confidence: number | null
  alt: { value: unknown; source: Source }[]; status: CheckStatus; score: number
}

export interface Result {
  id: number; address: string; market: Market; lat: number; lon: number
  score: number; band: [number, number]; excluded: boolean; excluded_because: string[]
  unverified_dealbreakers: string[]; counts: Record<CheckStatus, number>; inspectable: boolean
  checks: Check[]; mode: 'browse' | 'ranked'; rank: number
}

export interface SearchResponse {
  query: string | null; chips: Chip[]; unparsed: string | null; used_claude: boolean | null
  claude_error: string | null; mode: 'browse' | 'ranked'
  counts: { shown: number; excluded: number; total: number }
  requirements: Record<string, unknown>
  results: Result[]
  excluded: { id: number; address: string; why: string[] }[]
}

export interface Profile {
  name: string; icon: string; description: string; ref_sqft: number; shift_hours: number
  inbound: Record<string, number>; outbound: Record<string, number>
}

export interface Band { p10: number; p50: number; p90: number; n: number }
export interface SimSummary {
  profile: string; site: string; runs: number; volume_scale: number; seconds: number
  kpi: Record<string, Band | null>
  layout: Record<string, unknown>
  fleet: unknown
  assumptions: string[]
}
export interface SimRun {
  run_id: string; summary: SimSummary; model_id?: string; scene_url?: string; cached?: boolean; property_id?: number
  fusion?: { doors_source?: 'assumed' | 'vexcel' | string; why?: string; registration?: { confidence?: number; ok?: boolean; why?: string } | null }
}
export interface Hero { property_id: number; model_id: string; profile: string; run_id: string; scene_url?: string; headline?: string; demo_worthy?: boolean; doors_source?: string; registration_confidence?: number }

export interface ExteriorTwin {
  property_id: number
  imagery?: { parcel_collection?: string; parcel_date?: string; obliques?: string[]; dsm_date?: string }
  frame: { lat0: number; lon0: number; half_m: number; res_m: number }
  building?: {
    footprint_sqft?: number; height_ft_dsm_p90?: number; roof_condition?: number | null; roof_condition_scale?: string
    roof_material?: string | null; roof_defects?: string[]; skylights?: number; solar?: boolean
    footprint_local?: [number, number][]
  }
  doors?: { total: number; dock_high: number; drive_in: number; uncertain: number; note?: string
    items: { x: number; y: number; class: string; facing: string; confidence: number; width_ft?: number }[] }
  courts?: { facing: string; doors: number; depth_ft: number | null; p10_ft?: number; p90_ft?: number; confidence: string; verdict_53ft?: string }[]
  vehicles?: Record<string, number>
  site?: { parcel_acres?: number; paved_pct_of_parcel?: number }
}
export interface Twin {
  exterior: ExteriorTwin | null
  interior: { model_id: string; deck_ft?: number; clear?: { p5_ft?: number; p50_ft?: number } | number | null; clear_confidence?: string | number; office_sqft?: number; floor?: { indoor_sqft?: number; free_sqft?: number } } | null
  scans: Scan[]
  media: { ortho?: string | null; obliques?: Record<string, string> }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, init)
  } catch {
    throw new ApiError(0, 'The Warehouse PlayGround service is unreachable')
  }
  if (!r.ok) throw new ApiError(r.status, `${url} returned ${r.status}`)
  return r.json() as Promise<T>
}

const post = (body: unknown): RequestInit => ({
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
})

export const api = {
  warehouses: () => req<Pin[]>('/api/warehouses'),
  warehouse: (id: number) => req<WarehouseDetail>(`/api/warehouses/${id}`),
  search: (body: { query?: string; chips?: Chip[]; requirements?: Record<string, unknown> }) =>
    req<SearchResponse>('/api/search', post(body)),
  library: () => req<{ profiles: Record<string, Profile>; equipment: Record<string, unknown> }>('/api/library'),
  config: () => req<{ matterport_sdk_key: string; markets: string[] }>('/api/config'),
  twin: async (id: number): Promise<Twin> => {
    try {
      return await req<Twin>(`/api/warehouses/${id}/twin`)
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 404) throw e
      // Twin endpoint (stream B) not deployed yet: read the same precomputed exterior twin from /media directly.
      const ext = await req<ExteriorTwin>(`/media/twins/exterior/${id}.json`).catch(() => null)
      if (!ext) throw e
      return { exterior: ext, interior: null, scans: [], media: { ortho: `/media/twins/exterior/${id}_ortho.jpg` } }
    }
  },
  simRun: (body: { property_id: number; profile: string; runs?: number; volume_scale?: number; model_id?: string }) =>
    req<SimRun>('/api/sim/run', post({ runs: 10, volume_scale: 1.0, ...body })),
  heroes: () => req<Hero[]>('/api/sim/heroes'),
  simGet: (runId: string) => req<SimRun>(`/api/sim/${encodeURIComponent(runId)}`),
}

// ---- v2 (docs/CONTRACTS.md v2): Vexcel profile, ranking, semantic search, the two focused tests ----

/** A profile value: a bare number/string, or {value, source, date, method, estimate, ...}. Read with `pv()`. */
export type PV = number | string | boolean | null | undefined | { value?: unknown; source?: string; date?: string; method?: string; estimate?: boolean; [k: string]: unknown }

export interface NearbyHit {
  lat?: number; lon?: number; score?: number; distance_m?: number; likelihood?: 'likely' | 'possible' | string
  thumb_url?: string; label?: string; date?: string
}

export interface VexcelProfile {
  id: number
  captured?: { ortho?: string; dsm?: string; parcel_attrs?: string }
  parking?: { paved_area_sqft?: PV; parking_lot_area_sqft?: PV; car_stalls_est?: PV; cars_seen?: PV; trucks_seen?: PV; trailers_seen?: PV; truck_court_ft?: PV }
  building?: { footprint_sqft?: PV; height_ft_dsm?: PV; roof_condition?: PV; roof_material?: PV; roof_defects?: string[] | PV; solar?: PV; ac_units?: PV; skylights?: PV }
  site?: { parcel_acres?: PV; paved_pct?: PV; tree_cover_pct?: PV }
  history?: { ortho_dates?: string[]; change?: { years?: [number, number] | number[]; new_structure_m2?: number | null; removed_m2?: number | null; note?: string } | null }
  nearby?: Record<string, NearbyHit | NearbyHit[] | null>
  sources?: Record<string, unknown>
  confidence?: Record<string, unknown>
  /** set by the web client when the profile endpoint is not deployed and the card falls back to the cached exterior twin */
  _fallback?: string
}

export interface RankRow { id: number; value: number | string | null; unit?: string; source?: string; date?: string; tag?: string; est?: boolean }
export interface PlaceHit extends NearbyHit { lat: number; lon: number }

export const api2 = {
  vexcel: (id: number) => req<VexcelProfile>(`/api/vexcel/${id}`),
  vexcelRank: (metric: string, market?: string, top = 20) =>
    req<RankRow[] | { results: RankRow[]; metric?: unknown }>(`/api/vexcel/rank?metric=${encodeURIComponent(metric)}${market ? `&market=${encodeURIComponent(market)}` : ''}&top=${top}`),
  vexcelSearch: (q: { text: string; near_id?: number; lat?: number; lon?: number; radius_m?: number }) => {
    const p = new URLSearchParams({ text: q.text })
    if (q.near_id != null) p.set('near_id', String(q.near_id))
    if (q.lat != null) p.set('lat', String(q.lat))
    if (q.lon != null) p.set('lon', String(q.lon))
    if (q.radius_m != null) p.set('radius_m', String(q.radius_m))
    return req<PlaceHit[]>(`/api/vexcel/search?${p}`)
  },
  yardTest: (body: Record<string, unknown>) => req<Record<string, unknown>>('/api/yard/test', post(body)),
  fitTest: (body: Record<string, unknown>) => req<Record<string, unknown>>('/api/fit/test', post(body)),
  doors: (id: number) => req<{ doors?: unknown[] } | null>(`/api/warehouses/${id}/doors`),
}
