import { useEffect, useRef, useState, type ComponentType } from 'react'
import clsx from 'clsx'
import { Package, Layers, Package2, Snowflake, Truck, Cog, Construction, Wrench, Warehouse, Play, ChevronDown, Box, RefreshCw, Clock } from 'lucide-react'
import { api, ApiError, type Band, type Profile, type Scan, type SimRun } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtInt, fmtKSF, isNum } from '../lib/format'
import { SectionTitle, Skeleton } from './ui'
import sample from '../fixtures/simSample.json'
import ProvenanceNotice from './Provenance'

const ICONS: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  package: Package, pallet: Layers, crate: Package2, snowflake: Snowflake, van: Truck, gear: Cog, beam: Construction, wrench: Wrench,
}

let profilesP: Promise<Record<string, Profile>> | null = null
export const loadProfiles = () => (profilesP ??= api.library().then((l) => l.profiles).catch((e) => { profilesP = null; throw e }))

interface KpiDef { key: string; label: string; unit: string; fmt: (v: number) => string; of?: string; share?: boolean; lowerBetter?: boolean; hint: string }
const KPIS: KpiDef[] = [
  { key: 'trucks_done', label: 'Trucks turned', unit: '', fmt: (v) => fmtInt(v), of: 'trucks_planned', hint: 'Trucks fully loaded or unloaded within the shift' },
  { key: 'last_truck_out_h', label: 'Last truck out', unit: 'h', fmt: (v) => v.toFixed(1), lowerBetter: true, hint: 'Hours from shift start until the last truck departs' },
  { key: 'mean_dwell_min', label: 'Truck dwell', unit: 'min', fmt: (v) => v.toFixed(0), lowerBetter: true, hint: 'Average time a truck spends on site' },
  { key: 'unload_53ft_min', label: "53' unload", unit: 'min', fmt: (v) => v.toFixed(0), lowerBetter: true, hint: 'Median time to unload a 53 ft trailer' },
  { key: 'door_utilization', label: 'Door utilization', unit: '%', fmt: (v) => (v * 100).toFixed(0), share: true, hint: 'Share of the shift dock doors are occupied' },
  { key: 'congestion_delay_share', label: 'Congestion delay', unit: '%', fmt: (v) => (v * 100).toFixed(0), share: true, lowerBetter: true, hint: 'Share of vehicle travel time lost waiting for other vehicles' },
  { key: 'pallets_shipped', label: 'Pallets shipped', unit: '', fmt: (v) => fmtInt(v), of: 'pallets_shipped_plan', hint: 'Outbound pallets loaded onto trucks' },
  { key: 'mean_wait_for_door_min', label: 'Wait for a door', unit: 'min', fmt: (v) => v.toFixed(0), lowerBetter: true, hint: 'Average time a truck waits in the yard for a free door' },
]

type RunState = { phase: 'idle' } | { phase: 'running'; started: number } | { phase: 'done'; run: SimRun; sample: boolean } |
  { phase: 'error'; message: string; missing: boolean }

export default function SimPanel({ id, scan, address, sqft }: { id: number; scan: Scan | null; address: string; sqft: number | null }) {
  const [profiles, setProfiles] = useState<Record<string, Profile> | null>(null)
  const [profErr, setProfErr] = useState(false)
  const [profile, setProfile] = useState<string>('pallet_3pl')
  const [scale, setScale] = useState(1)
  const [st, setSt] = useState<RunState>({ phase: 'idle' })
  const openStage = useStore((s) => s.openStage)
  const simRequest = useStore((s) => s.simRequest)
  const seenNonce = useRef(0)
  const runSeq = useRef(0)
  const resultsRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (st.phase === 'done' || st.phase === 'running') resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [st.phase])

  useEffect(() => { loadProfiles().then(setProfiles, () => setProfErr(true)) }, [])

  const run = async (p = profile, v = scale, runId?: string) => {
    const seq = ++runSeq.current
    setSt({ phase: 'running', started: Date.now() })
    try {
      // the voice agent already ran it: load that result instead of re-running
      const r = runId ? await api.simGet(runId) : await api.simRun({ property_id: id, profile: p, volume_scale: v })
      if (seq === runSeq.current) setSt({ phase: 'done', run: r, sample: false })
    } catch (e) {
      if (seq !== runSeq.current) return
      const status = e instanceof ApiError ? e.status : -1
      const missing = !runId && (status === 404 || status === 0 || status === 502)
      setSt({ phase: 'error', missing, message: missing ? 'The simulation service is not reachable.'
        : status === 409 ? 'This building has no measured warehouse-floor scan to simulate in yet.'
        : 'The simulation could not run for this building.' })
    }
  }

  // voice agent: simulate {id, profile, volume_scale}
  useEffect(() => {
    if (!simRequest || simRequest.id !== id || simRequest.nonce === seenNonce.current) return
    seenNonce.current = simRequest.nonce
    setProfile(simRequest.profile)
    if (simRequest.volume_scale) setScale(simRequest.volume_scale)
    run(simRequest.profile, simRequest.volume_scale ?? scale, simRequest.run_id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simRequest, id])

  // ?sample=1 previews the labelled sample output (development / recording without the sim service)
  useEffect(() => {
    if (new URLSearchParams(location.search).has('sample')) setSt({ phase: 'done', run: sample as unknown as SimRun, sample: true })
  }, [id])

  const prof = profiles?.[profile]
  const done = st.phase === 'done' ? st : null

  return (
    <div className="space-y-6 p-4">
      <div>
        <SectionTitle right={<span className="text-[11px] text-ink-3">{fmtKSF(sqft)} building</span>}>1 · Your operation</SectionTitle>
        <div className="grid grid-cols-2 gap-2">
          {!profiles && !profErr && Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-[58px]" />)}
          {profErr && <div className="col-span-2 text-[12px] text-ink-3">Business profiles could not be loaded.</div>}
          {profiles && Object.entries(profiles).map(([k, p]) => {
            const Icon = ICONS[p.icon] ?? Warehouse
            const on = k === profile
            return (
              <button key={k} onClick={() => setProfile(k)} title={p.description} aria-label={p.description}
                className={clsx('flex h-[58px] items-center gap-2.5 rounded-xl px-3 text-left ring-1 ring-inset transition',
                  on ? 'bg-accent/12 ring-accent/60' : 'bg-white/[0.03] ring-line hover:ring-line-2')}>
                <span className={clsx('flex h-8 w-8 shrink-0 items-center justify-center rounded-lg', on ? 'bg-accent text-bg' : 'bg-white/5 text-ink-2')}>
                  <Icon size={16} />
                </span>
                <span className={clsx('text-[12px] font-medium leading-tight', on ? 'text-ink' : 'text-ink-2')}>{p.name}</span>
              </button>
            )
          })}
        </div>
        {prof && <p className="mt-2 text-[12px] leading-relaxed text-ink-3">{prof.description} {prof.shift_hours} h shift, volumes scaled to this building.</p>}
      </div>

      <div>
        <SectionTitle>2 · Volume</SectionTitle>
        <div className="grid grid-cols-4 gap-1 rounded-xl bg-white/[0.03] p-1 ring-1 ring-inset ring-line">
          {[0.75, 1, 1.25, 1.5].map((v) => (
            <button key={v} onClick={() => setScale(v)}
              className={clsx('h-8 rounded-lg text-[12px] font-medium tabular-nums transition', v === scale ? 'bg-panel-3 text-ink ring-1 ring-line-2' : 'text-ink-3 hover:text-ink-2')}>
              {v === 1 ? 'Typical' : `${v}×`}
            </button>
          ))}
        </div>
      </div>

      <button onClick={() => run()} disabled={st.phase === 'running' || !profiles}
        className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-accent text-[14px] font-semibold text-bg transition hover:brightness-110 disabled:opacity-60">
        {st.phase === 'running' ? <><RefreshCw size={15} className="animate-spin" /> Simulating 10 shifts…</> :
          <><Play size={15} fill="currentColor" /> {done ? 'Run again' : 'Run simulation'}</>}
      </button>

      <div ref={resultsRef} className="scroll-mt-4">
        <SectionTitle right={done && <span className="text-[11px] text-ink-3">P50 · P10–P90 over {done.run.summary.runs} shifts</span>}>
          3 · Results
        </SectionTitle>
        {done?.sample && (
          <div className="mb-2 rounded-lg bg-warn/10 px-3 py-2 text-[11px] text-warn ring-1 ring-inset ring-warn/25">
            Sample output from a synthetic 60 × 90 m building, not this property.
          </div>
        )}
        {st.phase === 'error' && (
          <div className="rounded-xl bg-white/[0.03] p-4 ring-1 ring-inset ring-line">
            <div className="text-[13px] text-ink">{st.message}</div>
            <div className="mt-3 flex gap-2">
              <button onClick={() => run()} className="h-8 rounded-lg bg-white/[0.06] px-3 text-[12px] text-ink ring-1 ring-inset ring-line-2 hover:bg-white/10">Retry</button>
              {st.missing && <button onClick={() => setSt({ phase: 'done', run: sample as unknown as SimRun, sample: true })}
                className="h-8 rounded-lg px-3 text-[12px] text-ink-2 hover:text-ink">Preview sample output</button>}
            </div>
          </div>
        )}
        {st.phase !== 'error' && (
          <div className="grid grid-cols-2 gap-2">
            {KPIS.map((k) => done
              ? <KpiTile key={k.key} def={k} band={done.run.summary.kpi[k.key] ?? null} of={k.of ? done.run.summary.kpi[k.of] ?? null : null} />
              : <div key={k.key} className={clsx('h-[96px] rounded-xl ring-1 ring-inset ring-line', st.phase === 'running' ? 'skeleton' : 'bg-white/[0.02]')}>
                  {st.phase === 'idle' && <div className="p-3 text-[11px] text-ink-3">{k.label}</div>}
                </div>)}
          </div>
        )}
        {st.phase === 'running' && <Elapsed started={st.started} />}
      </div>

      {done && (
        <>
          {done.run.fusion && <ProvenanceNotice fusion={done.run.fusion} compact />}
          <button disabled={!scan || done.sample}
            onClick={() => scan && openStage({ modelId: done.run.model_id ?? scan.model_id, sceneUrl: done.run.scene_url ?? `/api/sim/${done.run.run_id}/scene`, title: address,
              subtitle: `${profiles?.[done.run.summary.profile]?.name ?? 'Operation'} replay`, kpi: done.run.summary.kpi, runId: done.run.run_id, propertyId: id })}
            className="group flex w-full items-center gap-3 rounded-2xl bg-gradient-to-r from-violet-500/25 to-accent/25 p-4 text-left ring-1 ring-inset ring-violet-300/30 transition hover:ring-violet-200/60 disabled:cursor-not-allowed disabled:opacity-50">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-white text-bg"><Box size={20} /></span>
            <span className="flex-1">
              <span className="block text-[14px] font-semibold">Watch it inside the scan</span>
              <span className="block text-[12px] text-ink-2">
                {!scan ? 'No interior scan for this property' : done.sample ? 'Available once the simulation service runs' : 'Racks, lanes and vehicles in the real Matterport space'}
              </span>
            </span>
            <Play size={18} className="text-ink transition group-hover:translate-x-0.5" fill="currentColor" />
          </button>
          <LayoutFacts layout={done.run.summary.layout} />
          <Assumptions items={done.run.summary.assumptions} />
        </>
      )}
    </div>
  )
}

function Elapsed({ started }: { started: number }) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 500); return () => clearInterval(t) }, [])
  return (
    <div className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-3">
      <Clock size={11} /> Running replications in the scanned layout · {Math.floor((Date.now() - started) / 1000)} s
    </div>
  )
}

function KpiTile({ def, band, of }: { def: KpiDef; band: Band | null; of: Band | null }) {
  if (!band) {
    return (
      <div className="h-[96px] rounded-xl bg-white/[0.03] p-3 ring-1 ring-inset ring-line" title="Not produced by this run">
        <div className="text-[11px] text-ink-3">{def.label}</div>
        <div className="mt-1 text-[22px] font-semibold text-ink-3">?</div>
      </div>
    )
  }
  const hi = def.share ? 1 : Math.max(band.p90, of?.p50 ?? 0, band.p50 * 1.25, 1e-6) * 1.1
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / hi) * 100))}%`
  return (
    <div className="fade-up h-[96px] rounded-xl bg-white/[0.03] p-3 ring-1 ring-inset ring-line" title={def.hint}>
      <div className="text-[11px] text-ink-3">{def.label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[22px] font-semibold leading-none tabular-nums tracking-tight">{def.fmt(band.p50)}</span>
        <span className="text-[12px] text-ink-2">{def.unit}</span>
        {of && isNum(of.p50) && <span className="text-[11px] text-ink-3">/ {def.fmt(of.p50)}</span>}
      </div>
      <div className="relative mt-3 h-1 rounded-full bg-white/[0.07]">
        <div className="absolute inset-y-0 rounded-full bg-accent/40" style={{ left: pct(band.p10), width: `calc(${pct(band.p90)} - ${pct(band.p10)} + 2px)` }} />
        <div className="absolute -top-[3px] h-[10px] w-[2px] rounded bg-accent" style={{ left: pct(band.p50) }} />
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] tabular-nums text-ink-3">
        <span>P10 {def.fmt(band.p10)}</span><span>P90 {def.fmt(band.p90)}</span>
      </div>
    </div>
  )
}

export function LayoutFacts({ layout }: { layout: Record<string, unknown> }) {
  const items: [string, string | null][] = [
    ['Pallet positions', isNum(layout.positions) ? fmtInt(layout.positions) : null],
    ['Rack levels', isNum(layout.levels) ? String(layout.levels) : null],
    ['Aisle width', isNum(layout.aisle_ft) ? `${layout.aisle_ft} ft` : null],
    ['Staging lanes', isNum(layout.staging_lanes) ? String(layout.staging_lanes) : null],
  ]
  return (
    <div>
      <SectionTitle>Generated layout</SectionTitle>
      <div className="grid grid-cols-4 gap-2">
        {items.map(([l, v]) => (
          <div key={l} className="rounded-lg bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line">
            <div className="text-[11px] leading-tight text-ink-3">{l}</div>
            <div className="mt-0.5 text-[14px] font-semibold tabular-nums">{v ?? <span className="text-ink-3">?</span>}</div>
          </div>
        ))}
      </div>
      {typeof layout.putaway_vehicle === 'string' && (
        <div className="mt-1.5 text-[11px] text-ink-3">Put-away by {layout.putaway_vehicle.replace(/_/g, ' ')} · {String(layout.orientation ?? '')}</div>
      )}
    </div>
  )
}

export function Assumptions({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl ring-1 ring-inset ring-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-3.5 py-3 text-left">
        <span className="text-[12px] font-medium text-ink-2">Assumptions <span className="text-ink-3">({items.length})</span></span>
        <ChevronDown size={15} className={clsx('text-ink-3 transition', open && 'rotate-180')} />
      </button>
      {open && (
        <ul className="space-y-1.5 border-t border-line px-3.5 py-3">
          {items.map((a, i) => (
            <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-ink-2">
              <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-3" />{a.charAt(0).toUpperCase() + a.slice(1)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
