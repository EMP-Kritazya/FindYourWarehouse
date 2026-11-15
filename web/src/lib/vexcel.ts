// Vexcel profile loader (GET /api/vexcel/{id}, S1). While that endpoint is not deployed, fall back to the cached
// exterior twin, which already holds part of the same Vexcel evidence (labelled as such on the card).
import { api, api2, ApiError, type PV, type Twin, type VexcelProfile } from './api'

export interface Val { v: unknown; source: string; date?: string; method?: string; estimate: boolean; note?: string }

/** Read a profile value that may be bare or {value, source, date, method, estimate}. */
export function pv(x: PV, fallbackDate?: string, meta?: unknown): Val | null {
  if (x === null || x === undefined) return null
  const m = (meta && typeof meta === 'object' ? meta : {}) as Record<string, unknown>
  if (typeof x === 'object') {
    const o = x as Record<string, unknown>
    const v = 'value' in o ? o.value : 'count' in o ? o.count : 'area_sqft' in o ? o.area_sqft : null
    if (v === null || v === undefined) return o.note ? { v: null, source: String(o.source ?? 'vexcel'), estimate: false, note: String(o.note) } : null
    return {
      v, source: String(o.source ?? m.source ?? 'vexcel'), date: (o.date ?? o.captured ?? m.date ?? fallbackDate) as string | undefined,
      method: (o.method ?? m.method) as string | undefined, estimate: !!(o.estimate ?? m.estimate ?? /estimate/i.test(String(o.method ?? o.label ?? ''))),
      note: [o.note, o.source && `source: ${o.source}`].filter(Boolean).join(' · ') || undefined,
    }
  }
  return { v: x, source: String(m.source ?? 'vexcel'), date: (m.date as string | undefined) ?? fallbackDate, method: m.method as string | undefined, estimate: !!m.estimate }
}

function fromTwin(id: number, t: Twin): VexcelProfile | null {
  const e = t.exterior
  if (!e) return null
  const site = (e.site ?? {}) as Record<string, number>
  const veh = (e.vehicles ?? {}) as Record<string, number>
  const date = e.imagery?.parcel_date
  const court = [...(e.courts ?? [])].sort((a, b) => (b.doors ?? 0) - (a.doors ?? 0))[0]
  const paved = site.parcel_acres != null && site.paved_pct_of_parcel != null ? Math.round(site.parcel_acres * 43560 * site.paved_pct_of_parcel / 100) : null
  const b = e.building ?? {}
  return {
    id,
    captured: { ortho: date, dsm: e.imagery?.dsm_date, parcel_attrs: date },
    parking: {
      paved_area_sqft: paved != null ? { value: paved, method: 'parcel acres × Vexcel paved %', date } : null,
      parking_lot_area_sqft: null, car_stalls_est: null,
      cars_seen: veh.car != null ? { value: veh.car, method: 'vehicles counted on the parcel ortho', date } : null,
      trucks_seen: veh.truck != null ? { value: veh.truck, method: 'vehicles counted on the parcel ortho', date } : null,
      trailers_seen: veh['semi trailer'] != null ? { value: (veh['semi trailer'] ?? 0) + (veh.trailers_at_docks ?? 0), method: 'semi trailers on the parcel ortho', date } : null,
      truck_court_ft: court?.depth_ft != null ? { value: court.depth_ft, method: `${court.facing}-facing court, median depth (ortho + DSM)`, date } : null,
    },
    building: {
      footprint_sqft: b.footprint_sqft ?? null,
      height_ft_dsm: b.height_ft_dsm_p90 != null ? { value: b.height_ft_dsm_p90, method: 'DSM − DTM, P90 over the footprint', date: e.imagery?.dsm_date } : null,
      roof_condition: b.roof_condition ?? null, roof_material: b.roof_material ?? null, roof_defects: b.roof_defects ?? [],
      solar: b.solar ?? null, ac_units: (b as Record<string, unknown>).rooftop_ac_units as number ?? null, skylights: b.skylights ?? null,
    },
    site: { parcel_acres: site.parcel_acres ?? null, paved_pct: site.paved_pct_of_parcel ?? null, tree_cover_pct: site.tree_cover_pct_vexcel ?? null },
    history: { ortho_dates: [date, e.imagery?.dsm_date].filter(Boolean) as string[], change: null },
    nearby: {},
    _fallback: 'Partial: read from the cached exterior twin. The full Vexcel profile (parking-lot mask, stall estimate, history, nearby places) is still being computed.',
  }
}

const cache = new Map<number, Promise<VexcelProfile | null>>()
export function loadVexcel(id: number, twin?: () => Promise<Twin>) {
  if (!cache.has(id)) {
    cache.set(id, api2.vexcel(id).catch(async (e) => {
      if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 0 && e.status !== 501)) { cache.delete(id); throw e }
      cache.delete(id) // retry the real endpoint next time the card opens
      const t = await (twin ? twin() : api.twin(id)).catch(() => null)
      return t ? fromTwin(id, t) : null
    }))
  }
  return cache.get(id)!
}

export const yearOf = (d?: string) => (d ? String(d).slice(0, 4) : '')
export const fmtDist = (m?: number | null) =>
  m == null || !Number.isFinite(m) ? '?' : m >= 1609 ? `${(m / 1609.34).toFixed(1)} mi` : `${Math.round(m * 3.281).toLocaleString()} ft`
