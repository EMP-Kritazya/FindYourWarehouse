import { Suspense, lazy, type ComponentType, type ReactNode } from 'react'
import clsx from 'clsx'
import { ArrowLeft, Box, CheckCircle2, Forklift, Map as MapIcon, Truck, XCircle } from 'lucide-react'
import { useStore, type TestKind } from '../lib/store'
import { sendEvent } from '../lib/guide'
import type { Scan } from '../lib/api'
import { isNum } from '../lib/format'
import ErrorBoundary from './ErrorBoundary'
import { marginClass, marginText } from '../yard/format'
import { isManeuver, runManeuver, useYardUi, type ManeuverRequest } from '../yard/maneuver'
import { SectionTitle } from './ui'

// S3 / S4 own these forms. Lazy + boundary so a form in progress never breaks the card.
// FitForm is resolved with import.meta.glob so the app still builds before S4 creates the file.
const ManeuverForm = lazy(() => import('../yard/ManeuverPanel'))
const ManeuverResultCard = lazy(() => import('../yard/ManeuverPanel').then((m) => ({ default: m.ManeuverResultCard })))
type FitFormProps = { propertyId: number; modelId?: string; onResult?: (r: any) => void; pickedTarget?: { x: number; z: number } | null; onPickTarget?: () => void }
const fitMods = import.meta.glob<{ default: ComponentType<FitFormProps> }>('../matterport/FitForm.tsx')
const fitLoader = Object.values(fitMods)[0]
/** S4's equipment form; rendered in the stage sidebar next to the scan (it needs scan clicks for the route). */
export const FitForm = fitLoader ? lazy(fitLoader) : null

type Obj = Record<string, any>

export function yardSummary(id: number, r: unknown) {
  if (isManeuver(r)) {
    return { kind: 'yard_maneuver', id, run_id: r.run_id, summary: r.summary,
      issues: r.issues.slice(0, 6).map((x) => ({ class: x.class, severity: x.severity, text: x.text })),
      doors_used: r.doors_used.length, door_sources: [...new Set(r.doors_used.map((d) => d.source))], assumptions: r.assumptions.slice(0, 4) }
  }
  const o = (r ?? {}) as Obj
  const fits = (o.fits ?? []) as Obj[]
  return { kind: 'yard', id, run_id: o.run_id, fits: fits.map((f) => ({ door: f.door_id, vehicle: f.vehicle, ok: f.ok, margin_m: f.margin_m })),
    queue: o.queue, assumptions: (o.assumptions ?? []).slice(0, 4) }
}
export function fitSummary(id: number, r: unknown) {
  const o = (r ?? {}) as Obj
  return { kind: 'equipment', id, run_id: o.run_id, equipment: o.equipment?.name ?? o.equipment?.key ?? o.equipment,
    failed: ((o.checks ?? []) as Obj[]).filter((c) => !c.ok).map((c) => ({ kind: c.kind, need: c.need, have: c.have, unit: c.unit })),
    passed: ((o.checks ?? []) as Obj[]).filter((c) => c.ok).length, reachable_pct: o.reachable_pct }
}

/** Record a test result (from the form or from the guide's yardTest / equipmentTest command) and tell the guide. */
export function recordYard(id: number, r: unknown) {
  const st = useStore.getState()
  st.setYard({ propertyId: id, result: r })
  useStore.setState({ lastTest: { ...st.lastTest, [`yard:${id}`]: r } })
  sendEvent('testResult', yardSummary(id, r))
}
/** Run the multi-truck maneuver test (form, guide command or Rerun) and record the result. */
export async function runManeuverTest(req: ManeuverRequest) {
  const ui = useYardUi.getState()
  useYardUi.setState({ req, running: true, error: null, selectedIssue: null, selectedTruck: null })
  const st = useStore.getState()
  if (!st.yard || st.yard.propertyId !== req.property_id) st.setYard({ propertyId: req.property_id, result: null })
  try {
    const r = await runManeuver(req)
    recordYard(req.property_id, r)
    useYardUi.setState({ running: false })
    return r
  } catch (e) {
    useYardUi.setState({ running: false, error: e instanceof Error ? e.message : String(e), req: ui.req ?? req })
    throw e
  }
}
export const rerunManeuver = () => { const r = useYardUi.getState().req; if (r) runManeuverTest(r).catch(() => {}) }

export function openEquipment(id: number, modelId: string, title: string, fitResult?: unknown) {
  useStore.getState().openStage({ modelId, title, subtitle: 'Equipment test', mode: 'fit', fitResult, propertyId: id })
}
export function recordFit(id: number, modelId: string, title: string, r: unknown) {
  const st = useStore.getState()
  useStore.setState({ lastTest: { ...st.lastTest, [`fit:${id}`]: r } })
  st.openStage({ modelId, title, subtitle: 'Equipment test', mode: 'fit', fitResult: r, propertyId: id })
  sendEvent('testResult', fitSummary(id, r))
}

export default function TestPanel({ id, scan, address }: { id: number; scan: Scan | null; address: string }) {
  const kind = useStore((s) => s.testKind)
  const openTest = useStore((s) => s.openTest)
  const yard = useStore((s) => (s.yard?.propertyId === id ? s.yard : null))
  const last = useStore((s) => s.lastTest)
  const setYard = useStore((s) => s.setYard)
  const openStage = useStore((s) => s.openStage)
  const running = useYardUi((s) => s.running && s.req?.property_id === id)
  const yardErr = useYardUi((s) => (s.req?.property_id === id ? s.error : null))
  const pick = (k: TestKind | null) => {
    openTest(id, k)
    if (k === 'equipment' && scan) openEquipment(id, scan.model_id, address, last[`fit:${id}`])
  }
  const yardRes = (yard?.result ?? last?.[`yard:${id}`]) as Obj | undefined
  const fitRes = last?.[`fit:${id}`] as Obj | undefined

  if (!kind) {
    return (
      <div className="space-y-3 p-4">
        <p className="text-[12px] leading-relaxed text-ink-2">Two focused tests on the real building. Pick the question you care about.</p>
        <Choice icon={<Truck size={20} />} tone="teal" title="Yard test" sub="Outside"
          q="Can several trucks get in, turn, back in and leave at the same time?"
          meta="Every move planned with truck kinematics on the Vexcel yard · waits and conflicts pinned on the map" onClick={() => pick('yard')} done={!!yardRes} />
        <Choice icon={<Forklift size={20} />} tone="violet" title="Equipment test" sub="Inside"
          q="Do my machines fit and move through this building?"
          meta={scan ? 'Corridors, turns, doors and clear height in the Matterport scan' : 'Needs a Matterport interior scan: none on file'}
          disabled={!scan} onClick={() => pick('equipment')} done={!!fitRes} />
      </div>
    )
  }

  return (
    <div className="p-4">
      <button onClick={() => pick(null)} className="mb-3 flex items-center gap-1.5 text-[12px] text-ink-3 hover:text-ink"><ArrowLeft size={13} /> Both tests</button>
      {kind === 'yard' ? (
        <>
          <div className="mb-3 flex items-center gap-2"><Truck size={16} className="text-accent" /><h3 className="text-[15px] font-semibold">Yard test</h3><span className="text-[11px] text-ink-3">outside</span></div>
          {yardRes && isManeuver(yardRes) && (
            <Suspense fallback={<div className="skeleton mb-4 h-32" />}>
              <div className="-mt-1 mb-4">
                <ManeuverResultCard r={yardRes} shown={!!yard?.result} onShow={() => setYard({ propertyId: id, result: yardRes })}
                  onDocks={() => useStore.setState({ docksOpen: id })} />
              </div>
            </Suspense>
          )}
          {yardRes && isManeuver(yardRes) && <SectionTitle>Change trucks and rerun</SectionTitle>}
          <FormBox>
            <ManeuverForm propertyId={id} busy={running} onRun={(req) => { runManeuverTest(req).catch(() => {}) }} />
          </FormBox>
          {yardErr && <div className="mt-2 rounded-lg bg-bad/10 px-2.5 py-1.5 text-[12px] text-bad ring-1 ring-inset ring-bad/25">Could not run the yard test: {yardErr}</div>}
          {yardRes && !isManeuver(yardRes) ? <YardResult r={yardRes} onShow={() => setYard({ propertyId: id, result: yardRes })} shown={!!yard} /> : null}
        </>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2"><Forklift size={16} className="text-violet-300" /><h3 className="text-[15px] font-semibold">Equipment test</h3><span className="text-[11px] text-ink-3">inside</span></div>
          {!scan ? <div className="rounded-xl bg-white/[0.03] p-3 text-[12px] text-ink-3 ring-1 ring-inset ring-line">No interior scan for this property, so the equipment test can't run.</div> : (
            <div className="rounded-xl bg-gradient-to-br from-violet-500/10 to-transparent p-3.5 ring-1 ring-inset ring-violet-400/25">
              <ol className="list-decimal space-y-1 pl-4 text-[12px] leading-relaxed text-ink-2">
                <li>Pick your machine (forklift, reach truck, or your own dimensions).</li>
                <li>Pick a route: from a dock door to a point you click in the scan, or "reach everywhere".</li>
                <li>Choose the floor as scanned (today's racks and stock) or emptied.</li>
              </ol>
              <div className="mt-2 text-[11px] text-ink-3">Checked on the scan's 25 cm grid: corridor width, turning space, door openings, clear height.</div>
              <button onClick={() => openEquipment(id, scan.model_id, address, fitRes)}
                className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-violet-500/25 text-[13px] font-semibold text-violet-50 ring-1 ring-inset ring-violet-300/40 transition hover:bg-violet-500/35">
                <Box size={15} /> {fitRes ? 'Open the test in the scan' : 'Start in the 3D scan'}
              </button>
            </div>
          )}
          {fitRes && scan && <FitResult r={fitRes} onShow={() => openEquipment(id, scan.model_id, address, fitRes)} />}
        </>
      )}
    </div>
  )
}

function FormBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl bg-white/[0.02] ring-1 ring-inset ring-line">
      <ErrorBoundary fallback={<div className="p-3 text-[12px] text-bad">This form hit an error. Try again in a moment.</div>}>
        <Suspense fallback={<div className="skeleton m-3 h-24" />}>{children}</Suspense>
      </ErrorBoundary>
    </div>
  )
}

function Choice({ icon, title, sub, q, meta, onClick, disabled, tone, done }: {
  icon: ReactNode; title: string; sub: string; q: string; meta: string; onClick: () => void; disabled?: boolean; tone: 'teal' | 'violet'; done?: boolean
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      className={clsx('group flex w-full gap-3 rounded-xl p-3.5 text-left ring-1 ring-inset transition disabled:cursor-not-allowed disabled:opacity-45',
        tone === 'teal' ? 'bg-gradient-to-br from-accent/12 to-transparent ring-accent/25 hover:ring-accent/50' : 'bg-gradient-to-br from-violet-500/12 to-transparent ring-violet-400/25 hover:ring-violet-300/50')}>
      <div className={clsx('flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-black/30', tone === 'teal' ? 'text-accent' : 'text-violet-200')}>{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2"><span className="text-[14px] font-semibold">{title}</span><span className="text-[10px] uppercase tracking-[0.08em] text-ink-3">{sub}</span>
          {done && <span className="ml-auto rounded bg-white/[0.07] px-1.5 text-[10px] text-ink-2">result ready</span>}</div>
        <div className="mt-0.5 text-[13px] leading-snug text-ink">{q}</div>
        <div className="mt-1 text-[11px] text-ink-3">{meta}</div>
      </div>
    </button>
  )
}

const pct = (v: unknown) => (isNum(v) ? `${Math.round(v <= 1 ? v * 100 : v)}%` : '?')
const min = (v: unknown) => (isNum(v) ? `${Math.round(v)} min` : '?')

function YardResult({ r, onShow, shown }: { r: Obj; onShow: () => void; shown: boolean }) {
  const fits = (r.fits ?? []) as Obj[]
  const q = (r.queue ?? {}) as Obj
  const ok = fits.filter((f) => f.ok).length
  return (
    <div className="mt-4">
      <SectionTitle right={!shown && <button onClick={onShow} className="flex items-center gap-1 text-[11px] text-accent hover:underline"><MapIcon size={12} /> Show on map</button>}>Result</SectionTitle>
      <div className="grid grid-cols-3 gap-1.5">
        <Stat label="Doors that fit" value={fits.length ? `${ok}/${fits.length}` : '?'} tone={fits.length && ok === fits.length ? 'good' : ok === 0 && fits.length ? 'bad' : undefined} />
        <Stat label="Wait P50 / P90" value={`${min(q.wait_p50_min)} / ${min(q.wait_p90_min)}`} />
        <Stat label="Max queue" value={isNum(q.max_in_yard) ? `${q.max_in_yard} in yard` : '?'} sub={isNum(q.max_on_street) && q.max_on_street > 0 ? `${q.max_on_street} on street` : undefined} tone={isNum(q.max_on_street) && q.max_on_street > 0 ? 'bad' : undefined} />
      </div>
      {fits.length > 0 && (
        <div className="mt-2 divide-y divide-line overflow-hidden rounded-lg ring-1 ring-inset ring-line">
          {fits.slice(0, 8).map((f, i) => (
            <div key={i} className="flex items-center gap-2 bg-white/[0.02] px-2.5 py-1.5 text-[12px]">
              {f.ok ? <CheckCircle2 size={13} className="text-good" /> : <XCircle size={13} className="text-bad" />}
              <span className="text-ink">Door {String(f.door_id ?? i + 1)}</span>
              <span className="text-ink-3">{String(f.vehicle ?? '').replace(/_/g, ' ')}</span>
              {isNum(f.margin_m) && <span className={clsx('ml-auto tabular-nums', marginClass(f.margin_m, f.ok))}>{marginText(f.margin_m, f.ok)}</span>}
            </div>
          ))}
        </div>
      )}
      {isNum(q.door_utilization) && <div className="mt-1.5 text-[11px] text-ink-3">Door utilization {pct(q.door_utilization)}</div>}
    </div>
  )
}

function FitResult({ r, onShow }: { r: Obj; onShow: () => void }) {
  const checks = (r.checks ?? []) as Obj[]
  const bad = checks.filter((c) => !c.ok)
  return (
    <div className="mt-4">
      <SectionTitle right={<button onClick={onShow} className="flex items-center gap-1 text-[11px] text-violet-300 hover:underline"><Box size={12} /> Show in scan</button>}>Result</SectionTitle>
      <div className="grid grid-cols-3 gap-1.5">
        <Stat label="Checks passed" value={checks.length ? `${checks.length - bad.length}/${checks.length}` : '?'} tone={checks.length && !bad.length ? 'good' : bad.length ? 'bad' : undefined} />
        <Stat label="Floor reachable" value={pct(r.reachable_pct)} />
        <Stat label="Pinch points" value={String((r.pinch_points ?? []).length)} tone={(r.pinch_points ?? []).length ? 'bad' : undefined} />
      </div>
      {bad.slice(0, 5).map((c, i) => (
        <div key={i} className="mt-1.5 flex items-start gap-2 rounded-lg bg-bad/[0.07] px-2.5 py-1.5 text-[12px] ring-1 ring-inset ring-bad/20">
          <XCircle size={13} className="mt-0.5 shrink-0 text-bad" />
          <span className="text-ink-2"><span className="capitalize text-ink">{String(c.kind).replace(/_/g, ' ')}</span>: need {String(c.need)} {c.unit ?? ''}, have {String(c.have)} {c.unit ?? ''}{c.note ? ` · ${c.note}` : ''}</span>
        </div>
      ))}
    </div>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line">
      <div className="text-[10px] text-ink-3">{label}</div>
      <div className={clsx('mt-0.5 text-[14px] font-semibold tabular-nums', tone === 'good' && 'text-good', tone === 'bad' && 'text-bad')}>{value}</div>
      {sub && <div className="text-[10px] text-bad/80">{sub}</div>}
    </div>
  )
}
