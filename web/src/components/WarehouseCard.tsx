import { useEffect, useRef, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { X, Box, Play, Columns3, MapPin, Ruler, Truck, DoorOpen, Zap, Calendar, Tag, Home, ScanLine, Plane, Star, Sparkles, FlaskConical, PencilRuler } from 'lucide-react'
import { DEV_SIM, useStore } from '../lib/store'
import { askGuide, sendEvent } from '../lib/guide'
import TestPanel from './TestPanel'
import VexcelSection from './VexcelSection'
import { api, ApiError, type Fact, type Twin, type WarehouseDetail, type Scan } from '../lib/api'
import { AVAIL_LABEL, prettyWhy, fmtFt, fmtInt, fmtNeed, fmtRent, fmtSF, fmtUnit, isNum, titleCase } from '../lib/format'
import { ConfidenceDots, ScoreBand, SectionTitle, Skeleton, SourceChip, StatusIcon, Unknown, useAsync } from './ui'
import SimPanel from './SimPanel'
import { docksApi, proofApi, proofImageUrl, type DockStore } from '../lib/docksApi'

const detailCache = new Map<number, Promise<WarehouseDetail>>()
const twinCache = new Map<number, Promise<Twin>>()
export const loadDetail = (id: number) => {
  if (!detailCache.has(id)) detailCache.set(id, api.warehouse(id).catch((e) => { detailCache.delete(id); throw e }))
  return detailCache.get(id)!
}
export const loadTwin = (id: number) => {
  if (!twinCache.has(id)) twinCache.set(id, api.twin(id).catch((e) => { twinCache.delete(id); throw e }))
  return twinCache.get(id)!
}

export function bestScan(w: WarehouseDetail | null, twin?: Twin | null): Scan | null {
  const scans = [...(twin?.scans?.length ? twin.scans : w?.scans ?? [])]
  if (twin?.interior?.model_id) {
    const s = scans.find((x) => x.model_id === twin.interior!.model_id)
    return s ?? { model_id: twin.interior.model_id, class: 'warehouse_floor', floor_sqft: null, height_ft: null }
  }
  scans.sort((a, b) => (b.class === 'warehouse_floor' ? 1 : 0) - (a.class === 'warehouse_floor' ? 1 : 0) || (b.floor_sqft ?? 0) - (a.floor_sqft ?? 0))
  return scans[0] ?? null
}

const UNKNOWN_WHY = 'Not reported by CoStar, and not measured from the Matterport scan or Vexcel aerial yet.'

export default function WarehouseCard() {
  const id = useStore((s) => s.selectedId)
  const tab = useStore((s) => s.drawerTab)
  const setTab = useStore((s) => s.setTab)
  const select = useStore((s) => s.select)
  const pin = useStore((s) => (id ? s.pinsById.get(id) : undefined))
  const result = useStore((s) => s.search?.results.find((r) => r.id === id))
  const excluded = useStore((s) => s.search?.excluded.find((r) => r.id === id))
  const ranked = useStore((s) => s.search?.mode === 'ranked')
  const compareIds = useStore((s) => s.compareIds)
  const toggleCompare = useStore((s) => s.toggleCompare)
  const openStage = useStore((s) => s.openStage)
  const shortlisted = useStore((s) => (id ? s.shortlist.includes(id) : false))
  const shortlistDo = useStore((s) => s.shortlistDo)
  const detail = useAsync(id ? () => loadDetail(id) : null, [id])
  const twin = useAsync(id ? () => loadTwin(id) : null, [id])
  if (!id || !pin) return null
  const w = detail.data
  const scan = bestScan(w, twin.data)
  const walk = (mode: 'view' | 'markDoors' = 'view') => {
    if (!scan) return
    openStage({ modelId: scan.model_id, title: pin.address, subtitle: mode === 'markDoors' ? 'Mark dock doors' : 'Matterport 3D interior', mode, propertyId: id })
  }
  const ask = () => askGuide(`Tell me about ${pin.address}. What matters for me here?`, { id, address: pin.address })
  const tabs = (DEV_SIM ? ['overview', 'test', 'simulate'] : ['overview', 'test']) as ('overview' | 'test' | 'simulate')[]
  const TAB_LABEL = { overview: 'Evidence', test: 'Test it', simulate: 'Shift sim (dev)' }

  return (
    <aside key={id} className="glass drawer-in absolute bottom-4 right-4 top-4 z-20 flex w-[432px] flex-col overflow-hidden rounded-2xl">
      <Hero photo={pin.photo} ortho={twin.data?.media?.ortho ?? `/media/twins/exterior/${id}_ortho.jpg`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[12px] text-ink-2"><MapPin size={12} />{pin.city}{w?.zip ? `, ${w.state ?? ''} ${w.zip}` : ''}</div>
            <h2 className="mt-0.5 truncate text-[19px] font-semibold tracking-tight">{pin.address}</h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <Badge>{titleCase(pin.subtype.replace(/^(Industrial|Flex)_/, ''))}</Badge>
              {w?.building_class && <Badge>Class {w.building_class}</Badge>}
              <Badge tone={pin.availability === 'available' ? 'accent' : undefined}>{AVAIL_LABEL[pin.availability] ?? 'Unknown'}</Badge>
              {pin.inspectable && <Badge><Box size={11} /> 3D scan</Badge>}
            </div>
          </div>
        </div>
      </Hero>
      <div className="absolute right-3 top-3 flex gap-1.5">
        <button onClick={() => shortlistDo(shortlisted ? 'remove' : 'add', id)} title={shortlisted ? 'Remove from shortlist' : 'Add to shortlist'}
          className={clsx('flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] backdrop-blur-md transition',
            shortlisted ? 'bg-warn text-bg' : 'bg-black/50 text-ink hover:bg-black/70')}>
          <Star size={14} fill={shortlisted ? 'currentColor' : 'none'} /> {shortlisted ? 'Shortlisted' : 'Shortlist'}
        </button>
        <button onClick={() => toggleCompare(id)} title={compareIds.includes(id) ? 'Remove from compare' : 'Add to compare'}
          className={clsx('flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] backdrop-blur-md transition',
            compareIds.includes(id) ? 'bg-accent text-bg' : 'bg-black/50 text-ink hover:bg-black/70')}>
          <Columns3 size={14} /> {compareIds.includes(id) ? 'Comparing' : ''}
        </button>
        <button onClick={() => select(null)} title="Close" className="flex h-8 w-8 items-center justify-center rounded-lg bg-black/50 text-ink backdrop-blur-md hover:bg-black/70"><X size={16} /></button>
      </div>

      <div className="flex gap-1 border-b border-line px-4">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx('-mb-px border-b-2 px-3 py-2.5 text-[13px] font-medium transition',
              tab === t ? 'border-accent text-ink' : 'border-transparent text-ink-3 hover:text-ink-2')}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto">
        {tab === 'overview' ? (
          <div className="space-y-6 p-4">
            {ranked && result && <FitSummary result={result} />}
            {ranked && excluded && (
              <div className="rounded-xl bg-bad/10 p-3 text-[12px] text-bad ring-1 ring-inset ring-bad/20">
                <div className="font-medium">Excluded from results</div>
                {excluded.why.map((x) => <div key={x} className="mt-0.5 text-bad/85">{prettyWhy(x)}</div>)}
              </div>
            )}
            <Specs w={w} loading={detail.loading} error={!!detail.error} />
            <DocksRow id={id} />
            {ranked && result && result.checks.length > 1 && <FitChecklist checks={result.checks} />}
            <Aerial id={id} twin={twin.data} loading={twin.loading} error={twin.error} />
            <VexcelSection id={id} twin={() => loadTwin(id)} />
            <Inside w={w} twin={twin.data} scan={scan} onOpen={() => walk('view')} onMarkDoors={() => walk('markDoors')} />
          </div>
        ) : tab === 'test' ? (
          <TestPanel id={id} scan={scan} address={pin.address} />
        ) : (
          <SimPanel id={id} scan={scan} address={pin.address} sqft={w?.rba_sqft ?? pin.rba_sqft} />
        )}
      </div>

      {tab === 'overview' && (
        <footer className="grid grid-cols-[1fr_1fr_1.15fr] gap-2 border-t border-line p-3">
          <button disabled={!scan} onClick={() => walk('view')}
            className="flex h-10 items-center justify-center gap-2 rounded-xl bg-white/[0.06] text-[13px] font-medium text-ink ring-1 ring-inset ring-line-2 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
            title={scan ? 'Walk the 3D interior scan' : 'No interior scan for this property'}>
            <Box size={15} /> Walk inside
          </button>
          <button onClick={ask} title="Ask the guide about this site"
            className="flex h-10 items-center justify-center gap-2 rounded-xl bg-white/[0.06] text-[13px] font-medium text-ink ring-1 ring-inset ring-line-2 transition hover:bg-white/10">
            <Sparkles size={14} className="text-accent" /> Ask guide
          </button>
          <button onClick={() => setTab('test')}
            className="flex h-10 items-center justify-center gap-2 rounded-xl bg-accent text-[13px] font-semibold text-bg transition hover:brightness-110">
            <FlaskConical size={14} /> Test it
          </button>
        </footer>
      )}
    </aside>
  )
}

/** "Docks: 23 CoStar · 9 seen · 6 trailers at doors · Review" -> opens the 2D dock editor */
function DocksRow({ id }: { id: number }) {
  const st = useAsync(() => docksApi.get(id), [id])
  const s = (st.data as DockStore | null)?.summary
  const confirmed = (st.data as DockStore | null)?.doors.filter((d) => d.status === 'confirmed' || d.status === 'user_added').length ?? 0
  // first proof crop (Vexcel oblique + SAM3 box) as the row's thumbnail
  const proof = useAsync(() => proofApi.get(id), [id])
  const thumb = (() => {
    const p = proof.data
    if (!p) return null
    for (const [doorId, dp] of Object.entries(p.doors)) if (!dp.no_photo && dp.crops.length) return { src: proofImageUrl(id, doorId, dp.crops[0]), view: dp.crops[0].view }
    return null
  })()
  const [thumbOk, setThumbOk] = useState(true)
  const open = () => useStore.setState({ docksOpen: id })
  const n = (v: number | null | undefined) => (v == null ? '?' : String(v))
  return (
    <div>
      <SectionTitle right={s?.estimate && <span className="text-[11px] text-ink-3">{s.estimate.status === 'verified' ? 'verified' : 'estimate'}</span>}>Docks</SectionTitle>
      <button onClick={open} disabled={!st.data} data-testid="docks-row"
        className="flex w-full items-center gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5 text-left ring-1 ring-inset ring-line transition hover:bg-white/[0.06] hover:ring-line-2 disabled:cursor-default disabled:hover:bg-white/[0.03]">
        {thumb && thumbOk ? (
          <img src={thumb.src} alt={`Dock proof: Vexcel ${thumb.view}`} title={`Proof: Vexcel ${thumb.view} crop with the SAM3 door boxes`} onError={() => setThumbOk(false)}
            className="h-11 w-14 shrink-0 rounded-md object-cover ring-1 ring-inset ring-line" />
        ) : <Truck size={15} className="shrink-0 text-accent" />}
        <div className="min-w-0 flex-1">
          {st.loading ? <Skeleton className="h-4 w-48" /> : s ? (
            <>
              <div className="truncate text-[13px] tabular-nums text-ink">
                <span title="Dock doors reported by CoStar">{n(s.costar_docks)} CoStar</span>
                <span className="text-ink-3"> · </span><span title="Doors seen in Vexcel oblique photos">{n(s.detected_doors)} seen</span>
                <span className="text-ink-3"> · </span><span title="Trailers parked nose-out at the facade (occupied dock positions)">{n(s.docked_trailers)} trailers at doors</span>
              </div>
              <div className="truncate text-[11px] text-ink-3">
                {s.estimate?.value != null ? `Estimate ${s.estimate.value}${s.estimate.lo != null && s.estimate.hi != null ? ` (${s.estimate.lo}–${s.estimate.hi})` : ''}` : 'Estimate unknown'}
                {' · '}{confirmed ? `${confirmed} confirmed by a person` : 'none confirmed yet'}
              </div>
            </>
          ) : <div className="text-[12px] text-ink-3">Dock review isn't available for this property yet</div>}
        </div>
        {s && <span className="shrink-0 rounded-lg bg-accent/15 px-2.5 py-1 text-[12px] font-semibold text-accent ring-1 ring-inset ring-accent/30">Review</span>}
      </button>
    </div>
  )
}

function Hero({ photo, ortho, children }: { photo: string | null; ortho: string | null; children: ReactNode }) {
  const cands = [photo && { src: photo, source: 'costar', title: 'Photo: CoStar' }, ortho && { src: ortho, source: 'vexcel', title: 'Aerial: Vexcel' }]
    .filter(Boolean) as { src: string; source: string; title: string }[]
  const [i, setI] = useState(0)
  const cur = cands[i]
  return (
    <div className="relative h-[190px] shrink-0 overflow-hidden bg-panel-3">
      {cur && <img key={cur.src} src={cur.src} alt="" className="absolute inset-0 h-full w-full object-cover" onError={() => setI(i + 1)} />}
      <div className="absolute inset-0 bg-gradient-to-t from-panel via-panel/60 to-transparent" />
      {cur && <span className="absolute left-4 top-4 rounded bg-black/55 p-0.5 backdrop-blur"><SourceChip source={cur.source} title={cur.title} /></span>}
      <div className="absolute inset-x-0 bottom-0 p-4">{children}</div>
    </div>
  )
}

function Badge({ children, tone }: { children: ReactNode; tone?: 'accent' }) {
  return (
    <span className={clsx('inline-flex h-[22px] items-center gap-1 rounded-md px-2 text-[11px] font-medium backdrop-blur-md',
      tone === 'accent' ? 'bg-accent/20 text-accent' : 'bg-white/10 text-ink')}>
      {children}
    </span>
  )
}

function FitSummary({ result }: { result: NonNullable<ReturnType<typeof useStore.getState>['search']>['results'][number] }) {
  const c = result.counts
  return (
    <div className="rounded-xl bg-white/[0.03] p-3.5 ring-1 ring-inset ring-line">
      <div className="flex items-end justify-between">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Fit score · rank #{result.rank}</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-[30px] font-semibold leading-none tabular-nums">{result.score}</span>
            {result.band[1] > result.band[0] ? <span className="text-[12px] text-ink-3 tabular-nums">could be {result.band[0]}–{result.band[1]}</span>
              : c.fail + c.near + c.unknown === 0 && <span className="text-[12px] text-good">meets all {c.pass}</span>}
          </div>
        </div>
        <div className="flex gap-3 text-[12px] tabular-nums">
          <span className="text-good">✓ {c.pass}</span><span className="text-warn">~ {c.near}</span>
          <span className="text-bad">✗ {c.fail}</span><span className="text-ink-3">? {c.unknown}</span>
        </div>
      </div>
      {result.band[1] > result.band[0] && <div className="mt-3"><ScoreBand score={result.score} band={result.band} /></div>}
    </div>
  )
}

interface TileDef { key: string; label: string; icon: ReactNode; fmt: (v: unknown) => string; sub?: (w: WarehouseDetail) => string | null }
const TILES: TileDef[] = [
  { key: 'size_sqft', label: 'Building size', icon: <Ruler size={13} />, fmt: fmtSF, sub: (w) => (isNum(w.lot_acres) ? `${w.lot_acres} acre lot` : null) },
  { key: 'clear_height_ft', label: 'Clear height', icon: <Ruler size={13} className="rotate-90" />, fmt: fmtFt },
  { key: 'docks_total', label: 'Dock doors', icon: <Truck size={13} />, fmt: fmtInt, sub: () => null },
  { key: 'drive_ins', label: 'Drive-in doors', icon: <DoorOpen size={13} />, fmt: fmtInt, sub: (w) => (isNum(w.drive_in_h_ft) ? `${w.drive_in_w_ft ?? '?'} × ${w.drive_in_h_ft} ft` : null) },
  { key: 'power_amps', label: 'Power', icon: <Zap size={13} />, fmt: (v) => (isNum(v) ? `${fmtInt(v)} A` : '?'),
    sub: (w) => [w.power_volts ? `${w.power_volts} V` : null, w.power_phases ? `${w.power_phases}-phase` : null].filter(Boolean).join(' · ') || null },
  { key: 'year_built', label: 'Year built', icon: <Calendar size={13} />, fmt: (v) => (isNum(v) ? String(v) : '?') },
]

function Specs({ w, loading, error }: { w: WarehouseDetail | null; loading: boolean; error: boolean }) {
  if (loading || !w) {
    return (
      <div>
        <SectionTitle>Key specs</SectionTitle>
        {error ? <div className="text-[12px] text-ink-3">Specs could not be loaded.</div> : (
          <div className="grid grid-cols-2 gap-2">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-[76px]" />)}</div>
        )}
      </div>
    )
  }
  const listing = w.listings?.find((l) => l.status === 'Active') ?? null
  const rent = listing && (listing.rent_low_psf_yr ?? listing.rent_high_psf_yr)
  return (
    <div>
      <SectionTitle right={<span className="text-[11px] text-ink-3">source · confidence</span>}>Key specs</SectionTitle>
      <div className="grid grid-cols-2 gap-2">
        {TILES.map((t) => <SpecTile key={t.key} def={t} fact={w.facts[t.key]} w={w} />)}
        <Tile icon={<Tag size={13} />} label="Availability"
          value={listing ? (isNum(listing.available_sqft) ? fmtSF(listing.available_sqft) : AVAIL_LABEL[w.availability]) : AVAIL_LABEL[w.availability] ?? '?'}
          sub={listing ? `${listing.kind === 'sale' ? 'For sale' : 'For lease'}${rent ? ` · ${fmtRent(rent)}` : ''}` : w.availability === 'not_listed' ? 'No active listing' : 'Listing withdrawn'}
          source="costar" confidence={0.9} />
        <SpecTile def={{ key: 'roof_condition', label: 'Roof condition', icon: <Home size={13} />, fmt: (v) => (isNum(v) ? `${v} / 5` : '?') }}
          fact={w.facts.roof_condition} w={w} subOverride={w.facts.roof_condition?.detail?.replace(/^Vexcel 1-5 scale; /, '') ?? null} />
      </div>
    </div>
  )
}

function SpecTile({ def, fact, w, subOverride }: { def: TileDef; fact?: Fact; w: WarehouseDetail; subOverride?: string | null }) {
  const has = fact && fact.value !== null && fact.value !== undefined
  const altNum = fact?.alt?.filter((a) => isNum(a.value)) ?? []
  return (
    <Tile icon={def.icon} label={def.label} value={has ? def.fmt(fact!.value) : null}
      sub={subOverride ?? (has ? def.sub?.(w) ?? fact!.detail ?? null : null)} source={has ? fact!.source : null} confidence={has ? fact!.confidence : null}>
      {has && isNum(fact!.value) && altNum.length > 0 && (
        <AltBars main={{ value: fact!.value as number, source: fact!.source ?? 'costar' }} alts={altNum as { value: number; source: string }[]} fmt={def.fmt} />
      )}
    </Tile>
  )
}

function Tile({ icon, label, value, sub, source, confidence, children }: {
  icon: ReactNode; label: string; value: string | null; sub?: string | null; source?: string | null; confidence?: number | null; children?: ReactNode
}) {
  return (
    <div className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-inset ring-line">
      <div className="flex items-center gap-1.5 text-[11px] text-ink-3">{icon}{label}</div>
      <div className="mt-1.5 flex min-h-6 items-center">
        {value && value !== '?' ? <span className="text-[17px] font-semibold tabular-nums tracking-tight">{value}</span> : <Unknown why={UNKNOWN_WHY} />}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-ink-2" title={sub}>{sub}</div>}
      {children}
      {source && (
        <div className="mt-2 flex items-center justify-between"><SourceChip source={source} /><ConfidenceDots value={confidence} /></div>
      )}
    </div>
  )
}

function AltBars({ main, alts, fmt }: { main: { value: number; source: string }; alts: { value: number; source: string }[]; fmt: (v: unknown) => string }) {
  const rows = [main, ...alts]
  const max = Math.max(...rows.map((r) => r.value)) || 1
  const COLORS: Record<string, string> = { costar: 'bg-sky-400/70', matterport: 'bg-violet-400/80', vexcel: 'bg-amber-400/80' }
  return (
    <div className="mt-2 space-y-1" title="Reported (CoStar) vs measured">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5 text-[10px] text-ink-3">
          <span className="w-[58px] shrink-0 truncate">{r.source === 'matterport' ? 'Scan' : r.source === 'vexcel' ? 'Aerial' : 'CoStar'}</span>
          <div className="h-1.5 flex-1 rounded-full bg-white/[0.06]"><div className={clsx('h-full rounded-full', COLORS[r.source] ?? 'bg-ink-3')} style={{ width: `${(r.value / max) * 100}%` }} /></div>
          <span className="w-[42px] shrink-0 text-right tabular-nums text-ink-2">{fmt(r.value)}</span>
        </div>
      ))}
    </div>
  )
}

function FitChecklist({ checks }: { checks: NonNullable<ReturnType<typeof useStore.getState>['search']>['results'][number]['checks'] }) {
  return (
    <div>
      <SectionTitle>Fit checklist</SectionTitle>
      <div className="divide-y divide-line overflow-hidden rounded-xl ring-1 ring-inset ring-line">
        {checks.map((c) => (
          <div key={c.fact} className="flex items-center gap-3 bg-white/[0.02] px-3 py-2.5">
            <StatusIcon status={c.status} />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-ink">{c.label}{c.level === 'D' && <span className="ml-1.5 text-[10px] uppercase tracking-wide text-ink-3">must</span>}</div>
              <div className="text-[11px] text-ink-3">need {fmtNeed(c.need, c.unit)}</div>
            </div>
            <div className="text-right">
              <div className={clsx('text-[13px] font-medium tabular-nums', c.have == null ? 'text-ink-3' : 'text-ink')}
                title={c.have == null ? UNKNOWN_WHY : undefined}>{c.have == null ? '?' : fmtUnit(c.have, c.unit)}</div>
              {c.source && <div className="mt-0.5"><SourceChip source={c.source} /></div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Aerial({ id, twin, loading, error }: { id: number; twin: Twin | null; loading: boolean; error: unknown }) {
  const ext = twin?.exterior
  const obliques = Object.entries(twin?.media?.obliques ?? {})
  const [view, setViewRaw] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const req = useStore((s) => (s.aerialReq?.id === id ? s.aerialReq : null))
  const setView = (u: string | null, k?: string) => { setViewRaw(u); sendEvent('openAerial', { id, view: k ?? 'top', date: twin?.exterior?.imagery?.parcel_date }) }
  useEffect(() => {
    if (!req) return
    const want = (req.view ?? '').toLowerCase()
    const hit = obliques.find(([k]) => want && k.toLowerCase().includes(want.replace(/^oblique-?/, '')))
    setViewRaw(hit ? hit[1] : null)
    box.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req?.nonce, obliques.length])
  if (loading) return <div><SectionTitle>Aerial · Vexcel</SectionTitle><Skeleton className="aspect-square w-full" /></div>
  if (error || !ext || !twin?.media?.ortho) {
    return (
      <div>
        <SectionTitle>Aerial · Vexcel</SectionTitle>
        <div className="flex h-24 items-center justify-center gap-2 rounded-xl bg-white/[0.03] text-[12px] text-ink-3 ring-1 ring-inset ring-line">
          <Plane size={14} /> {error instanceof ApiError && error.status !== 404 ? 'Aerial analysis could not be loaded' : 'No aerial analysis for this property yet'}
        </div>
      </div>
    )
  }
  const h = ext.frame.half_m
  const b = ext.building
  const doors = ext.doors
  const court = [...(ext.courts ?? [])].sort((a, z) => (z.doors ?? 0) - (a.doors ?? 0))[0]
  const footprints = ((ext as { buildings?: { footprint_local?: [number, number][]; main?: boolean }[] }).buildings ?? [])
  const src = view ?? twin.media.ortho
  return (
    <div ref={box} className="scroll-mt-3">
      <SectionTitle right={<span className="flex items-center gap-1.5 text-[11px] text-ink-3"><SourceChip source="vexcel" />{ext.imagery?.parcel_date ?? ''}</span>}>
        Aerial
      </SectionTitle>
      <div className="relative aspect-square w-full overflow-hidden rounded-xl bg-panel-3 ring-1 ring-inset ring-line">
        <img src={src} alt="Aerial view" className="absolute inset-0 h-full w-full object-cover" />
        {!view && (
          <svg viewBox={`${-h} ${-h} ${2 * h} ${2 * h}`} className="absolute inset-0 h-full w-full">
            {footprints.map((f, i) => f.footprint_local && (
              <polygon key={i} points={f.footprint_local.map(([x, y]) => `${x},${-y}`).join(' ')}
                fill={f.main ? 'rgba(79,209,197,0.10)' : 'none'} stroke={f.main ? '#4fd1c5' : 'rgba(255,255,255,0.4)'}
                strokeWidth={h / 160} strokeDasharray={f.main ? undefined : `${h / 60} ${h / 80}`} />
            ))}
            {doors?.items.map((d, i) => (
              <g key={i}>
                <circle cx={d.x} cy={-d.y} r={h / 45} fill={d.class === 'drive_in' ? '#fbbf24' : '#4fd1c5'} stroke="#0a0d12" strokeWidth={h / 300} opacity={0.4 + 0.6 * d.confidence} />
              </g>
            ))}
          </svg>
        )}
        <div className="absolute bottom-2 left-2 flex gap-2 rounded-md bg-black/60 px-2 py-1 text-[10px] text-ink-2 backdrop-blur">
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-accent" />Dock door</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-warn" />Drive-in</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 border border-accent" />Building</span>
        </div>
      </div>
      {obliques.length > 0 && (
        <div className="mt-2 flex gap-1.5">
          <ObliqueThumb label="Top" src={twin.media.ortho!} active={!view} onClick={() => setView(null)} />
          {obliques.slice(0, 4).map(([k, u]) => <ObliqueThumb key={k} label={k.replace('oblique-', '')} src={u} active={view === u} onClick={() => setView(u, k)} />)}
        </div>
      )}
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Mini label="Roof" value={isNum(b?.roof_condition) ? `${b!.roof_condition}/5` : null} sub={b?.roof_material ?? undefined} />
        <Mini label="Doors seen" value={doors ? String(doors.total) : null} sub={doors ? `${doors.dock_high} dock · ${doors.drive_in} drive-in` : undefined} title={doors?.note} />
        <Mini label="Truck court" value={court && isNum(court.depth_ft) ? `${court.depth_ft} ft` : null}
          sub={court?.verdict_53ft?.replace(/\s*\(.*\)$/, '') ?? (court ? `${court.facing} side` : undefined)}
          title={court ? `${court.facing}-facing court, P10–P90 ${court.p10_ft ?? '?'}–${court.p90_ft ?? '?'} ft, ${court.confidence} confidence` : 'No truck court found in front of the doors'} />
      </div>
    </div>
  )
}

function ObliqueThumb({ label, src, active, onClick }: { label: string; src: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={clsx('relative h-12 flex-1 overflow-hidden rounded-lg ring-1 ring-inset', active ? 'ring-accent' : 'ring-line hover:ring-line-2')}>
      <img src={src} alt="" className="h-full w-full object-cover" />
      <span className="absolute inset-x-0 bottom-0 bg-black/60 text-center text-[9px] uppercase tracking-wide text-ink-2">{label}</span>
    </button>
  )
}

function Mini({ label, value, sub, title }: { label: string; value: string | null; sub?: string; title?: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2.5 py-2 ring-1 ring-inset ring-line" title={title}>
      <div className="text-[10px] text-ink-3">{label}</div>
      <div className="mt-0.5 text-[14px] font-semibold tabular-nums">{value ?? <span className="text-ink-3" title={UNKNOWN_WHY}>?</span>}</div>
      {sub && <div className="truncate text-[10px] text-ink-2">{sub}</div>}
    </div>
  )
}

function Inside({ w, twin, scan, onOpen, onMarkDoors }: { w: WarehouseDetail | null; twin: Twin | null; scan: Scan | null; onOpen: () => void; onMarkDoors: () => void }) {
  const scans = (twin?.scans?.length ? twin.scans : w?.scans) ?? []
  const interior = twin?.interior
  return (
    <div>
      <SectionTitle right={<SourceChip source="matterport" />}>Inside</SectionTitle>
      {!scan ? (
        <div className="flex h-16 items-center justify-center gap-2 rounded-xl bg-white/[0.03] text-[12px] text-ink-3 ring-1 ring-inset ring-line">
          <ScanLine size={14} /> No interior scan for this property
        </div>
      ) : (
        <button onClick={onOpen} className="group relative flex w-full items-center gap-3 overflow-hidden rounded-xl bg-gradient-to-br from-violet-500/15 to-accent/10 p-3.5 text-left ring-1 ring-inset ring-violet-400/25 transition hover:ring-violet-300/50">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-black/30 text-violet-200"><Box size={20} /></div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium">{scan.name ?? 'Interior scan'}</div>
            <div className="text-[11px] text-ink-2">
              {[scan.class === 'warehouse_floor' ? 'Warehouse floor' : titleCase(scan.class ?? 'scan'), isNum(scan.floor_sqft) ? fmtSF(scan.floor_sqft) : null,
                isNum(scan.height_ft) ? `${fmtFt(scan.height_ft)} high` : null].filter(Boolean).join(' · ')}
            </div>
            {interior && (() => {
              const clear = isNum(interior.clear) ? interior.clear : interior.clear?.p5_ft
              return (isNum(clear) || isNum(interior.deck_ft)) && (
                <div className="mt-0.5 text-[11px] text-violet-200/80" title="Measured from the scan: usable clear height (5th percentile under the deck) and deck height">
                  Measured clear {fmtFt(clear)} · deck {fmtFt(interior.deck_ft)}
                </div>
              )
            })()}
          </div>
          <span className="text-[12px] font-medium text-violet-200 group-hover:text-white">Open 3D →</span>
        </button>
      )}
      {scan && (
        <button onClick={onMarkDoors} title="Optional cross-check: click the two edges of each real dock door in the 3D scan."
          className="mt-2 flex w-full items-center gap-2.5 rounded-xl bg-white/[0.03] px-3 py-2.5 text-left ring-1 ring-inset ring-line transition hover:bg-white/[0.06] hover:ring-line-2">
          <PencilRuler size={15} className="shrink-0 text-warn" />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium">Verify doors in 3D <span className="text-[11px] font-normal text-ink-3">optional</span></div>
            <div className="text-[11px] text-ink-3">Docks are reviewed on the aerial (Docks above); the scan is a cross-check</div>
          </div>
          <span className="text-[12px] text-ink-2">→</span>
        </button>
      )}
      {scans.length > 1 && <div className="mt-1.5 text-[11px] text-ink-3">{scans.length} scans on file · showing the best warehouse-floor scan</div>}
    </div>
  )
}
