// Ranking metrics for `rankBy` (CONTRACTS v2 GET /api/vexcel/rank). Each has a plain definition shown above the list,
// so "biggest parking lot" always names the metric it used.
import { api2, ApiError, type ExteriorTwin, type Pin, type RankRow } from './api'

export interface MetricDef { key: string; label: string; unit: string; better: 'max' | 'min'; definition: string; alt?: string }

const DEFS: MetricDef[] = [
  { key: 'parking_area_sqft', label: 'Parking area', unit: 'SF', better: 'max', alt: 'car_stalls_est',
    definition: 'Area Vexcel segments as "parking lot" on the parcel ortho (SAM3 mask, capture date shown).' },
  { key: 'paved_area_sqft', label: 'Paved area', unit: 'SF', better: 'max', alt: 'parking_area_sqft',
    definition: 'All paved non-building ground on the parcel (Vexcel hardscapes attribute).' },
  { key: 'car_stalls_est', label: 'Car stalls (est.)', unit: 'stalls', better: 'max', alt: 'parking_area_sqft',
    definition: 'Estimate: parking area ÷ 350 SF per stall (incl. aisles). Not a count.' },
  { key: 'trucks_seen', label: 'Trucks seen', unit: 'trucks', better: 'max', alt: 'trailers_seen',
    definition: 'Trucks counted on the parcel in the latest Vexcel aerial (one snapshot, not a daily average).' },
  { key: 'trailers_seen', label: 'Trailers seen', unit: 'trailers', better: 'max', alt: 'trucks_seen',
    definition: 'Semi trailers counted on the parcel in the latest Vexcel aerial (one snapshot).' },
  { key: 'truck_court_ft', label: 'Truck court depth', unit: 'ft', better: 'max',
    definition: 'Clear paved depth in front of the main dock wall, measured on the Vexcel ortho + DSM (median ray).' },
  { key: 'roof_condition', label: 'Roof condition', unit: '/5', better: 'max',
    definition: 'Vexcel roof condition score, 1 (poor) to 5 (excellent), from the latest aerial.' },
  { key: 'solar', label: 'Solar', unit: '', better: 'max', definition: 'Solar panels detected on the roof by Vexcel.' },
  { key: 'tree_cover_pct', label: 'Tree cover', unit: '%', better: 'min', definition: 'Share of the parcel under tree canopy (Vexcel).' },
  { key: 'building_height_ft', label: 'Building height', unit: 'ft', better: 'max',
    definition: 'Roof height above ground from the Vexcel DSM (90th percentile over the footprint).' },
]
export const METRICS: Record<string, MetricDef> = Object.fromEntries(DEFS.map((d) => [d.key, d]))

const PLACE_LABEL: Record<string, string> = {
  grocery: 'grocery store', highway_ramp: 'highway on-ramp', rail_yard: 'rail yard', truck_stop: 'truck stop',
  port_container: 'port / container yard', residential: 'residential area',
}
export const placeLabel = (k: string) => PLACE_LABEL[k] ?? k.replace(/_/g, ' ')

const ALIASES: Record<string, string> = {
  parking: 'parking_area_sqft', parking_lot: 'parking_area_sqft', biggest_parking_lot: 'parking_area_sqft',
  paved: 'paved_area_sqft', stalls: 'car_stalls_est', trucks: 'trucks_seen', trailers: 'trailers_seen',
  truck_court: 'truck_court_ft', roof: 'roof_condition', height: 'building_height_ft', trees: 'tree_cover_pct',
}

export function metricDef(metric: string): MetricDef {
  const m = ALIASES[metric] ?? metric
  if (METRICS[m]) return METRICS[m]
  const dm = /^distance_to:(.+)$/.exec(m)
  if (dm) {
    const what = placeLabel(dm[1])
    return { key: m, label: `Distance to ${what}`, unit: 'mi', better: 'min',
      definition: `Straight-line distance to the best Vexcel image-embedding match for "${what}" within ~3 km. Matches are "likely" or "possible", never certain.` }
  }
  return { key: m, label: m.replace(/_/g, ' '), unit: '', better: 'max', definition: 'Metric as defined by the ranking service.' }
}

const extCache = new Map<number, Promise<ExteriorTwin | null>>()
const ext = (id: number) => {
  if (!extCache.has(id)) extCache.set(id, fetch(`/media/twins/exterior/${id}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null))
  return extCache.get(id)!
}

/** Fallback while the S1 rank endpoint is missing: compute what the cached exterior twins already hold. */
function fromTwin(metric: string, e: ExteriorTwin): number | null {
  const site = e.site as Record<string, number> | undefined
  const v = e.vehicles as Record<string, number> | undefined
  switch (metric) {
    case 'paved_area_sqft':
      return site?.parcel_acres != null && site?.paved_pct_of_parcel != null ? Math.round(site.parcel_acres * 43560 * site.paved_pct_of_parcel / 100) : null
    case 'trucks_seen': return v?.truck ?? null
    case 'trailers_seen': return v ? (v['semi trailer'] ?? 0) + (v.trailers_at_docks ?? 0) : null
    case 'truck_court_ft': {
      const c = [...(e.courts ?? [])].sort((a, b) => (b.doors ?? 0) - (a.doors ?? 0))[0]
      return c?.depth_ft ?? null
    }
    case 'roof_condition': return e.building?.roof_condition ?? null
    case 'building_height_ft': return e.building?.height_ft_dsm_p90 ?? null
    case 'tree_cover_pct': return (site as Record<string, number> | undefined)?.tree_cover_pct_vexcel ?? null
    case 'solar': return e.building?.solar == null ? null : e.building.solar ? 1 : 0
    default: return null
  }
}

export interface Ranking { metric: MetricDef; rows: RankRow[]; via: 'service' | 'cached_twin'; note?: string }

interface AttrTierRow { id: number; value: number | null; lo?: number | null; hi?: number | null; source?: string; status?: string; basis?: string }
interface AttrRanking { attr: string; label: string; unit: string; order: 'asc' | 'desc'; verified: AttrTierRow[]; estimated: AttrTierRow[];
  unknown: { id: number; why?: string }[]; coverage: { verified: number; estimated: number; unknown: number } }

/** CoStar-attribute ranking (CONTRACTS v3 GET /api/rank/attribute): verified (CoStar) first, then imagery estimates,
 *  labelled as such; unknown buildings are counted, never ranked as 0. Metric key: "attr:<attr>". */
async function rankAttribute(spec: string, market: string, top: number): Promise<Ranking> {
  const [attr, order] = spec.split(':')         // "rba_sqft:asc" = smallest first
  const q = new URLSearchParams({ attr, market, top: String(top), ...(order ? { order } : {}) })
  const r = await fetch(`/api/rank/attribute?${q}`)
  if (!r.ok) throw new ApiError(r.status, `rank attribute ${r.status}`)
  const d = (await r.json()) as AttrRanking
  const unit = d.unit === 'doors' ? '' : d.unit === 'sqft' ? 'SF' : d.unit
  const fmtUnit = (v: number) => (d.unit === 'doors' ? `${v} doors` : unit === 'SF' ? `${Math.round(v).toLocaleString()} SF` : `${v}${unit ? ' ' + unit : ''}`)
  const rows: RankRow[] = [
    ...d.verified.map((x) => ({ id: x.id, value: x.value == null ? null : fmtUnit(x.value), source: x.source, tag: x.source ?? 'CoStar' })),
    ...d.estimated.map((x) => {
      const v = x.lo != null && x.hi != null && x.lo !== x.hi ? `${x.lo}–${x.hi}` : x.status === 'lower_bound' && x.lo != null ? `≥ ${x.lo}` : x.value ?? '?'
      return { id: x.id, value: `${v}${d.unit === 'doors' ? ' doors' : ''}`, source: x.source, tag: 'estimate', est: true }
    }),
  ]
  const label = d.label.replace(/^\w/, (c) => c.toUpperCase()) + (d.order === 'asc' ? (/sf|sq/i.test(d.unit) ? ' (smallest first)' : ' (lowest first)') : '')
  return {
    metric: { key: `attr:${spec}`, label, unit: '', better: d.order === 'asc' ? 'min' : 'max',
      definition: `CoStar value where CoStar has it; otherwise an imagery estimate (dashed tag: a range or lower bound, not a count).` },
    rows, via: 'service',
    note: `${d.coverage.verified} from CoStar · ${d.coverage.estimated} estimated from imagery · ${d.coverage.unknown} unknown (not ranked, never counted as 0).`,
  }
}

export async function rank(metric: string, market: string, pins: Pin[], top = 20): Promise<Ranking> {
  if (metric.startsWith('attr:')) return rankAttribute(metric.slice(5), market, top)
  const def = metricDef(metric)
  try {
    const r = await api2.vexcelRank(def.key, market, top)
    const rows = Array.isArray(r) ? r : r.results
    const m = (!Array.isArray(r) ? r.metric : null) as { label?: string; definition?: string; unit?: string; order?: string; alternatives?: string[] } | null
    const UNIT: Record<string, string> = { sqft: 'SF', count: '', '1-5': '/5' }
    const merged: MetricDef = m ? { ...def, label: m.label ? m.label.replace(/^\w/, (c) => c.toUpperCase()) : def.label,
      definition: m.definition ?? def.definition, unit: m.unit != null ? UNIT[m.unit] ?? m.unit : def.unit,
      better: m.order === 'asc' ? 'min' : m.order === 'desc' ? 'max' : def.better, alt: m.alternatives?.[0] ?? def.alt } : def
    const unknown = !Array.isArray(r) ? (r as { unknown?: unknown[] | number }).unknown : undefined
    const nUnknown = Array.isArray(unknown) ? unknown.length : typeof unknown === 'number' ? unknown : 0
    return { metric: merged, rows: rows.map((x) => ({ ...x, unit: x.unit != null ? UNIT[x.unit] ?? x.unit : merged.unit })), via: 'service',
      note: nUnknown ? `${nUnknown} warehouse${nUnknown === 1 ? '' : 's'} not measured for this metric (left out).` : undefined }
  } catch (e) {
    if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 0)) throw e
  }
  const inMarket = pins.filter((p) => p.market === market)
  const vals = await Promise.all(inMarket.map(async (p) => {
    const e = await ext(p.id)
    return { id: p.id, value: e ? fromTwin(def.key, e) : null, unit: def.unit, source: 'vexcel', date: e?.imagery?.parcel_date }
  }))
  const have = vals.filter((x) => x.value != null) as (RankRow & { value: number })[]
  have.sort((a, b) => (def.better === 'max' ? b.value - a.value : a.value - b.value))
  const note = have.length ? `Computed in the browser from the cached exterior twins (${have.length} of ${inMarket.length} have it): the ranking service is not deployed yet.`
    : 'The ranking service is not deployed yet and the cached twins do not hold this metric.'
  return { metric: def, rows: have.slice(0, top), via: 'cached_twin', note }
}
