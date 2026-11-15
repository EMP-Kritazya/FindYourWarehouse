// True cost per pallet: a compact panel beside the property card. Every line says where its number comes from;
// assumed unit costs are editable ("what if"), and a rent estimate is never shown as the building's own rent.
import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { RotateCcw, Wallet, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { SrcTag } from '../racks/PlanProps'
import { fetchCost, money, RENT_TIER, useCost, type Cost, type CostOverrides } from './useCost'

/** The operation the cost is judged for: the buyer's own (when the analyst knows it), else 3PL pallet storage. */
export function useCostOperation() {
  const b = useStore((s) => s.buyerProfile) as { business?: string } | null
  return typeof b?.business === 'string' ? b.business : undefined
}

export default function CostPanel() {
  const id = useStore((s) => s.costOpen)
  const selectedId = useStore((s) => s.selectedId)
  const op = useCostOperation()
  const base = useCost(id, op)
  const [ov, setOv] = useState<CostOverrides>({})
  const [what, setWhat] = useState<Cost | null>(null)
  useEffect(() => { setOv({}); setWhat(null) }, [id])
  useEffect(() => {                                     // close when the card closes or moves on
    if (id != null && selectedId !== id) useStore.setState({ costOpen: null })
  }, [id, selectedId])
  useEffect(() => {
    if (id == null || !Object.keys(ov).length) { setWhat(null); return }
    const t = setTimeout(() => { fetchCost(id, op, ov).then(setWhat) }, 400)
    return () => clearTimeout(t)
  }, [id, op, ov])
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape' && id != null) useStore.setState({ costOpen: null }) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [id])
  if (id == null || base === null) return null
  const c = what ?? base
  const close = () => useStore.setState({ costOpen: null })
  const edited = Object.keys(ov).length > 0
  const num = (k: keyof CostOverrides, label: string, ph: string, unit: string) => (
    <label className="flex items-center justify-between gap-2 text-[11.5px] text-ink-3">{label}
      <span className="flex items-center gap-1">
        <input type="number" inputMode="decimal" placeholder={ph} value={ov[k] ?? ''} aria-label={label}
          onChange={(e) => setOv((o) => { const v = e.target.value; const n = { ...o }; if (v === '') delete n[k]; else n[k] = Number(v); return n })}
          className="h-7 w-[84px] rounded-md bg-panel-2 px-1.5 text-right text-[12px] tabular-nums text-ink ring-1 ring-inset ring-line placeholder:text-ink-3" />
        <span className="w-[42px] text-[11px]">{unit}</span>
      </span>
    </label>
  )
  return (
    <aside className="glass drawer-in absolute right-[436px] top-4 z-30 flex max-h-[calc(100%-250px)] w-[340px] flex-col overflow-hidden rounded-2xl" aria-label="True cost per pallet" data-testid="cost-panel">
      <header className="flex items-start gap-2 border-b border-line px-4 py-2.5">
        <Wallet size={15} className="mt-0.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold">True cost per pallet</h2>
          <div className="truncate text-[11px] text-ink-3">{c ? `${c.operation_name ?? c.operation} · ${c.address}` : 'Computing…'}</div>
        </div>
        <button onClick={close} aria-label="Close (Esc)" title="Close (Esc)" className="rounded p-1 text-ink-3 hover:bg-white/10 hover:text-ink"><X size={14} /></button>
      </header>
      {!c ? <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-8 rounded-lg" />)}</div> : (
        <div className="scroll-thin space-y-3 overflow-y-auto px-4 py-3">
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-[26px] font-semibold tabular-nums text-ink">{money(c.per_position_month.value ?? c.per_position_month.ops_only, 2)}</span>
              <span className="text-[12px] text-ink-3">per pallet position / month{c.per_position_month.value == null && c.per_position_month.ops_only != null ? ' (before rent)' : ''}</span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-3">
              {c.per_pallet_moved.value != null && <span>{money(c.per_pallet_moved.value, 2)} per pallet moved</span>}
              {c.total_year.value != null && <span>· {money(c.total_year.value)} / yr</span>}
              {c.per_position_month.rough && <span className="rounded bg-warn/12 px-1.5 text-[10.5px] font-semibold uppercase text-warn ring-1 ring-inset ring-warn/30" title="The scan covers little of the building, so the pallet count is scaled up a lot">rough</span>}
            </div>
            {c.plan_note && <div className="mt-1 text-[11.5px] leading-snug text-ink-3">{c.plan_note}</div>}
          </div>

          <ul className="divide-y divide-line/60 rounded-xl bg-white/[0.02] px-3 ring-1 ring-inset ring-line" data-testid="cost-lines">
            {c.lines.map((l) => (
              <li key={l.label} className="py-2" title={l.basis}>
                <div className="flex items-center gap-2 text-[12.5px]">
                  <span className="min-w-0 flex-1 text-ink-2">{l.label}</span>
                  <span className="font-semibold tabular-nums text-ink">{l.value == null ? '?' : `${money(l.value)}/yr`}</span>
                  <SrcTag s={l.source} />
                </div>
                <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-3">{l.basis}</div>
              </li>
            ))}
          </ul>

          <div className="rounded-xl bg-white/[0.02] px-3 py-2 text-[11.5px] ring-1 ring-inset ring-line">
            <div className="flex items-center gap-2">
              <span className="text-ink-3">Rent</span>
              <span className="font-semibold tabular-nums text-ink">{c.rent.value != null ? `${money(c.rent.value, 2)}/SF/yr` : 'unknown'}</span>
              {c.rent.low != null && c.rent.high != null && c.rent.low !== c.rent.high && <span className="tabular-nums text-ink-3">({money(c.rent.low, 2)}–{money(c.rent.high, 2)})</span>}
              <span className="ml-auto"><SrcTag s={c.rent.source} /></span>
            </div>
            <div className={clsx('mt-0.5 leading-snug', c.rent.tier === 'market' ? 'text-warn/90' : 'text-ink-3')}>{RENT_TIER[c.rent.tier]}: {c.rent.basis}</div>
            {c.rent.service_mix && Object.keys(c.rent.service_mix).length > 0 && (
              <div className="mt-0.5 text-ink-3">Lease types: {Object.entries(c.rent.service_mix).map(([k, v]) => `${k} ×${v}`).join(', ')}</div>
            )}
            <div className="mt-1 text-ink-3">Space: {c.space.sqft != null ? `${c.space.sqft.toLocaleString()} SF` : '?'} · {c.space.basis}</div>
          </div>

          <details className="rounded-xl bg-white/[0.02] px-3 py-2 ring-1 ring-inset ring-line" open={edited}>
            <summary className="flex cursor-pointer list-none items-center gap-2 text-[12px] font-medium text-ink">
              What if…<span className="text-[11px] font-normal text-ink-3">use your own numbers</span>
              {edited && <button onClick={(e) => { e.preventDefault(); setOv({}) }} className="ml-auto flex items-center gap-1 text-[11px] text-ink-3 hover:text-ink"><RotateCcw size={11} /> reset</button>}
            </summary>
            <div className="mt-2 space-y-1.5">
              {num('rent_psf', 'Rent', base?.rent.value != null ? String(base.rent.value) : 'e.g. 9.5', '$/SF/yr')}
              {num('sqft', 'Space', base?.space.sqft != null ? String(base.space.sqft) : 'SF', 'SF')}
              {num('wage_hour', 'Operator wage (loaded)', '25', '$/h')}
            </div>
          </details>
          <p className="text-[11px] leading-snug text-ink-3">Racking, forklift lease and wage are typical US values (assumed), not quotes. Pallet positions come from the interior planner on the scanned floor.</p>
        </div>
      )}
    </aside>
  )
}

/** The slim line under the property card's tiles. Renders nothing while loading or when unavailable. */
export function CostLineHud({ id }: { id: number }) {
  const op = useCostOperation()
  const c = useCost(id, op)
  const open = useStore((s) => s.costOpen === id)
  if (!c) return null
  const v = c.per_position_month.value ?? c.per_position_month.ops_only
  return (
    <button onClick={() => useStore.setState({ costOpen: open ? null : id, floodOpen: null })} data-testid="hud-cost"
      className={clsx('flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] ring-1 ring-inset transition',
        open ? 'bg-accent/10 ring-accent/40' : 'bg-white/[0.03] ring-line hover:bg-white/[0.06]')}>
      <Wallet size={13} className="shrink-0 text-accent" />
      <span className="text-ink-3">True cost</span>
      <span className="font-semibold tabular-nums text-ink">{v != null ? `${money(v, 2)}/pallet/mo` : 'unknown'}</span>
      <span className="min-w-0 flex-1 truncate text-[11px] text-ink-3">
        {c.per_position_month.value == null && v != null ? '· before rent' : `· ${RENT_TIER[c.rent.tier]}`}{c.per_position_month.rough ? ' · rough' : ''}
      </span>
      <span className="text-ink-3">›</span>
    </button>
  )
}
