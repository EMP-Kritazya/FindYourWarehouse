import { useEffect, useRef, useState } from 'react'
import { MapPin, X } from 'lucide-react'
import { useStore } from '../lib/store'
import { mapApi } from '../lib/mapApi'
import { isNum } from '../lib/format'

/** showPlace: a labelled marker with a thumbnail card, kept glued to its lat/lon as the camera moves. */
export default function PlacePopup() {
  const place = useStore((s) => s.place)
  const setPlace = useStore((s) => s.setPlace)
  const ref = useRef<HTMLDivElement>(null)
  const [thumbOk, setThumbOk] = useState(true)
  useEffect(() => setThumbOk(true), [place?.thumb_url])

  useEffect(() => {
    if (!place) return
    let raf = 0
    let map = mapApi.get()
    const place1 = () => {
      map = map ?? mapApi.get()
      const el = ref.current
      if (!map || !el) { raf = requestAnimationFrame(place1); return }
      const p = map.project([place.lon, place.lat])
      const c = map.getContainer()
      const vis = p.x > -50 && p.y > -50 && p.x < c.clientWidth + 50 && p.y < c.clientHeight + 50
      // keep the card inside the visible map area (between the rail and the property card)
      const sel = useStore.getState().selectedId != null
      const lo = 376 + 8 + 110, hi = c.clientWidth - (sel ? 456 : 16) - 8 - 110
      const x = Math.min(Math.max(p.x, lo), Math.max(lo, hi))
      el.style.transform = `translate(${Math.round(x)}px, ${Math.round(p.y)}px) translate(-50%, calc(-100% - 16px))`
      const stem = el.querySelector<HTMLElement>('[data-stem]')
      if (stem) stem.style.transform = `translateX(${Math.round(p.x - x)}px)`
      el.style.opacity = vis ? '1' : '0'
    }
    const tick = () => { place1() }
    place1()
    const m = mapApi.get()
    m?.on('move', tick)
    m?.on('resize', tick)
    return () => { cancelAnimationFrame(raf); m?.off('move', tick); m?.off('resize', tick) }
  }, [place])

  if (!place) return null
  const dist = isNum(place.distance_m) ? (place.distance_m >= 1609 ? `${(place.distance_m / 1609.34).toFixed(1)} mi` : `${Math.round(place.distance_m * 3.281).toLocaleString()} ft`) : null
  return (
    <div ref={ref} className="pointer-events-auto absolute left-0 top-0 z-[18] w-[220px] opacity-0 transition-opacity duration-200">
      <div className="glass fade-up overflow-hidden rounded-xl">
        {place.thumb_url && thumbOk && (
          <img src={place.thumb_url} alt="" onError={() => setThumbOk(false)} className="h-[110px] w-full object-cover" />
        )}
        <div className="flex items-start gap-2 p-2.5">
          <MapPin size={14} className="mt-0.5 shrink-0 text-warn" />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium leading-tight text-ink">{place.label}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-2">
              {place.likelihood && <span className={place.likelihood === 'likely' ? 'rounded bg-good/15 px-1.5 text-good' : 'rounded bg-warn/15 px-1.5 text-warn'}
                title={isNum(place.score) ? `Vexcel image-embedding match, score ${place.score.toFixed(2)}. A match, not a confirmed place.` : 'Vexcel image-embedding match'}>{place.likelihood}</span>}
              {dist && <span className="tabular-nums">{dist} away</span>}
              <span className="inline-flex h-[16px] items-center rounded px-1 text-[9px] font-medium uppercase tracking-wide text-amber-300 ring-1 ring-inset ring-amber-400/25">Vexcel</span>
            </div>
          </div>
          <button onClick={() => setPlace(null)} title="Hide" className="rounded p-0.5 text-ink-3 hover:bg-white/10 hover:text-ink"><X size={13} /></button>
        </div>
      </div>
      <div data-stem className="mx-auto h-3 w-px bg-warn/80" />
    </div>
  )
}
