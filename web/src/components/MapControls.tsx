import { Plus, Minus, Rotate3d, LocateFixed } from 'lucide-react'
import { mapApi } from '../lib/mapApi'
import { useStore } from '../lib/store'
import { IconButton } from './ui'
import { useEffect, useState } from 'react'
import { basemapSource, onBasemap, type BasemapSource } from '../lib/basemap'

/** Zoom / tilt / recentre + attribution, docked to the right of the results rail (never under a panel). */
export default function MapControls() {
  const market = useStore((s) => s.market)
  const [src, setSrc] = useState<BasemapSource>(basemapSource())
  useEffect(() => onBasemap(setSrc), [])
  return (
    <div className="absolute bottom-4 left-[376px] z-10 flex flex-col items-start gap-2">
      <div className="glass flex flex-col rounded-xl p-1">
        <IconButton title="Zoom in" onClick={() => mapApi.zoomBy(1)}><Plus size={16} /></IconButton>
        <IconButton title="Zoom out" onClick={() => mapApi.zoomBy(-1)}><Minus size={16} /></IconButton>
        <div className="mx-1.5 my-0.5 h-px bg-line" />
        <IconButton title="Toggle 3D tilt" onClick={() => mapApi.togglePitch()}><Rotate3d size={16} /></IconButton>
        <IconButton title={`Recentre on ${market}`} onClick={() => mapApi.flyToMarket(market)}><LocateFixed size={16} /></IconButton>
      </div>
      <div className="max-w-[300px] text-[10px] leading-tight text-ink-2/80 [text-shadow:0_1px_2px_rgb(0_0_0/0.9)]">
        {src === 'vexcel' ? <span title="Aerial basemap: Vexcel Imaging, through the ProInspect tile proxy">Imagery © Vexcel Imaging · </span>
          : <span title="The Vexcel tile proxy is not serving yet: Esri World Imagery fallback">Imagery © Esri, Maxar, Earthstar Geographics · </span>}
        <a href="https://openfreemap.org" target="_blank" rel="noreferrer" className="hover:text-ink-2">© OpenFreeMap</a>{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="hover:text-ink-2">© OpenStreetMap</a>
      </div>
    </div>
  )
}
