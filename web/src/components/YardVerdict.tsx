// Yard verdict: the buyer-facing result of the SAME truck test on every building (5 x 53 ft semis), from
// GET /api/yard/verdict/{id}. Cached runs answer instantly; "Run" computes it (10-30 s). Used on the card and in Compare.
import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { Loader2, Play, Truck } from 'lucide-react'
import { SectionTitle } from './ui'

export interface Verdict {
  status: 'computed' | 'not_run'
  grade?: 'good' | 'limited' | 'poor'
  line?: string
  test: string
  dock_doors?: number
  semi_backin_ok?: number
  docked_max?: number
  trucks?: number
  completed?: number
  makespan_min?: number
  worst_wait_min?: number
  limits?: { class: string; text: string }[]
  basis?: string
}

const cache = new Map<number, Verdict>()

export function useYardVerdict(id: number | null) {
  const [v, setV] = useState<Verdict | null>(id != null ? cache.get(id) ?? null : null)
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState('')
  const load = useCallback(async (run: boolean) => {
    if (id == null) return
    if (run) setRunning(true)
    setErr('')
    try {
      const r = await fetch(`/api/yard/verdict/${id}${run ? '?run=true' : ''}`)
      if (!r.ok) throw new Error(String(r.status))
      const d = (await r.json()) as Verdict
      cache.set(id, d)
      setV(d)
    } catch {
      setErr('The yard test could not run for this property')
    } finally {
      setRunning(false)
    }
  }, [id])
  useEffect(() => { if (id != null && !cache.has(id)) load(false); else setV(id != null ? cache.get(id)! : null) }, [id, load])
  return { v, running, err, run: () => load(true) }
}

export const GRADE: Record<string, { label: string; cls: string }> = {
  good: { label: 'Good', cls: 'bg-good/15 text-good ring-good/30' },
  limited: { label: 'Limited', cls: 'bg-warn/15 text-warn ring-warn/30' },
  poor: { label: 'Poor', cls: 'bg-bad/15 text-bad ring-bad/30' },
}

export function GradeChip({ grade }: { grade?: string }) {
  const g = GRADE[grade ?? ''] ?? null
  if (!g) return <span className="rounded-md px-1.5 py-0.5 text-[11px] text-ink-3 ring-1 ring-inset ring-line">?</span>
  return <span className={clsx('rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset', g.cls)}>{g.label}</span>
}

/** The card row: grade, the one-line verdict, the main limit, and Run / Watch. */
export function YardVerdictRow({ id, onWatch }: { id: number; onWatch: () => void }) {
  const { v, running, err, run } = useYardVerdict(id)
  return (
    <div>
      <SectionTitle right={<span className="text-[11px] text-ink-3">5 × 53 ft semis</span>}>Yard verdict</SectionTitle>
      <div className="rounded-xl bg-white/[0.03] px-3 py-2.5 ring-1 ring-inset ring-line" data-testid="yard-verdict">
        {v?.status === 'computed' ? (
          <>
            <div className="flex items-start gap-2">
              <GradeChip grade={v.grade} />
              <div className="min-w-0 flex-1 text-[12.5px] leading-snug text-ink">{v.line}</div>
            </div>
            {v.limits?.[0] && <div className="mt-1.5 text-[11.5px] leading-snug text-ink-3">Main limit: {v.limits[0].text}</div>}
            <div className="mt-2 flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-[11px] text-ink-3" title={`${v.test}. ${v.basis ?? ''}`}>{v.basis}</span>
              <button onClick={onWatch} className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent/15 px-2.5 py-1 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/25">
                <Play size={12} /> Watch it
              </button>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-3">
            <Truck size={15} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1 text-[12px] leading-snug text-ink-3">
              {err || 'Not tested yet: can 5 tractor-trailers get in, back into the docks and leave?'}
            </div>
            <button onClick={run} disabled={running}
              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent/15 px-2.5 py-1 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30 hover:bg-accent/25 disabled:opacity-60">
              {running ? <><Loader2 size={12} className="animate-spin" /> ~20 s</> : 'Run'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/** A compact compare cell. */
export function YardVerdictCell({ id }: { id: number }) {
  const { v, running, run } = useYardVerdict(id)
  if (v?.status !== 'computed') {
    return (
      <button onClick={run} disabled={running} className="text-[11.5px] text-accent hover:underline disabled:text-ink-3">
        {running ? 'testing…' : 'Run test'}
      </button>
    )
  }
  return (
    <div className="min-w-0" title={v.line}>
      <GradeChip grade={v.grade} />
      <div className="mt-1 text-[11.5px] leading-snug tabular-nums text-ink-2">
        {v.semi_backin_ok}/{v.dock_doors} docks fit · {v.docked_max} at once
        <br />{v.completed ? `${v.completed}/${v.trucks} in ${v.makespan_min} min` : `0/${v.trucks}: no truck got in`}
      </div>
    </div>
  )
}
