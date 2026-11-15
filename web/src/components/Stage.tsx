import { Suspense, lazy, useState } from 'react'
import { X, Box } from 'lucide-react'
import ProvenanceNotice from './Provenance'
import { useStore } from '../lib/store'
import { api, type SimRun } from '../lib/api'
import { fmtSF, isNum } from '../lib/format'
import { SectionTitle, Skeleton, useAsync } from './ui'
import { loadDetail, loadTwin } from './WarehouseCard'
import { Assumptions, LayoutFacts } from './SimPanel'
import { FitForm, fitSummary } from './TestPanel'
import ErrorBoundary from './ErrorBoundary'
import { sendEvent } from '../lib/guide'

// Stream A owns MatterportStage; lazy so a broken/in-progress stage never takes the map down.
const MatterportStage = lazy(() => import('../matterport/MatterportStage'))

const KPI_LABELS: [string, string, (v: number) => string][] = [
  ['trucks_done', 'Trucks turned', (v) => v.toFixed(0)],
  ['last_truck_out_h', 'Last truck out', (v) => `${v.toFixed(1)} h`],
  ['mean_dwell_min', 'Truck dwell', (v) => `${v.toFixed(0)} min`],
  ['door_utilization', 'Door utilization', (v) => `${(v * 100).toFixed(0)}%`],
  ['congestion_delay_share', 'Congestion delay', (v) => `${(v * 100).toFixed(0)}%`],
  ['unload_53ft_min', "53' unload", (v) => `${v.toFixed(0)} min`],
]

// same colour rules as the stage legend (web/src/matterport/sceneRenderer.ts)
const FLEET_COLORS: [RegExp, string, string][] = [
  [/reach|order_picker|vna|turret|narrow/i, '#2f7cf6', 'Reach / narrow-aisle'],
  [/pallet_jack|walkie|tugger|jack/i, '#22b35e', 'Pallet jack'],
  [/amr|robot/i, '#a25cf2', 'Robot'],
  [/fork|counterbalance|lift/i, '#ff8a1f', 'Forklift'],
]
const fleetColor = (kind: string) => FLEET_COLORS.find(([re]) => re.test(kind))?.[1] ?? '#9aa3ad'
const prettyKind = (k: string) => k.replace(/_/g, ' ').replace(/\b\w/, (m) => m.toUpperCase())

export default function Stage() {
  const stage = useStore((s) => s.stage)
  const sdkKey = useStore((s) => s.sdkKey)
  const openStage = useStore((s) => s.openStage)
  const [ready, setReady] = useState(false)
  const [doors, setDoors] = useState<number | null>(null)
  const [picked, setPicked] = useState<{ x: number; z: number } | null>(null)
  const run = useAsync(stage?.runId ? () => api.simGet(stage.runId!) : null, [stage?.runId])
  const pid = stage?.propertyId ?? run.data?.property_id
  const twin = useAsync(pid ? () => loadTwin(pid) : null, [pid])
  const detail = useAsync(pid ? () => loadDetail(pid) : null, [pid])
  if (!stage) return null
  const kpi = stage.kpi ?? run.data?.summary.kpi
  return (
    <div className="fade-up absolute inset-0 z-40 flex gap-4 bg-bg/80 p-4 backdrop-blur-sm">
      <div className="relative flex-1 overflow-hidden rounded-2xl bg-black ring-1 ring-line">
        <Suspense fallback={<div className="skeleton absolute inset-0 rounded-none" />}>
          {!sdkKey ? <div className="skeleton absolute inset-0 rounded-none" /> : <MatterportStage key={stage.modelId + (stage.sceneUrl ?? '') + (stage.mode ?? '')} modelId={stage.modelId} sdkKey={sdkKey}
            sceneUrl={stage.sceneUrl} onReady={() => setReady(true)} title={stage.title}
            {...({ mode: stage.mode ?? 'view', fitResult: stage.fitResult, propertyId: stage.propertyId, onPickTarget: setPicked,
              onDoorsChanged: (d: unknown) => setDoors(Array.isArray(d) ? d.length : Array.isArray((d as { doors?: unknown[] })?.doors) ? (d as { doors: unknown[] }).doors.length : null) } as Record<string, unknown>)} />}
        </Suspense>
      </div>
      <aside className="glass flex w-[412px] shrink-0 flex-col overflow-hidden rounded-2xl">
        <header className="flex items-start justify-between gap-2 border-b border-line p-4">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-violet-300"><Box size={12} />{stage.subtitle ?? 'Interior'}</div>
            <h2 className="mt-1 truncate text-[16px] font-semibold">{stage.title}</h2>
            <div className="mt-1 text-[11px] text-ink-3">Matterport scan {stage.modelId}{ready ? '' : ' · loading'}</div>
          </div>
          <button onClick={() => { setReady(false); openStage(null) }} title="Back to map"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.06] text-ink hover:bg-white/10"><X size={16} /></button>
        </header>

                <div className="scroll-thin flex-1 space-y-5 overflow-y-auto p-4">
          {kpi && (
            <div>
              <SectionTitle>Shift result · P50</SectionTitle>
              <div className="grid grid-cols-2 gap-1.5">
                {KPI_LABELS.map(([k, l, f]) => {
                  const b = kpi[k]
                  return (
                    <div key={k} className="rounded-lg bg-white/[0.03] px-3 py-2 ring-1 ring-inset ring-line">
                      <div className="text-[11px] text-ink-3">{l}</div>
                      <div className="text-[15px] font-semibold tabular-nums">{b ? f(b.p50) : '?'}</div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {stage.runId && run.loading && <div className="space-y-2"><Skeleton className="h-14" /><Skeleton className="h-10" /><Skeleton className="h-24" /></div>}
          {run.data && <RunDetails run={run.data} scannedSqft={twin.data?.interior?.floor?.indoor_sqft ?? null} rba={detail.data?.rba_sqft ?? null} />}

          {!stage.runId && stage.mode === 'markDoors' && (
            <div className="space-y-2 text-[12px] leading-relaxed text-ink-2">
              <SectionTitle>Mark the real doors</SectionTitle>
              <p>Walk to the dock wall. For each real door, click its left edge, then its right edge. Pick the door type, then save.</p>
              <p className="text-ink-3">Only doors marked here (or detected) are used by the Yard and Equipment tests. No door is assumed.</p>
              {doors != null && <div className="rounded-lg bg-accent/10 px-3 py-2 text-accent ring-1 ring-inset ring-accent/25">{doors} door{doors === 1 ? '' : 's'} marked</div>}
            </div>
          )}
          {!stage.runId && stage.mode === 'fit' && stage.propertyId != null && FitForm && (
            <div className="-mx-1 rounded-xl ring-1 ring-inset ring-line">
              <ErrorBoundary fallback={<div className="p-3 text-[12px] text-bad">The equipment form hit an error.</div>}>
                <Suspense fallback={<Skeleton className="m-3 h-24" />}>
                  <FitForm propertyId={stage.propertyId} modelId={stage.modelId} pickedTarget={picked}
                    onResult={(r: unknown) => {
                      const id = stage.propertyId!
                      useStore.setState((st) => ({ lastTest: { ...st.lastTest, [`fit:${id}`]: r }, stage: st.stage ? { ...st.stage, fitResult: r } : st.stage }))
                      sendEvent('testResult', fitSummary(id, r))
                    }} />
                </Suspense>
              </ErrorBoundary>
            </div>
          )}
          {!stage.runId && stage.mode === 'fit' && <FitSide r={stage.fitResult as Record<string, any> | undefined} />}
          {!stage.runId && (!stage.mode || stage.mode === 'view') && (
            <p className="text-[12px] leading-relaxed text-ink-2">
              Walk the real interior captured by Matterport. Drag to look around, click the floor to move.
            </p>
          )}
        </div>
        {/* reserved dock for the voice orb (positioned by App over this footer) */}
        <footer className="h-[92px] shrink-0 border-t border-line" />
      </aside>
    </div>
  )
}

function RunDetails({ run, scannedSqft, rba }: { run: SimRun; scannedSqft: number | null; rba: number | null }) {
  const f = run.fusion
  const fleet = new Map<string, number>()
  for (const v of (run.summary.fleet as { kind: string }[] | undefined) ?? []) fleet.set(v.kind, (fleet.get(v.kind) ?? 0) + 1)
  const share = isNum(scannedSqft) && isNum(rba) && rba > 0 ? Math.min(1, scannedSqft / rba) : null
  return (
    <>
      <div>
        <SectionTitle>Data provenance</SectionTitle>
        <ProvenanceNotice fusion={f} />
      </div>

      <div>
        <SectionTitle>Simulated area</SectionTitle>
        <div className="text-[12px] text-ink-2">
          Simulated in the scanned area: <span className="font-semibold text-ink tabular-nums">{isNum(scannedSqft) ? fmtSF(scannedSqft) : '?'}</span>
          {' '}of <span className="font-semibold text-ink tabular-nums">{isNum(rba) ? fmtSF(rba) : '?'}</span> RBA
        </div>
        {share != null && (
          <div className="mt-2 h-1.5 rounded-full bg-white/[0.07]" title={`${Math.round(share * 100)}% of the building`}>
            <div className="h-full rounded-full bg-violet-400/80" style={{ width: `${share * 100}%` }} />
          </div>
        )}
      </div>

      {fleet.size > 0 && (
        <div>
          <SectionTitle>Fleet</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {[...fleet].map(([kind, n]) => (
              <span key={kind} className="inline-flex h-7 items-center gap-2 rounded-full bg-white/[0.04] px-2.5 text-[12px] ring-1 ring-inset ring-line">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: fleetColor(kind) }} />
                <span className="text-ink-2">{prettyKind(kind)}</span>
                <span className="font-semibold tabular-nums">{n}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <LayoutFacts layout={run.summary.layout} />
      <Assumptions items={run.summary.assumptions} />
    </>
  )
}

function FitSide({ r }: { r?: Record<string, any> }) {
  if (!r) return <p className="text-[12px] leading-relaxed text-ink-2">Equipment test mode. Pick the equipment and a route in the scan to check corridors, turns, doors and clear height.</p>
  const checks = (r.checks ?? []) as Record<string, any>[]
  return (
    <div>
      <SectionTitle>Equipment test · {String(r.equipment?.name ?? r.equipment?.key ?? '')}</SectionTitle>
      <div className="mb-2 text-[12px] text-ink-2">Floor reachable: <span className="font-semibold text-ink tabular-nums">{isNum(r.reachable_pct) ? `${Math.round(r.reachable_pct <= 1 ? r.reachable_pct * 100 : r.reachable_pct)}%` : '?'}</span></div>
      <div className="divide-y divide-line overflow-hidden rounded-lg ring-1 ring-inset ring-line">
        {checks.map((c, i) => (
          <div key={i} className="flex items-center gap-2 bg-white/[0.02] px-2.5 py-1.5 text-[12px]">
            <span className={c.ok ? 'text-good' : 'text-bad'}>{c.ok ? '✓' : '✗'}</span>
            <span className="capitalize text-ink">{String(c.kind).replace(/_/g, ' ')}</span>
            <span className="ml-auto tabular-nums text-ink-2">need {String(c.need)} · have {String(c.have)} {c.unit ?? ''}</span>
          </div>
        ))}
      </div>
      {(r.assumptions ?? []).length > 0 && <div className="mt-2 text-[11px] leading-snug text-ink-3">{(r.assumptions as string[]).slice(0, 4).join(' · ')}</div>}
    </div>
  )
}
