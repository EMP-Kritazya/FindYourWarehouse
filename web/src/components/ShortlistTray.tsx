import { useState } from 'react'
import clsx from 'clsx'
import { ChevronUp, Columns3, Star, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { fmtKSF } from '../lib/format'

/** Pinned shortlist tray at the bottom of the rail: star from the card (or the guide's `shortlist` command), compare from here. */
export default function ShortlistTray() {
  const ids = useStore((s) => s.shortlist)
  const open = useStore((s) => s.shortlistOpen)
  const pinsById = useStore((s) => s.pinsById)
  const doIt = useStore((s) => s.shortlistDo)
  const select = useStore((s) => s.select)
  const setCompare = useStore((s) => s.setCompare)
  const compareIds = useStore((s) => s.compareIds)
  const compareOpen = useStore((s) => s.compareOpen)
  const showCompare = useStore((s) => s.showCompare)
  const pendingCompare = compareIds.length >= 2 && !compareOpen && compareIds.join() !== ids.slice(0, 4).join()
  const pins = ids.map((i) => pinsById.get(i)).filter(Boolean) as NonNullable<ReturnType<typeof pinsById.get>>[]

  return (
    <section className="border-t border-line bg-warn/[0.03]">
      <div className="flex h-11 items-center gap-2 px-3">
        <button onClick={() => doIt(open ? 'hide' : 'show')} disabled={!pins.length}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-1 text-left disabled:cursor-default">
          <Star size={14} className={pins.length ? 'text-warn' : 'text-ink-3'} fill={pins.length ? 'currentColor' : 'none'} />
          <span className="text-[13px] font-medium text-ink">Shortlist</span>
          <span className="text-[12px] tabular-nums text-ink-3">{pins.length || ''}</span>
          {!pins.length && <span className="truncate text-[11px] text-ink-3">· star a warehouse to keep it here</span>}
          {pins.length > 0 && !open && (
            <span className="ml-1 flex -space-x-1.5">
              {pins.slice(0, 5).map((p) => <Mini key={p.id} id={p.id} photo={p.photo} />)}
            </span>
          )}
          {pins.length > 0 && <ChevronUp size={14} className={clsx('ml-auto text-ink-3 transition', !open && 'rotate-180')} />}
        </button>
        {pendingCompare && (
          <button onClick={() => showCompare(true)} title="Reopen the comparison you built from the cards"
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-lg bg-white/[0.07] px-2 text-[12px] text-ink ring-1 ring-inset ring-line-2 hover:bg-white/10">
            <Columns3 size={13} /> {compareIds.length}
          </button>
        )}
        {pins.length >= 2 && (
          <button onClick={() => setCompare(ids.slice(0, 4))} title="Compare the shortlist side by side"
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-lg bg-warn/90 px-2.5 text-[12px] font-semibold text-bg hover:bg-warn">
            <Columns3 size={13} /> Compare
          </button>
        )}
      </div>
      {open && pins.length > 0 && (
        <div className="scroll-thin max-h-[200px] space-y-0.5 overflow-y-auto px-2 pb-2">
          {pins.map((p, i) => (
            <div key={p.id} className="group flex items-center gap-2.5 rounded-lg px-1.5 py-1 hover:bg-white/[0.04]">
              <span className="w-3 text-[11px] tabular-nums text-ink-3">{i + 1}</span>
              <button onClick={() => select(p.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <Mini id={p.id} photo={p.photo} size="md" />
                <span className="min-w-0">
                  <span className="block truncate text-[12px] font-medium text-ink">{p.address}</span>
                  <span className="block text-[11px] text-ink-3">{fmtKSF(p.rba_sqft)} · {p.market}</span>
                </span>
              </button>
              <button onClick={() => doIt('remove', p.id)} title="Remove from shortlist" className="rounded p-1 text-ink-3 opacity-0 transition hover:bg-white/10 hover:text-ink group-hover:opacity-100"><X size={12} /></button>
            </div>
          ))}
          {pins.length > 4 && <div className="px-2 text-[10px] text-ink-3">Compare shows the first 4.</div>}
        </div>
      )}
    </section>
  )
}

function Mini({ id, photo, size = 'sm' }: { id: number; photo: string | null; size?: 'sm' | 'md' }) {
  const cands = [`/media/twins/exterior/${id}_ortho.jpg`, photo].filter(Boolean) as string[]
  const [i, setI] = useState(0)
  const cls = size === 'sm' ? 'h-6 w-6 rounded-md ring-2 ring-panel' : 'h-8 w-8 rounded-md ring-1 ring-white/10'
  return i < cands.length
    ? <img src={cands[i]} alt="" onError={() => setI(i + 1)} className={clsx('shrink-0 object-cover', cls)} />
    : <span className={clsx('shrink-0 bg-panel-3', cls)} />
}
