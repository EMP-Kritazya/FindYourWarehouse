# Matterport stage (stream A) — verified 2026-11-14

Self-hosted **Bundle SDK 26.10.4** (`web/public/bundle/`, served at `/bundle/showcase.html`) on `localhost:8080`.
All results below come from headless Chromium (SwiftShader) runs against scan **QHMtBN5Nnrc** (property 12896757).

## 1. Coordinate mapping (VERIFIED, exact)

Model API (`model_overview(model)['pano_positions']`, frame: +X, +Y, +Z up) vs SDK `sdk.Sweep.data` positions:

```
Showcase (x, y, z) = (X, Z, -Y)
```

- 51/51 panos, max abs error **0.0 m** on every axis (least-squares affine fit returns exactly
  `[[1,0,0],[0,0,-1],[0,1,0]]`, zero offset, residual 4e-14).
- **Ids match:** the Model API pano id == SDK sweep `sid` (== the `Sweep.data` key, == `Model.getData().sweeps[].uuid`).
  The `uuid` field inside `Sweep.data` entries is a different hex id — don't use it.
- **Yaw:** a heading angle `a = atan2(dY, dX)` in the Model frame (CCW about +Z from +X) is **the same number** as
  three.js `rotation.y` in Showcase (Ry(a)·(1,0,0) = (cos a, 0, −sin a) = mapped (cos a, sin a, 0)). So `yaw` in the
  scene JSON = model-frame heading in radians, no sign flip.
- **Floor:** interior twin floor `z0 = −0.028` (mesh frame) → `floor_y = −0.028`; sweeps sit at y ≈ 1.23–1.32
  (camera ≈ 1.3 m above floor), consistent with the mesh and pano frames sharing the vertical axis.
  [U] The mesh OBJ frame == pano frame in X/Y was not independently checked (fixture overlay lands where the
  sweep grid predicts, which is suggestive, not proof).
- QHMtBN5Nnrc layout in Showcase coords: warehouse floor sweeps on a ~15 m grid at x ∈ [−70, −3], z ∈ [9, 54];
  the office cluster is around x ∈ [−6, 12], z ∈ [0, 14] (start sweep `k84h6amdq64qh527r6gzta63c` at (0.09, 1.30, 0.11)).
  Real dock doors are on the wall near z ≈ 56.

## 2. Moving objects inside Showcase (VERIFIED)

- Custom components **do** run `onInit` / `onTick` (~every frame, `tickDelta` in ms) when registered with
  `sdk.Scene.register(name, factory)` **provided the component declares at least one input and `addComponent` is
  given initial inputs**. With `inputs: {}` and `addComponent(name)` the SDK throws `t.onChanged is not a function`
  and `onInit` never runs — that was the earlier failure.
- Mutating three.js objects under `outputs.objectRoot` inside `onTick` re-renders every frame (no manual invalidation).
  Before/after screenshots show vehicles at different positions.
- **Cross-realm gotcha:** Showcase's three.js (r184, `this.context.three` / `sdk.Scene.THREE`) lives in the iframe.
  Typed arrays created in the parent window fail its `instanceof` checks
  ("Unsupported buffer data format"): build attributes with `new T.Float32BufferAttribute(plainArray, 3)` and create
  canvases with the iframe's `document` (`this.context.renderer.domElement.ownerDocument`).
- Lighting: everything uses `MeshBasicMaterial` / `LineBasicMaterial` / sprites (unlit, exact colours), so no
  `mp.lights` is needed. GLB vehicles were not used (coloured boxes sized from `dims`).
- `sdk.R3F.registerR3F({onFrame,...})` (hidden/experimental) also exposes `{renderer, scene, camera}` and an
  every-frame callback — a fallback if the component path ever breaks.

## 3. Camera

- `Mode.moveTo(DOLLHOUSE, {position, rotation:{x:pitch°, y:yaw°}, transition})` places the dollhouse camera exactly
  (pose read back equals the request). Yaw 0 looks along −z, +yaw turns toward −x, negative pitch looks down.
  `TransitionType.INSTANT` moves resolve in ~10–200 ms, so "follow vehicle" re-issues an INSTANT dollhouse move
  every 100 ms (serialised; overlapping camera moves get rejected).
- Floor plan: `Mode.moveTo(FLOORPLAN, {position:{x,0,z}, rotation:{x:-90,y:0}})`.
- Walk: `Sweep.moveTo(sid, {rotation, transition: FLY, transitionTime})` to the nearest sweep ≥ 4 m from the target
  that is not inside a rack footprint, looking at the target. This is the money shot: the real panorama with racks,
  lanes, heatmap and moving trucks composited into it.

## 4. Component (`web/src/matterport/`)

- `MatterportStage.tsx` — props per CONTRACTS.md plus optional `scene?: SimScene` (object instead of `sceneUrl`).
  Overlay: legend, Overview / Floor plan / Walk / Follow-vehicle, play/pause, scrub, speed 1/5/10/30/60×, labels.
  Playback loops at `duration_s`.
- `stage.ts` — `StageController` (connect, register `pi.simstage`, playback clock, camera presets).
  Debug handle: `window.__piStage`.
- `sceneRenderer.ts` — builds the scene: racks = one InstancedMesh + merged edge lines; lanes and heat tiles =
  InstancedMesh; doors = panels + labels (cyan = vexcel, amber "?" = assumed; brighter while a truck is docked,
  `trucks[].door` is an index into `doors`); vehicles = box + mast (front = local +x) + pallet when
  `state` is loaded/drop + label. Colours: forklift orange, reach/narrow-aisle blue, pallet jack/tugger green,
  robots purple. All geometries/materials/textures are disposed on scene change and unmount.
- `fixture-scene.json` — **SYNTHETIC** test scene (`synthetic: true`) on QHMtBN5Nnrc's warehouse floor.
- `dev.html` / `dev.tsx` — harness: `http://localhost:8080/src/matterport/dev.html` (key from `/api/config`, or `?k=`;
  `?scene=<url>` to load a real scene; `&play=1&speed=30`).

Performance (SwiftShader, i.e. CPU rendering, worst case): 26 frames/s with the fixture, 18 frames/s with 60 vehicles
+ 300 racks; scene rebuild 6 ms. A real GPU will be much faster.

## Limitations

- Doors have no width in the contract; drawn as 2.7×3.0 m (dock) or 3.7×4.3 m (drive-in) panels.
- Trucks/trailers are not drawn (only door occupancy). Vehicle GLBs are not loaded.
- Only tested headless on port 8080; other ports are untested with this SDK key.

## Polish pass (after the hero-scene check, run 1090e291319b910b)
- Dollhouse "Ctrl + click or right-click and drag to pan" prompt: no URL param controls it; Showcase shows it while
  `localStorage['peekaboo_dh_prompt_dismissed']` (and `_rotate`) < 3. The stage sets both to 3 before loading the
  same-origin iframe. URL params used: `play=1 qs=1 title=0 brand=0 help=0 hl=0 tour=0` (Matterport logo stays).
- Lanes honour `yaw` (they were drawn unrotated, i.e. 15.85 m along z, spilling past the floor edge); doors honour
  `w`/`h` and get a floor pad so they read from above. Vehicle ids may be numbers; labels read "Reach 4" etc.
- Autoplay at 10× and Overview on load. Legend: one line, bottom-left above the controls.
- Data issue (stream B): in the hero scene all 15 doors are `source: assumed` on the x ≈ −78 wall, which in the
  walk view is a plain block wall with windows and no dock doors; the real overhead doors are on the high-z wall
  (z ≈ 56–65).
- (2nd pass) Walk targets the dock doors: the sweep scored closest to 10 m inside the door-line centre (6–16 m band,
  not inside a rack), facing the wall square-on. Door panels sit 0.3 m inside the wall (else the scan mesh hides them).
  Labels: world height = 0.036 × camera distance, clamped to 0.15–1.1 m, fading out between 32 and 42 m.
  Run 976cc2aa9b69e6e5: the real overhead doors are visible behind the markers; the assumed 4 m pitch does not match
  the real door spacing (e.g. D12 lands on a personnel door).
