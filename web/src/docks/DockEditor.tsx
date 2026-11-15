// Dock editor side panel (right column, 432 px; replaces the card while open). Summary + estimate basis, accept all,
// interior alignment (flip / looks right), the door list by facade, the selected door's kind / reject / delete, Save.
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import clsx from 'clsx'
import { Check, ChevronLeft, DoorOpen, Eye, EyeOff, FlipHorizontal2, Loader2, RotateCcw, Trash2, Truck, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { KIND_LABEL, STATUS_LABEL, type DockDoor, type DoorKind } from '../lib/docksApi'
import { SourceChip } from '../components/ui'
import { useYardUi } from '../yard/maneuver'
import { runManeuverTest } from '../components/TestPanel'
import { DOOR_KIND_RGB } from './DockMap'
import { useDockEd } from './state'

const FACING: Record<string, string> = { N: 'North', E: 'East', S: 'South', W: 'West' }
const EVIDENCE_LABEL: Record<string, string> = { oblique: 'Oblique photo', trailer: 'Docked trailer', well: 'Truck well', costar_span: 'CoStar span fill', vision: 'Vision model', user: 'Added by a person' }
const statusCls: Record<string, string> = {
  confirmed: 'text-good bg-good/10 ring-good/25', user_added: 'text-accent bg-accent/10 ring-accent/25', proposed: 'text-ink-2 bg-white/[0.05] ring-white/10',
  inferred: 'text-ink-3 bg-white/[0.03] ring-white/10', rejected: 'text-bad bg-bad/10 ring-bad/25',
}

export default function DockEditor({ propertyId }: { propertyId: number }) {
  const ed = useDockEd()
  const pin = useStore((s) => s.pinsById.get(propertyId))
  useEffect(() => { ed.load(propertyId) }, [propertyId])            // eslint-disable-line react-hooks/exhaustive-deps
  const close = () => useStore.setState({ docksOpen: null })
  const base = ed.base
  const counts = useMemo(() => {
    const c: Record<string, number> = { confirmed: 0, user_added: 0, proposed: 0, inferred: 0, rejected: 0 }
    ed.doors.forEach((d) => { c[d.status] = (c[d.status] ?? 0) + 1 })
    return c
  }, [ed.doors])
  const usable = ed.doors.length - counts.rejected
  const groups = useMemo(() => {
    const m = new Map<string, { label: string; doors: DockDoor[]; b: number; e: number }>()
    for (const d of ed.doors) {
      if (!ed.showRejected && d.status === 'rejected') continue
      const e = base?.buildings.find((b) => b.id === d.building)?.edges.find((x) => x.id === d.edge)
      const k = `${d.building}:${d.edge}`
      if (!m.has(k)) m.set(k, { label: `${FACING[e?.facing ?? ''] ?? e?.facing ?? '?'} wall · edge ${d.edge}${e ? ` · ${Math.round(e.len_m / 0.3048)} ft` : ''}`, doors: [], b: d.building, e: d.edge })
      m.get(k)!.doors.push(d)
    }
    for (const g of m.values()) g.doors.sort((a, b) => a.s_m - b.s_m)
    return [...m.values()].sort((a, b) => b.doors.length - a.doors.length)
  }, [ed.doors, ed.showRejected, base])
  const selDoor = ed.doors.find((d) => d.id === ed.sel) ?? null
  // map selection -> scroll the list row into view
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ed.sel) return
    listRef.current?.querySelector(`[data-door="${CSS.escape(ed.sel)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [ed.sel])
  const yardReq = useYardUi((s) => (s.req?.property_id === propertyId ? s.req : null))
  const rerun = () => {
    close()
    useStore.getState().openTest(propertyId, 'yard')
    if (yardReq) runManeuverTest(yardReq).catch(() => {})
  }
  const sm = base?.summary
  const est = sm?.estimate

  return (
    <aside className="glass drawer-in absolute bottom-4 right-4 top-4 z-20 flex w-[432px] flex-col overflow-hidden rounded-2xl" data-testid="dock-editor">
      <header className="flex items-start gap-2 border-b border-line px-4 py-3">
        <button onClick={close} title="Back to the card" className="-ml-1 mt-0.5 rounded-lg p-1 text-ink-3 hover:bg-white/[0.06] hover:text-ink"><ChevronLeft size={16} /></button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><DoorOpen size={15} className="text-accent" /><h2 className="text-[15px] font-semibold">Dock review</h2>
            {base?._fixture && <span className="rounded bg-warn/15 px-1.5 text-[10px] font-medium text-warn">dev fixture</span>}</div>
          <div className="truncate text-[12px] text-ink-3">{pin?.address ?? `Property ${propertyId}`} · doors on the Vexcel footprint</div>
        </div>
        <button onClick={close} title="Close (Esc)" className="rounded-lg p-1 text-ink-3 hover:bg-white/[0.06] hover:text-ink"><X size={16} /></button>
      </header>

      <div className="scroll-thin flex-1 space-y-5 overflow-y-auto p-4">
        {ed.loading && <div className="space-y-2"><div className="skeleton h-16" /><div className="skeleton h-10" /><div className="skeleton h-40" /></div>}
        {ed.error && <div className="rounded-xl bg-bad/10 p-3 text-[12px] text-bad ring-1 ring-inset ring-bad/20">Dock data could not be loaded: {ed.error}</div>}
        {base && (
          <>
            <section>
              <Title right={<SourceChip source="vexcel" />}>Evidence</Title>
              <div className="grid grid-cols-4 gap-1.5">
                <Stat label="CoStar docks" value={sm?.costar_docks} sub={sm?.costar_drive_ins != null ? `${sm.costar_drive_ins} drive-in` : undefined} />
                <Stat label="Seen" value={sm?.detected_doors} sub="obliques" />
                <Stat label="Trailers" value={sm?.docked_trailers} sub="at doors" />
                <Stat label="Confirmed" value={counts.confirmed + counts.user_added} sub="by a person" tone={counts.confirmed + counts.user_added ? 'good' : undefined} />
              </div>
              {est && (
                <div className="mt-1.5 rounded-xl bg-white/[0.03] px-3 py-2 ring-1 ring-inset ring-line">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[11px] text-ink-3">Dock-high estimate</span>
                    <span className="text-[15px] font-semibold tabular-nums">{est.value ?? '?'}</span>
                    {est.lo != null && est.hi != null && <span className="text-[12px] tabular-nums text-ink-3">range {est.lo}–{est.hi}</span>}
                    <span className={clsx('ml-auto rounded px-1.5 text-[10px] font-medium uppercase tracking-wide',
                      est.status === 'verified' ? 'bg-good/10 text-good' : est.status === 'unknown' ? 'bg-white/5 text-ink-3' : 'bg-warn/10 text-warn')}>{est.status.replace('_', ' ')}</span>
                  </div>
                  {est.basis && <div className="mt-0.5 text-[11.5px] leading-snug text-ink-2">{est.basis}</div>}
                </div>
              )}
            </section>

            <section>
              <Title right={<button onClick={ed.toggleRejected} className="flex items-center gap-1 text-[11px] text-ink-3 hover:text-ink">
                {ed.showRejected ? <EyeOff size={12} /> : <Eye size={12} />}{ed.showRejected ? 'Hide' : 'Show'} rejected ({counts.rejected})</button>}>Doors on the map</Title>
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1 text-[12px] text-ink-2 tabular-nums">
                  <span className="font-semibold text-ink">{usable}</span> usable · {counts.confirmed} confirmed · {counts.user_added} added · {counts.proposed} proposed · {counts.inferred} inferred
                </div>
                <button onClick={ed.acceptAll} disabled={!counts.proposed && !counts.inferred} data-testid="accept-all"
                  className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-accent/15 px-2.5 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/35 hover:bg-accent/25 disabled:opacity-40">
                  <Check size={13} /> Accept all proposals
                </button>
              </div>

              {selDoor && <DoorDetail d={selDoor} />}

              <div className="mt-2 space-y-2.5" ref={listRef}>
                {groups.map((g) => {
                  const open = g.doors.filter((d) => d.status === 'proposed' || d.status === 'inferred').length
                  return (
                  <div key={g.label}>
                    <div className="mb-1 flex items-center gap-2 text-[11px] text-ink-3"><span>{g.label}</span><span className="tabular-nums">· {g.doors.length}</span>
                      {open > 0 && <button onClick={() => ed.confirmWall(g.b, g.e)} data-testid="confirm-wall"
                        className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-good ring-1 ring-inset ring-good/25 hover:bg-good/10"><Check size={11} /> Confirm all on this wall ({open})</button>}
                    </div>
                    <div className="space-y-0.5">
                      {g.doors.map((d) => (
                        <button key={d.id} data-door={d.id} onClick={() => ed.focusDoor(d.id)}
                          className={clsx('flex w-full items-center gap-2 rounded-xl px-2.5 py-1.5 text-left text-[12px] transition',
                            d.id === ed.sel ? 'bg-accent/10 ring-1 ring-inset ring-accent/40' : 'hover:bg-white/[0.04]', d.status === 'rejected' && 'opacity-55')}>
                          <KindDot d={d} />
                          <span className="w-8 font-mono text-ink">{d.id}</span>
                          <span className="w-[68px] text-ink-2">{KIND_LABEL[d.kind] ?? d.kind}</span>
                          <span className="font-mono text-[11px] tabular-nums text-ink-3">{d.width_ft != null ? `${d.width_ft}′` : '?'}</span>
                          <span className="ml-auto truncate text-[10.5px] text-ink-3">{d.evidence.map((e) => EVIDENCE_LABEL[e.type] ?? e.type).filter((v, i, a) => a.indexOf(v) === i).join(', ')}</span>
                          <StatusChip s={d.status} />
                        </button>
                      ))}
                    </div>
                  </div>
                  )
                })}
                {!groups.length && <div className="rounded-xl bg-white/[0.03] p-3 text-[12px] text-ink-3 ring-1 ring-inset ring-line">No doors yet. Pick Add door (2) on the map, then click a wall.</div>}
              </div>
            </section>

            <section>
              <Title>Yard gate</Title>
              <div className="text-[12px] leading-snug text-ink-2">
                {ed.gate ? <>{ed.gate.source === 'user' ? 'Set by you.' : 'Inferred from the parcel and pavement (no road data).'} Use Move gate (3) on the map to change it.</> : 'No gate found. The yard test will pick the street side.'}
              </div>
            </section>

            {ed.reg?.model_id && (
              <section>
                <Title right={<SourceChip source="matterport" />}>Interior scan alignment</Title>
                <div className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-inset ring-line">
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="font-mono text-ink-2">{ed.reg.model_id}</span>
                    <span className="text-ink-3">rotated {ed.reg.flip}°</span>
                    {ed.reg.confidence != null && <span className="text-ink-3">· auto confidence {Math.round(ed.reg.confidence * 100)}%</span>}
                    {ed.reg.confirmed_by_user && <span className="ml-auto flex items-center gap-1 text-good"><Check size={12} /> checked</span>}
                  </div>
                  {ed.reg.note && <div className="mt-1 text-[11.5px] leading-snug text-ink-3">{ed.reg.note}</div>}
                  <div className="mt-2 flex gap-1.5">
                    <Btn onClick={ed.flip}><FlipHorizontal2 size={13} /> Flip 180°</Btn>
                    <Btn onClick={ed.confirmAlign} disabled={ed.reg.confirmed_by_user} tone="good"><Check size={13} /> Alignment looks right</Btn>
                  </div>
                  {ed.reg.scan_outline?.length ? (
                    <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-ink-3">
                      <span className="mr-1">Move scan</span>
                      {([['W', -1, 0], ['E', 1, 0], ['N', 0, 1], ['S', 0, -1]] as const).map(([lab, dx, dy]) => (
                        <span key={lab} className="flex overflow-hidden rounded-lg ring-1 ring-inset ring-line">
                          <button type="button" title={`1 m ${lab}`} onClick={() => ed.nudge(dx, dy)} className="px-1.5 py-1 hover:bg-white/5">{lab}</button>
                          <button type="button" title={`5 m ${lab}`} onClick={() => ed.nudge(dx * 5, dy * 5)} className="border-l border-line px-1.5 py-1 text-ink-3 hover:bg-white/5">5</button>
                        </span>
                      ))}
                      {(ed.reg.shift?.[0] || ed.reg.shift?.[1]) ? <span className="ml-auto tabular-nums">{ed.reg.shift![0]} m E, {ed.reg.shift![1]} m N</span> : null}
                    </div>
                  ) : null}
                  <div className="mt-1.5 text-[11px] leading-snug text-ink-3">{ed.reg.scan_outline?.length
                    ? 'The violet dashed outline is the scanned floor placed on the building. Flip or move it until it sits where the scan really is (match the dock wall), then confirm. Doors appear inside the 3D scan only after you confirm.'
                    : 'The scan floor outline is not available on this map yet. Check that the dock wall in "Walk inside" is the wall these doors are on, then confirm.'}</div>
                </div>
              </section>
            )}
          </>
        )}
      </div>

      <footer className="border-t border-line p-3">
        {ed.saveError && <div className="mb-2 rounded-lg bg-bad/10 px-2.5 py-1.5 text-[11.5px] leading-snug text-bad ring-1 ring-inset ring-bad/20">Not saved: {ed.saveError}</div>}
        {ed.savedAt && !ed.dirty ? (
          <div className="flex items-center gap-2">
            <span className="flex flex-1 items-center gap-1.5 text-[12px] text-good"><Check size={14} /> Saved. The yard test now uses these doors.</span>
            <button onClick={rerun} className="flex h-10 items-center gap-2 rounded-xl bg-accent px-3 text-[13px] font-semibold text-bg hover:brightness-110">
              <Truck size={14} /> {yardReq ? 'Rerun yard test' : 'Open yard test'}
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-[1fr_2fr] gap-2">
            <button onClick={ed.discard} disabled={!ed.dirty}
              className="flex h-10 items-center justify-center gap-1.5 rounded-xl bg-white/[0.06] text-[13px] text-ink ring-1 ring-inset ring-line-2 hover:bg-white/10 disabled:opacity-40">
              <RotateCcw size={13} /> Discard
            </button>
            <button onClick={() => ed.save()} disabled={!ed.dirty || ed.saving} data-testid="save-docks"
              className="flex h-10 items-center justify-center gap-2 rounded-xl bg-accent text-[13px] font-semibold text-bg hover:brightness-110 disabled:opacity-45">
              {ed.saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} {ed.dirty ? 'Save doors' : 'No changes'}
            </button>
          </div>
        )}
      </footer>
    </aside>
  )
}

function DoorDetail({ d }: { d: DockDoor }) {
  const ed = useDockEd()
  const kinds: DoorKind[] = ['dock_high', 'drive_in', 'unknown']
  return (
    <div className="mt-2 rounded-xl bg-accent/[0.06] p-3 ring-1 ring-inset ring-accent/30" data-testid="door-detail">
      <div className="flex items-center gap-2">
        <KindDot d={d} />
        <span className="font-mono text-[13px] font-semibold">{d.id}</span>
        <StatusChip s={d.status} />
        <span className="ml-auto font-mono text-[11px] tabular-nums text-ink-3">{d.s_m.toFixed(1)} m along · {d.width_ft ?? '?'}×{d.height_ft ?? '?'} ft</span>
      </div>
      <div className="mt-2 flex overflow-hidden rounded-lg ring-1 ring-inset ring-line">
        {kinds.map((k) => (
          <button key={k} onClick={() => ed.setKind(d.id, k)} disabled={d.status === 'rejected'}
            className={clsx('flex-1 py-1.5 text-[12px] font-medium transition', d.kind === k ? 'bg-accent/20 text-accent' : 'text-ink-2 hover:bg-white/[0.05]')}>{KIND_LABEL[k]}</button>
        ))}
      </div>
      <div className="mt-2 flex gap-1.5">
        {d.status === 'rejected'
          ? <Btn onClick={() => ed.restore(d.id)}><RotateCcw size={13} /> Restore</Btn>
          : <Btn onClick={() => ed.reject(d.id)} tone="bad"><X size={13} /> Not a door <kbd className="font-mono text-[10px] opacity-60">R</kbd></Btn>}
        {d.status === 'user_added' && <Btn onClick={() => ed.remove(d.id)} tone="bad"><Trash2 size={13} /> Delete</Btn>}
        {(d.status === 'proposed' || d.status === 'inferred') && <Btn onClick={() => ed.confirm(d.id)} tone="good"><Check size={13} /> Confirm <kbd className="font-mono text-[10px] opacity-60">C</kbd></Btn>}
      </div>
      {d.evidence.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11.5px] text-ink-2">
          {d.evidence.map((e, i) => (
            <li key={i} className="flex min-w-0 gap-1.5" title={e.note}><span className="shrink-0 whitespace-nowrap text-ink-3">{EVIDENCE_LABEL[e.type] ?? e.type}</span><span className="shrink-0 tabular-nums text-ink-3">{Math.round(e.conf * 100)}%</span>{e.note && <span className="min-w-0 truncate">· {e.note}</span>}</li>
          ))}
        </ul>
      )}
      {d.kind === 'unknown' && d.status !== 'rejected' && <div className="mt-1.5 text-[11px] text-ink-3">No dock-high or drive-in cue found for this door. Pick its kind if you know it.</div>}
    </div>
  )
}

function KindDot({ d }: { d: DockDoor }) {
  const c = DOOR_KIND_RGB[d.kind] ?? DOOR_KIND_RGB.unknown
  const col = `rgb(${c.join(' ')})`
  const solid = d.status === 'confirmed' || d.status === 'user_added'
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-[3px]" style={solid ? { background: col } : d.status === 'inferred' ? { background: `rgb(${c.join(' ')} / 0.4)` } : { border: `1.5px dashed ${col}` }} />
}
function StatusChip({ s }: { s: DockDoor['status'] }) {
  return <span className={clsx('inline-flex h-[17px] shrink-0 items-center rounded px-1.5 text-[9.5px] font-medium uppercase tracking-wide ring-1 ring-inset', statusCls[s])}>{STATUS_LABEL[s] ?? s}</span>
}
function Title({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return <div className="mb-2 flex items-center justify-between"><h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">{children}</h3>{right}</div>
}
function Stat({ label, value, sub, tone }: { label: string; value: number | null | undefined; sub?: string; tone?: 'good' }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2 py-1.5 ring-1 ring-inset ring-line">
      <div className="truncate text-[10px] text-ink-3">{label}</div>
      <div className={clsx('mt-0.5 text-[15px] font-semibold tabular-nums', value == null && 'text-ink-3', tone === 'good' && 'text-good')} title={value == null ? 'Unknown: not reported or not found' : undefined}>{value ?? '?'}</div>
      {sub && <div className="truncate text-[10px] text-ink-3">{sub}</div>}
    </div>
  )
}
function Btn({ children, onClick, disabled, tone }: { children: ReactNode; onClick: () => void; disabled?: boolean; tone?: 'good' | 'bad' }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className={clsx('flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium ring-1 ring-inset transition disabled:opacity-40',
        tone === 'bad' ? 'text-bad ring-bad/30 hover:bg-bad/10' : tone === 'good' ? 'text-good ring-good/30 hover:bg-good/10' : 'text-ink ring-line-2 hover:bg-white/[0.06]')}>
      {children}
    </button>
  )
}
