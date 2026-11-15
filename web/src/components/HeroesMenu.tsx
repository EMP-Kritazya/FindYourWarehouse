import { useEffect, useRef, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { PlayCircle, Truck, Forklift, PencilRuler } from 'lucide-react'
import { api, type Hero, type Profile } from '../lib/api'
import { DEV_SIM, useStore, type TestKind } from '../lib/store'
import { bestScan, loadDetail, loadTwin } from './WarehouseCard'

/** The demo building (CONTRACTS v2): 3200 Sam Houston Pkwy, scan QHMtBN5Nnrc. */
export const DEMO_ID = 12896757
import { loadProfiles } from './SimPanel'

/** Presenter shortcut: the demo property card with the Test tab open (old shift replays only with the dev flag). */
export default function HeroesMenu() {
  const [heroes, setHeroes] = useState<Hero[]>([])
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [open, setOpen] = useState(false)
  const pinsById = useStore((s) => s.pinsById)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!DEV_SIM) return
    api.heroes().then((h) => setHeroes(h.filter((x) => x.demo_worthy)), () => {})
    loadProfiles().then(setProfiles, () => {})
  }, [])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  const demo = pinsById.get(DEMO_ID)
  const test = (k: TestKind | null) => { setOpen(false); useStore.getState().openTest(DEMO_ID, k) }
  const markDoors = () => {
    setOpen(false)
    Promise.all([loadDetail(DEMO_ID), loadTwin(DEMO_ID).catch(() => null)]).then(([w, t]) => {
      const scan = bestScan(w, t)
      if (scan) useStore.getState().openStage({ modelId: scan.model_id, title: w.address, subtitle: 'Mark dock doors', mode: 'markDoors', propertyId: DEMO_ID })
    }).catch(() => {})
  }
  const go = (h: Hero) => {
    setOpen(false)
    const s = useStore.getState()
    const pin = s.pinsById.get(h.property_id)
    s.select(h.property_id, { tab: 'simulate' })
    s.openStage({ modelId: h.model_id, sceneUrl: h.scene_url ?? `/api/sim/${h.run_id}/scene`, runId: h.run_id, propertyId: h.property_id,
      title: pin?.address ?? `Property ${h.property_id}`, subtitle: `${profiles[h.profile]?.name ?? h.profile.replace(/_/g, ' ')} replay` })
  }
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} title="Demo property: open its tests"
        className={clsx('flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] transition',
          open ? 'bg-white/10 text-ink' : 'text-ink-3 hover:bg-white/5 hover:text-ink-2')}>
        <PlayCircle size={14} /> Demos
      </button>
      {open && (
        <div className="glass fade-up absolute right-0 top-9 z-40 w-[288px] rounded-xl p-1.5">
          <div className="px-2.5 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Demo property</div>
          <button onClick={() => test(null)} className="flex w-full flex-col rounded-lg px-2.5 py-2 text-left hover:bg-white/[0.06]">
            <span className="text-[13px] font-medium text-ink">{demo?.address ?? '3200 Sam Houston Pkwy'}</span>
            <span className="text-[11px] text-ink-3">Open the card on the Test tab</span>
          </button>
          <DemoItem icon={<Truck size={13} className="text-accent" />} label="Yard test" sub="trucks in, turn, dock, queue" onClick={() => test('yard')} />
          <DemoItem icon={<Forklift size={13} className="text-violet-300" />} label="Equipment test" sub="machines through the building" onClick={() => test('equipment')} />
          <DemoItem icon={<PencilRuler size={13} className="text-warn" />} label="Mark doors" sub="in the Matterport scan" onClick={markDoors} />
          {heroes.length > 0 && <div className="mt-1 border-t border-line px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-3">Shift replays (dev)</div>}
          {heroes.map((h) => (
            <button key={h.run_id} onClick={() => go(h)}
              className="flex w-full flex-col rounded-lg px-2.5 py-2 text-left hover:bg-white/[0.06]">
              <span className="text-[13px] font-medium text-ink">{pinsById.get(h.property_id)?.address ?? `Property ${h.property_id}`}</span>
              <span className="text-[11px] text-ink-3">{profiles[h.profile]?.name ?? h.profile.replace(/_/g, ' ')}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function DemoItem({ icon, label, sub, onClick }: { icon: ReactNode; label: string; sub: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 pl-4 text-left hover:bg-white/[0.06]">
      {icon}<span className="text-[12px] text-ink">{label}</span><span className="truncate text-[11px] text-ink-3">{sub}</span>
    </button>
  )
}
