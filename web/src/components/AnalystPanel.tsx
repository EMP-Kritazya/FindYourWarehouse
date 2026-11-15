// S4: the Analyst thread (plan B2, CONTRACTS_v3 §4). Text-first: the user's ask, the agent's answer, a collapsible
// trail of the steps it took (tool -> one-line finding from the server's `step` messages) and evidence chips (the
// buildings those steps touched). VoiceOrb owns the websocket and renders this panel; voice stays optional.
import { useEffect, useRef, useState, type FormEvent } from 'react'
import clsx from 'clsx'
import { ChevronRight, AlertTriangle, Send, Volume2, VolumeX, X, Sparkles } from 'lucide-react'
import { useStore } from '../lib/store'

export interface Step { tool: string; summary: string; ids?: number[]; ok?: boolean }
export interface ThreadMsg {
  id: number
  role: 'user' | 'agent'
  text: string
  partial?: boolean
  pending?: boolean
  kind?: 'event'
  steps: Step[]
  unverified?: string[]
  error?: string
}
export interface Chip { label: string; say: string }

export const TOOL_LABEL: Record<string, string> = {
  rank_attribute: 'Ranked the catalog',
  dock_evidence: 'Checked dock evidence',
  compare_warehouses: 'Compared',
  find_comparables: 'Found comparables',
  yard_maneuver_test: 'Yard maneuver test',
  search_warehouses: 'Searched',
  rank_by: 'Ranked from aerials',
  closest_to: 'Searched nearby',
  building_brief: 'Read the building',
  find_address: 'Found the address',
  update_buyer_profile: 'Noted your needs',
  shortlist: 'Shortlist',
  listing_contact: 'Checked the listing',
  request_tour: 'Logged a tour request',
  prepare_test: 'Planned a test',
  run_test: 'Ran a test',
  ui: 'Moved the screen',
  task: 'Delegated',
  get_facts: 'Read the facts',
  explain_rank: 'Explained the ranking',
  compare_buildings: 'Read the briefs',
  vexcel_profile: 'Read the aerials',
  vexcel_rank: 'Ranked from aerials',
  vexcel_search: 'Searched the aerials',
  vexcel_detect: 'Ran detection',
  vexcel_imagery: 'Checked imagery',
  vexcel_metrics: 'Read metric definitions',
  interior_facts: 'Read the scan',
  last_test_result: 'Read the last test',
  note: 'Working note',
}

export default function AnalystPanel({ thread, status, error, chips, connected, hint, muted, onMute, onSend, onClose }: {
  thread: ThreadMsg[]
  status: string
  error: string
  chips: Chip[]
  connected: boolean
  hint: string
  muted: boolean
  onMute: () => void
  onSend: (text: string, quiet?: boolean) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const last = thread[thread.length - 1]
  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [thread.length, last?.text, last?.steps.length, status])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const t = draft.trim()
    if (!t) return
    onSend(t)
    setDraft('')
  }

  return (
    <div className="glass pointer-events-auto relative flex w-[380px] max-w-[calc(100vw-32px)] flex-col overflow-hidden rounded-2xl text-[13px] text-ink" role="log" aria-live="polite" aria-label="Analyst">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">
          <Sparkles size={12} className="text-accent" /> Analyst
          <span className={clsx('ml-1 h-1.5 w-1.5 rounded-full', connected ? 'bg-good' : 'bg-bad')} title={connected ? 'Connected' : 'Connecting…'} />
        </div>
        <div className="flex items-center gap-0.5">
          <button onClick={onMute} title={muted ? 'Voice replies off' : 'Voice replies on'} aria-label={muted ? 'Unmute voice' : 'Mute voice'}
            className="flex h-6 w-6 items-center justify-center rounded-md text-ink-3 hover:bg-white/[0.08] hover:text-ink">
            {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
          </button>
          <button onClick={onClose} title="Hide" aria-label="Hide" className="flex h-6 w-6 items-center justify-center rounded-md text-ink-3 hover:bg-white/[0.08] hover:text-ink"><X size={13} /></button>
        </div>
      </div>

      <div ref={scroller} className="scroll-thin max-h-[min(46vh,380px,var(--dock-room,380px))] space-y-2.5 overflow-y-auto px-3 py-2.5">
        {!thread.length && (
          <div className="text-[12.5px] leading-snug text-ink-3">
            Ask an analysis question: which warehouse has the most loading docks, compare this one for a cross-dock, can 5 trucks use this yard at once.
          </div>
        )}
        {thread.map((m) => m.role === 'user'
          ? <div key={m.id} className={clsx('ml-8 rounded-xl rounded-br-sm bg-accent/[0.12] px-2.5 py-1.5 text-[12.5px] leading-snug text-ink', m.partial && 'italic opacity-70')}>{m.text}</div>
          : <AgentMsg key={m.id} m={m} live={m === last} status={m === last ? status : ''} />)}
        {error && <div className="flex items-start gap-1.5 text-[11.5px] text-bad"><AlertTriangle size={12} className="mt-[2px] shrink-0" />{error}</div>}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2" aria-label="Suggested next steps">
          {chips.map((c, i) => (
            <button key={i} type="button" title={c.say} aria-label={c.say} onClick={() => onSend(c.say, true)}
              className="rounded-full bg-white/[0.05] px-2.5 py-[3px] text-[11.5px] text-ink-2 ring-1 ring-inset ring-line-2 hover:bg-accent/15 hover:text-accent hover:ring-accent/40">
              {c.label}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="flex items-center gap-1.5 border-t border-line px-2 py-2">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask the analyst…" aria-label="Message the analyst"
          className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-accent" />
        <button type="submit" title="Send" aria-label="Send" disabled={!draft.trim()}
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-40"><Send size={14} /></button>
      </form>
      <div className="px-3 pb-1.5 text-[11px] text-ink-3">{connected ? hint : 'Connecting…'}</div>
    </div>
  )
}

function AgentMsg({ m, live, status }: { m: ThreadMsg; live: boolean; status: string }) {
  const [open, setOpen] = useState(false)
  const pins = useStore((s) => s.pinsById)
  const select = useStore((s) => s.select)
  const ids = [...new Set(m.steps.flatMap((s) => s.ids ?? []))].filter((i) => pins.has(i)).slice(0, 6)
  return (
    <div className={clsx('mr-2', m.kind === 'event' && 'border-l-2 border-accent/30 pl-2')}>
      {m.steps.length > 0 && (
        <button onClick={() => setOpen((o) => !o)} className="mb-1 flex items-center gap-1 text-[11px] text-ink-3 hover:text-ink-2" aria-expanded={open}>
          <ChevronRight size={12} className={clsx('transition', open && 'rotate-90')} />
          {m.steps.length} step{m.steps.length > 1 ? 's' : ''}: {[...new Set(m.steps.map((s) => TOOL_LABEL[s.tool] ?? s.tool))].slice(0, 3).join(' → ')}
        </button>
      )}
      {open && (
        <ol className="mb-1.5 space-y-1 border-l border-line-2 pl-2.5">
          {m.steps.map((s, i) => (
            <li key={i} className="text-[11.5px] leading-snug">
              <span className={clsx('font-medium', s.ok === false ? 'text-bad' : 'text-ink-2')}>{TOOL_LABEL[s.tool] ?? s.tool}</span>
              <span className="text-ink-3"> · {s.summary}</span>
            </li>
          ))}
        </ol>
      )}
      {m.text && <div className="whitespace-pre-wrap leading-snug text-ink">{m.text}</div>}
      {live && (m.pending || status) && (
        <div className="mt-0.5 animate-pulse text-[11.5px] text-ink-3">{status || 'Thinking'}…</div>
      )}
      {ids.length > 0 && !m.pending && (
        <div className="mt-1.5 flex flex-wrap gap-1" aria-label="Evidence">
          {ids.map((i) => (
            <button key={i} onClick={() => select(i)} title="Open the card" aria-label="Open the card"
              className="max-w-[170px] truncate rounded-md bg-panel-3 px-1.5 py-[1px] text-[11px] text-ink-2 ring-1 ring-inset ring-line hover:text-accent hover:ring-accent/40">
              {pins.get(i)?.address ?? i}
            </button>
          ))}
        </div>
      )}
      {m.unverified && m.unverified.length > 0 && (
        <div className="mt-1 flex items-center gap-1 text-[11px] text-warn" title="These numbers were not returned by any tool in this conversation">
          <AlertTriangle size={11} /> unverified: {m.unverified.join(', ')}
        </div>
      )}
      {m.error && <div className="mt-1 text-[11px] text-bad">{m.error}</div>}
    </div>
  )
}
