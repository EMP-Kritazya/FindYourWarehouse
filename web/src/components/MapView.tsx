import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Map, useControl, type MapRef } from '@vis.gl/react-maplibre'
import { MapLibreOverlay, type MapLibreOverlayProps } from '@deck.gl/maplibre'
import { TerrainLayer } from '@deck.gl/geo-layers'
import { BitmapLayer, ColumnLayer, LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import { loadTwin } from './WarehouseCard'
import type { PickingInfo } from '@deck.gl/core'
import type { StyleSpecification } from 'maplibre-gl'
import { attachVexcel, buildStyle } from '../lib/basemap'
import PlacePopup from './PlacePopup'
import { DockLegend, DockProofPopup, lastDockClick, useDockLayers } from '../docks/DockOverlay'
import { useStore } from '../lib/store'
import { mapApi, MARKET_VIEW } from '../lib/mapApi'
import type { Pin, Result } from '../lib/api'
import { AVAIL_LABEL, fmtFt, fmtKSF, prettyWhy } from '../lib/format'
import { PIN } from '../lib/theme'

function DeckOverlay(props: MapLibreOverlayProps) {
  const overlay = useControl<MapLibreOverlay>(() => new MapLibreOverlay({ ...props, interleaved: true }))
  overlay.setProps(props)
  return null
}

type RGBA = [number, number, number, number]
interface Datum { pin: Pin; result?: Result; color: RGBA; px: number; rank?: number; excluded: boolean }

const metresPerPixel = (lat: number, zoom: number) => (78271.517 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom

export default function MapView() {
  const pins = useStore((s) => s.pins)
  const market = useStore((s) => s.market)
  const search = useStore((s) => s.search)
  const selectedId = useStore((s) => s.selectedId)
  const hoveredId = useStore((s) => s.hoveredId)
  const highlightIds = useStore((s) => s.highlightIds)
  const compareIds = useStore((s) => s.compareIds)
  const select = useStore((s) => s.select)
  const hover = useStore((s) => s.hover)
  const shortlist = useStore((s) => s.shortlist)
  const place = useStore((s) => s.place)
  const ranking = useStore((s) => s.ranking)
  const mapRef = useRef<MapRef>(null)
  const [style, setStyle] = useState<StyleSpecification | null>(null)
  useEffect(() => { let live = true; buildStyle().then((st) => live && setStyle(st)); return () => { live = false } }, [])
  const tipRef = useRef<HTMLDivElement>(null)
  // zoom bucket (changes on zoomend only) keeps tower heights constant in screen pixels
  const [zoom, setZoom] = useState(MARKET_VIEW.Houston.zoom)
  // Vexcel ortho of the selected property, draped on the ground at its true footprint
  const [ortho, setOrtho] = useState<{ id: number; url: string; bounds: [number, number, number, number] } | null>(null)
  useEffect(() => {
    if (!selectedId) { setOrtho(null); return }
    let live = true
    loadTwin(selectedId).then((t) => {
      const f = t.exterior?.frame
      if (!live || !f || !t.media?.ortho) return
      const dLat = f.half_m / 111320, dLon = f.half_m / (111320 * Math.cos((f.lat0 * Math.PI) / 180))
      setOrtho({ id: selectedId, url: t.media.ortho, bounds: [f.lon0 - dLon, f.lat0 - dLat, f.lon0 + dLon, f.lat0 + dLat] })
    }).catch(() => {})
    return () => { live = false }
  }, [selectedId])

  // 3D site: Vexcel nDSM (DSM - DTM) heights with the Vexcel ortho draped on top (backend api_terrain.py)
  const [terrain, setTerrain] = useState<{ id: number; url: string; texture: string; bounds: [number, number, number, number] } | null>(null)
  useEffect(() => {
    if (!selectedId) { setTerrain(null); return }
    let live = true
    fetch(`/api/twins/${selectedId}/terrain`).then((r) => (r.ok ? r.json() : null))
      .then((t) => { if (live && t) setTerrain({ id: selectedId, url: t.url, texture: t.texture, bounds: t.bounds }) })
      .catch(() => {})
    return () => { live = false }
  }, [selectedId])
  const show3d = !!terrain && terrain.id === selectedId

  // the selected property's footprint, doors and gate (docks/DockOverlay); its tower hides so the building shows
  const dock = useDockLayers(selectedId)

  const rankOf = useMemo(() => {
    const m = new globalThis.Map<number, number>()
    ranking?.data?.rows.forEach((r, i) => m.set(r.id, i + 1))
    return m
  }, [ranking])

  const data: Datum[] = useMemo(() => {
    const byId = new globalThis.Map<number, Result>()
    search?.results.forEach((r) => byId.set(r.id, r))
    const excludedIds = new Set(search?.excluded.map((e) => e.id) ?? [])
    const ranked = search?.mode === 'ranked'
    return pins.filter((p) => p.market === market).map((pin) => {
      const result = byId.get(pin.id)
      const excluded = ranked && (excludedIds.has(pin.id) || !result)
      let color: RGBA
      if (ranked) {
        color = excluded ? PIN.excluded : result!.rank <= 5 ? PIN.top : result!.rank <= 15 ? PIN.mid : PIN.low
      } else {
        color = pin.availability === 'available' ? PIN.available : pin.availability === 'off_market_listing' ? PIN.offMarket : PIN.notListed
      }
      const sq = pin.rba_sqft ?? 20000
      const px = excluded ? 4 : 10 + 38 * Math.min(1, Math.sqrt(sq / 250000))
      const mr = rankOf.get(pin.id)
      if (rankOf.size) {
        // a metric ranking (rankBy) takes over the pin colours: ranked = accent, the rest dimmed
        color = mr ? (mr <= 5 ? PIN.top : PIN.mid) : PIN.excluded
        return { pin, result, color, px: mr ? 14 + 34 * (1 - (mr - 1) / Math.max(1, rankOf.size)) : 4, rank: mr, excluded: !mr }
      }
      return { pin, result, color, px, rank: ranked && !excluded ? result!.rank : undefined, excluded }
    })
  }, [pins, market, search, rankOf])

  const onHover = useCallback((info: PickingInfo<Datum>) => {
    const d = info.object
    hover(d ? d.pin.id : null)
    const el = tipRef.current
    if (!el) return
    if (!d) { el.style.opacity = '0'; return }
    el.style.opacity = '1'
    el.style.transform = `translate(${info.x + 14}px, ${info.y + 14}px)`
    const p = d.pin
    const why = d.excluded && search?.excluded.find((e) => e.id === p.id)?.why.map(prettyWhy)
    el.innerHTML = `<div class="font-medium text-[13px] text-ink">${p.address}</div>
      <div class="text-[12px] text-ink-2 mt-0.5">${fmtKSF(p.rba_sqft)} · ${fmtFt(p.clear_height_ft)} clear · ${AVAIL_LABEL[p.availability] ?? ''}</div>
      ${d.rank ? `<div class="text-[12px] text-accent mt-1">#${d.rank} · score ${d.result!.score}</div>` : ''}
      ${why && why.length ? `<div class="text-[12px] text-bad mt-1">Excluded: ${why[0]}</div>` : ''}
      ${p.inspectable ? '<div class="text-[11px] text-ink-3 mt-1">3D interior scan available</div>' : ''}`
    const c = mapRef.current?.getCanvas()
    if (c) c.style.cursor = 'pointer'
  }, [hover, search])

  const layers = useMemo(() => {
    const lat = MARKET_VIEW[market].center[1]
    const mpp = metresPerPixel(lat, zoom)
    const focus = new Set<number>([...highlightIds, ...compareIds])
    if (hoveredId) focus.add(hoveredId)
    const halo = data.filter((d) => d.pin.id === selectedId || focus.has(d.pin.id))
    const listed = new Set(shortlist)
    const starred = data.filter((d) => listed.has(d.pin.id))
    const selPin = selectedId ? data.find((d) => d.pin.id === selectedId)?.pin : undefined
    const near = place && (place.nearId ? data.find((d) => d.pin.id === place.nearId)?.pin : selPin)
    const ranked = data.filter((d) => d.rank && d.rank <= 20)
    return [
      show3d && new TerrainLayer({
        id: `vexcel-3d-${terrain!.id}`, elevationData: terrain!.url, texture: terrain!.texture, bounds: terrain!.bounds,
        elevationDecoder: { rScaler: 6553.6, gScaler: 25.6, bScaler: 0.1, offset: -10000 },
        meshMaxError: 0.6, color: [255, 255, 255],
      }),
      !show3d && ortho && ortho.id === selectedId && new BitmapLayer({
        id: 'vexcel-ortho', image: ortho.url, bounds: ortho.bounds, opacity: 0.95,
        transitions: { opacity: 600 },
      }),
      // dark puck under every tower so pins read on bright imagery
      new ScatterplotLayer<Datum>({
        id: 'pin-shadow', data, radiusUnits: 'pixels', getPosition: (d) => [d.pin.lon, d.pin.lat],
        getRadius: (d) => (d.excluded ? 5 : 10), getFillColor: [6, 9, 14, 150],
        updateTriggers: { getRadius: [search, rankOf] },
      }),
      new ScatterplotLayer<Datum>({
        id: 'pin-base', data, pickable: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels', stroked: true,
        getPosition: (d) => [d.pin.lon, d.pin.lat], getRadius: (d) => (d.excluded ? 3.5 : 7),
        getFillColor: (d) => [d.color[0], d.color[1], d.color[2], Math.round(d.color[3] * 0.55)],
        getLineColor: (d) => (d.pin.inspectable && !d.excluded ? [240, 244, 250, 235] : [6, 9, 14, 200]),
        getLineWidth: 1.5, onHover, onClick: (i) => i.object && select(i.object.pin.id),
        updateTriggers: { getFillColor: [search, rankOf], getLineColor: [search, rankOf], getRadius: [search, rankOf] },
        transitions: { getFillColor: 400, getRadius: 400 },
      }),
      new ColumnLayer<Datum>({
        id: 'pin-towers', data: dock.hideTowerId != null ? data.filter((d) => d.pin.id !== dock.hideTowerId) : data, pickable: true, extruded: true, diskResolution: 12, radiusUnits: 'pixels', radius: 5,
        getPosition: (d) => [d.pin.lon, d.pin.lat], getElevation: (d) => d.px * mpp,
        getFillColor: (d) => d.color, material: { ambient: 0.55, diffuse: 0.6, shininess: 32, specularColor: [60, 64, 70] },
        onHover, onClick: (i) => i.object && select(i.object.pin.id),
        updateTriggers: { getElevation: [zoom, search, rankOf], getFillColor: [search, rankOf] },
        transitions: { getElevation: 500, getFillColor: 400 },
      }),
      new ScatterplotLayer<Datum>({
        id: 'pin-halo', data: halo, radiusUnits: 'pixels', lineWidthUnits: 'pixels', stroked: true, filled: true,
        getPosition: (d) => [d.pin.lon, d.pin.lat], getRadius: (d) => (d.pin.id === selectedId ? 16 : 12),
        getFillColor: [79, 209, 197, 40], getLineColor: (d) => (d.pin.id === selectedId ? [79, 209, 197, 255] : [226, 232, 240, 200]),
        getLineWidth: 2, updateTriggers: { getRadius: [selectedId], getLineColor: [selectedId] },
      }),
      new ScatterplotLayer<Datum>({
        id: 'pin-shortlist', data: starred, radiusUnits: 'pixels', lineWidthUnits: 'pixels', stroked: true, filled: false,
        getPosition: (d) => [d.pin.lon, d.pin.lat], getRadius: 11, getLineColor: [251, 191, 36, 235], getLineWidth: 2,
      }),
      // docks are drawn through the 3D site surface (x-ray), so walls and roofs never hide them
      ...dock.layers.map((l) => (l ? l.clone({ parameters: { depthCompare: "always", depthWriteEnabled: false } }) : l)),
      place && near && new LineLayer({
        id: 'place-link', data: [{ a: [near.lon, near.lat], b: [place.lon, place.lat] }], getSourcePosition: (d: { a: number[] }) => d.a as [number, number],
        getTargetPosition: (d: { b: number[] }) => d.b as [number, number], getColor: [251, 191, 36, 200], getWidth: 2, widthUnits: 'pixels',
      }),
      place && new ScatterplotLayer({
        id: 'place-dot', data: [place], radiusUnits: 'pixels', lineWidthUnits: 'pixels', stroked: true,
        getPosition: (d: { lon: number; lat: number }) => [d.lon, d.lat], getRadius: 9, getFillColor: [251, 191, 36, 230],
        getLineColor: [10, 13, 18, 255], getLineWidth: 2.5,
      }),
      new TextLayer<Datum>({
        id: 'pin-rank', data: ranked, getPosition: (d) => [d.pin.lon, d.pin.lat, d.px * mpp + 4 * mpp],
        getText: (d) => String(d.rank), getSize: 12, sizeUnits: 'pixels', fontFamily: 'Inter, system-ui, sans-serif',
        fontWeight: 600, getColor: [10, 14, 20, 255], background: true, getBackgroundColor: (d) => d.color,
        backgroundPadding: [5, 2, 5, 2], getPixelOffset: [0, -8], characterSet: '0123456789',
        updateTriggers: { getPosition: [zoom] },
      }),
    ]
  }, [data, zoom, market, selectedId, hoveredId, highlightIds, compareIds, onHover, select, search, ortho, terrain, show3d, shortlist, place, dock])

  useEffect(() => () => mapApi.attach(null), [])

  return (
    <div className="absolute inset-0">
      {style && <Map
        ref={mapRef}
        initialViewState={{ longitude: MARKET_VIEW.Houston.center[0], latitude: MARKET_VIEW.Houston.center[1],
          zoom: MARKET_VIEW.Houston.zoom, pitch: 45, bearing: -12 }}
        mapStyle={style}
        maxPitch={75}
        attributionControl={false}
        onLoad={(e) => { mapApi.attach(e.target); attachVexcel(e.target); mapApi.flyToMarket(useStore.getState().market) }}
        onZoomEnd={(e) => setZoom(Math.round(e.target.getZoom() * 4) / 4)}
        onMouseOut={() => { if (tipRef.current) tipRef.current.style.opacity = '0' }}
        onClick={(e) => {
          if (e.defaultPrevented || useStore.getState().hoveredId) return
          // a click on a door (deck.gl) also reaches the map: let the door popup win
          setTimeout(() => { if (Date.now() - lastDockClick > 400) select(null) }, 0)
        }}
        style={{ position: 'absolute', inset: 0 }}
      >
        <DeckOverlay layers={layers} onHover={(i: PickingInfo) => { if (!i.object) { onHover(i as PickingInfo<Datum>); const c = mapRef.current?.getCanvas(); if (c) c.style.cursor = '' } }} />
      </Map>}
      <PlacePopup />
      <DockProofPopup />
      <DockLegend />
      <div ref={tipRef} className="pointer-events-none absolute left-0 top-0 z-30 max-w-72 rounded-lg border border-line bg-panel/95 px-3 py-2 shadow-xl opacity-0 transition-opacity duration-100" />
    </div>
  )
}
