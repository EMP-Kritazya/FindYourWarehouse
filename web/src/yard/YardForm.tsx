// Yard test input: the buyer's own trucks (types and counts per day), operating hours, dock minutes, doors.
import { useState } from 'react'
import { Loader2, Truck } from 'lucide-react'
import { runYardTest, TRUCK_TYPES, type TruckType, type YardRequest, type YardResult } from './types'

interface Props {
  propertyId: number
  onResult: (r: YardResult) => void
  /** doors of a previous run, so the buyer can pick which ones to use */
  doors?: { id: string; source: string }[]
  initial?: YardRequest
  compact?: boolean
}

const hh = (h: number) => `${String(Math.floor(h)).padStart(2, '0')}:00`

export default function YardForm({ propertyId, onResult, doors, initial, compact }: Props) {
  const init = (k: TruckType) => initial?.trucks.find((t) => t.type === k)?.per_day ?? (k === 'semi_53' ? 12 : k === 'box_truck_26' ? 8 : 0)
  const [counts, setCounts] = useState<Record<TruckType, number>>({
    semi_53: init('semi_53'), box_truck_26: init('box_truck_26'), last_mile_van: init('last_mile_van'),
  })
  const [mins, setMins] = useState<Record<TruckType, number>>(() => {
    const o = {} as Record<TruckType, number>
    TRUCK_TYPES.forEach((t) => { o[t.key] = initial?.dock_minutes?.[t.key] ?? t.dockMin })
    return o
  })
  const [hours, setHours] = useState<[number, number]>(initial?.hours ?? [6, 16])
  const [picked, setPicked] = useState<Set<string> | null>(initial?.door_ids ? new Set(initial.door_ids) : null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const total = Object.values(counts).reduce((a, b) => a + b, 0)

  async function submit() {
    setBusy(true); setErr(null)
    const trucks = TRUCK_TYPES.filter((t) => counts[t.key] > 0).map((t) => ({ type: t.key, per_day: counts[t.key] }))
    const dock_minutes: Partial<Record<TruckType, number>> = {}
    TRUCK_TYPES.forEach((t) => { if (counts[t.key] > 0 && mins[t.key] !== t.dockMin) dock_minutes[t.key] = mins[t.key] })
    const req: YardRequest = { property_id: propertyId, trucks, hours }
    if (Object.keys(dock_minutes).length) req.dock_minutes = dock_minutes
    if (picked && doors && picked.size && picked.size < doors.length) req.door_ids = [...picked]
    try { onResult(await runYardTest(req)) } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  const num = 'w-14 rounded-md border border-line bg-panel-2 px-2 py-1 text-right text-[13px] text-ink tabular-nums outline-none focus:border-accent'
  return (
    <div className={compact ? 'space-y-3' : 'space-y-4'}>
      {!compact && (
        <div>
          <div className="flex items-center gap-2 text-[15px] font-semibold text-ink"><Truck size={16} className="text-accent" />Yard test</div>
          <p className="mt-1 text-[12.5px] leading-snug text-ink-2">Enter your trucks. We check whether each one can turn and back into the docks on this site, and how long they queue.</p>
        </div>
      )}
      <div className="rounded-lg border border-line">
        <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 border-b border-line px-3 py-1.5 text-[11px] uppercase tracking-wide text-ink-3">
          <span>Truck</span><span className="w-14 text-right">Per day</span><span className="w-14 text-right">Dock min</span>
        </div>
        {TRUCK_TYPES.map((t) => (
          <div key={t.key} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 px-3 py-1.5">
            <span className={`text-[13px] ${counts[t.key] ? 'text-ink' : 'text-ink-3'}`}>{t.label}</span>
            <input aria-label={`${t.label} per day`} type="number" min={0} max={400} value={counts[t.key]} className={num}
              onChange={(e) => setCounts({ ...counts, [t.key]: Math.max(0, Math.round(Number(e.target.value) || 0)) })} />
            <input aria-label={`${t.label} dock minutes`} type="number" min={1} max={480} value={mins[t.key]} className={num}
              onChange={(e) => setMins({ ...mins, [t.key]: Math.max(1, Number(e.target.value) || t.dockMin) })} />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 text-[13px] text-ink-2">
        <span>Receiving hours</span>
        <select aria-label="start hour" value={hours[0]} onChange={(e) => setHours([Number(e.target.value), Math.max(Number(e.target.value) + 1, hours[1])])}
          className="rounded-md border border-line bg-panel-2 px-1.5 py-1 text-ink">
          {Array.from({ length: 23 }, (_, h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
        <span>to</span>
        <select aria-label="end hour" value={hours[1]} onChange={(e) => setHours([hours[0], Number(e.target.value)])}
          className="rounded-md border border-line bg-panel-2 px-1.5 py-1 text-ink">
          {Array.from({ length: 24 - hours[0] }, (_, i) => hours[0] + 1 + i).map((h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
      </div>
      {doors && doors.length > 0 && (
        <div>
          <div className="mb-1 text-[12px] text-ink-3">Doors to use ({picked ? picked.size : doors.length} of {doors.length})</div>
          <div className="flex flex-wrap gap-1">
            {doors.map((d) => {
              const on = !picked || picked.has(d.id)
              return (
                <button key={d.id} type="button" title={d.source} aria-label={d.source}
                  onClick={() => { const s = new Set(picked ?? doors.map((x) => x.id)); if (on) s.delete(d.id); else s.add(d.id); setPicked(s) }}
                  className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${on ? 'bg-accent/20 text-accent' : 'bg-panel-2 text-ink-3 line-through'}`}>{d.id}</button>
              )
            })}
          </div>
        </div>
      )}
      {err && <div className="rounded-md border border-bad/40 bg-bad/10 px-2 py-1.5 text-[12px] text-bad">Could not run the test: {err}</div>}
      <button type="button" disabled={busy || total === 0} onClick={submit}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-[13px] font-semibold text-bg disabled:opacity-50">
        {busy ? <><Loader2 size={14} className="animate-spin" />Testing {total} trucks…</> : <>Run yard test ({total} trucks/day)</>}
      </button>
    </div>
  )
}
