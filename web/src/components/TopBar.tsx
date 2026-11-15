import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { Search, X, Loader2, AlertTriangle, Sparkles, CornerDownLeft } from 'lucide-react'
import { MARKETS, useStore } from '../lib/store'
import type { Chip } from '../lib/api'
import { chipValue, isNum } from '../lib/format'

export function MarketTabs() {
  const market = useStore((s) => s.market)
  const setMarket = useStore((s) => s.setMarket)
  const pins = useStore((s) => s.pins)
  const idx = MARKETS.indexOf(market)
  return (
    <div className="glass relative flex h-10 items-center rounded-full p-1" role="tablist" aria-label="Market">
      <div className="absolute top-1 bottom-1 w-[148px] rounded-full bg-accent/15 ring-1 ring-inset ring-accent/40 transition-transform duration-300 ease-out"
        style={{ transform: `translateX(${idx * 148}px)` }} />
      {MARKETS.map((m) => {
        const n = pins.filter((p) => p.market === m).length
        return (
          <button key={m} role="tab" aria-selected={m === market} onClick={() => setMarket(m)}
            className={clsx('relative z-10 flex h-8 w-[148px] items-center justify-center gap-2 rounded-full text-[13px] font-medium transition-colors',
              m === market ? 'text-accent' : 'text-ink-2 hover:text-ink')}>
            {m}
            {n > 0 && <span className={clsx('text-[11px] tabular-nums', m === market ? 'text-accent/70' : 'text-ink-3')}>{n}</span>}
          </button>
        )
      })}
    </div>
  )
}

const EXAMPLES = [
  '40k SF with 28 ft clear and plenty of docks',
  'cold storage near the port, available now',
  'small warehouse under $12/SF with a 3D tour',
]

export function SearchBar() {
  const query = useStore((s) => s.query)
  const setQuery = useStore((s) => s.setQuery)
  const runSearch = useStore((s) => s.runSearch)
  const clearSearch = useStore((s) => s.clearSearch)
  const searching = useStore((s) => s.searching)
  const search = useStore((s) => s.search)
  const error = useStore((s) => s.searchError)
  const [focus, setFocus] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') { e.preventDefault(); input.current?.focus() }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])

  const chips = search?.chips ?? []
  return (
    <div className="w-full">
      <form onSubmit={(e) => { e.preventDefault(); runSearch({ query }); input.current?.blur() }}
        className={clsx('glass flex h-12 items-center gap-3 rounded-2xl px-4 transition', focus && 'ring-1 ring-accent/50')}>
        {searching ? <Loader2 size={18} className="animate-spin text-accent" /> : <Search size={18} className="text-ink-3" />}
        <input ref={input} value={query} onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
          placeholder="Describe the space you need, in your own words"
          className="h-full flex-1 bg-transparent text-[14px] text-ink placeholder:text-ink-3 focus:outline-none" />
        {(query || search) && (
          <button type="button" onClick={clearSearch} title="Clear search" aria-label="Clear search" className="rounded-md p-1 text-ink-3 hover:bg-white/5 hover:text-ink">
            <X size={16} />
          </button>
        )}
        {!query && !search && <kbd className="rounded border border-line px-1.5 text-[11px] text-ink-3">/</kbd>}
        {query && !searching && <CornerDownLeft size={14} className="text-ink-3" />}
      </form>

      {focus && !query && !search && (
        <div className="glass fade-up mt-2 rounded-xl p-1.5">
          {EXAMPLES.map((ex) => (
            <button key={ex} onMouseDown={(e) => { e.preventDefault(); runSearch({ query: ex }); input.current?.blur() }}
              className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] text-ink-2 hover:bg-white/5 hover:text-ink">
              <Sparkles size={14} className="text-accent/70" /> {ex}
            </button>
          ))}
        </div>
      )}

      {error && <div className="fade-up mt-2 rounded-lg bg-bad/10 px-3 py-2 text-[12px] text-bad ring-1 ring-bad/25">{error}</div>}

      {chips.length > 0 && (
        <div className="scroll-thin fade-up mt-1.5 flex gap-1.5 overflow-x-auto pb-1 pr-6 [&>*]:shrink-0" style={{ maskImage: 'linear-gradient(to right, #000 calc(100% - 28px), transparent)' }} data-testid="search-chips">
          {chips.map((c, i) => (
            <ChipView key={`${c.field}-${i}`} chip={c}
              onRemove={() => runSearch({ chips: chips.filter((_, j) => j !== i) })}
              onChange={(v) => runSearch({ chips: chips.map((x, j) => (j === i ? { ...x, value: v, source: 'user', conflict: null } : x)) })} />
          ))}
          {search?.unparsed && (
            <span title="Words the search did not turn into a requirement" className="glass inline-flex h-7 items-center rounded-full px-2.5 text-[12px] italic text-ink-3">
              not used: “{search.unparsed}”
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function ChipView({ chip, onRemove, onChange }: { chip: Chip; onRemove: () => void; onChange: (v: unknown) => void }) {
  const [edit, setEdit] = useState(false)
  const [draft, setDraft] = useState('')
  const isBool = typeof chip.value === 'boolean'
  const label = chip.label.replace(/\s*\(.*?\)\s*/g, ' ').trim()
  const conflict = chip.conflict && chip.conflict.claude !== undefined
  const srcTitle = chip.source === 'claude' ? 'Understood by Claude' : chip.source === 'user' ? 'Edited by you' : 'Parsed by rules'

  const commit = () => {
    setEdit(false)
    const t = draft.trim()
    if (!t) return
    const n = Number(t.replace(/[,$\s]|sf|ft/gi, '').replace(/k$/i, '000'))
    onChange(isNum(chip.value) ? (Number.isFinite(n) ? n : chip.value) : t)
  }

  return (
    <span title={`${srcTitle} from “${chip.said}”${conflict ? ` · Claude read ${String(chip.conflict!.claude)}; kept the ${chip.conflict!.kept} value` : ''}`}
      className={clsx('glass group inline-flex h-7 items-center gap-1.5 rounded-full pl-2.5 pr-1 text-[12px]',
        conflict && 'ring-1 ring-warn/40')}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', chip.source === 'claude' ? 'bg-accent' : chip.source === 'user' ? 'bg-ink' : 'bg-ink-3')} />
      <span className="text-ink-3">{label}</span>
      <span className="text-ink-3">{chip.op && chip.op !== '=' ? chip.op : ''}</span>
      {edit ? (
        <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEdit(false) }}
          className="w-20 rounded bg-white/10 px-1 text-ink focus:outline-none" />
      ) : (
        <button onClick={() => (isBool ? onChange(!chip.value) : (setDraft(String(chip.value ?? '')), setEdit(true)))}
          className="font-medium text-ink hover:text-accent" title="Edit" aria-label="Edit">
          {chipValue(chip)}
        </button>
      )}
      {conflict && <AlertTriangle size={12} className="text-warn" />}
      <button onClick={onRemove} title="Remove" aria-label="Remove" className="rounded-full p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink">
        <X size={12} />
      </button>
    </span>
  )
}
