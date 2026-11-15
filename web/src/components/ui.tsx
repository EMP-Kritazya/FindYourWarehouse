import { useEffect, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { Check as CheckIcon, Minus, X, HelpCircle } from 'lucide-react'
import type { CheckStatus } from '../lib/api'
import { sourceLabel } from '../lib/format'

const SRC_STYLE: Record<string, string> = {
  costar: 'text-sky-300 bg-sky-400/10 ring-sky-400/25',
  matterport: 'text-violet-300 bg-violet-400/10 ring-violet-400/25',
  vexcel: 'text-amber-300 bg-amber-400/10 ring-amber-400/25',
  derived: 'text-ink-2 bg-white/5 ring-white/10',
  claude: 'text-accent bg-accent/10 ring-accent/25',
  rules: 'text-ink-2 bg-white/5 ring-white/10',
}

export function SourceChip({ source, title }: { source?: string | null; title?: string }) {
  if (!source) return null
  return (
    <span title={title ?? `Source: ${sourceLabel(source)}`}
      className={clsx('inline-flex h-[18px] items-center rounded px-1.5 text-[11px] font-medium uppercase tracking-wide ring-1 ring-inset',
        SRC_STYLE[source] ?? SRC_STYLE.derived)}>
      {sourceLabel(source)}
    </span>
  )
}

/** 5 dots, filled by confidence (0..1). */
export function ConfidenceDots({ value }: { value?: number | null }) {
  if (value == null) return null
  const n = Math.max(1, Math.round(value * 5))
  return (
    <span className="inline-flex items-center gap-[3px]" title={`Confidence ${Math.round(value * 100)}%`}>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={clsx('h-[5px] w-[5px] rounded-full', i < n ? 'bg-ink-2' : 'bg-line-2')} />
      ))}
    </span>
  )
}

export function Unknown({ why }: { why: string }) {
  return (
    <span title={why} className="inline-flex h-6 w-6 cursor-help items-center justify-center rounded-full bg-white/5 text-[13px] font-semibold text-ink-3 ring-1 ring-inset ring-white/10">
      ?
    </span>
  )
}

export function StatusIcon({ status, size = 16 }: { status: CheckStatus; size?: number }) {
  const cls = 'inline-flex shrink-0 items-center justify-center rounded-full'
  const s = { width: size + 4, height: size + 4 }
  if (status === 'pass') return <span style={s} className={clsx(cls, 'bg-good/15 text-good')}><CheckIcon size={size - 4} strokeWidth={3} /></span>
  if (status === 'fail') return <span style={s} className={clsx(cls, 'bg-bad/15 text-bad')}><X size={size - 4} strokeWidth={3} /></span>
  if (status === 'near') return <span style={s} className={clsx(cls, 'bg-warn/15 text-warn')}><Minus size={size - 4} strokeWidth={3} /></span>
  return <span style={s} className={clsx(cls, 'bg-white/5 text-ink-3')}><HelpCircle size={size - 2} /></span>
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} />
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2.5 flex items-center justify-between">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">{children}</h3>
      {right}
    </div>
  )
}

/** Score band: thin track 0..100 with the uncertainty band and a tick at the score. */
export function ScoreBand({ score, band, compact }: { score: number; band: [number, number]; compact?: boolean }) {
  return (
    <div className={clsx('relative w-full rounded-full bg-white/[0.06]', compact ? 'h-1.5' : 'h-2')}
      title={`Score ${score} (could be ${band[0]}–${band[1]} once unknowns are verified)`}>
      <div className="absolute inset-y-0 rounded-full bg-accent/25" style={{ left: `${band[0]}%`, width: `${Math.max(1, band[1] - band[0])}%` }} />
      <div className="absolute inset-y-0 rounded-full bg-accent" style={{ left: `${Math.max(0, score - 1)}%`, width: 3 }} />
    </div>
  )
}

export function useAsync<T>(fn: (() => Promise<T>) | null, deps: unknown[]) {
  const [st, setSt] = useState<{ data: T | null; error: unknown; loading: boolean }>({ data: null, error: null, loading: !!fn })
  useEffect(() => {
    if (!fn) { setSt({ data: null, error: null, loading: false }); return }
    let live = true
    setSt({ data: null, error: null, loading: true })
    fn().then((data) => live && setSt({ data, error: null, loading: false }),
      (error) => live && setSt({ data: null, error, loading: false }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return st
}

export function IconButton({ children, title, onClick, active, className }: {
  children: ReactNode; title: string; onClick?: () => void; active?: boolean; className?: string
}) {
  return (
    <button title={title} aria-label={title} onClick={onClick}
      className={clsx('inline-flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 transition hover:bg-white/8 hover:text-ink',
        active && 'bg-accent/15 text-accent', className)}>
      {children}
    </button>
  )
}
