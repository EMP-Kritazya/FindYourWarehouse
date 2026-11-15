// One-glance property panel: no scrolling. Who/where, a fit line, six tiles a buyer decides on (size, clear height,
// docks, availability, yard verdict, interior capacity), the three things to DO, and drawers for the detail.
import clsx from 'clsx'
import { ArrowRight, Box, Columns3, Layers3, Loader2, MapPin, MessageSquare, Star, Truck, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useStore } from '../lib/store'
import { askGuide } from '../lib/guide'
import type { Pin, Result, Twin, WarehouseDetail } from '../lib/api'
import { GRADE, useYardVerdict } from './YardVerdict'
import { useStorage } from '../racks/StorageSummary'

const k = (v: number | null | undefined) => (v == null ? '?' : v >= 10000 ? `${(v / 1000).toFixed(v >= 100000 ? 0 : 1)}k` : v.toLocaleString())

export default function PropertyHud({ id, pin, w, twin, result, excluded, ranked, scan, photo, onDetail }: {
  id: number; pin: Pin; w: WarehouseDetail | null; twin: Twin | null; result?: Result; excluded?: { why: string[] }; ranked: boolean
  scan: { model_id: string } | null; photo: string | null; onDetail: (tab: 'overview' | 'test') => void
}) {
  const select = useStore((s) => s.select)
  const shortlisted = useStore((s) => s.shortlist.includes(id))
  const shortlistDo = useStore((s) => s.shortlistDo)
  const comparing = useStore((s) => s.compareIds.includes(id))
  const toggleCompare = useStore((s) => s.toggleCompare)
  const yard = useYardVerdict(id)
  const inside = useStorage(id)

  // the fit line
  const fit = excluded ? { tone: 'bad', text: `Fails a must-have: ${excluded.why[0] ?? 'see details'}` }
    : ranked && result ? {
      tone: result.counts.fail ? 'warn' : 'good',
      text: `#${result.rank} for your needs · ${result.counts.pass ?? 0} met${result.counts.unknown ? ` · ${result.counts.unknown} unknown` : ''}${result.counts.fail ? ` · ${result.counts.fail} missed` : ''}`,
    } : { tone: 'muted', text: 'Describe your operation in the search bar to see how well it fits' }

  // tiles
  const scanClear = typeof twin?.interior?.clear === 'object' && twin?.interior?.clear ? twin.interior.clear.p5_ft : null
  const avail = (w?.listings ?? []).filter((l) => l.status === 'Active')
  const availSf = avail.reduce((a, l) => a + (l.available_sqft ?? 0), 0)
  const rents = avail.flatMap((l) => [l.rent_low_psf_yr, l.rent_high_psf_yr]).filter((x): x is number => !!x)
  const yv = yard.v?.status === 'computed' ? yard.v : null
  const iv = inside?.status === 'computed' ? inside : null

  const yardTest = () => (window as unknown as { __proinspectUi?: (c: string, a: unknown) => void }).__proinspectUi?.('yardTest', { id, trucks: [{ type: 'semi_53', count: 5 }] })
  const planner = () => plannable && scan && useStore.setState({ interiorOpen: { pid: id, modelId: scan.model_id, title: pin.address } })
  // any scan can be WALKED, even one too small to plan on (the planner needs a measured warehouse floor)
  const tour = scan ?? [...(w?.scans ?? [])].sort((a, b) => ((b as { floor_sqft?: number }).floor_sqft ?? 0) - ((a as { floor_sqft?: number }).floor_sqft ?? 0))[0] ?? null
  const walk = () => tour && useStore.getState().openStage({ modelId: tour.model_id, title: pin.address, subtitle: 'Matterport 3D interior', mode: 'view', propertyId: id })
  const noPlanWhy = inside?.status === 'no_scan' ? inside.why : undefined
  const plannable = !!scan && !noPlanWhy                 // the backend decides whether a scan is big enough to plan on

  return (
    <aside key={id} className="glass drawer-in absolute right-4 top-4 z-20 w-[400px] overflow-hidden rounded-2xl" data-testid="property-hud">
      {/* header: photo strip with the address over it */}
      <div className="relative h-[104px] overflow-hidden">
        {photo && <img src={photo} alt="" className="absolute inset-0 h-full w-full object-cover opacity-75"
          onError={(e) => { const im = e.target as HTMLImageElement; const o = `/media/twins/exterior/${id}_ortho.jpg`; if (!im.src.endsWith(o)) im.src = o; else im.style.display = 'none' }} />}
        <div className="absolute inset-0 bg-gradient-to-t from-panel via-panel/70 to-transparent" />
        <div className="absolute right-2.5 top-2.5 flex gap-1.5">
          <IconBtn on={shortlisted} tone="warn" title={shortlisted ? 'Remove from shortlist' : 'Add to shortlist'} onClick={() => shortlistDo(shortlisted ? 'remove' : 'add', id)}><Star size={15} fill={shortlisted ? 'currentColor' : 'none'} /></IconBtn>
          <IconBtn on={comparing} tone="accent" title={comparing ? 'Remove from compare' : 'Add to compare'} onClick={() => toggleCompare(id)}><Columns3 size={15} /></IconBtn>
          <IconBtn title="Close" onClick={() => select(null)}><X size={16} /></IconBtn>
        </div>
        <div className="absolute bottom-2.5 left-4 right-4">
          <div className="flex items-center gap-1.5 text-[12px] text-ink-2"><MapPin size={12} />{pin.city}{w?.zip ? ` ${w.zip}` : ''}
            {w?.building_class && <span className="text-ink-3">· Class {w.building_class}</span>}
            {pin.inspectable && <span className="flex items-center gap-1 text-violet-300">· <Box size={11} /> 3D scan</span>}</div>
          <h2 className="truncate text-[19px] font-semibold tracking-tight">{pin.address}</h2>
        </div>
      </div>

      <div className="space-y-3 px-4 pb-4 pt-2">
        <div className={clsx('rounded-lg px-2.5 py-1.5 text-[12.5px]',
          fit.tone === 'good' ? 'bg-good/10 text-good' : fit.tone === 'warn' ? 'bg-warn/10 text-warn' : fit.tone === 'bad' ? 'bg-bad/10 text-bad' : 'bg-white/[0.04] text-ink-3')}>{fit.text}</div>

        <div className="grid grid-cols-3 gap-1.5" data-testid="hud-tiles">
          <Tile label="Size" v={k(w?.rba_sqft ?? pin.rba_sqft)} unit="SF" sub={w?.lot_acres ? `${w.lot_acres} acre lot` : 'CoStar'} />
          <Tile label="Clear height" v={pin.clear_height_ft != null ? String(pin.clear_height_ft) : '?'} unit="ft" sub={scanClear ? `scan: ${scanClear} ft usable` : 'CoStar'} />
          <Tile label="Dock doors" v={w?.docks_external != null ? String(w.docks_external) : '?'} sub={w?.drive_ins != null ? `+ ${w.drive_ins} drive-in` : 'CoStar'} />
          <Tile label="Available" v={availSf ? k(availSf) : pin.availability === 'available' ? '✓' : '—'} unit={availSf ? 'SF' : ''}
            sub={rents.length ? `$${Math.min(...rents)}–${Math.max(...rents)}/SF/yr` : availSf ? 'rent not listed' : pin.availability === 'available' ? 'listed' : 'not listed'} />
          <Tile label="Yard · 5 semis" onClick={yv ? yardTest : yard.run}
            v={yv ? (GRADE[yv.grade ?? '']?.label ?? '?') : yard.running ? '' : 'Run'} tone={yv?.grade === 'good' ? 'good' : yv?.grade === 'poor' ? 'bad' : yv ? 'warn' : 'link'}
            sub={yv ? `${yv.semi_backin_ok}/${yv.dock_doors} docks fit · ${yv.docked_max} at once` : yard.running ? 'testing ~20 s' : 'test the truck yard'}
            icon={yard.running ? <Loader2 size={14} className="animate-spin" /> : undefined} />
          <Tile label="Inside · 3PL" onClick={plannable ? planner : undefined}
            v={iv ? k(iv.positions) : noPlanWhy ? (tour ? 'Too small' : '—') : scan ? '…' : '—'} unit={iv ? 'pallets' : ''} tone={iv ? undefined : plannable ? 'link' : undefined}
            sub={iv ? `${Math.round(iv.reachable_pct ?? 0)}% reach · ${iv.machines_needed ?? '?'} forklifts` : noPlanWhy ? (tour ? 'scan too small to plan on' : 'no interior scan') : scan ? 'planning…' : 'no interior scan'}
            title={noPlanWhy} />
        </div>
        {noPlanWhy && tour && <div className="text-[11.5px] leading-snug text-ink-3">{noPlanWhy.replace(/^the /, 'The ')}</div>}
        {iv?.scan_coverage_pct != null && iv.scan_coverage_pct < 30 && (
          <div className="text-[11.5px] leading-snug text-warn">Interior numbers cover only the {Math.round(iv.scan_coverage_pct)}% of the building that was scanned.</div>
        )}

        <div className="grid grid-cols-3 gap-1.5">
          <Action icon={<Truck size={15} />} label="Yard test" onClick={yardTest} />
          <Action icon={<Layers3 size={15} />} label="Interior planner" onClick={planner} disabled={!plannable} primary />
          <Action icon={<Box size={15} />} label="Walk inside" onClick={walk} disabled={!tour} />
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2.5 text-[12.5px]">
          <Drawer onClick={() => onDetail('overview')}>Evidence & sources</Drawer>
          <Drawer onClick={() => useStore.setState({ docksOpen: id })}>Docks</Drawer>
          <Drawer onClick={() => onDetail('test')}>Test results</Drawer>
          <button onClick={() => askGuide(`Tell me about ${pin.address}. What matters for me here?`, { id, address: pin.address })}
            className="ml-auto flex items-center gap-1 text-ink-3 hover:text-ink"><MessageSquare size={13} /> Ask</button>
        </div>
      </div>
    </aside>
  )
}

function Tile({ label, v, unit, sub, tone, onClick, icon, title }: { label: string; v: string; unit?: string; sub?: string; tone?: 'good' | 'warn' | 'bad' | 'link'; onClick?: () => void; icon?: ReactNode; title?: string }) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag onClick={onClick} title={title} className={clsx('min-w-0 rounded-xl bg-white/[0.035] px-2.5 py-2 text-left ring-1 ring-inset ring-line',
      onClick && 'transition hover:bg-white/[0.07] hover:ring-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent')}>
      <div className="truncate text-[11px] uppercase tracking-[0.06em] text-ink-3">{label}</div>
      <div className={clsx('mt-0.5 flex items-baseline gap-1 text-[18px] font-semibold tabular-nums leading-tight',
        tone === 'good' ? 'text-good' : tone === 'warn' ? 'text-warn' : tone === 'bad' ? 'text-bad' : tone === 'link' ? 'text-accent' : 'text-ink')}>
        {icon}{v}{unit && <span className="text-[12px] font-normal text-ink-3">{unit}</span>}
      </div>
      {sub && <div className="truncate text-[11.5px] text-ink-3">{sub}</div>}
    </Tag>
  )
}

function Action({ icon, label, onClick, disabled, primary }: { icon: ReactNode; label: string; onClick: () => void; disabled?: boolean; primary?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className={clsx('flex h-11 items-center justify-center gap-1.5 rounded-xl text-[12.5px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        primary ? 'bg-accent text-bg hover:brightness-110' : 'bg-white/[0.06] text-ink ring-1 ring-inset ring-line-2 hover:bg-white/10')}>
      {icon}{label}
    </button>
  )
}

function Drawer({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return <button onClick={onClick} className="flex items-center gap-1 text-ink-2 hover:text-accent">{children}<ArrowRight size={12} /></button>
}

function IconBtn({ children, onClick, title, on, tone }: { children: ReactNode; onClick: () => void; title: string; on?: boolean; tone?: 'warn' | 'accent' }) {
  return (
    <button onClick={onClick} title={title} aria-label={title}
      className={clsx('flex h-8 w-8 items-center justify-center rounded-lg backdrop-blur-md transition',
        on ? (tone === 'warn' ? 'bg-warn text-bg' : 'bg-accent text-bg') : 'bg-black/50 text-ink hover:bg-black/70')}>{children}</button>
  )
}
