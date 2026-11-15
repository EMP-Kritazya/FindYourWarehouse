// Imperative camera control shared by the store, the voice handler and the map. Kept outside React state.
import type { Map as MLMap } from 'maplibre-gl'
import type { Market } from './api'

export const MARKET_VIEW: Record<Market, { center: [number, number]; zoom: number }> = {
  Houston: { center: [-95.40, 29.80], zoom: 9.6 },
  'Los Angeles': { center: [-118.27, 34.03], zoom: 10.3 },
}

let map: MLMap | null = null
let locked = false
const HANDLERS = ['dragRotate', 'dragPan', 'scrollZoom', 'boxZoom', 'doubleClickZoom', 'keyboard', 'touchZoomRotate', 'touchPitch'] as const
/** pixels of the viewport covered by floating panels, so the camera centres in the visible map area */
let padding = { top: 150, bottom: 110, left: 380, right: 40 }

export const mapApi = {
  attach(m: MLMap | null) { map = m; if (m && locked) mapApi.setLocked(true) },
  /** While the dock editor or the yard view covers the map, the main map must not rotate, pan or zoom under it. */
  setLocked(on: boolean) {
    locked = on
    if (!map) return
    for (const h of HANDLERS) { const x = (map as unknown as Record<string, { enable(): void; disable(): void } | undefined>)[h]; if (x) { if (on) x.disable(); else x.enable() } }
    map.getContainer().style.pointerEvents = on ? 'none' : ''
  },
  isLocked() { return locked },
  /** frame a footprint (lon/lat points) in the visible map area, looking down at a gentle pitch */
  fitFootprint(points: [number, number][], pitch = 25) {
    if (!map || !points.length) return
    let w = 180, s = 90, e = -180, n = -90
    for (const [x, y] of points) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y) }
    const want = { top: padding.top + 60, bottom: padding.bottom + 40, left: padding.left + 40, right: padding.right + 40 }
    const cur = map.getPadding()
    const pad = { top: Math.max(0, want.top - (cur.top ?? 0)), bottom: Math.max(0, want.bottom - (cur.bottom ?? 0)),
      left: Math.max(0, want.left - (cur.left ?? 0)), right: Math.max(0, want.right - (cur.right ?? 0)) }
    map.fitBounds([[w, s], [e, n]], { padding: pad, maxZoom: 19, pitch, bearing: 0, duration: 1200, essential: true })
  },
  get() { return map },
  setPadding(p: Partial<typeof padding>) { padding = { ...padding, ...p } },
  flyToMarket(m: Market) {
    map?.flyTo({ ...MARKET_VIEW[m], pitch: 45, bearing: -12, duration: 2200, essential: true, padding })
  },
  flyToPin(p: { lon: number; lat: number }) {
    if (!map) return
    // long, eased arc from city zoom down onto the parcel (the Vexcel parcel ortho is draped there)
    const far = map.getZoom() < 13
    map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 16.4), pitch: 30, bearing: 0,
      duration: far ? 3200 : 1600, curve: 1.5, essential: true, padding,
      easing: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2) })
  },
  fitTo(points: [number, number][], maxZoom = 13) {
    if (!map || !points.length) return
    if (points.length === 1) { mapApi.flyToPin({ lon: points[0][0], lat: points[0][1] }); return }
    let w = 180, s = 90, e = -180, n = -90
    for (const [x, y] of points) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y) }
    // flyTo's `padding` persists on the map and fitBounds ADDS its own padding on top of it, so pass only the
    // difference: the rail (left), the drawer when open (right) and the search bar + chips (top) stay clear.
    const want = { top: padding.top + 90, bottom: padding.bottom + 30, left: padding.left + 30, right: padding.right + 30 }
    const cur = map.getPadding()
    const pad = { top: Math.max(0, want.top - (cur.top ?? 0)), bottom: Math.max(0, want.bottom - (cur.bottom ?? 0)),
      left: Math.max(0, want.left - (cur.left ?? 0)), right: Math.max(0, want.right - (cur.right ?? 0)) }
    map.fitBounds([[w, s], [e, n]], { padding: pad, maxZoom, pitch: 45, bearing: -12, duration: 1800, essential: true })
  },
  flyToPoint(lon: number, lat: number, zoom = 15.2) {
    map?.flyTo({ center: [lon, lat], zoom, pitch: 45, bearing: -12, duration: 1800, essential: true, padding })
  },
  zoomBy(d: number) { if (map) map.easeTo({ zoom: map.getZoom() + d, duration: 300 }) },
  togglePitch() { if (map) map.easeTo({ pitch: map.getPitch() > 20 ? 0 : 50, bearing: map.getPitch() > 20 ? 0 : -15, duration: 600 }) },
}
