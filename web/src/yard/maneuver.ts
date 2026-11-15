// Yard maneuver test (docs/CONTRACTS_v3.md section 3): types, client, playback sampling and shared UI state.
// Everything animated comes from the backend's keys (dt 0.2 s). We interpolate position linearly and headings by the
// shortest angle between two keys; we never extrapolate past the first or last key.
import { create } from 'zustand'
import type { BoxGeom } from './types'

export type ManTruckType = 'semi_53' | 'box_truck_26' | 'last_mile_van'
export const MAN_TYPES: ManTruckType[] = ['semi_53', 'box_truck_26', 'last_mile_van']
export const MAX_PER_TYPE = 12

export type Phase = 'enter' | 'to_door' | 'reverse' | 'docked' | 'pull_out' | 'exit' | 'wait' | 'staging' | string
/** [t_s, lon, lat, hdg_tractor_deg, hdg_trailer_deg, gear(+1|-1|0), phase] */
export type ManKey = [number, number, number, number, number, number, Phase]
export type IssueClass = 'geometric' | 'assumption' | 'planner' | 'congestion'

export interface ManVehicle {
  id: string; type: ManTruckType | string
  target: { kind: 'door' | 'staging' | 'street' | string; id: string | null } | null
  color_idx: number
  keys: ManKey[]
  path: [number, number, number][]
  status: 'done' | 'waiting' | 'failed' | string
  /** where = closest reached point; pose = closest reached pose [lon, lat, hdg_tractor, hdg_trailer] (stream P) */
  fail?: { class: IssueClass | string; text: string; where?: [number, number] | null; pose?: [number, number, number, number] | null }
}
export interface ManEvent { t_s: number; truck: string; kind: 'wait' | 'conflict' | 'arrive' | 'dock' | 'depart' | string; where: [number, number]; blocked_by?: string; seconds?: number; text: string }
export interface ManIssue { class: IssueClass; severity: 'high' | 'med' | 'low'; where: [number, number] | null; text: string; trucks: string[]; door?: string }
export interface ManDoor { id: string; lon: number; lat: number; heading_deg: number; kind: string; status: string; source: 'confirmed' | 'proposed' | 'assumed' | string }
export interface ManeuverResult {
  run_id: string
  frame: { lat0: number; lon0: number }
  gate: { lon: number; lat: number; source: string } | null
  doors_used: ManDoor[]
  vehicle_geometry: Record<string, { reference: string; tractor: BoxGeom; trailer: BoxGeom | null }>
  vehicles: ManVehicle[]
  events: ManEvent[]
  issues: ManIssue[]
  summary: { trucks: number; completed: number; concurrent_max: number; makespan_s: number; total_wait_s: number; worst_wait: { truck: string | null; s: number } | null; headline: string }
  assumptions: string[]
  cached?: boolean
  /** client marker: dev fixture, not the planner */
  _fixture?: boolean
}
export interface ManeuverRequest { property_id: number; trucks: { type: ManTruckType; count: number }[]; dwell_s?: number; door_ids?: string[]; gate?: { lon: number; lat: number }; seed?: number }

export const isManeuver = (r: unknown): r is ManeuverResult =>
  !!r && typeof r === 'object' && Array.isArray((r as ManeuverResult).vehicles) && !!(r as ManeuverResult).summary && 'headline' in ((r as ManeuverResult).summary ?? {})

async function devFixture(req: ManeuverRequest): Promise<ManeuverResult | null> {
  if (!import.meta.env.DEV || req.property_id !== 12896757) return null
  try {
    const r = await fetch('/media/yard/maneuver/sample_12896757_5.json')
    if (r.ok) return { ...(await r.json()), _fixture: true }
  } catch { /* fall through */ }
  const m = await import('../fixtures/maneuver_12896757_5.json')
  return { ...(m.default as unknown as ManeuverResult), _fixture: true }
}

export async function runManeuver(req: ManeuverRequest): Promise<ManeuverResult> {
  let r: Response
  try {
    r = await fetch('/api/yard/maneuver', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) })
  } catch (e) {
    const fx = await devFixture(req); if (fx) return fx
    throw e
  }
  if (!r.ok) {
    if (r.status === 404 || r.status === 405) { const fx = await devFixture(req); if (fx) { console.info('[yard] /api/yard/maneuver not live yet: dev fixture'); return fx } }
    let msg = `${r.status}`
    try { const b = await r.json(); msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail) } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json()
}

/** map an agent's args (v2 per_day or v3 count) onto a maneuver request */
export function trucksFromArgs(trucks: unknown): ManeuverRequest['trucks'] {
  const arr = Array.isArray(trucks) ? trucks : []
  const out: ManeuverRequest['trucks'] = []
  for (const t of arr) {
    const type = String((t as Record<string, unknown>)?.type ?? '') as ManTruckType
    if (!MAN_TYPES.includes(type)) continue
    const raw = Number((t as Record<string, unknown>).count ?? (t as Record<string, unknown>).per_day ?? 0)
    const count = Math.max(0, Math.min(MAX_PER_TYPE, Math.round(raw || 0)))
    if (count > 0) out.push({ type, count })
  }
  return out
}

// ---- playback sampling ----
export const angDiff = (a: number, b: number) => ((b - a + 540) % 360) - 180
export interface Pose { lon: number; lat: number; h: number; ht: number; gear: number; phase: Phase }
export function samplePose(keys: ManKey[], t: number): Pose | null {
  if (!keys.length || t < keys[0][0] || t > keys[keys.length - 1][0]) return null
  let lo = 0, hi = keys.length - 1
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (keys[mid][0] <= t) lo = mid; else hi = mid }
  const a = keys[lo], b = keys[hi]
  const f = b[0] > a[0] ? Math.min(1, Math.max(0, (t - a[0]) / (b[0] - a[0]))) : 0
  return {
    lon: a[1] + (b[1] - a[1]) * f, lat: a[2] + (b[2] - a[2]) * f,
    h: (a[3] + angDiff(a[3], b[3]) * f + 360) % 360, ht: (a[4] + angDiff(a[4], b[4]) * f + 360) % 360,
    gear: f < 0.5 ? a[5] : b[5], phase: f < 0.5 ? a[6] : b[6],
  }
}

/** oriented box from rear_m..front_m along a compass heading about (lon, lat) */
export function boxPoly(lon: number, lat: number, hDeg: number, rear: number, front: number, w: number, lat0: number): [number, number][] {
  const h = (hDeg * Math.PI) / 180, fx = Math.sin(h), fy = Math.cos(h), rx = fy, ry = -fx
  const kx = 1 / (111320 * Math.cos((lat0 * Math.PI) / 180)), ky = 1 / 110574
  const pt = (a: number, s: number): [number, number] => [lon + (fx * a + rx * s) * kx, lat + (fy * a + ry * s) * ky]
  return [pt(rear, -w / 2), pt(front, -w / 2), pt(front, w / 2), pt(rear, w / 2), pt(rear, -w / 2)]
}

// distinct, colour-blind-friendly-ish truck palette on the dark basemap
export const TRUCK_PALETTE: [number, number, number][] = [
  [79, 209, 197], [96, 165, 250], [244, 114, 182], [250, 204, 21], [167, 139, 250], [52, 211, 153],
  [251, 146, 60], [56, 189, 248], [232, 121, 249], [163, 230, 53], [248, 113, 113], [226, 232, 240],
]
export const truckColor = (i: number) => TRUCK_PALETTE[((i % TRUCK_PALETTE.length) + TRUCK_PALETTE.length) % TRUCK_PALETTE.length]
export const rgb = (c: [number, number, number]) => `rgb(${c[0]} ${c[1]} ${c[2]})`

export const ISSUE_STYLE: Record<IssueClass, { label: string; color: [number, number, number]; cls: string; explain: string }> = {
  geometric: { label: 'Geometric limit', color: [248, 113, 113], cls: 'text-bad bg-bad/10 ring-bad/30',
    explain: 'Verified from the imagery geometry: the measured space is smaller than the vehicle needs.' },
  assumption: { label: 'Our assumption', color: [251, 191, 36], cls: 'text-warn bg-warn/10 ring-warn/30',
    explain: 'Depends on something we assumed (proposed doors, parked neighbours, safety margin). Confirm it to firm this up.' },
  planner: { label: 'Planner limit', color: [167, 139, 250], cls: 'text-violet-300 bg-violet-400/10 ring-violet-400/30',
    explain: 'Our planner did not find a route. That is not proof the move is impossible; a driver may still make it.' },
  congestion: { label: 'Congestion', color: [251, 146, 60], cls: 'text-orange-300 bg-orange-400/10 ring-orange-400/30',
    explain: 'Trucks waited for each other (over 60 s, or competing for one zone of the court).' },
}

/** one row per distinct sentence (the planner can repeat a finding per truck); n = display number, i = index in issues */
export function issueRows(issues: ManIssue[]) {
  const seen = new Set<string>()
  let n = 0
  return issues.map((x, i) => ({ ...x, i, n: 0 })).filter((x) => (seen.has(x.text) ? false : (seen.add(x.text), true))).map((x) => ({ ...x, n: ++n }))
}

export const PHASE_LABEL: Record<string, string> = {
  enter: 'entering', to_door: 'driving to door', reverse: 'backing in', docked: 'at the door', pull_out: 'pulling out',
  exit: 'leaving', wait: 'waiting', staging: 'staged',
}

// ---- shared UI state between the map overlay and the card's Test tab ----
interface YardUi {
  /** the request behind the current result (for Rerun) */
  req: ManeuverRequest | null
  running: boolean
  error: string | null
  selectedIssue: number | null
  selectedTruck: string | null
  setReq: (r: ManeuverRequest | null) => void
  selectIssue: (i: number | null) => void
  selectTruck: (id: string | null) => void
  /** bumped to ask the player to rerun */
  rerunNonce: number
}
export const useYardUi = create<YardUi>((set) => ({
  req: null, running: false, error: null, selectedIssue: null, selectedTruck: null, rerunNonce: 0,
  setReq: (r) => set({ req: r }),
  selectIssue: (i) => set({ selectedIssue: i }),
  selectTruck: (id) => set({ selectedTruck: id }),
}))
