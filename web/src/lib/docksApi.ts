// Dock store v3 client (docs/CONTRACTS_v3.md section 1). The DockEditor and the card talk to the store only through here.
// Doors live in the exterior twin frame: local ENU metres about frame.lat0/lon0 (x east, y north).

export type DoorKind = 'dock_high' | 'drive_in' | 'unknown'
export type DoorStatus = 'proposed' | 'inferred' | 'confirmed' | 'rejected' | 'user_added'
export interface DockEvidence { type: 'oblique' | 'trailer' | 'well' | 'costar_span' | 'vision' | string; conf: number; ref?: string; note?: string }
export interface DockDoor {
  /** display label: wall letter + index left to right seen from outside (S1, S2 ...); id stays the edit key */
  label?: string
  id: string; building: number; edge: number; s_m: number
  x: number; y: number; lon: number; lat: number; normal: [number, number]
  width_ft: number | null; height_ft: number | null
  kind: DoorKind; kind_conf: number; status: DoorStatus; evidence: DockEvidence[]
  /** a person's reason when they categorised or rejected it */
  reason?: string
}
export interface DockEdge { id: number; a: [number, number]; b: [number, number]; facing: string; len_m: number; normal?: [number, number] }
export interface DockBuilding { id: number; outline: [number, number][]; edges: DockEdge[] }
export interface DockGate { x: number; y: number; lon: number; lat: number; source: 'inferred' | 'user' | string }
export interface DockRegistration {
  model_id: string | null; flip: 0 | 180; confirmed_by_user: boolean; confidence: number | null; note?: string
  /** ENU = R(phi) p_rot + t (flip 0) or R(phi_flipped) p_rot + t_flipped (flip 180) */
  phi?: number; t?: [number, number]; phi_flipped?: number; t_flipped?: [number, number]
  /** optional: the scan floor outline in the scan's rot frame (metres), to draw the alignment overlay */
  scan_outline?: [number, number][]
  /** a person's slide of the scan outline, ENU metres, added to t */
  shift?: [number, number]
}
export interface DockEstimate { value: number | null; lo: number | null; hi: number | null; status: 'verified' | 'estimated' | 'lower_bound' | 'unknown' | string; basis: string }
export interface DockSummary {
  costar_docks: number | null; costar_drive_ins: number | null; detected_doors: number | null; docked_trailers: number | null
  confirmed: number | null; estimate: DockEstimate | null
}
export interface DockStore {
  property_id: number; version: number; updated_at: string | null
  frame: { lat0: number; lon0: number }
  buildings: DockBuilding[]
  gate: DockGate | null
  registration: DockRegistration | null
  doors: DockDoor[]
  summary: DockSummary
  /** client-side marker: this came from a dev fixture, not the API */
  _fixture?: boolean
}
export interface DockSaveBody { doors: DockDoor[]; gate?: DockGate | null; registration?: DockRegistration | null }

// ---- frame maths (same equirectangular constants as backend tools/elevation.py) ----
const KY = 110_574
const kx = (lat0: number) => 111_320 * Math.cos((lat0 * Math.PI) / 180)
export const enuToLonLat = (f: { lat0: number; lon0: number }, x: number, y: number): [number, number] => [f.lon0 + x / kx(f.lat0), f.lat0 + y / KY]
export const lonLatToEnu = (f: { lat0: number; lon0: number }, lon: number, lat: number): [number, number] => [(lon - f.lon0) * kx(f.lat0), (lat - f.lat0) * KY]

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status}`
    try { const b = await r.json(); msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail ?? b) } catch { /* not json */ }
    throw Object.assign(new Error(msg), { status: r.status })
  }
  return r.json() as Promise<T>
}

/** Dev-only fallback while S1's endpoint is being built: the S1 file under /media, then our own fixture. */
async function devFixture(id: number): Promise<DockStore | null> {
  if (!import.meta.env.DEV) return null
  try {
    const r = await fetch(`/media/docks/${id}.json`)
    if (r.ok) return { ...(await r.json()), _fixture: true }
  } catch { /* fall through */ }
  if (id !== 12896757) return null
  const m = await import('../fixtures/docks_12896757.json')
  return { ...(m.default as unknown as DockStore), _fixture: true }
}

const cache = new Map<number, Promise<DockStore>>()

export const docksApi = {
  get(id: number, fresh = false): Promise<DockStore> {
    if (!fresh && cache.has(id)) return cache.get(id)!
    const p = fetch(`/api/warehouses/${id}/docks`).then((r) => j<DockStore>(r)).catch(async (e) => {
      const fx = await devFixture(id)
      if (fx) { console.info('[docksApi] using dev fixture for', id, '-', String(e?.message ?? e)); return fx }
      throw e
    })
    cache.set(id, p)
    p.catch(() => cache.delete(id))
    return p
  },
  async save(id: number, body: DockSaveBody): Promise<DockStore> {
    const r = await fetch(`/api/warehouses/${id}/docks`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const out = await j<DockStore | { ok?: boolean }>(r)
    // the PUT may return the saved store or just an ack; re-read in the latter case
    const store = out && 'doors' in out ? (out as DockStore) : await fetch(`/api/warehouses/${id}/docks`).then((x) => j<DockStore>(x))
    cache.set(id, Promise.resolve(store))
    return store
  },
  async evidence(ids: number[]): Promise<Record<string, DockSummary>> {
    return fetch(`/api/docks/evidence?ids=${ids.join(',')}`).then((r) => j(r))
  },
  invalidate(id: number) { cache.delete(id) },
}

export const KIND_LABEL: Record<DoorKind, string> = { dock_high: 'Dock-high', drive_in: 'Drive-in', unknown: 'Unknown' }
export const STATUS_LABEL: Record<DoorStatus, string> = {
  proposed: 'Proposed', inferred: 'Inferred', confirmed: 'Confirmed', rejected: 'Rejected', user_added: 'Added by you',
}
/** doors the yard test will use (never rejected) */
export const usableDoors = (s: DockStore | null | undefined) => (s?.doors ?? []).filter((d) => d.status !== 'rejected')

// ---- proof (stream E): which Vexcel oblique crop and SAM3 boxes each door came from ----
export interface ProofHit { px_bbox?: number[]; conf?: number; label?: string }
export interface ProofCrop { crop_id: string; view: string; camera_dir?: string | null; date?: string | null; url?: string | null; hits?: ProofHit[] | number | null; conf?: number | null; label?: string | null }
export type DoorProof = { crops: ProofCrop[]; no_photo?: false } | { crops: []; no_photo: true; why: string }
export interface ProofIndex { doors: Record<string, DoorProof>; _fixture?: boolean }

/** accept {door: [crops] | {no_photo, why}}, {doors: {...}} or {doors: [{id, crops|proof, no_photo?, why?}]} */
function normProof(raw: unknown): ProofIndex {
  const r = (raw ?? {}) as Record<string, unknown>
  const src = (r.doors ?? r) as unknown
  const out: Record<string, DoorProof> = {}
  const one = (v: unknown): DoorProof => {
    if (Array.isArray(v)) return { crops: v as ProofCrop[] }
    const o = (v ?? {}) as Record<string, unknown>
    if (o.no_photo) return { crops: [], no_photo: true, why: String(o.why ?? 'No photo evidence for this door.') }
    return { crops: ((o.crops ?? o.proof ?? []) as ProofCrop[]) }
  }
  if (Array.isArray(src)) for (const d of src as Record<string, unknown>[]) out[String(d.id ?? d.door)] = one(d)
  else if (src && typeof src === 'object') for (const [k, v] of Object.entries(src as Record<string, unknown>)) if (!['property_id', 'version', 'updated_at'].includes(k)) out[k] = one(v)
  return { doors: out }
}

export const proofImageUrl = (pid: number, doorId: string, c: ProofCrop) =>
  c.url || `/api/docks/${pid}/proof/${encodeURIComponent(c.crop_id)}.jpg?door=${encodeURIComponent(doorId)}`

/** Dev-only stand-in until /api/docks/{pid}/proof is live: the twin's whole-site obliques (not the facade crop), labelled. */
async function proofFixture(pid: number): Promise<ProofIndex | null> {
  if (!import.meta.env.DEV) return null
  try {
    const [store, twin] = await Promise.all([docksApi.get(pid), fetch(`/api/warehouses/${pid}/twin`).then((r) => (r.ok ? r.json() : null))])
    const obl: Record<string, string> = twin?.media?.obliques ?? {}
    const date: string | null = twin?.exterior?.imagery?.parcel_date ?? null
    const doors: Record<string, DoorProof> = {}
    for (const d of store.doors) {
      const ob = d.evidence.find((e) => e.type === 'oblique')
      const view = ob?.ref?.split(':')[1] ?? ''
      if (ob && obl[view]) doors[d.id] = { crops: [{ crop_id: `fixture-${view}`, view, camera_dir: view.replace('oblique-', ''), date, url: obl[view], hits: null, conf: ob.conf, label: 'loading dock door' }] }
      else if (d.evidence.some((e) => e.type === 'costar_span') || d.status === 'inferred') doors[d.id] = { crops: [], no_photo: true, why: 'No photo evidence: placed at the observed spacing to reach CoStar’s count.' }
      else doors[d.id] = { crops: [], no_photo: true, why: 'No photo evidence on file for this door.' }
    }
    return { doors, _fixture: true }
  } catch { return null }
}

const proofCache = new Map<number, Promise<ProofIndex>>()
export const proofApi = {
  get(pid: number): Promise<ProofIndex> {
    if (proofCache.has(pid)) return proofCache.get(pid)!
    const p = fetch(`/api/docks/${pid}/proof`).then((r) => j<unknown>(r)).then(normProof).catch(async (e) => {
      const fx = await proofFixture(pid)
      if (fx) return fx
      throw e
    })
    proofCache.set(pid, p)
    p.catch(() => proofCache.delete(pid))
    return p
  },
}

/** Confirm or reject one door from the main-map popup: saves the whole door list through the store API. */
/** A person's review of one door: its type (confirms it) or why it is not a door (rejects it). */
export async function reviewDoor(pid: number, doorId: string, patch: { status: 'confirmed' | 'rejected'; kind?: DoorKind; reason?: string }): Promise<DockStore> {
  const s = await docksApi.get(pid, true)
  const doors = s.doors.map((d) => (d.id === doorId ? { ...d, ...patch, ...(patch.kind ? { kind_conf: 1 } : {}),
    ...(patch.status === 'confirmed' && !patch.reason ? { reason: undefined } : {}) } : d))
  return docksApi.save(pid, { doors, gate: s.gate, registration: s.registration })
}
export async function setDoorStatus(pid: number, doorId: string, status: 'confirmed' | 'rejected'): Promise<DockStore> {
  const s = await docksApi.get(pid, true)
  const doors = s.doors.map((d) => (d.id === doorId ? { ...d, status } : d))
  return docksApi.save(pid, { doors, gate: s.gate, registration: s.registration })
}
