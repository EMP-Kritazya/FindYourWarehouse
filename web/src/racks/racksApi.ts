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
export interface InteriorZone { id: string; type: 'selective' | 'dense' | 'shelving' | 'cantilever' | 'open'; rect?: number[]; from: number; to: number; why: string; what?: string; positions: number; area_sqft: number; main_aisle?: boolean; levels?: number }
export interface InteriorProofs {
  machine: string; machine_name: string; reachable_pct: number; unreachable_rows: string[]; lift_ok: boolean; lift_note: string
  avg_trip_m: number | null; minutes_per_pallet: number | null; pallets_per_hour: number | null
  daily_pallet_moves: number; shift_hours: number; machines_needed: number | null; basis: string | null
}
export interface InteriorPlan extends Omit<RackPlan, 'rows' | 'equipment' | 'equipment_name'> {
  operation: string; operation_name: string; why: string; machine: string; machine_name: string
  pallet: PalletSpec; dims: InteriorDims; feasibility: Feasibility[]; machine_pos: { x: number; y: number; heading_deg: number } | null
  zones: InteriorZone[]; objects: InteriorObject[]; pick_faces: number; long_goods_positions?: number; scan_coverage_pct?: number | null; proofs: InteriorProofs
  rows: (RackRow & { zone?: string; type?: string; reachable?: boolean; trip_m?: number | null; levels?: number; per_bay?: number; level_ft?: number; deep?: number })[]
}
export interface Operation { key: string; name: string; why: string; machine: string }
/** Where a dimension comes from. Never present an assumed value as a measurement. */
export type DimSource = 'measured' | 'assumed' | 'calculated' | 'yours'
export interface Dim { value: number | null; unit: string; source: DimSource; basis?: string }
export interface PalletInput { preset?: string; width_in: number; depth_in: number; load_in: number }
export interface PalletSpec extends PalletInput {
  preset: string; name: string; per_bay: number; frame_depth_in: number; bay_in: number; level_in: number
  source: 'assumed' | 'yours'; is_default: boolean
}
export interface InteriorDims {
  pallet: { name: string; preset: string; width: Dim; depth: Dim; load_height: Dim; note: string }
  rack: { frame_depth: Dim; bay_width: Dim; pallets_per_bay: Dim; level_pitch: Dim; levels: Dim; beam_heights_ft: number[]; top_beam: Dim; top_load: Dim }
  building: { clear_height: Dim; roof_deck: Dim; sprinkler_clearance: Dim; usable_height: Dim; column_bay: Dim | null }
  aisle: Dim
  machine: { name: string; basis?: string; length: Dim; width: Dim; height: Dim; lift_height: Dim; capacity: Dim }
  lane: { width: Dim; pallet_depth: Dim }
}
export interface Feasibility { id: string; label: string; ok: boolean; detail: string }
export const PALLET_PRESETS: { key: string; name: string; width_in: number; depth_in: number }[] = [
  { key: 'gma_48x40', name: '48 × 40 in · US standard', width_in: 40, depth_in: 48 },
  { key: '42x42', name: '42 × 42 in', width_in: 42, depth_in: 42 },
  { key: '48x48', name: '48 × 48 in', width_in: 48, depth_in: 48 },
  { key: 'euro_1200x800', name: 'Euro 1200 × 800 mm', width_in: 31.5, depth_in: 47.2 },
]
export const interiorApi = {
  operations: () => fetch('/api/interior/operations').then((r) => j<Operation[]>(r)),
  plan: (pid: number, body: { operation: string; machine?: string; keep?: string[] | null; orientation?: string; zone_types?: Record<string, string>; edits?: RackEdits; pallet?: PalletInput | null }) =>
    fetch(`/api/interior/${pid}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then((r) => j<InteriorPlan>(r)),
  summary: (pid: number, operation?: string) => fetch(`/api/interior/${pid}/summary${operation ? `?operation=${operation}` : ''}`).then((r) => j<StorageSummary & { reachable_pct?: number; minutes_per_pallet?: number; machines_needed?: number; machine?: string }>(r)),
}
