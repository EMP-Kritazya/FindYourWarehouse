// Yard test types (docs/CONTRACTS.md, POST /api/yard/test). Headings are compass bearings (deg clockwise from north).

export type TruckType = 'semi_53' | 'box_truck_26' | 'last_mile_van'
export const TRUCK_TYPES: { key: TruckType; label: string; short: string; dockMin: number }[] = [
  { key: 'semi_53', label: 'Tractor + 53 ft trailer', short: "53' semi", dockMin: 35 },
  { key: 'box_truck_26', label: '26 ft box truck', short: '26 ft box', dockMin: 20 },
  { key: 'last_mile_van', label: 'Last-mile van', short: 'Van', dockMin: 12 },
]

export interface YardRequest {
  property_id: number
  trucks: { type: TruckType; per_day?: number; per_hour?: number[] }[]
  door_ids?: string[]
  dock_minutes?: Partial<Record<TruckType, number>>
  hours?: [number, number]
}

export interface YardDoor { id: string; source: string; kind: string; enu: { x: number; y: number }; lonlat: [number, number]; normal: [number, number]; width_m: number }
export interface YardFit {
  door_id: string; vehicle: TruckType; ok: boolean; geometric_ok: boolean; reachable_from_gate: boolean
  margin_m: number; maneuver_s: number; depth_needed_ft: number; neighbors_parked: number; crosses_parcel_line: boolean
  conflicts: { lon: number; lat: number; what: string; intrusion_m: number }[]
  path: [number, number, number, number, number][]
  swept: GeoPoly
}
export interface GeoPoly { type: "Polygon" | "MultiPolygon"; coordinates: number[][][] | number[][][][] }
export interface BoxGeom { rear_m: number; front_m: number; width_m: number }
export interface YardResult {
  run_id: string
  property_id: number
  doors: YardDoor[]
  door_source: 'marked_in_scan' | 'detected_oblique' | 'assumed' | string
  fits: YardFit[]
  summary: { type: TruckType; name: string; doors_ok: number; doors_tested: number; best_margin_m: number; depth_needed_ft: number; verdict: string; margin_text?: string }[]
  queue: {
    runs: number; max_in_yard: number; max_in_yard_p90: number; max_on_street: number; max_on_street_p90: number
    runs_with_street_spill: number; wait_p50_min: number | null; wait_p90_min: number | null; wait_max_min: number | null
    door_utilization: number; door_utilization_by_door: Record<string, number>; unserved_per_day: number; trucks_per_day: number
    timeline: [number, number, number][]; at_doors: [number, number][]
    staging_area_m2: number; staging_usable_m2: number; staging_semi_equivalents: number; staging_note: string
    dock_minutes: Record<string, number>; hours: [number, number]
  }
  vehicles: { id: string; type: TruckType; door_id: string; wait_min: number; keys: [number, number, number, number, string, number][] }[]
  vehicle_geometry: Record<string, { reference: string; tractor: BoxGeom; trailer: BoxGeom | null }>
  entrance: { lonlat: [number, number]; method: string; street_dir: [number, number] }
  stalls: [number, number][]
  frame: { lat0: number; lon0: number; half_m: number; bounds: [number, number, number, number]; ortho_url: string }
  parcel: GeoPoly | null
  assumptions: string[]
}

export async function runYardTest(req: YardRequest): Promise<YardResult> {
  const r = await fetch('/api/yard/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) })
  if (!r.ok) {
    let msg = `${r.status}`
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json()
}

export async function getYardRun(runId: string): Promise<YardResult> {
  const r = await fetch(`/api/yard/${encodeURIComponent(runId)}`)
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export const DOOR_SOURCE_LABEL: Record<string, string> = {
  marked_in_scan: 'Doors marked by a person in the 3D scan',
  detected_oblique: 'Doors detected in Vexcel oblique photos (a lower bound)',
  assumed: 'Door positions assumed (no marked doors)',
}
