// Dock editor draft state, shared by the map overlay (DockMap) and the side panel (DockEditor).
// The draft is kept per property while the app lives, so closing and reopening the editor keeps unsaved edits.
import { create } from 'zustand'
import {
  docksApi, enuToLonLat, type DockBuilding, type DockDoor, type DockEdge, type DockGate, type DoorKind, type DockRegistration,
  type DockStore,
} from '../lib/docksApi'

type DoorKindT = DoorKind
export type DockTool = 'select' | 'add' | 'gate'
const KIND_CYCLE: DoorKind[] = ['dock_high', 'drive_in', 'unknown']
/** list / keyboard order: by building, edge, then position along the edge */
export const doorOrder = (ds: DockDoor[]) => [...ds].sort((a, b) => a.building - b.building || a.edge - b.edge || a.s_m - b.s_m)
export type EdgeKey = `${number}:${number}`
export const edgeKey = (b: number, e: number): EdgeKey => `${b}:${e}`

interface Draft { doors: DockDoor[]; gate: DockGate | null; reg: DockRegistration | null; prev: Record<string, DockDoor['status']>; dirty: boolean }

interface DockEd {
  pid: number | null
  base: DockStore | null
  loading: boolean
  error: string | null
  doors: DockDoor[]
  gate: DockGate | null
  reg: DockRegistration | null
  /** status before a reject, for restore */
  prev: Record<string, DockDoor['status']>
  dirty: boolean
  saving: boolean
  saveError: string | null
  savedAt: number | null
  sel: string | null
  hoverEdge: EdgeKey | null
  showRejected: boolean
  /** editor tool: select (default) / add a door / move the gate */
  tool: DockTool
  /** a request to fly the editor map to a door (list click) */
  focus: { id: string; nonce: number } | null

  load: (pid: number) => Promise<void>
  select: (id: string | null) => void
  setHoverEdge: (k: EdgeKey | null) => void
  toggleRejected: () => void
  setTool: (t: DockTool) => void
  focusDoor: (id: string) => void
  /** step the selection along the list order (dir +1 / -1) */
  step: (dir: number) => void
  confirm: (id: string) => void
  confirmWall: (b: number, e: number) => void
  cycleKind: (id: string) => void
  moveDoor: (id: string, s: number) => void
  setKind: (id: string, k: DoorKindT) => void
  reject: (id: string) => void
  restore: (id: string) => void
  remove: (id: string) => void
  addDoor: (b: number, e: number, s: number) => string | null
  acceptAll: () => void
  flip: () => void
  nudge: (dx: number, dy: number) => void
  confirmAlign: () => void
  setGate: (lon: number, lat: number) => void
  save: () => Promise<DockStore | null>
  discard: () => void
}

const drafts = new Map<number, Draft>()

export function findEdge(base: Pick<DockStore, "buildings"> | null, b: number, e: number): { bld: DockBuilding; edge: DockEdge } | null {
  const bld = base?.buildings.find((x) => x.id === b)
  const edge = bld?.edges.find((x) => x.id === e)
  return bld && edge ? { bld, edge } : null
}

/** outward unit normal of an edge, from the outline's winding (robust for L-shaped footprints) */
export function outwardNormal(bld: DockBuilding, edge: DockEdge): [number, number] {
  if (edge.normal) return edge.normal
  const o = bld.outline
  let area = 0
  for (let i = 0; i < o.length - 1; i++) area += o[i][0] * o[i + 1][1] - o[i + 1][0] * o[i][1]
  const ux = edge.b[0] - edge.a[0], uy = edge.b[1] - edge.a[1], L = Math.hypot(ux, uy) || 1
  // CCW (area > 0): outward is to the right of travel; CW: to the left
  return area > 0 ? [uy / L, -ux / L] : [-uy / L, ux / L]
}

/** place a door at offset s (m, clamped to [0, len]) along its edge; returns position fields */
export function placeOnEdge(base: DockStore, b: number, e: number, s: number) {
  const f = findEdge(base, b, e)
  if (!f) return null
  const { edge } = f
  const len = Math.hypot(edge.b[0] - edge.a[0], edge.b[1] - edge.a[1])
  const sc = Math.max(0, Math.min(len, s))
  const x = edge.a[0] + ((edge.b[0] - edge.a[0]) / (len || 1)) * sc
  const y = edge.a[1] + ((edge.b[1] - edge.a[1]) / (len || 1)) * sc
  const [lon, lat] = enuToLonLat(base.frame, x, y)
  return { s_m: round(sc, 2), x: round(x, 2), y: round(y, 2), lon: round(lon, 7), lat: round(lat, 7) }
}
/** project an ENU point onto an edge: offset along it (clamped) and perpendicular distance */
export function projectOnEdge(edge: DockEdge, x: number, y: number) {
  const ux = edge.b[0] - edge.a[0], uy = edge.b[1] - edge.a[1], L = Math.hypot(ux, uy) || 1
  const s = ((x - edge.a[0]) * ux + (y - edge.a[1]) * uy) / L
  const sc = Math.max(0, Math.min(L, s))
  const px = edge.a[0] + (ux / L) * sc, py = edge.a[1] + (uy / L) * sc
  return { s: sc, d: Math.hypot(x - px, y - py), len: L }
}
const round = (v: number, n: number) => Math.round(v * 10 ** n) / 10 ** n

/** an explicit confirm: proposals become confirmed (moving or retyping a door never changes its status) */
const confirmed = (d: DockDoor): DockDoor['status'] => (d.status === 'proposed' || d.status === 'inferred' ? 'confirmed' : d.status)

export const useDockEd = create<DockEd>((set, get) => {
  const persist = () => {
    const s = get()
    if (s.pid != null) drafts.set(s.pid, { doors: s.doors, gate: s.gate, reg: s.reg, prev: s.prev, dirty: s.dirty })
  }
  const edit = (fn: (d: DockDoor[]) => DockDoor[], extra: Partial<DockEd> = {}) => { set({ doors: fn(get().doors), dirty: true, savedAt: null, ...extra }); persist() }
  return {
    pid: null, base: null, loading: false, error: null, doors: [], gate: null, reg: null, prev: {}, dirty: false,
    saving: false, saveError: null, savedAt: null, sel: null, hoverEdge: null, showRejected: false, tool: 'select', focus: null,

    load: async (pid) => {
      if (get().pid === pid && get().base) return
      set({ pid, base: null, loading: true, error: null, sel: null, hoverEdge: null, saveError: null, savedAt: null, tool: 'select', focus: null })
      try {
        const base = await docksApi.get(pid)
        if (get().pid !== pid) return
        const d = drafts.get(pid)
        set({ base, loading: false, doors: d?.doors ?? base.doors, gate: d?.gate ?? base.gate, reg: d?.reg ?? base.registration,
          prev: d?.prev ?? {}, dirty: d?.dirty ?? false })
      } catch (e) {
        if (get().pid === pid) set({ loading: false, error: e instanceof Error ? e.message : String(e) })
      }
    },
    select: (id) => set({ sel: id }),
    setHoverEdge: (k) => { if (k !== get().hoverEdge) set({ hoverEdge: k }) },
    toggleRejected: () => set({ showRejected: !get().showRejected }),
    setTool: (t) => set({ tool: t, hoverEdge: null }),
    focusDoor: (id) => set({ sel: id, focus: { id, nonce: Date.now() } }),
    step: (dir) => {
      const s = get()
      const list = doorOrder(s.doors.filter((d) => s.showRejected || d.status !== 'rejected'))
      if (!list.length) return
      const i = list.findIndex((d) => d.id === s.sel)
      const next = list[i < 0 ? (dir > 0 ? 0 : list.length - 1) : (i + dir + list.length) % list.length]
      get().focusDoor(next.id)
    },
    confirm: (id) => edit((ds) => ds.map((d) => (d.id === id ? { ...d, status: confirmed(d) } : d))),
    confirmWall: (b, e) => edit((ds) => ds.map((d) => (d.building === b && d.edge === e ? { ...d, status: confirmed(d) } : d))),
    cycleKind: (id) => {
      const d = get().doors.find((x) => x.id === id)
      if (d) get().setKind(id, KIND_CYCLE[(KIND_CYCLE.indexOf(d.kind) + 1) % KIND_CYCLE.length])
    },
    moveDoor: (id, s) => {
      const base = get().base
      if (!base) return
      edit((ds) => ds.map((d) => {
        if (d.id !== id) return d
        const p = placeOnEdge(base, d.building, d.edge, s)
        return p ? { ...d, ...p } : d                          // position only: dragging never changes the status
      }))
    },
    setKind: (id, k) => edit((ds) => ds.map((d) => (d.id === id ? { ...d, kind: k, kind_conf: 1 } : d))),
    reject: (id) => {
      const d = get().doors.find((x) => x.id === id)
      if (!d || d.status === 'rejected') return
      edit((ds) => ds.map((x) => (x.id === id ? { ...x, status: 'rejected' } : x)), { prev: { ...get().prev, [id]: d.status } })
    },
    restore: (id) => edit((ds) => ds.map((x) => (x.id === id ? { ...x, status: get().prev[id] && get().prev[id] !== 'rejected' ? get().prev[id] : 'confirmed' } : x))),
    remove: (id) => edit((ds) => ds.filter((x) => x.id !== id || x.status !== 'user_added'), { sel: get().sel === id ? null : get().sel }),
    addDoor: (b, e, s) => {
      const base = get().base
      const f = findEdge(base, b, e)
      if (!base || !f) return null
      const p = placeOnEdge(base, b, e, s)
      if (!p) return null
      let n = 1
      const ids = new Set(get().doors.map((d) => d.id))
      while (ids.has(`u${n}`)) n++
      const id = `u${n}`
      const door: DockDoor = { id, building: b, edge: e, ...p, normal: outwardNormal(f.bld, f.edge), width_ft: 9, height_ft: 10,
        kind: 'dock_high', kind_conf: 1, status: 'user_added', evidence: [] }
      edit((ds) => [...ds, door], { sel: id, tool: 'select' })
      return id
    },
    acceptAll: () => edit((ds) => ds.map((d) => (d.status === 'proposed' || d.status === 'inferred' ? { ...d, status: 'confirmed' } : d))),
    flip: () => { const r = get().reg; if (!r) return; set({ reg: { ...r, flip: r.flip === 180 ? 0 : 180, confirmed_by_user: false }, dirty: true, savedAt: null }); persist() },
    nudge: (dx, dy) => { const r = get().reg; if (!r) return; const s = r.shift ?? [0, 0]; set({ reg: { ...r, shift: [Math.round((s[0] + dx) * 100) / 100, Math.round((s[1] + dy) * 100) / 100], confirmed_by_user: false }, dirty: true, savedAt: null }); persist() },
    confirmAlign: () => { const r = get().reg; if (!r) return; set({ reg: { ...r, confirmed_by_user: true }, dirty: true, savedAt: null }); persist() },
    setGate: (lon, lat) => {
      const base = get().base
      if (!base) return
      const kx = 111_320 * Math.cos((base.frame.lat0 * Math.PI) / 180)
      set({ gate: { x: round((lon - base.frame.lon0) * kx, 2), y: round((lat - base.frame.lat0) * 110_574, 2), lon: round(lon, 7), lat: round(lat, 7), source: 'user' }, dirty: true, savedAt: null })
      persist()
    },
    save: async () => {
      const s = get()
      if (s.pid == null) return null
      set({ saving: true, saveError: null })
      try {
        const out = await docksApi.save(s.pid, { doors: s.doors, gate: s.gate, registration: s.reg })
        drafts.delete(s.pid)
        set({ base: out, doors: out.doors, gate: out.gate, reg: out.registration, prev: {}, dirty: false, saving: false, savedAt: Date.now() })
        return out
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        set({ saving: false, saveError: s.base?._fixture ? `The dock store API is not live yet (showing a dev fixture): ${msg}` : msg })
        return null
      }
    },
    discard: () => {
      const s = get()
      if (s.pid != null) drafts.delete(s.pid)
      if (s.base) set({ doors: s.base.doors, gate: s.base.gate, reg: s.base.registration, prev: {}, dirty: false, sel: null, saveError: null })
    },
  }
})
