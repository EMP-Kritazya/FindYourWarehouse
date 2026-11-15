# ProInspect v2 contracts: guided buying, buyer-driven tests, full Vexcel intelligence, deepagents (2026-11-14 night)

Plan: /Users/kritazyaupreti/.claude/plans/okay-i-m-thinking-of-linear-minsky.md. READ `CLAUDE.md` first: it holds every
verified fact, every reliability rule and the history of what failed. ~5 h build budget, then freeze.

## Product (user's words, condensed)
Two things, seamlessly orchestrated:
1. **Help the user FIND and CHOOSE a good warehouse.** This is CoStar's aim: selling properties.
   - A voice agent is a **guide all the way along**: search → shortlist → inspect → test → decide.
   - At each step it proactively points out the few attributes that matter for THIS user, not everything.
   - It moves the screens itself (fly to, open the card, show the aerial, open the scan, start a test).
2. **Simulate what the buyer cares about, with two FOCUSED tests on the real building:**
   - **Yard test (outside):** "can my trucks get in, turn, dock, and how long do they queue?"
   - **Equipment test (inside):** "do my machines fit and move through this building?"

The old 12-hour random shift is hidden from the UI. Its backend stays, for regression only.

## The guide (S2 behaviour, S5 surfaces it)
- **Keep a buyer profile through the session:**
  - business type, size, trucks and equipment they mentioned;
  - must-haves vs nice-to-haves;
  - the shortlist.
  Never force an interview; learn from what they say.
- **Narrate key points proactively, 1–3 short sentences per event, chosen by relevance to the profile:**
  - `openCard`: availability and asking terms, size vs need, clear height (CoStar vs scan), docks/drive-ins, the yard
    (truck court, paved/parking area), power, and one Vexcel insight (roof condition, trucks seen, nearby highway).
    Flag a mismatch or unknown that matters.
  - `search` results: the top 3 and why, plus what excluded the rest.
  - Opening the scan / aerial: what to look at.
  - After a test: the verdict and the one thing that limits it.
- **Suggest the next step each time** ("Want me to check if your 53-footers can back in?", "Add it to your shortlist?",
  "Compare with #2?").
- **Shortlist and compare:** keep the shortlist in the session and drive the compare view. When asked "which should I buy?",
  recommend with reasons and caveats, sourced.
- **Selling help, honestly:** listing status, broker/listing contact (fake contact data is labelled), asking rent/price
  where present, and "request a tour" (logs intent, no external send).

## Reliability rules (non-negotiable)
- **Agents decide, tools compute.** Every number has a source, a capture date, a method and a confidence.
- **Unknown is "?"** with the reason. Estimates are labelled as estimates.
- **No invented geometry:**
  - doors appear only when **marked by a person in the scan** or detected;
  - no assumed door panels on walls;
  - embedding search results are "likely/possible" with their score, never certain.
- **Ambiguous superlatives:** pick a defined metric, say which one, offer the alternative. Never refuse.
- **Keys never reach the browser.** The Vexcel WMTS capabilities document embeds the API key, so proxy it.
- After any backend change: restart the backend on 8010 and purge stale caches. Run
  `cd backend && ../.venv/bin/python -m pytest -q tests` before reporting (101 green today). No git. Never print secrets.

## Runtime
- **Backend:** `cd backend && ../.venv/bin/uvicorn proinspect.api:app --port 8010`.
  - Port 8000 is the user's SiteWalk server: never use or kill it.
  - The lead keeps one backend running on 8010. Streams test with FastAPI TestClient, or their own uvicorn on **8011–8015**
    (S1 8011, S2 8012, S3 8013, S4 8014), and stop it when done.
- **Web:** Vite on **8080**, the only port where the Matterport key works. The lead keeps it running; streams don't restart it.
  For a private check, S5 may run Vite on 8090, but Matterport only works on 8080.
- `api.py` auto-includes `router` from `api_sim`, `api_twin`, `api_concierge`, `api_vexcel`, `api_yard` and `api_fit`.
  `build/` is served at `/media/`.
- **Python 3.12 venv:** `ProInspect/.venv`. Already installed: deepagents 0.7.38, langchain 1.5.0, langchain-anthropic
  1.7.10, simpy, shapely, scipy, playwright. Ask the lead before adding packages.

## Ownership
| Stream | Owns |
|---|---|
| S1 Vexcel intelligence | `backend/proinspect/tools/vexcel_tiles.py`, `backend/proinspect/twin/vexcel_profile.py`, `backend/proinspect/vexcel_rank.py`, `backend/proinspect/api_vexcel.py`, `backend/tests/test_vexcel_*.py`, `build/vexcel/**` |
| S2 deepagents guide/concierge | `backend/proinspect/agents/**`, `backend/proinspect/api_concierge.py`, `backend/proinspect/voice/**`, `backend/tests/test_concierge*.py`, `web/src/voice/**` |
| S3 Yard test | `backend/proinspect/yard/**`, `backend/proinspect/api_yard.py`, `backend/tests/test_yard*.py`, `web/src/yard/**` |
| S4 Equipment test + doors | `backend/proinspect/fit/**`, `backend/proinspect/doors.py`, `backend/proinspect/api_fit.py`, `backend/tests/test_fit*.py`, `backend/tests/test_doors*.py`, `web/src/matterport/**`, `build/doors/**` |
| S5 Web app | `web/**` except `web/src/matterport/**`, `web/src/yard/**`, `web/src/voice/**` |
| Lead | `CLAUDE.md`, `docs/CONTRACTS.md`, `backend/proinspect/api.py`, integration |

Read-only shared code (call it, don't edit it; ask the lead for changes): `tools/vexcel.py`, `tools/elevation.py`,
`tools/matterport.py`, `twin/exterior.py`, `twin/interior.py`, `twin/fuse.py`, `sim/**`, `rank.py`, `query.py`,
`catalog.py`, `facts.py`, `library/**`, `api_sim.py`, `api_twin.py`.

## Shared data: the door store (S4 owns; S3 and S2 read through `proinspect.doors`)
File `build/doors/{model_id}.json`:
```
{ "model_id": "...", "property_id": 12896757, "updated_at": ISO8601,
  "doors": [ { "id": "d1", "kind": "dock_high" | "drive_in" | "personnel",
     "a": {"x":..,"y":..,"z":..}, "b": {...},     # the two door edges clicked in Matterport (Showcase metres)
     "center": {"x":..,"z":..}, "width_m": .., "height_m": .. | null, "sill_m": .. | null,
     "normal": {"x":..,"z":..},                   # outward, Showcase frame (pointing out of the building)
     "source": "marked_in_scan" | "dev_fixture", "marked_at": ISO8601 } ] }
```
`proinspect/doors.py` (S4) API:
- `load(model_id) -> dict|None`
- `save(model_id, property_id, doors)`
- `to_sim(model_id, site) -> [Door]`, converted into a sim Site frame with `proinspect.sim.export.showcase_to_sim`
- `to_enu(model_id, property_id) -> [{x, y, normal, width_m, kind}] | None`, local ENU metres of the exterior twin frame, using
  the scan-to-footprint registration in `twin/fuse.py`. When registration isn't trustworthy, return None and say why.

S4 ships `doors.py` (load/save/to_sim) and `GET/PUT /api/warehouses/{id}/doors` FIRST, within 45 min.

## Endpoints (new)
- **S1:**
  - `GET /api/tiles/{z}/{x}/{y}.jpg`: Vexcel WMTS proxy with disk cache and a coverage fallback, plus `Cache-Control`.
  - `GET /api/vexcel/{id}`: the profile (contents below).
  - `GET /api/vexcel/search?text=&near_id=|lat=&lon=&radius_m=`: semantic search →
    `[{lat, lon, score, distance_m, likelihood: likely|possible, thumb_url}]`.
  - `GET /api/vexcel/rank?metric=&market=&top=` → `[{id, value, unit, source, date}]`, with
    `metric ∈ {parking_area_sqft, paved_area_sqft, car_stalls_est, trucks_seen, trailers_seen, truck_court_ft,
     roof_condition, solar, tree_cover_pct, building_height_ft, distance_to:<place>}`.
- **S3:** `POST /api/yard/test {property_id, trucks:[{type:"semi_53"|"box_truck_26"|"last_mile_van", per_day | per_hour:[..24]}],
  door_ids?:[..], dock_minutes?:{type:min}, hours?:[start,end]}` →
  ```
  { run_id, doors:[{id, source, enu:{x,y}, lonlat}], fits:[{door_id, vehicle, ok, margin_m, conflicts:[{lon,lat,what}],
    path:[[t_s, lon, lat, heading_deg]], swept: GeoJSON polygon}],
    queue:{max_in_yard, max_on_street, wait_p50_min, wait_p90_min, door_utilization, timeline:[[t_s, in_yard, on_street]]},
    vehicles:[{id, type, keys:[[t_s, lon, lat, heading_deg, state]]}], frame:{lat0, lon0}, assumptions:[..] }
  ```
- **S4:**
  - `GET/PUT /api/warehouses/{id}/doors` (door store).
  - `POST /api/fit/test {property_id, model_id?, equipment: <library key> | {custom dims}, route: {from_door?: id,
    to: {x, z} | "auto"}, floor: "as_scanned"|"emptied"}` →
    ```
    { run_id, model_id, floor_y, equipment:{...}, checks:[{kind: corridor|turn|door|clear_height|lift, ok, need, have,
      unit, at:{x,y,z}, note}], path:[[t_s, x, z, yaw]], pinch_points:[{x,z,width_m,need_m}],
      reachable_pct, heat:{cell, cells:[[x,z,v]]}, assumptions:[..] }
    ```
    All x/y/z are in Showcase coordinates.
- **S2:** `WS /ws/concierge` keeps its v1 protocol, and adds:
  - client → server `{type:"event", name:"openCard"|"searchResults"|"openScan"|"openAerial"|"testResult", data}`, so the
    guide reacts to what the user does by clicking, not only to speech;
  - server → client `{type:"suggestions", items:[{label, say}]}` (next-step chips).
- **Agent UI commands** (S2 emits, S5 executes):
  - the existing `search`, `flyTo`, `openCard`, `highlight`, `compare`, `showInside`;
  - new `yardTest {id, trucks?, run_id?}`, `equipmentTest {id, equipment?, run_id?}`,
    `showPlace {lat, lon, label, thumb_url?}`, `rankBy {metric, market?}`, `showAerial {id, view?, year?}`,
    `shortlist {action: add|remove|show, id?}`.

## Vexcel profile `GET /api/vexcel/{id}` (S1)
```
{ id, captured: {ortho: date, dsm: date, parcel_attrs: date},
  parking: {paved_area_sqft, parking_lot_area_sqft (SAM3 'parking lot' on ortho, method+date), car_stalls_est (÷350 SF, 'estimate'),
            cars_seen, trucks_seen, trailers_seen, truck_court_ft},
  building: {footprint_sqft, height_ft_dsm, roof_condition, roof_material, roof_defects[], solar, ac_units, skylights},
  site: {parcel_acres, paved_pct, tree_cover_pct},
  history: {ortho_dates:[..], change: {years:[a,b], new_structure_m2, removed_m2, note}},
  nearby: {grocery: {...best}, highway_ramp: {...}, rail_yard: {...}, truck_stop: {...}, port_container: {...}, residential: {...}},
  sources: {...}, confidence: {...} }
```

## Front-end component contracts
- **S3** `web/src/yard/YardView.tsx`: `export default function YardView({propertyId, result?, onClose})` — the 3D yard
  replay in deck.gl over the map; S5 mounts it. The basemap tiles come from `/api/tiles`.
  `web/src/yard/YardForm.tsx`: `export default function YardForm({propertyId, onResult})`.
- **S4** `web/src/matterport/MatterportStage.tsx` (existing) gains `mode: "view" | "markDoors" | "fit"`, `fitResult?`,
  `onDoorsChanged?`.
  `web/src/matterport/FitForm.tsx`: `export default function FitForm({propertyId, modelId, onResult})`.
- **S2** `web/src/voice/VoiceOrb.tsx` (existing) keeps its props and adds `onEvent` plumbing. S5 calls
  `sendEvent(name, data)` exported from `web/src/voice/bus.ts` when the user clicks things. It also renders suggestion
  chips near the orb.
- Each stream creates placeholder exports within 15 minutes so S5 can import them.

## Demo building
3200 Sam Houston Pkwy, 12896757, scan QHMtBN5Nnrc (60k SF scanned of 88,670 RBA; the real dock doors are on the
high-z wall, Showcase z ≈ 64). The user marks its doors in the S4 UI before the demo. S4 may pre-mark 2–3 doors to
test with, but must label them `"source": "marked_in_scan"` only if a person verified them; otherwise use
`"dev_fixture"` and delete them before handing over.
