// Basemap: aerial imagery with a light label/road layer on top. No vector building fills or extrusions.
//  - Vexcel aerial tiles through the backend proxy /api/tiles/{z}/{x}/{y}.jpg (S1; the key never reaches the browser).
//  - Esri World Imagery (keyless, attributed) underneath: it shows through wherever a Vexcel tile is missing or 4xx,
//    and is the whole basemap while the proxy is not deployed.
//  - OpenFreeMap (OpenMapTiles schema) for place / road / water labels and major-road lines only.
import type { Map as MLMap, StyleSpecification, LayerSpecification } from 'maplibre-gl'

const OFM_STYLE = 'https://tiles.openfreemap.org/styles/dark'
export const VEXCEL_TILES = '/api/tiles/{z}/{x}/{y}.jpg'
const ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

// subtle darkening so pins and labels read; lighter when zoomed in on a parcel
const IMAGERY_PAINT = {
  'raster-brightness-max': ['interpolate', ['linear'], ['zoom'], 8, 0.62, 13, 0.72, 16, 0.86] as unknown as number,
  'raster-saturation': -0.25,
  'raster-contrast': 0.06,
  'raster-fade-duration': 250,
}

const KEEP_SYMBOLS = /^(place_|water_name|highway_name_)/
const KEEP_LINES = /^(highway_motorway_inner|highway_motorway_subtle|highway_major_inner|highway_major_subtle)$/

function labelLayers(ofm: StyleSpecification): LayerSpecification[] {
  const out: LayerSpecification[] = []
  for (const l of ofm.layers) {
    if (l.type === 'symbol' && KEEP_SYMBOLS.test(l.id)) {
      const isPlace = l.id.startsWith('place_')
      out.push({
        ...l,
        paint: {
          ...(l.paint ?? {}),
          'text-color': isPlace ? '#f4f7fb' : l.id.startsWith('water') ? '#bcd7f2' : '#e6ebf2',
          'text-halo-color': 'rgba(6,9,14,0.85)',
          'text-halo-width': isPlace ? 1.6 : 1.3,
          'text-halo-blur': 0.4,
        },
      } as LayerSpecification)
    } else if (l.type === 'line' && KEEP_LINES.test(l.id)) {
      // major roads only as a faint guide at city zoom; the imagery shows them once you're close
      const motorway = l.id.includes('motorway')
      out.push({
        ...l,
        paint: {
          'line-color': motorway ? '#f3c77a' : '#e8edf5',
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 7, motorway ? 0.45 : 0, 11, motorway ? 0.38 : 0.16, 14, motorway ? 0.16 : 0.06, 15.5, 0],
          'line-width': ['interpolate', ['exponential', 1.4], ['zoom'], 7, motorway ? 0.8 : 0.4, 12, motorway ? 1.6 : 0.8, 15, motorway ? 3 : 1.5],
        },
      } as LayerSpecification)
    }
  }
  return out
}

export async function buildStyle(): Promise<StyleSpecification> {
  let ofm: StyleSpecification | null = null
  try { ofm = await (await fetch(OFM_STYLE)).json() } catch { /* offline: imagery only */ }
  const vector = ofm ? Object.entries(ofm.sources).find(([, s]) => s.type === 'vector') : undefined
  return {
    version: 8,
    glyphs: ofm?.glyphs ?? 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sprite: ofm?.sprite,
    sources: {
      esri: { type: 'raster', tiles: [ESRI_TILES], tileSize: 256, maxzoom: 19,
        attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' },
      ...(vector ? { [vector[0]]: vector[1] } : {}),
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#0a0d12' } },
      { id: 'esri', type: 'raster', source: 'esri', paint: IMAGERY_PAINT } as LayerSpecification,
      ...(ofm ? labelLayers(ofm) : []),
    ],
  } as StyleSpecification
}

export type BasemapSource = 'vexcel' | 'esri'
let current: BasemapSource = 'esri'
const listeners = new Set<(s: BasemapSource) => void>()
export const basemapSource = () => current
export function onBasemap(fn: (s: BasemapSource) => void) { listeners.add(fn); return () => { listeners.delete(fn) } }
const setSource = (s: BasemapSource) => { current = s; listeners.forEach((f) => f(s)) }

/** Probe the Vexcel proxy; add it above Esri when it serves tiles, retry while it is not deployed. */
export function attachVexcel(map: MLMap) {
  let stopped = false
  let errors = 0
  const probe = async () => {
    if (stopped || map.getSource('vexcel')) return
    let ok = false
    try {
      // a Houston tile at z12: any 2xx image means the proxy is up
      const r = await fetch('/api/tiles/12/963/1688.jpg')
      ok = r.ok && (r.headers.get('content-type') ?? '').startsWith('image')
    } catch { ok = false }
    if (stopped) return
    if (!ok) { setTimeout(probe, 30000); return }
    if (map.getSource('vexcel')) return
    map.addSource('vexcel', { type: 'raster', tiles: [VEXCEL_TILES], tileSize: 256, minzoom: 8, maxzoom: 20,
      attribution: 'Aerial imagery © Vexcel Imaging' })
    const firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol' || l.type === 'line')?.id
    map.addLayer({ id: 'vexcel', type: 'raster', source: 'vexcel', paint: IMAGERY_PAINT } as LayerSpecification, firstLabel)
    setSource('vexcel')
  }
  // a run of failing Vexcel tiles (4xx/5xx) → drop back to Esri until the next probe
  const onErr = (e: { sourceId?: string }) => {
    if (e.sourceId !== 'vexcel') return
    if (++errors < 25) return
    errors = 0
    if (map.getLayer('vexcel')) map.removeLayer('vexcel')
    if (map.getSource('vexcel')) map.removeSource('vexcel')
    setSource('esri')
    setTimeout(probe, 60000)
  }
  map.on('error', onErr as never)
  probe()
  return () => { stopped = true; map.off('error', onErr as never) }
}
