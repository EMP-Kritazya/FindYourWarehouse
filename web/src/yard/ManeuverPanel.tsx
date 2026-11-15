// Card side of the yard maneuver test: truck picker (to-scale cards + 0-12 steppers) and the compact results card.
import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { ChevronDown, DoorOpen, Loader2, Map as MapIcon, Minus, Plus } from 'lucide-react'
import EquipmentCard from '../components/EquipmentCard'
import { devCheckGlyphs, type EquipItem } from '../components/EquipmentGlyph'
import { ISSUE_STYLE, MAN_TYPES, issueRows, rgb, truckColor, MAX_PER_TYPE, useYardUi, type IssueClass, type ManTruckType, type ManeuverRequest, type ManeuverResult } from './maneuver'

let libCache: Promise<Record<string, EquipItem>> | null = null
export const loadLibrary = () => (libCache ??= fetch('/api/library').then((r) => r.json()).then((j) => j.equipment ?? {}).catch((e) => { libCache = null; throw e }))

const DEFAULT: Record<ManTruckType, number> = { semi_53: 3, box_truck_26: 1, last_mile_van: 1 }

interface FormProps { propertyId: number; onRun: (req: ManeuverRequest) => void; busy?: boolean }

export default function ManeuverForm({ propertyId, onRun, busy }: FormProps) {
  const prev = useYardUi((s) => (s.req?.property_id === propertyId ? s.req : null))
  const [lib, setLib] = useState<Record<string, EquipItem>>({})
  const [counts, setCounts] = useState<Record<ManTruckType, number>>(() => {
    if (!prev) return { ...DEFAULT }
    const o = { semi_53: 0, box_truck_26: 0, last_mile_van: 0 }
    prev.trucks.forEach((t) => { o[t.type] = t.count })
    return o
  })
  const [dwell, setDwell] = useState(prev?.dwell_s ?? 90)
  useEffect(() => { loadLibrary().then((l) => { setLib(l); devCheckGlyphs(l) }).catch(() => {}) }, [])
  const total = MAN_TYPES.reduce((a, k) => a + counts[k], 0)
  const step = (k: ManTruckType, d: number) => setCounts((c) => ({ ...c, [k]: Math.max(0, Math.min(MAX_PER_TYPE, c[k] + d)) }))
  const submit = () => onRun({ property_id: propertyId, trucks: MAN_TYPES.filter((k) => counts[k] > 0).map((k) => ({ type: k, count: counts[k] })), dwell_s: dwell })

  return (
    <div className="space-y-2.5 p-3" data-testid="maneuver-form">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Trucks arriving together</div>
      <div className="space-y-1.5">
        {MAN_TYPES.map((k) => {
          const it = lib[k]
          return it ? (
            <EquipmentCard key={k} itemKey={k} item={it} compact frameFt={74} frameHFt={14} selected={counts[k] > 0}
              right={<Stepper value={counts[k]} onDec={() => step(k, -1)} onInc={() => step(k, +1)} label={it.name ?? k} />} />
          ) : <div key={k} className="skeleton h-[86px]" />
        })}
      </div>
      <div className="flex items-center gap-2 text-[12px] text-ink-2">
        <span>Time at the door</span>
        <select value={dwell} onChange={(e) => setDwell(Number(e.target.value))} aria-label="dwell seconds"
          className="h-7 rounded-lg bg-panel-2 px-1.5 text-[12px] text-ink ring-1 ring-inset ring-line outline-none">
          {[60, 90, 180, 300].map((s) => <option key={s} value={s}>{s < 120 ? `${s} s` : `${s / 60} min`}</option>)}
        </select>
        <span className="text-[11px] text-ink-3">a maneuver test, not a shift</span>
      </div>
      <button type="button" disabled={busy || total === 0} onClick={submit}
        className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-accent text-[13px] font-semibold text-bg transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? <><Loader2 size={14} className="animate-spin" />Planning {total} trucks…</> : <>Run yard test · {total} truck{total === 1 ? '' : 's'} at once</>}
      </button>
    </div>
  )
}

function Stepper({ value, onDec, onInc, label }: { value: number; onDec: () => void; onInc: () => void; label: string }) {
  const b = 'flex h-7 w-7 items-center justify-center rounded-lg text-ink-2 ring-1 ring-inset ring-line hover:bg-white/[0.06] hover:text-ink disabled:opacity-35'
  return (
    <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <button type="button" className={b} onClick={onDec} disabled={value <= 0} aria-label={`fewer ${label}`}><Minus size={13} /></button>
      <span className={clsx('w-6 text-center text-[14px] font-semibold tabular-nums', value ? 'text-ink' : 'text-ink-3')}>{value}</span>
      <button type="button" className={b} onClick={onInc} disabled={value >= MAX_PER_TYPE} aria-label={`more ${label}`}><Plus size={13} /></button>
    </div>
  )
}

const min = (s: number | null | undefined) => (s == null ? '?' : s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`)

export function ManeuverResultCard({ r, shown, onShow, onDocks }: { r: ManeuverResult; shown: boolean; onShow: () => void; onDocks?: () => void }) {
  const selIssue = useYardUi((s) => s.selectedIssue)
  const selectIssue = useYardUi((s) => s.selectIssue)
  const s = r.summary
  const order: IssueClass[] = ['geometric', 'congestion', 'planner', 'assumption']
  const classes = order.filter((c) => r.issues.some((x) => x.class === c))
  const doorSrc = r.doors_used.reduce<Record<string, number>>((a, d) => ({ ...a, [d.source]: (a[d.source] ?? 0) + 1 }), {})
  const rows = issueRows(r.issues)
  // truck-level first: one line per truck (its failure, else its own wait/conflict issue); per-door checks collapsed
  const truckRows = r.vehicles.map((v) => {
    const waitKeys = v.keys.filter((k) => k[6] === 'wait').length * 0.2
    const evs = r.events.filter((e) => e.truck === v.id && (e.kind === 'wait' || e.kind === 'conflict'))
    const worstEv = evs.reduce<(typeof evs)[number] | null>((a, e) => (!a || (e.seconds ?? 0) > (a.seconds ?? 0) ? e : a), null)
    const wait = Math.max(waitKeys, worstEv?.seconds ?? 0)
    const own = rows.find((x) => x.trucks?.includes(v.id) && (v.status !== 'failed' || x.class === v.fail?.class))
    const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
    // this truck's own sentence: its failure, else its worst wait event, else its own issue
    const text = v.fail?.text ? cap(v.fail.text) : worstEv?.text ? cap(worstEv.text) : wait > 1 ? `Waited ${min(wait)} in total.` : own?.text ?? null
    const cls = (v.status === 'failed' ? v.fail?.class : wait > 1 ? (own?.class ?? 'congestion') : own?.class) as IssueClass | undefined
    return { v, wait, cls, text, issue: own?.i }
  })
  const truckIssueIdx = new Set(truckRows.map((t) => t.issue).filter((i): i is number => i != null))
  const doorChecks = rows.filter((x) => !truckIssueIdx.has(x.i) && !(x.trucks?.length))
  const selTruck = useYardUi((s) => s.selectedTruck)
  const selectTruck = useYardUi((s) => s.selectTruck)
  const label = (t: string) => ({ semi_53: '53\' semi', box_truck_26: '26\' box', last_mile_van: 'van' } as Record<string, string>)[t] ?? t
  return (
    <div className="mt-4" data-testid="maneuver-result">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Result</h3>
        {!shown && <button onClick={onShow} className="flex items-center gap-1 text-[11px] text-accent hover:underline"><MapIcon size={12} /> Show on map</button>}
      </div>
      <div className="rounded-xl bg-white/[0.03] p-3 text-[13px] leading-snug text-ink ring-1 ring-inset ring-line">{s.headline}</div>
      <div className="mt-1.5 grid grid-cols-4 gap-1.5">
        <Stat label="Completed" value={`${s.completed}/${s.trucks}`} tone={s.completed === s.trucks ? 'good' : 'bad'} />
        <Stat label="At once" value={String(s.concurrent_max)} />
        <Stat label="All clear in" value={min(s.makespan_s)} />
        <Stat label="Worst wait" value={s.worst_wait && s.worst_wait.s > 0 ? min(s.worst_wait.s) : 'none'} sub={s.worst_wait?.s ? s.worst_wait.truck ?? undefined : undefined}
          tone={s.worst_wait && s.worst_wait.s > 60 ? 'warn' : undefined} />
      </div>

      {/* status strip: one chip per truck */}
      <div className="mt-2 flex flex-wrap gap-1" data-testid="truck-strip">
        {truckRows.map(({ v, wait, cls }) => {
          const failed = v.status === 'failed'
          return (
            <button key={v.id} type="button" onClick={() => { selectTruck(v.id === selTruck ? null : v.id); if (!shown) onShow() }}
              title={failed ? `${v.id} not completed (${ISSUE_STYLE[cls as IssueClass]?.label ?? cls})` : wait > 1 ? `${v.id} waited ${min(wait)}` : `${v.id} done`}
              className={clsx('inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] ring-1 ring-inset transition',
                v.id === selTruck ? 'bg-accent/10 ring-accent/40' : 'ring-line hover:bg-white/[0.05]')}>
              <span className="h-2 w-2 rounded-full" style={{ background: rgb(truckColor(v.color_idx)) }} />
              <span className="font-mono text-ink">{v.id}</span>
              {failed ? <span className="text-bad">✕ {cls ?? 'failed'}</span> : wait > 1 ? <span className="text-warn">waited {min(wait)}</span> : <span className="text-good">✓</span>}
            </button>
          )
        })}
      </div>

      {truckRows.some((t) => t.text) && (
        <div className="mt-3">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Trucks</div>
          <div className="space-y-1">
            {truckRows.filter((t) => t.text).map(({ v, cls, text }) => (
              <button key={v.id} type="button" onClick={() => { selectTruck(v.id === selTruck ? null : v.id); if (!shown) onShow() }}
                className={clsx('flex w-full items-start gap-2 rounded-xl p-2.5 text-left transition',
                  v.id === selTruck ? 'bg-accent/10 ring-1 ring-inset ring-accent/40' : 'hover:bg-white/[0.04]')}>
                <span className={clsx('mt-[1px] flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full px-1 text-[11px] font-bold',
                  v.status === 'failed' ? 'bg-bad text-bg' : 'bg-warn text-bg')}>{v.status === 'failed' ? '✕' : '!'}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[11.5px] text-ink">{v.id}</span><span className="text-[11px] text-ink-3">{label(v.type)}</span>
                    {cls && ISSUE_STYLE[cls] && <span className={clsx('ml-auto inline-flex h-[17px] items-center rounded px-1.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset', ISSUE_STYLE[cls].cls)}>{ISSUE_STYLE[cls].label}</span>}
                  </div>
                  <div className="text-[12px] leading-snug text-ink-2">{text}</div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {doorChecks.length > 0 && (
        <details className="mt-2 rounded-xl bg-white/[0.02] ring-1 ring-inset ring-line" data-testid="door-checks">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-2 text-[12px] text-ink-2">
            <ChevronDown size={12} />Door checks ({doorChecks.length})
            <span className="ml-auto flex gap-1">{order.map((c) => [c, doorChecks.filter((x) => x.class === c).length] as const).filter(([, n]) => n).map(([c, n]) => (
              <span key={c} className={clsx('inline-flex h-[17px] items-center rounded px-1.5 text-[11px] font-semibold tabular-nums ring-1 ring-inset', ISSUE_STYLE[c].cls)}>{n} {c}</span>
            ))}</span>
          </summary>
          <div className="space-y-0.5 px-1 pb-1">
            {doorChecks.map((x) => (
              <button key={x.i} type="button" onClick={() => { selectIssue(x.i === selIssue ? null : x.i); if (!shown) onShow() }}
                className={clsx('flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition',
                  x.i === selIssue ? 'bg-accent/10 ring-1 ring-inset ring-accent/40' : 'hover:bg-white/[0.04]')}>
                <span className="mt-[1px] flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-bg"
                  style={{ background: `rgb(${ISSUE_STYLE[x.class]?.color.join(' ')})` }}>{x.n}</span>
                <div className="min-w-0 flex-1 text-[11.5px] leading-snug text-ink-2">{x.door && <span className="mr-1 font-mono text-ink">{x.door}</span>}{x.text}</div>
              </button>
            ))}
          </div>
        </details>
      )}
      {classes.length > 0 && (
        <details className="mt-1.5 rounded-xl bg-white/[0.02] px-2.5 py-2 ring-1 ring-inset ring-line">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-[11.5px] text-ink-3"><ChevronDown size={12} />What the issue types mean</summary>
          <dl className="mt-1.5 space-y-1.5">
            {classes.map((c) => (
              <div key={c} className="text-[11.5px] leading-snug">
                <dt className={clsx('inline font-semibold', ISSUE_STYLE[c].cls.split(' ')[0])}>{ISSUE_STYLE[c].label}: </dt>
                <dd className="inline text-ink-2">{ISSUE_STYLE[c].explain}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      <div className="mt-3 flex items-center gap-2 rounded-xl bg-white/[0.02] px-2.5 py-2 text-[11.5px] text-ink-2 ring-1 ring-inset ring-line">
        <DoorOpen size={14} className="shrink-0 text-ink-3" />
        <span className="min-w-0 flex-1">{r.doors_used.length} doors used · {Object.entries(doorSrc).map(([k, n]) => `${n} ${k}`).join(' · ') || 'none'}</span>
        {onDocks && <button onClick={onDocks} className="shrink-0 text-[11.5px] font-medium text-accent hover:underline">Review docks</button>}
      </div>
      {r.assumptions.length > 0 && (
        <details className="mt-1.5 text-[11.5px] text-ink-2">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-ink-3"><ChevronDown size={12} />Assumptions ({r.assumptions.length})</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </details>
      )}
    </div>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn' }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2 py-1.5 ring-1 ring-inset ring-line">
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className={clsx('mt-0.5 text-[14px] font-semibold tabular-nums', tone === 'good' && 'text-good', tone === 'bad' && 'text-bad', tone === 'warn' && 'text-warn')}>{value}</div>
      {sub && <div className="truncate font-mono text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}
