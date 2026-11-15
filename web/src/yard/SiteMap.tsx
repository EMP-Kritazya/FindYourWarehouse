// Shared aerial site map for the yard player and the dock editor: Vexcel tiles (via the backend proxy) on Esri,
// with a deck.gl overlay interleaved into MapLibre.
import { forwardRef, type ReactNode } from 'react'
import { Map, useControl, type MapProps, type MapRef } from '@vis.gl/react-maplibre'
import { MapLibreOverlay, type MapLibreOverlayProps } from '@deck.gl/maplibre'
import type { StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

export function DeckOverlay(props: MapLibreOverlayProps) {
  const overlay = useControl<MapLibreOverlay>(() => new MapLibreOverlay({ ...props, interleaved: true }))
  overlay.setProps(props)
  return null
}

export const SITE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    esri: { type: 'raster', tileSize: 256, maxzoom: 19, attribution: 'Esri, Maxar, Earthstar Geographics',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'] },
    vexcel: { type: 'raster', tileSize: 256, minzoom: 12, maxzoom: 21, attribution: 'Vexcel Imaging', tiles: [`${typeof location !== 'undefined' ? location.origin : ''}/api/tiles/{z}/{x}/{y}.jpg`] },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#0a0d12' } },
    { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-brightness-max': 0.8, 'raster-saturation': -0.3 } },
    { id: 'vexcel', type: 'raster', source: 'vexcel', paint: { 'raster-brightness-max': 0.8, 'raster-saturation': -0.25, 'raster-fade-duration': 200 } },
  ],
}

interface Props {
  /** [w, s, e, n] to frame on first render */
  bounds?: [number, number, number, number]
  center?: [number, number]
  zoom?: number
  overlay: MapLibreOverlayProps
  children?: ReactNode
  cursor?: string
  /** extra MapLibre props (mouse handlers etc.) */
  mapProps?: Partial<MapProps>
  padding?: { top: number; bottom: number; left: number; right: number }
}

export const SiteMap = forwardRef<MapRef, Props>(function SiteMap({ bounds, center, zoom = 17.6, overlay, children, cursor, mapProps, padding }, ref) {
  const key = bounds ? bounds.join(',') : center ? center.join(',') : 'none'
  if (!bounds && !center) return null
  return (
    <Map ref={ref} key={key} mapStyle={SITE_STYLE} maxZoom={21} attributionControl={false} dragRotate={false} pitchWithRotate={false}
      cursor={cursor} {...mapProps}
      initialViewState={bounds ? { bounds, fitBoundsOptions: { padding: padding ?? { top: 70, bottom: 90, left: 40, right: 40 } } } : { longitude: center![0], latitude: center![1], zoom }}
      style={{ position: 'absolute', inset: 0 }}>
      <DeckOverlay {...overlay} />
      {children}
    </Map>
  )
})

/** bbox of lon/lat points, padded by `padM` metres */
export function lonLatBounds(pts: [number, number][], padM = 15): [number, number, number, number] | undefined {
  if (!pts.length) return undefined
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity
  for (const [x, y] of pts) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y) }
  const lat = (s + n) / 2, kx = padM / (111320 * Math.cos((lat * Math.PI) / 180)), ky = padM / 110574
  return [w - kx, s - ky, e + kx, n + ky]
}

/** split a polyline into dash pieces (metres), for dashed lines without the deck.gl extension */
export function dashed(path: [number, number][], on = 2, off = 1.6): [number, number][][] {
  if (path.length < 2) return []
  const lat0 = path[0][1], kx = 111320 * Math.cos((lat0 * Math.PI) / 180), ky = 110574
  const out: [number, number][][] = []
  let cur: [number, number][] = [path[0]], drawing = true, left = on
  for (let i = 1; i < path.length; i++) {
    let [ax, ay] = path[i - 1]
    const [bx, by] = path[i]
    let segLen = Math.hypot((bx - ax) * kx, (by - ay) * ky)
    while (segLen > 1e-6) {
      const step = Math.min(left, segLen), f = step / segLen
      const px = ax + (bx - ax) * f, py = ay + (by - ay) * f
      if (drawing) cur.push([px, py])
      ax = px; ay = py; segLen -= step; left -= step
      if (left <= 1e-6) {
        if (drawing && cur.length > 1) out.push(cur)
        drawing = !drawing; left = drawing ? on : off; cur = [[ax, ay]]
      }
    }
  }
  if (drawing && cur.length > 1) out.push(cur)
  return out
}
