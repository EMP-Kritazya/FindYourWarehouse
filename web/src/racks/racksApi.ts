// Storage capacity planner API (backend proinspect/racks.py, api_racks.py).
export interface RackRow {
  id: string; x0: number; x1: number; y0: number; y1: number; bays: number; slots: number
  slots_kept: number; ok: boolean; why?: string | null; deleted?: boolean; moved_m?: number
}
export interface RackEdits { deleted?: string[]; moved?: Record<string, number> }
export interface RackPlan {
  property_id: number; model_id: string; equipment: string; equipment_name: string; orientation: 'perpendicular' | 'parallel'
  run_id: string; positions: number; levels: number; aisle_ft: number; levels_basis: string
  clear: { scan_p5_ft: number; scan_deck_ft?: number | null; costar_ft?: number | null }
  sprinkler: string; scanned_sqft: number; sqft_per_position: number | null
  building_estimate: { value: number; ratio: number; basis: string } | null
  cost_per_position_yr: { value: number; basis: string } | null
  rent: { psf_yr: number; source: string } | null
  positions_lost_to_columns: number | null; cube_used_pct: number | null
  staging_lanes: number; dock_doors: number; rows: RackRow[]; transposed: boolean
  lanes: { x0: number; x1: number; y0: number; y1: number }[]
  plan: { width_m: number; depth_m: number; outline: [number, number][][]; columns: [number, number][]
          obstructions: [number, number, number, number][]; dock_wall: 'bottom' | 'left' }
  scene_url: string; assumptions: string[]
}
export interface TruckOption {
  equipment: string; name?: string; positions?: number; levels?: number; aisle_ft?: number
  sqft_per_position?: number | null; building_estimate?: number | null; cost_per_position_yr?: number | null
  vs_counterbalance_pct?: number; error?: string
}
export interface StorageSummary {
  status: 'computed' | 'no_scan'; why?: string; line?: string; positions?: number; building_estimate?: number | null
  levels?: number; sqft_per_position?: number | null; cost_per_position_yr?: number | null
  positions_lost_to_columns?: number | null; equipment?: string
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).detail ?? m } catch { /* ignore */ } throw new Error(m) }
  return r.json() as Promise<T>
}

export const racksApi = {
  plan: (pid: number, equipment: string, orientation: string, edits?: RackEdits) =>
    fetch(`/api/racks/${pid}`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ equipment, orientation, edits: edits ?? {} }) }).then((r) => j<RackPlan>(r)),
  trucks: (pid: number, orientation: string) =>
    fetch(`/api/racks/${pid}/trucks?orientation=${orientation}`).then((r) => j<{ trucks: TruckOption[] }>(r)),
  summary: (pid: number) => fetch(`/api/racks/${pid}/summary`).then((r) => j<StorageSummary>(r)),
}

// ---------------------------------------------------------------- interior planner (backend proinspect/interior.py)
export interface InteriorObject { id: string; kind: string; height: 'low' | 'full'; area_sqft: number; x0: number; x1: number; y0: number; y1: number; default_keep: boolean; kept: boolean }
export interface InteriorZone { id: string; type: 'selective' | 'dense' | 'shelving' | 'cantilever' | 'open'; rect?: number[]; from: number; to: number; why: string; what?: string; positions: number; area_sqft: number; main_aisle?: boolean }
export interface InteriorProofs {
  machine: string; machine_name: string; reachable_pct: number; unreachable_rows: string[]; lift_ok: boolean; lift_note: string
  avg_trip_m: number | null; minutes_per_pallet: number | null; pallets_per_hour: number | null
  daily_pallet_moves: number; shift_hours: number; machines_needed: number | null; basis: string | null
}
export interface InteriorPlan extends Omit<RackPlan, 'rows' | 'equipment' | 'equipment_name'> {
  operation: string; operation_name: string; why: string; machine: string; machine_name: string
  zones: InteriorZone[]; objects: InteriorObject[]; pick_faces: number; long_goods_positions?: number; scan_coverage_pct?: number | null; proofs: InteriorProofs
  rows: (RackRow & { zone?: string; type?: string; reachable?: boolean; trip_m?: number | null })[]
}
export interface Operation { key: string; name: string; why: string; machine: string }
export const interiorApi = {
  operations: () => fetch('/api/interior/operations').then((r) => j<Operation[]>(r)),
  plan: (pid: number, body: { operation: string; machine?: string; keep?: string[] | null; orientation?: string; zone_types?: Record<string, string>; edits?: RackEdits }) =>
    fetch(`/api/interior/${pid}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then((r) => j<InteriorPlan>(r)),
  summary: (pid: number, operation?: string) => fetch(`/api/interior/${pid}/summary${operation ? `?operation=${operation}` : ''}`).then((r) => j<StorageSummary & { reachable_pct?: number; minutes_per_pallet?: number; machines_needed?: number; machine?: string }>(r)),
}
