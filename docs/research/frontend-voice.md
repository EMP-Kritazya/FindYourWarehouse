# Front-end + voice stack: research memo (2026-11-14)

Scope: map with 110 pins, a parcel 3D scene, the interior, sim replay, and the voice orb. Backend is FastAPI + SSE.
Every version below was checked against the npm registry: it is the highest stable release published **before
2026-10-31**, so all are at least 2 weeks old. Pin them exactly (`--save-exact`). Newer releases exist
(maplibre 6.18, next 16.4.6, @elevenlabs/client 1.36), but they are too new to use.

## TL;DR decisions
1. **Map, parcel and outdoor sim = MapLibre GL JS 6 + deck.gl 9.4, interleaved on one WebGL2 canvas** through
   `@deck.gl/maplibre` (`MapLibreOverlay`), wrapped by `@vis.gl/react-maplibre`. Pins → fly-to → parcel is one camera
   move on one canvas (no context switch). The parcel scene itself is deck.gl layers: a single-image `TerrainLayer`
   (DSM + ortho), `PolygonLayer` footprints extruded to the DSM height, and overlays for docks and the swept path.
2. **Interior + indoor sim = React Three Fiber** on a second `<Canvas>` that cross-fades in over the map. It holds the
   Matterport mesh converted to meshopt GLB, with instanced vehicles. The Matterport Showcase iframe is a "photo tour"
   tab, not the sim surface.
3. **Basemap needs no key:** OpenFreeMap (`https://tiles.openfreemap.org/styles/dark`, `/liberty`, `/positron`, `/bright`,
   `/fiord`; all return 200 with no key). For satellite context, Esri World Imagery raster tiles (keyless, attribution
   required). Cesium/Google 3D Tiles and MapTiler/Mapbox all need keys, so skip them.
4. **Voice = our own pipeline, not ElevenLabs Agents.** The browser streams the mic straight to **Scribe v2 Realtime**
   with a single-use token minted by FastAPI. The committed transcript goes to the FastAPI Claude agent. Map tool calls go
   out as SSE events immediately, and the text is streamed into the ElevenLabs **TTS WebSocket** (flash model). Barge-in
   is handled client-side.
5. **App = Vite 8 + React 19 SPA**, Tailwind v4 + shadcn/ui. Not Next.js: the app is client-only WebGL, and Next adds
   SSR/`dynamic(ssr:false)` friction with nothing to gain. (The plan says Next.js. If the team insists, `next@16.4.3`
   works with every map and 3D component behind `dynamic(..., {ssr:false})`.)

## Exact package list
```
# app shell
vite@8.3.5  @vitejs/plugin-react@6.1.2  react@19.3.1  react-dom@19.3.1  typescript@5.9.3
@types/react@19.3.0  @types/react-dom@19.3.0
# map + geo 3D
maplibre-gl@6.16.0  @vis.gl/react-maplibre@8.1.3
deck.gl@9.4.1  @deck.gl/core@9.4.1  @deck.gl/layers@9.4.1  @deck.gl/geo-layers@9.4.1
@deck.gl/mesh-layers@9.4.1  @deck.gl/aggregation-layers@9.4.1  @deck.gl/maplibre@9.4.1  @deck.gl/react@9.4.1
@luma.gl/core@9.4.2  @loaders.gl/core@4.5.4  @loaders.gl/gltf@4.5.4
# interior 3D
three@0.187.0  @types/three@0.187.0  @react-three/fiber@9.8.2  @react-three/drei@10.7.9
@react-three/postprocessing@3.2.2
# Matterport embed
@matterport/sdk@1.6.2
# voice (browser)
@elevenlabs/client@1.32.1   # Scribe.connect (realtime STT); @elevenlabs/react@1.20.2 if you want useScribe
@ricky0123/vad-web@0.0.31   # optional local VAD for instant barge-in
# UI
tailwindcss@4.3.3  @tailwindcss/vite@4.3.3  shadcn@4.23.0 (CLI)  tw-animate-css@1.4.0
class-variance-authority@0.7.1  clsx@2.1.1  tailwind-merge@3.7.0  lucide-react@1.65.0
motion@14.9.0  cmdk@1.1.1  sonner@2.0.8  zustand@5.0.16  @tanstack/react-query@5.105.0
# asset tooling (dev / backend precompute)
obj2gltf@3.2.0  @gltf-transform/cli@4.5.1  gltfjsx@6.5.3
```
Peer dependencies check out: `@deck.gl/maplibre@9.4.1` accepts `maplibre-gl ^4.5.1||^5||^6`; R3F 9.8.2 needs
`react >=19 <19.4`; `@tailwindcss/vite@4.3.3` accepts vite ^8. Keep every `@deck.gl/*` on the same exact version
(`~9.4.0` peer). Avoid TypeScript 7.0.2: it is the new native compiler, and the ESLint/tsserver ecosystem lags, so
use 5.9.3. `threebox-plugin` was last published in 2022 and targets Mapbox v1/v2; drop it.

## 1. Map + 3D architecture
| Option | Verdict |
|---|---|
| MapLibre + deck.gl interleaved | **Pick.** Keyless, one GL context, `flyTo`, built-in pickable pins, glTF via `ScenegraphLayer`, DSM via `TerrainLayer`, trips and heatmaps all built in |
| react-map-gl | Same thing: `@vis.gl/react-maplibre` is react-map-gl v8's MapLibre-only entry. Use it for `<Map>` + `useControl` |
| R3F inside MapLibre (threebox / custom layer) | Camera-matrix sync is fiddly and threebox is dead. Use R3F only for the local-meter interior |
| CesiumJS (`cesium@1.146.0`) / Google Photorealistic 3D Tiles | Google tiles need a Maps key; Cesium World Terrain/Imagery need an Ion token. Heavy bundle. Skip |

```
App (zustand store: selectedId, mode: 'map'|'parcel'|'interior'|'sim', simTime)
├─ <MapStage> @vis.gl/react-maplibre <Map mapStyle=OFM dark maxPitch=85>
│    └─ <DeckOverlay interleaved layers=[pins, footprints, terrain, docks, sweptPath, trucks, trips, heat]>
├─ <InteriorStage> R3F <Canvas> (opacity 0→1 when mode==='interior'; map keeps running underneath, or pause)
│    └─ <Mesh glb/> <Instances vehicles/> <HeatPlane/> <OrbitControls/>
├─ <SearchBar+Chips> <ResultsRail> <FitCard> <Timeline> <VoiceOrb>  (shadcn, absolutely positioned)
└─ SSE client → dispatches agent actions: map.flyTo, pin.highlight, layer.show, card.open, sim.play
```
**Fly-to parcel:** `map.flyTo({center, zoom: 18.5, pitch: 60, bearing, duration: 2500, essential: true})`. On
`moveend`, switch to parcel mode by adding the parcel layers and fading the OFM building layer (`fill-extrusion-opacity`)
to 0 so it doesn't z-fight with our footprints. Basemap 3D buildings: the OFM `liberty` style has `building-3d`.
**Into the interior:** animate pitch/zoom toward the footprint, fade the roof (`PolygonLayer` opacity → 0), then
cross-fade (~400 ms) to the R3F canvas, whose camera starts at the same bearing. This is cheap and reads as "seamless".
(Full-fidelity alternative: put the interior GLB in deck.gl as a `ScenegraphLayer` with
`coordinateSystem: METER_OFFSETS` at the parcel origin. It is one canvas but has worse indoor camera controls.)

**Parcel layers (deck.gl):**
- `TerrainLayer` single-mesh mode: `elevationData: '/api/parcel/{id}/dsm.png'` (absolute URL, no `{x}{y}`),
  `texture: '/api/parcel/{id}/ortho.jpg'`, `bounds: [w,s,e,n]`, `meshMaxError: 0.5`. The backend encodes the DSM as
  Terrarium-style RGB: `elevationDecoder {rScaler:256, gScaler:1, bScaler:1/256, offset:-32768}`. Keep it ≤1024² px.
- Footprints: `PolygonLayer({extruded:true, getElevation: dsm-derived height, wireframe:true})`.
  Docks: `PolygonLayer`/`IconLayer` on the façade. Swept path: `PathLayer` + translucent `PolygonLayer`
  (green or red by pass/fail).
- Pins: `ScatterplotLayer` (radiusUnits 'pixels', stroked) + `TextLayer` for the ranking number. Hover/click uses
  `pickable:true`, `onClick`. At 110 points there is no need for clustering.

**Gotchas:** MapLibre 6 ships ESM only, so no UMD `<script>` tag; import `maplibre-gl/dist/maplibre-gl.css`. Interleaved
mode needs WebGL2, `interleaved` is fixed at construction, and only one interleaved overlay is allowed per map. deck
layers are **not draped** on MapLibre terrain, so don't enable MapLibre `terrain`: our DSM lives in deck. Use `beforeId`
to keep pins above the basemap labels. Use `useControl(() => new MapLibreOverlay({interleaved:true}))` and call
`overlay.setProps({layers})` on every render. Always render OSM/OpenFreeMap (and Esri, if used) attribution.

## 2. Vehicles + heatmap (sim replay)
- **The sim backend emits a columnar trace:** for each vehicle, `type`, `t[]` (s), `xy[]` in local meters, and `heading[]`.
  Ship it as a binary Float32Array (`/api/sim/{run}/trace.bin`) or compact JSON. Interpolate on the client (binary
  search plus lerp per vehicle per frame). 50–500 vehicles is trivial on the CPU; the cost is draw calls, not math.
- **Outdoor (yard, trucks):** `ScenegraphLayer` per model type (one layer per glb): `data = vehicles of that type`,
  `getPosition` / `getOrientation: [0, -headingDeg, 90]` (glTF is Y-up, so fix the axis with the roll of 90),
  `sizeScale` to meters, `_lighting:'pbr'`, and `updateTriggers: {getPosition: simTime}`. Use
  `coordinateSystem: COORDINATE_SYSTEM.METER_OFFSETS` + `coordinateOrigin: [lon,lat]` so the sim stays in meters.
  Trails: `TripsLayer` (`getPath`, `getTimestamps`, `currentTime: simTime`, `trailLength: 30`, `fadeTrail`).
  ScenegraphLayer instances the geometry per primitive. Keep the models under 20k tris and avoid skinned animation
  (`_animations` plays all instances in sync).
- **Indoor (forklifts, AMRs, reach trucks):** in R3F, use one `InstancedMesh` per vehicle type (drei `<Merged>` for
  multi-mesh GLBs). Write `instanceMatrix` inside `useFrame` from a ref and set `needsUpdate`. **Never put per-frame
  positions in React state or zustand subscriptions that re-render.** Fork height and lift can be a second instanced
  part, or skip them.
- **Heatmap:** the sim already has a 25 cm occupancy grid. Precompute a per-cell congestion value and render it as a
  texture: R3F `DataTexture` on a floor plane (indoor), or deck `BitmapLayer` (outdoor). This is deterministic and
  matches the KPI. Use deck `HeatmapLayer` (aggregation-layers) only for density-of-points looks. For a time-sliced heat,
  bake N frames into a texture array or just swap textures per bucket.
- **Timeline scrubber:** shadcn `Slider` bound to `simTime` in a ref, with rAF playback at 1–60× speed. Throttle the
  KPI-card updates to 4 Hz.

## 3. Matterport mesh + Showcase SDK
- Our backend already fetches the mesh URL via GraphQL `model{assets{meshes{format resolution url}}}`
  (`tools/matterport.py:mesh_obj`; 50k / 500k). URLs are signed and expire, so download the bytes once. Textures/MTL
  may be separate assets (`assets{textures{...}}`). Verify per model; fall back to the untextured mesh with vertex AO.
- **Convert offline, don't load OBJ in the browser:** `npx obj2gltf@3.2.0 -i model.obj -o model.glb` (picks up the
  `.mtl` and JPEGs next to it), then
  `npx @gltf-transform/cli@4.5.1 optimize model.glb model.opt.glb --compress meshopt --texture-compress webp --texture-size 2048`.
  A 500k-tri model typically drops 5–10x. Load with drei `useGLTF('/mesh/{id}.glb')`. Meshopt decoding is bundled
  (`three/examples/jsm/libs/meshopt_decoder.module.js`), which works offline. **Avoid Draco**: drei pulls its decoder from
  gstatic by default, which breaks `PROINSPECT_OFFLINE` unless you self-host `/draco/`.
- If you must load OBJ live: use `MTLLoader.setPath(dir).load('model.mtl')` → `materials.preload()` →
  `OBJLoader.setMaterials(m)`. Expect slow main-thread parsing (seconds at 500k).
- **Axes:** Matterport OBJ is Z-up meters. Wrap it in `<group rotation={[-Math.PI/2,0,0]}>`, then apply the
  scan↔footprint transform from `physics/register.py` (send it as a 4x4 from the API). Use `side: DoubleSide` and
  `castShadow={false}`, cut the ceiling with `material.clippingPlanes` (`gl.localClippingEnabled = true`) for a dollhouse view,
  and use `frustumCulled` defaults.
- **Showcase (photo tour):** `import { setupSdk } from '@matterport/sdk'`, then
  `const sdk = await setupSdk(SDK_KEY, {space: modelId, container: '#mp', iframeQueryParams: {qs:1, play:1, brand:0}})`.
  Raw alternative: import map `MP_SDK → https://api.matterport.com/sdk/bootstrap/3.1.0-0-g1cd7e0997a/sdk.es6.js?applicationKey=KEY`
  plus iframe `https://my.matterport.com/show?m=ID&play=1&applicationKey=KEY`, then `connect(iframe)`. The SDK key is
  **domain-bound**: add `localhost:5173` (the Vite port) and the demo host to the key's allow list. Serve over http(s),
  never `file://`. `sdk.Camera.pose.subscribe`, `sdk.Mode.moveTo`, `sdk.Mattertag`/Tag APIs, and `sdk.Sweep.moveTo`
  let the agent "walk" you to the lowest obstruction. The key is public-ish (it is in the iframe URL), and that's fine.

## 4. Free vehicle / warehouse models (verify each license on the page before shipping; credit CC-BY in an About panel)
| Need | Source | License | Link |
|---|---|---|---|
| Box truck, delivery van, truck (+ flat variants) | Kenney **Car Kit** (45 files, OBJ/FBX/glTF) | CC0 | https://kenney.nl/assets/car-kit |
| Conveyors, boxes, industrial props | Kenney **Conveyor Kit** (140 files) | CC0 | https://kenney.nl/assets/conveyor-kit |
| Forklift | "Forklift" by KolosStudios (Poly Pizza) | CC-BY 3.0 | https://poly.pizza/m/DTQBuenKJY |
| Pallet jack | "Pallet Truck" by KolosStudios (Poly Pizza) | CC-BY 3.0 | https://poly.pizza/m/snDq7v3GRO |
| Semi tractor | "Basic Semi-truck" by WilliWam (Poly Pizza; has decorative spikes, so strip them in Blender) | CC0 | https://poly.pizza/m/kGA4N3G7Jv |
| Cargo truck | "Cargo Truck" by J-Toastie | CC-BY 3.0 | https://poly.pizza/m/Fy3WI3uXNQ |
| Van | "Van" by Poly by Google | CC-BY 3.0 (Google Poly default) | https://poly.pizza/m/aT_24cDaW1a |
| Pallets | Quaternius / Kenney "Pallet" on Poly Pizza | CC0 | https://poly.pizza/m/cUAsYHDqfD , https://poly.pizza/m/J6bhnc2wFP |
| Shelving / rack | "Storage Shelf" by Jarlan Perez | CC-BY 3.0 | https://poly.pizza/m/6gKdASmfB9U |
| Low-poly forklift (PSX style) | scoppio itch.io pack | CC0 | https://scoppio.itch.io/forklift-chain-hook-and-barrel |
| More | Sketchfab search with the *Downloadable* + license filter (CC0 / CC-BY), queries "reach truck", "pallet racking", "AMR" | per model | https://sketchfab.com/search?features=downloadable&licenses=7c23a1ba438d4306920229c12afcb5f9&q=forklift (CC0 filter id; verify) |

**Procedural fallbacks (recommended for accuracy):** draw a 53 ft trailer as a box (16.15 × 2.6 × 4.1 m) attached to
the tractor glb with an articulation angle from the swept-path sim. Draw selective pallet racking as instanced boxes
and beams generated from the layout (exact bay dimensions matter more than looks). Draw the AMR as a rounded box plus a
lidar puck. Normalize every model in Blender or `gltf-transform` to real meters, origin at the rear axle, +X forward,
then `gltf-transform optimize --compress meshopt`. Reach trucks have no good free model: use the forklift with a
scaled mast, labelled "reach truck".

## 5. Voice: ElevenLabs
**Why not the Agents Platform (`@elevenlabs/react` `useConversation`):** (a) our key is scoped to TTS+STT, so
creating, configuring or signing agent sessions needs ConvAI permissions we don't have. (b) To make Claude the brain, you
must expose a **public OpenAI-compatible `/v1/chat/completions` SSE endpoint** (Custom LLM; it can't be used with
realtime models). That means ngrok, translating Anthropic to OpenAI format including tool calls, and map actions either
as ElevenLabs "client tools" defined in their dashboard or as a side channel. (c) Its turn-taking is good but opaque. It
would win on latency only if it used a native realtime model, which defeats "our agent is the brain".

**Our pipeline (lowest latency we control):**
```
mic ──(browser, @elevenlabs/client Scribe.connect, token)──► wss://api.elevenlabs.io/v1/speech-to-text/realtime
     partial_transcript → live caption in the orb; committed_transcript → POST /api/voice/turn {text, session}
FastAPI: Claude stream (tools: map.flyTo, search, open_card, run_sim…) ──SSE──► browser
     • tool_use blocks → emit `action` SSE events immediately (map moves while Claude is still talking)
     • text deltas → server-side TTS WebSocket (multi-stream-input, eleven_flash_v2_5) → audio chunks
       → SSE `audio` events (base64 PCM/mp3) or a /ws/voice socket → browser AudioWorklet/MediaSource queue
```
- **STT:** `POST /v1/single-use-token/realtime_scribe` from FastAPI (xi-api-key server-side; token TTL 15 min, single
  use). The browser calls `Scribe.connect({token, modelId:'scribe_v2_realtime', commitStrategy: VAD or MANUAL, microphone:{echoCancellation:true, noiseSuppression:true}})`.
  WS params: `audio_format=pcm_16000`, `commit_strategy=manual|vad|turn_prediction`, `vad_silence_threshold_secs`,
  `keyterms=[ "dock-high","clear height","reach truck","Burlington" ]` (boosts domain words). Use **manual commit +
  push-to-talk (hold Space / press the orb)** at the venue, because a noisy hall breaks VAD; keep `vad`/`turn_prediction`
  as a toggle. *Check that our scoped key can mint tokens*; if not, proxy the audio through a FastAPI WebSocket using the
  header key. This adds about 30–80 ms.
- **TTS:** the `wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/multi-stream-input?model_id=eleven_flash_v2_5&output_format=pcm_24000`
  socket stays open per session, with one context per reply. Send text deltas, `flush:true` at sentence ends, and
  `close_context` on interrupt. Limits: 5 contexts per connection, 20 s inactivity timeout, so keepalive. No eleven_v3 on
  multi-context. **No `voices_read`, so hardcode a premade voice_id** (pick one in the web UI, copy its id). Never call
  `/v1/voices`. Alternative: a `tts_websocket` single-use token lets the browser open the TTS socket itself, with text
  relayed from SSE. Prefer server-side, though: it gives one place to cancel.
- **Claude:** use `anthropic` (Python) `messages.stream`, model `claude-opus-5-5` with `output_config.effort:"low"` for the
  concierge turn (thinking is always on and effort is the latency knob). Optional `speed:"fast"` (Opus 5.5 fast mode,
  2x price) if TTFT/throughput is short. Set `eager_input_streaming:true` on map tools and validate the parsed input
  before acting. Put the tool list and system prompt first with `cache_control` so repeated turns hit the cache.
  Prompt for ≤2 spoken sentences and push detail to the UI cards. Speak a short filler from the client ("Pulling that
  up…") the moment the commit fires. Haiku 5.5 is the faster/cheaper option if the team chooses it.
- **Barge-in:** while audio plays, keep Scribe (or `@ricky0123/vad-web`) running. On speech start (or a partial with ≥2
  words that isn't echo), immediately (1) stop and clear the audio queue (`AudioBufferSourceNode.stop()`, reset the
  worklet ring buffer), (2) `POST /api/voice/cancel`, after which the server cancels the Claude stream task and sends
  `close_context` to TTS, (3) record the truncated assistant text as what was actually spoken in history. Keep the map
  actions already applied. Duck the TTS volume by 50% on a VAD blip before fully cancelling, to avoid false triggers.
  Echo cancellation only works when TTS plays through the same page's audio graph (it does).
- **Latency budget:** STT final ~150–300 ms after end of speech, Claude TTFT ~0.5–1 s at low effort, flash TTS first
  audio ~75–150 ms. Target ≤1.3 s to first audio; map actions often land sooner.

## 6. App framework + UI
- **Vite 8 SPA.** It has instant HMR with WebGL, no SSR hydration traps for `window`/WebGL2, a `server.proxy` to FastAPI
  (`/api` → :8000, so the SSE goes through the same origin and avoids CORS), and a static `dist/` for offline demo
  hosting from FastAPI `StaticFiles`. Next.js 16 brings nothing for a client-only app and needs `ssr:false` wrappers
  around every map/3D/SDK component.
- **UI:** Tailwind v4 (CSS-first `@import "tailwindcss"` + `@theme` tokens, `@tailwindcss/vite` plugin). Run shadcn CLI
  `npx shadcn@4.23.0 init` (it supports Vite and Tailwind v4), then add `command` (search bar via cmdk), `badge`/`toggle-group`
  (chips), `sheet` (results rail), `card`, `slider` (timeline), `tooltip`, `sonner`. Use a dark glass look:
  `bg-zinc-950/70 backdrop-blur-md border-white/10` over the OFM `dark` style. Use `motion` for the orb
  (amplitude-driven scale from an AnalyserNode). Render text over 3D with drei `<Html>` sparingly, since it's DOM-heavy.
- **State:** zustand for UI and mode, refs for per-frame sim data, react-query for REST. SSE uses the native `EventSource`
  (GET); for POST-initiated streams use `fetch` + `ReadableStream`.

## Risks
| Risk | Mitigation |
|---|---|
| OpenFreeMap public instance is down or slow at the demo | Pre-download HOU/LA tiles at z0–16 into a PMTiles file (protomaps) and serve it locally; keep the style JSON local |
| Matterport SDK key not allow-listed for localhost or the demo domain | Fix it on day 1; the R3F mesh viewer is the primary path anyway |
| Mesh textures missing or 500k mesh too heavy | Use 50k res + meshopt; texture-size 2048; fall back to the untextured mesh with AO |
| DSM/ortho misaligned with footprints | One `bounds` source of truth from the backend (EPSG:4326), and a visual check on hero 238885 |
| Scoped ElevenLabs key can't mint single-use tokens | Proxy STT via a FastAPI WS with the header key |
| Noisy venue breaks VAD / barge-in | Push-to-talk by default, VAD toggle, typed input always available |
| deck.gl + MapLibre minor-version drift | Exact pins above; same version for all `@deck.gl/*` |
| CC-BY attribution forgotten | Credits panel generated from an `assets/LICENSES.json` |

## Sources
deck.gl MapLibre integration: https://deck.gl/docs/api-reference/maplibre/overview ·
TerrainLayer: https://deck.gl/docs/api-reference/geo-layers/terrain-layer ·
ScenegraphLayer: https://deck.gl/docs/api-reference/mesh-layers/scenegraph-layer ·
OpenFreeMap: https://openfreemap.org/quick_start/ ·
Matterport Embed SDK: https://developers.matterport.com/embed-sdk/quickstart/ and the npm README of `@matterport/sdk` ·
Scribe realtime API: https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime ·
Scribe client streaming: https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming ·
Single-use tokens: https://elevenlabs.io/docs/api-reference/tokens/create ·
TTS multi-context WS: https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/multi-context-web-socket ·
Agents custom LLM: https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm ·
Kenney: https://kenney.nl/assets · Poly Pizza model pages linked above · npm registry (versions and dates).
