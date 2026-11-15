// Matterport scene contract (docs/CONTRACTS.md, GET /api/sim/{run_id}/scene). Showcase world metres, y up.
// yaw: radians about Showcase +y (three.js rotation.y); a box's w / a vehicle's length l lie along local +x.
export type VehicleState = 'empty' | 'loaded' | 'pick' | 'drop' | 'idle' | string;
export type VehicleKey = [t_s: number, x: number, z: number, yaw: number, state: VehicleState];

export interface SimScene {
  run_id?: string;
  property_id?: number | string;
  model_id?: string;
  profile?: string;
  synthetic?: boolean;
  floor_y: number;
  scanned_area?: [number, number][];
  racks?: { x: number; z: number; w: number; d: number; h: number; yaw?: number }[];
  lanes?: { x: number; z: number; w: number; d: number; yaw?: number; door?: number; inbound?: boolean }[];
  doors?: { x: number; z: number; yaw?: number; w?: number; h?: number; kind?: string; source?: string }[];
  heat?: { cell: number; cells: [number, number, number][] };
  vehicles?: {
    id: string | number;
    kind: string;
    glb?: string;
    dims?: { l: number; w: number; h: number };
    keys: VehicleKey[];
  }[];
  trucks?: { id: string; kind?: string; inbound?: boolean; door?: number; arrive_s?: number; dock_s?: number; depart_s?: number }[];
  duration_s?: number;
  kpi?: unknown;
  assumptions?: string[];
}

/** The minimal slice of the Bundle SDK we use (typed loosely on purpose: the full typings live in /bundle/sdk.d.ts). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type MpSdk = any;

// ---------------------------------------------------------------- doors marked in the scan (door store, S4)
export type DoorKind = 'dock_high' | 'drive_in' | 'personnel';
export interface Pt3 { x: number; y: number; z: number }
export interface MarkedDoor {
  id: string;
  kind: DoorKind;
  a: Pt3; b: Pt3;
  center: { x: number; z: number };
  width_m: number;
  height_m: number | null;
  sill_m: number | null;
  normal: { x: number; z: number };
  source: 'marked_in_scan' | 'dev_fixture';
  marked_at?: string;
}
export interface DoorStore { model_id: string; property_id: number | null; updated_at: string | null; doors: MarkedDoor[] }

// ---------------------------------------------------------------- equipment test result (POST /api/fit/test)
export interface FitCheck {
  kind: 'corridor' | 'aisle' | 'turn' | 'door' | 'clear_height' | 'lift' | string;
  label?: string;
  ok: boolean | null;            // null = unknown ('?')
  need: number | null; have: number | null; unit: string;
  at?: { x: number; y: number; z: number } | null;
  note?: string;
}
export interface FitResult {
  run_id: string;
  property_id?: number;
  model_id: string;
  floor_y: number;
  floor?: 'as_scanned' | 'emptied';
  ok?: boolean | null;
  verdict?: string;
  counts?: { pass: number; fail: number; unknown: number };
  equipment: { key?: string; name?: string; length_ft: number; width_ft: number; height_lowered_ft: number; lift_height_ft?: number | null; turn_radius_ft?: number | null; min_aisle_ft?: number | null };
  checks: FitCheck[];
  path: [t_s: number, x: number, z: number, yaw: number][];
  pinch_points: { x: number; z: number; width_m: number; need_m: number; ok?: boolean; label?: string }[];
  reachable_pct: number | null;
  heat: { cell: number; cells: [number, number, number][]; legend?: Record<string, string> };
  route?: unknown;
  assumptions: string[];
}
