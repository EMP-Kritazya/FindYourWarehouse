// Storage line for the warehouse card and the Compare panel (GET /api/racks/{id}/summary: reach-truck plan).
import { useEffect, useState } from 'react'
import { Layers3 } from 'lucide-react'
import { useStore } from '../lib/store'
import { SectionTitle } from '../components/ui'
import { interiorApi, type StorageSummary } from './racksApi'

type Interior = StorageSummary & { reachable_pct?: number; minutes_per_pallet?: number; machines_needed?: number; machine?: string;
  scan_coverage_pct?: number | null; scanned_sqft?: number; building_estimate_unreliable?: number | null; pallets_per_hour?: number | null }

const cache = new Map<number, Interior>()

export function useStorage(id: number) {
  const [s, setS] = useState<Interior | null>(cache.get(id) ?? null)
  useEffect(() => {
    if (cache.has(id)) { setS(cache.get(id)!); return }
    let live = true
    interiorApi.summary(id).then((r) => { cache.set(id, r); if (live) setS(r) })
      .catch(() => { const r = { status: 'no_scan' as const, why: 'no measured floor' }; cache.set(id, r); if (live) setS(r) })
    return () => { live = false }
  }, [id])
  return s
}

const n = (v: number | null | undefined) => (v == null ? '?' : Math.round(v).toLocaleString())

export function StorageRow({ id }: { id: number }) {
  const s = useStorage(id)
  const openTest = useStore((st) => st.openTest)
  if (!s || s.status !== 'computed') return null
  return (
    <div>
      <SectionTitle right={<span className="text-[11px] text-ink-3">3PL layout on the scanned floor</span>}>Interior</SectionTitle>
      <button onClick={() => openTest(id, 'storage')} data-testid="storage-row"
        className="flex w-full items-center gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5 text-left ring-1 ring-inset ring-line transition hover:bg-white/[0.06]">
        <Layers3 size={16} className="shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] tabular-nums text-ink">
            <b className="font-semibold">{n(s.positions)}</b> pallet positions
            {s.building_estimate ? <span className="text-ink-3"> · ~{n(s.building_estimate)} whole building (est.)</span>
              : s.scan_coverage_pct != null ? <span className="text-warn"> · scan covers {Math.round(s.scan_coverage_pct)}% of the building</span> : null}
          </div>
          <div className="truncate text-[11px] text-ink-3">
            {s.reachable_pct != null ? `${Math.round(s.reachable_pct)}% reachable (${(s.machine ?? '').toLowerCase()})` : `${s.levels} levels`}
            {s.minutes_per_pallet ? ` · ${s.minutes_per_pallet} min/pallet` : ''}
            {s.machines_needed ? ` · ${s.machines_needed} forklift${s.machines_needed > 1 ? 's' : ''} needed` : ''}
          </div>
        </div>
        <span className="shrink-0 rounded-lg bg-accent/15 px-2.5 py-1 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30">Plan inside</span>
      </button>
    </div>
  )
}

export function StorageCell({ id }: { id: number }) {
  const s = useStorage(id)
  if (!s) return <span className="text-[11.5px] text-ink-3">…</span>
  if (s.status !== 'computed') return <span className="text-[11.5px] text-ink-3" title={s.why}>no scan</span>
  return (
    <div className="min-w-0 text-[11.5px] leading-snug tabular-nums text-ink-2">
      <div className="text-[14px] font-semibold text-ink">{n(s.positions)}</div>
      <div>scanned · {s.levels} lvls</div>
      {s.building_estimate ? <div className="text-ink-3">~{n(s.building_estimate)} bldg (est.)</div>
        : s.scan_coverage_pct != null ? <div className="text-warn">scan = {Math.round(s.scan_coverage_pct)}% of bldg</div> : null}
      <div className="text-ink-3">{s.reachable_pct != null ? `${Math.round(s.reachable_pct)}% reachable` : ''}{s.minutes_per_pallet ? ` · ${s.minutes_per_pallet} min/pal` : ''}</div>
      {s.machines_needed ? <div className="text-ink-3">{s.machines_needed} forklift{s.machines_needed > 1 ? 's' : ''} (3PL volume)</div> : null}
    </div>
  )
}


/** Under the planner button: the default (3PL) result, with how much of the building the scan covers. */
export function InteriorBrief({ id }: { id: number }) {
  const s = useStorage(id)
  if (!s) return <div className="skeleton h-24 rounded-xl" />
  if (s.status !== 'computed') return <div className="rounded-xl bg-white/[0.03] p-3 text-[12px] text-ink-3 ring-1 ring-inset ring-line">{s.why ?? 'No measured floor.'}</div>
  const cov = s.scan_coverage_pct ?? null
  const low = cov != null && cov < 30
  return (
    <div className="space-y-2" data-testid="interior-brief">
      <div className="text-[11px] uppercase tracking-[0.08em] text-ink-3">Default layout: 3PL pallet storage (change it in the planner)</div>
      <div className="grid grid-cols-2 gap-2">
        <B label="Pallet positions" v={n(s.positions)} sub={`on the scanned ${n(s.scanned_sqft)} SF · ${s.levels} levels`} />
        <B label="Forklift reach" v={s.reachable_pct != null ? `${Math.round(s.reachable_pct)}%` : '?'} sub={s.machine ?? ''} />
        <B label="Minutes per pallet" v={s.minutes_per_pallet != null ? String(s.minutes_per_pallet) : '?'} sub="dock to rack and back" />
        <B label="Forklifts needed" v={s.machines_needed != null ? String(s.machines_needed) : '—'} sub="for the profile's daily volume" />
      </div>
      <div className={low ? 'rounded-xl bg-warn/10 p-2.5 text-[12px] leading-snug text-warn ring-1 ring-inset ring-warn/25' : 'rounded-xl bg-white/[0.03] p-2.5 text-[12px] leading-snug text-ink-2 ring-1 ring-inset ring-line'}>
        {cov == null ? 'Building size not in CoStar: no whole-building estimate.'
          : low ? `The scan covers only ${Math.round(cov)}% of the building, so these numbers describe that part only. A whole-building figure (~${n(s.building_estimate_unreliable)}) would be a guess and is not shown as a result.`
          : `The scan covers ${Math.round(cov)}% of the building. Whole building, estimated: ~${n(s.building_estimate)} positions (assumes the unscanned part is similar).`}
      </div>
    </div>
  )
}

function B({ label, v, sub }: { label: string; v: string; sub?: string }) {
  return (
    <div className="rounded-xl bg-white/[0.03] px-3 py-2 ring-1 ring-inset ring-line">
      <div className="text-[11px] uppercase tracking-[0.06em] text-ink-3">{label}</div>
      <div className="text-[18px] font-semibold tabular-nums text-ink">{v}</div>
      {sub && <div className="truncate text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}
