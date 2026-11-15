// True cost per pallet (GET/POST /api/economics/{id}). Failure-safe: any error -> null, the UI then shows nothing.
import { useEffect, useState } from 'react'
import type { DimSource } from '../racks/racksApi'

export interface CostLine { label: string; value: number | null; unit: string; source: DimSource; basis: string }
export interface Cost {
  property_id: number; address: string; market: string; operation: string; operation_name?: string; headline: string
  rent: { tier: 'asking' | 'own' | 'comps' | 'market' | 'unknown' | 'yours'; value: number | null; low?: number; high?: number; source: DimSource; kind?: string; basis: string; n?: number; years?: number[]; service_mix?: Record<string, number> }
  space: { sqft: number | null; source: DimSource; basis: string; share_of_building: number }
  positions?: { value: number; source: DimSource; rough: boolean; basis: string }
  lines: CostLine[]; total_year: { value: number | null; complete: boolean; note: string | null }
  per_position_month: { value: number | null; rough: boolean; ops_only: number | null; basis: string }
  per_pallet_moved: { value: number | null; basis: string }
  moves_per_day?: number | null; plan_note?: string
}
export interface CostOverrides { rent_psf?: number; sqft?: number; wage_hour?: number }

const cache = new Map<string, Cost | null>()
export const costKey = (id: number, op?: string) => `${id}:${op ?? ''}`

export async function fetchCost(id: number, op?: string, overrides?: CostOverrides): Promise<Cost | null> {
  try {
    const r = overrides
      ? await fetch(`/api/economics/${id}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ operation: op, overrides }) })
      : await fetch(`/api/economics/${id}${op ? `?operation=${encodeURIComponent(op)}` : ''}`)
    return r.ok ? ((await r.json()) as Cost) : null
  } catch {
    return null
  }
}

/** undefined = loading, null = unavailable (feature off, error, unknown id) */
export function useCost(id: number | null, op?: string) {
  const k = id == null ? '' : costKey(id, op)
  const [c, setC] = useState<Cost | null | undefined>(id == null ? null : cache.get(k))
  useEffect(() => {
    if (id == null) { setC(null); return }
    if (cache.has(k)) { setC(cache.get(k)); return }
    let live = true
    setC(undefined)
    fetchCost(id, op).then((r) => { cache.set(k, r); if (live) setC(r) })
    return () => { live = false }
  }, [id, op, k])
  return c
}

export const money = (v: number | null | undefined, d = 0) =>
  (v == null ? '?' : `$${v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })}`)
export const RENT_TIER: Record<string, string> = {
  asking: 'asking rent (listing)', own: "this building's signed lease", comps: 'submarket lease comps',
  market: 'market-wide comps (rough)', unknown: 'rent unknown', yours: 'your rent',
}
