# ProInspect: what we have (read this before building)

HackPrinceton F26, CoStar challenge ("Inside and Out: AI for the Built World"). Sponsors give us three things:
**CoStar property data** (this folder), **Matterport 3D scans** of those properties, and **Vexcel aerial imagery and
analytics**. This file is a quick glance at what is real. If something is not listed here or in the kit docs,
assume we don't have it, and check before claiming it.

## Ground rules (anti-hallucination)

- **Source of truth:** `index.md`, then `models/<dataset>.md` (every field, meaning, enum), then `concepts/*.md`.
  Schemas are in `schemas/` (JSON Schema + `openapi.yaml`), and enum int-to-name lookups are in `schemas/enums.json`.
- **Never invent a field name.** Grep the model page or schema first. Field names are camelCase and records are nested.
- **Enums are integers.** Decode them with `schemas/enums.json`. `0` usually means Unknown/None, not "no".
- **null or missing means unknown**, not zero or false. A `has…` flag that is null is unknown.
- **Join on integer ids, never on names.** Group geography by `cityId`, `countyId` or `primarySubmarketId`, not by name strings.
- **The sample is not a market.** These are the properties on CoStar's *Matterport capture list*, so they don't represent either city.
- **Emails and phones are fake** (`contact-<code>@example.com`, area code 555). They are consistent across datasets, so they still join.
- Data "as of" date: around **2026-11-09 to 2026-11-12**.

## Scope

- **Two markets only:** Houston (`researchMarketId` 14, 1,758 commercial properties) and Los Angeles (20, 1,581).
  Every property is inside the city limits. Residential covers Houston plus the Galveston/League City area (1,700 listings) and LA (420).
- **Commercial: 3,339 properties.** By type: Multifamily 2,765, Office 289, Industrial 122, GeneralRetail 65,
  Hospitality 55, Flex 31, Specialty 5, Land 5, Healthcare 2. **Split by `propertyType` before totaling anything.**
  3,308 are Existing; filter `constructionStatus` = Existing for standing stock.
- **Warehouses:** 93 `Industrial_Warehouse` + 8 `Industrial_Distribution` + 9 `Flex_LightDistribution` = 110 properties
  (95 HOU / 15 LAX), 106 with ≥1 Matterport scan, 175 scans total. Broader Industrial+Flex = 153 properties, 231 scans.
- **Residential: 2,120 MLS listings** for 1,867 homes. 1,975 are for sale and 145 for rent. 787 Active, 6 Contract, 1,321 Sold, 6 Inactive or Removed.
- **The commercial and residential sides have no id link.** Connect them only by lat/lon or ZIP.

## Files

| Path | What |
|---|---|
| `commercial-properties.csv` | `PropertyId, MatterportModelId, Address, City, State`: 13,147 rows, one per property × scan (51 properties have no scan) |
| `residential-listings.csv` | `ListingId, ResidentialListingId, MatterportModelId, Address, City, State`: 2,224 rows (1 listing has no scan, 4 have hidden addresses) |
| `data/*.jsonl` | 8 datasets, one JSON record per line (~400 MB total; `commercial-property.jsonl` alone is 193 MB, so stream it) |
| `tools/expand_records.py` | Writes records out as pretty JSON to `records/<dataset>/<id>.json`, for browsing (all datasets ≈ 1 GB) |
| `vexcel_quickstart.py` | Working Vexcel API 2.0 example (see the Vexcel section below) |

Load with `pandas.read_json(p, lines=True)` plus `json_normalize`, or DuckDB `read_json_auto`.

## Datasets

| Dataset | Records | One record = | Key |
|---|---|---|---|
| `commercial-property` | 3,339 | A building: address + lat/lon, market/submarket, type/subtype, class and rating, GBA/RBA, stories, construction events, renovations, parking, lot, zoning, parcels, amenities, contacts, notes, photos + Matterport attachments, type-specific block | `id` = PropertyId |
| `commercial-listing-summary` | 1,022 | A for-lease or for-sale marketing listing (741 Active, 281 OffMarket) on 629 of the properties | `id` |
| `location-occupancy` | 74,093 | A tenancy (company × property × time window); a lease deal is present on ~42k of them | `id` |
| `company-detail` | 49,575 | A tenant company *location*: name, address, employees, industry, profile | `id` = `tenantLocationId` |
| `commercial-sale-comp` | 6,120 | A completed sale that includes one of the properties | `id` |
| `apartment-listing` | 1,747 | An Apartments.com listing for a community: rents and availability by floor plan, management company, main photo | `id`; one per `propertyId` |
| `residential-listings` | 2,120 | A Homes.com MLS listing: prices, status, `property` (beds, baths, sqft, lot acres, yearBuilt, taxes), media, agents, history, AVM | `id` = ListingId |
| `residential-listing-features` | 1,899 | A listing's features as readable text: `highlights`, `sections[].lines`, `features[]`, `derivedValues` | `id` = ListingId |

Type-specific blocks on a commercial property: `propertyOffice`, `propertyRetail`, `propertyIndustrial`, `propertyFlex`,
`propertyHospitality`, `propertyApartment` (floor plans `models[]` with `units[]`), `propertySportsAndEntertainment`,
`propertyResidence`, `land`. A block is null when it doesn't apply.

## Joins

```
commercial-property.id ← listing.propertyId | listing.forLeaseSummary.properties[].id | .spaces[].propertyId
                       ← location-occupancy.propertyId
                       ← sale-comp.entities[].propertyId   (portfolio sales include properties outside the kit)
                       ← apartment-listing.propertyId
property.propertyApartment.models[].id ← apartment-listing.apartmentModels[].id
location-occupancy.tenantLocationId → company-detail.id   (null = unknown tenant; 57,225 of 74,093 join)
apartment-listing.propertyManagementCompanyId → company-detail.id   (1,072 of 1,747)
residential-listings.id ↔ residential-listing-features.id   (1:1; 221 listings have no features record)
```
Current tenants of a building: its occupancies with a null `toDate`, joined to company-detail.

## Images and media already in the data

- **Commercial photos:** `attachments[].attachmentUri` and `primaryPhoto` link to CoStar's CDN at size `/112/`
  (up to 1880×900). Swap the size segment: 102, 104, 110, 111, 113 (1400×720), 114, 116 (2048²). There are 140,677 attachments
  (building, interior, aerial, floor plan, site plan, plat map) and 12,280 Matterport scans.
- **Residential media:** `media[]` (`images.homes.com` or `imagescdn.homes.com`, size `/112/`; also 114, 210 = 2560×1440, 102).
  **`media[].scenes[].type` is a CoStar scene classification** (`SceneClassificationType`, ~270 labels: Kitchen,
  Bathroom, Bedroom, roof types such as ShingleRoof/MetalRoof, WaterHeater, SolarPanels, SmokeDetector, Fence, Attic, and more).
  50,706 of 74,582 media items carry scene labels, which is useful for room-level inspection.
- `residential-listing-features` has a "Home Design" section with lines like "Composition Roof", "Slab Foundation" and
  "Built in 1952". `source` is `Mls` for 99.5% of features and `MachineLearning` for the rest.

## Matterport (3D scans)

- **Model id** = `MatterportModelId` in the CSVs = `videoKey` in the data. An attachment or media item is a Matterport
  model when `videoExternalReferenceType` (commercial) or `externalReferenceType` (residential) is **36**
  (MatterportScan) or **234** (MatterportStreamingKey). Residential `media.type` 43 = Matterport3DScan.
  Apartment floor-plan and unit tours are in `propertyApartment.models[].attachments` and `.units[].attachments`.
- Public viewer: `https://my.matterport.com/show/?m=<modelId>` (residential media `uri` already holds this).
- **Credentials for Matterport and Vexcel are in `ProInspect/.env`** (the user confirmed this; never print or commit it).
  The sibling project `../Co-STAR/costar-hackathon-kit` (an earlier build, SiteWalk) used:
  - **Model API (GraphQL):** `POST https://api.matterport.com/api/models/graph`, basic auth = (API key, API token).
    It exposes `model { rooms { label classifications dimensions(units: imperial){areaFloor height} panoLocations } panoLocations { position } }`.
    Newer scans have auto-classified rooms; older ones only have panos. Pano image URLs are signed and expire, so cache bytes, not URLs.
  - **Embed/Showcase SDK:** iframe `my.matterport.com/show?m=<id>&play=1&applicationKey=<SDK key>` plus the bootstrap
    `api.matterport.com/sdk/bootstrap/3.1.0-…/sdk.es6.js`. The SDK key is domain-bound, so add `localhost:<port>` to its allow list and serve over http, not `file://`.
  - Env var names there were spelled `MATTERPLOT_API_KEY`, `MATTERPLOT_API_TOKEN` and `MATTERPLOT_SDK_KEY` (sic). The product is **Matterport**.
  - Treat these as "worked before" and re-verify against our own keys.

## Vexcel (aerial)

**Confirmed in `vexcel_quickstart.py`** (API base `https://api.vexcelgroup.com/v2`, header `X-API-KEY`, env `VEXCEL_API_KEY`, retry on 429):

| Endpoint | What it does |
|---|---|
| `GET property/extract` | Elements attributes for the parcel and structures at a WKT point (`include=precomputed`, `latest=true`). Includes roof material, shape and condition per structure, footprint area, structure count, parcel tree-cover %, pool detected. 404 means no parcel at that point |
| `POST ortho/extract` | Ortho (top-down) image clipped to a WKT polygon, with a buffer and drawn overlays |
| `GET oriented/collections` | Which oblique directions and capture dates exist at a location |
| `POST oriented/extract` | Oblique image (`oblique-north/south/east/west`); only the `urban` layer has obliques |
| `POST elements/detect` | Open-vocabulary detection (model `sam3`) of any text label, on ortho or oblique: bbox, confidence, pixel mask |
| `POST embedding/extract` | Semantic search (text, location, URL or image) over zoom-19 tiles (~60 m) in an area |

- Layers: `urban` (7.5 cm ortho + oblique), `urban-r` (7.5 cm, no obliques), `wide-area` (15 cm), `urban-g`.
- **WKT is `POINT(lon lat)`**, the reverse of lat/lon. The oriented/extract drawing colors take bare `"r,g,b"` plus an explicit transparent fill.

**Also offered by Vexcel (per the user; endpoint names and params not yet confirmed here, so look them up before use):**
aerial imagery, oblique imagery, native imagery, Digital Surface Model (DSM), Digital Terrain Model (DTM), 3D cities,
multispectral imagery, historical imagery, historical DSM, rooftop equipment, roof attributes, property amenities,
vegetation attributes, roadway intelligence, semantic embeddings.
(DSM minus DTM gives object and building heights. Historical imagery and DSM allow change-over-time comparisons.)

## Phase 0 findings (verified 2026-11-14; raw data in build/spike/)
- **Scan coverage:** of 175 warehouse scans, 173 load (2 have no mesh). 74 scans on **57 properties** cover real warehouse
  floor (≥5k SF floor, ≥16 ft high). The measured roof-deck height runs about 7–10 ft above CoStar's clear height.
  Floor area is sometimes larger than RBA because yards are included in the scan.
  One model can be shared by several PropertyIds (multi-building parks).
- **Vexcel structure attributes:** footprint, roof condition (1–5) and material, roof defects (rust, patch, ponding,
  tarp...), rooftop AC units, skylights and solar, eave height and max roof height (often null for warehouses), ground elevation.
  Parcel attributes include vehicle counts, hardscape % and tree cover %.
- **Obliques:** all 4 directions exist on most Houston/LA sites. **15–17 historical orthos per site (2016→2026)**, and DSM per year 2023–26.
- **Dock and door detection:** works on **obliques, not the ortho**. Run all 4 directions; labels "loading dock door" /
  "overhead garage door" at threshold 0.3–0.5. On 238885 the east oblique found 9 doors (≤0.83 confidence). It finds
  grade-level drive-in doors too, so classify dock-high vs drive-in later.
- **Hero candidates:**
  - 238885 (6445 Burlington Dr, HOU, 3 buildings, metal roofs)
  - 237474 (HOU, 40 ft clear, 40k SF scanned)
  - 278605 (4801 Staunton Ave, LA, 65k SF scanned, trucks and containers detected)
- **Code:** `backend/proinspect/`, which holds `tools/matterport.py`, `tools/vexcel.py`, `tools/elevation.py`, `cache.py`
  and `warehouses.py`. The venv is `ProInspect/.venv` (Python 3.12).
- **Voice: ElevenLabs** (`ELEVEN_LABS_API_KEY`). The key is scoped: TTS and STT work, but it can't list voices or read
  the user account. Flash v2.5 TTS reaches first audio in 0.27 s; Scribe STT takes 0.5 s. Default voice: "Sarah" `EXAVITQu4vr4xnSDxMaL` (young woman; was George `JBFqnCBsd6RMkjVDRZzb`); override with `PROINSPECT_VOICE_ID`. The browser fallback picks a female English system voice (Samantha/Ava/Google US English…).

## Phase 1 modules (backend/proinspect/)
- `catalog.py` builds `build/catalog/warehouses.json`: one clean record per warehouse, with specs, listings and rent,
  current tenants, last sale and scan coverage. Unknown values stay None.
- `library/equipment.yaml` (15 vehicles, typical values with a `basis`) and `library/profiles.yaml` (8 business profiles).
- `requirements.py`: **search needs nothing.** `missing_for("search")` is []; "rank" needs at least one criterion
  or a profile; "simulate" needs a profile and a truck, plus `confirmed`. Each criterion is a deal-breaker (D) or a
  nice-to-have (N) with a weight from 1 to 5.
- `query.py`: free text → chips. Deterministic rules handle numbers, places and keywords. Claude
  (`claude-opus-5-5`, structured output, server-side refusal fallback) is called only when words are left unparsed,
  and each of its filters must quote the query, or it is dropped. The rules win when the two conflict.
  `near` resolves only against our own catalog (ZIP or address text); otherwise the chip says it couldn't place it.
  With no criteria, `rank.py` returns `mode: "browse"`, sorted by availability, then 3D interior, then size.
- `rank.py`: deterministic ranking over facts {value, source, confidence}. Measured facts override CoStar and keep
  CoStar's value in `alt`. A failed deal-breaker excludes the building; an unknown one flags it and widens the score band.
- Tests: `cd backend && ../.venv/bin/python -m pytest -q tests`.
- `api.py` (FastAPI): `/api/warehouses`, `/api/search` (works with no input), `/api/library`, `/api/requirements/missing`.

## Phase 2: interior twin (`twin/interior.py`) + validation (`validate.py`), verified 2026-11-14
- **Mesh asset labels are not trusted.** Some "500k" meshes are tiny stubs (e.g. BNGoeNCMHtk: 252 vertices). `load_mesh`
  picks the densest mesh that spans ≥ 10 m and records why in `mesh_note`. Measurements are NOT disk-cached (only
  downloads are), so a changed method never serves stale numbers. Output goes to `build/twins/interior/<model>.{json,npz}`.
- **Clear height is a triple:** roof deck, lowest obstruction P5 and P50. Offices and mezzanines (a ceiling at 2–4 m)
  are measured separately as `office_sqft`.
- **Validation vs CoStar** (42 scans, `build/validation/interior.md`):
  - CoStar's "clear" ≈ deck − 1.1 ft. On held-out scans: MAE 2.3 ft, 45% within ±2 ft, P90 error 4.3 ft.
  - Our P5 obstruction runs 2.5 ft below CoStar; it is the conservative value for rack heights.
  - Show both, labelled.
- **Scans cover a median 22% of the RBA.** Always say which area the simulation runs on.
- **Columns:** a column must appear in both a low slice and a high slice. Columns within 1 m of the floor edge are
  pilasters; bay spacing is fitted on interior columns only.
  - The fit takes the largest consistent spacing; sub-multiples fit equally well, which was a bug.
  - Result: 2 of 2 buildings with a CoStar reference fall within 0.5 ft.
  - With no interior columns and good coverage, the structure is reported as "clear span".

## Phase 2: exterior twin (`twin/exterior.py`), validated against CoStar on 108 sites (`build/validation/exterior.json`)
- **What it computes:**
  - every building ≥ 3k SF on the parcel, with doors from all 4 obliques. Each oblique carries its full camera model, so
    door size = pixels × GSD (÷ sin tilt for height);
  - door class (dock-high / drive-in / uncertain) from the size and truck-well cues;
  - truck court depth by rays over a paved mask (nDSM < 0.5 m and not green; vehicles count as transparent);
  - roof condition and defects, vehicles on the parcel.
- **Door detection is NOT reliable:**
  - no doors found at 60 of 108 sites;
  - correlation with CoStar dock counts is −0.07;
  - long dock walls are foreshortened in obliques.
  So doors are only a **lower bound** (`Fact.bound="lower"`): a lower bound below a required minimum is "unknown", never a fail.
- **Drive-in door size:** MAE 2.3 ft (width) and 3.3 ft (height).
- **Truck court:** only 3 sites have a CoStar value; 2 are within 2 ft and 1 is badly off. Confidence is capped at 0.5.
- **Parcel mismatch:** when the Vexcel buildings are < 30% of CoStar's RBA (e.g. 11175666), the parcel is probably wrong,
  and exterior facts are suppressed.
- `facts.py` turns the twins into ranker Facts at confidences below CoStar's 0.8. Measurements fill gaps; CoStar's value
  wins where it exists and the measured value is kept in `alt`.

## Phase 5 (started): simulation engine (`backend/proinspect/sim/`)
- **Modules:**
  - `site.py`: floor grid + doors. The dock wall is normalised to low y. The real floor uses the interior **structure**
    grid: today's contents are cleared, walls/columns/full-height racks are kept.
  - `layout.py`:
    - selective racks perpendicular to the dock wall, with aisles from the put-away truck's `min_aisle_ft`;
    - flow-through staging lanes, sized to fit;
    - an offset search penalising columns in aisles; unreachable bays are dropped and counted;
    - a density sanity check in SF per position (my original `positions_per_ksf` profile values were wrong and were removed).
  - `traffic.py`: lane graph (1.25 m) + **zone control**. Each node admits 1 vehicle; narrow rack aisles (and their
    mouths) are single-occupancy locks; reroutes and deadlock breaks are counted.
    Windowed space-time A* was tried first and was too slow in Python (420 s per shift, gridlock).
  - `shift.py`: the SimPy shift.
    - Trucks: appointments + lateness, dedicated inbound/outbound doors, one crew per trailer.
    - Pallet tasks: unload, put-away, retrieve, load, with handling times (lognormal CV 0.3) and lift time per level.
    - Unload time is an OUTPUT.
  - `run.py`: R seeded replications → P10/P50/P90, plus a log hash for determinism.
- **Status:**
  - the synthetic 60×90 m 3PL completes every truck with pallets conserved, 0.8 s per shift; 8 golden tests in `tests/test_sim.py`;
  - real scans: 12896757 (60k SF) and 238885 run fully;
  - 237474 is wrong because doors were placed on an office wall.
  **Next:** the fusion step, i.e. register the scan to the Vexcel footprint and place doors from the obliques; then the
  trace API and the 3D replay.
- **Known gaps (stated in the result `assumptions`):** pallet flows only (no each-picking/AMRs/conveyors yet), no breaks or
  charging, and the 53' unload comes out at about 21 min vs the 35 min industry reference.

## Matterport as the stage (verified 2026-11-14)
- The hackathon SDK key (`MATTERPLOT_SDK_KEY`, issued by the Discord bot; we can't edit its allowed domains) works on
  **http://localhost:8080** with the Embed SDK and with the self-hosted **Bundle SDK 26.10.4**. Serve the web app on 8080.
- Hero scans are `visibility: public`.
- The Embed SDK has no Scene API.
- Bundle SDK `Scene.createObjects` + `mp.gltfLoader` rendered a glTF inside scan QHMtBN5Nnrc. Motion and custom
  components were not yet working at hand-off: Stream A owns that.

## Parallel build (from 2026-11-14 evening; deadline ~11 h, demo needs 2 h)
- Contracts + file ownership: `docs/CONTRACTS.md`. The streams:
  - A: Matterport stage (sim replay inside the Bundle SDK)
  - B: scan↔footprint fusion, sim scene export, sim/twin APIs, hero precompute
  - C: Vite web app (map, search, cards, simulate flow)
  - D: voice concierge (Claude tools + ElevenLabs) over `/ws/concierge`
- `api.py` auto-includes `api_sim`, `api_twin`, `api_concierge` routers and serves `build/` at `/media/`.
- **Ports:**
  - ProInspect backend: **8010**.
  - Port 8000 is the user's old SiteWalk server: never use it or kill it.
  - Web app: **8080**, the only port verified for the Matterport key.
- **Stream A (Matterport stage), done:** the sim replays INSIDE the Bundle SDK viewer (dollhouse, floor plan, walk, follow).
  - `web/src/matterport/MatterportStage.tsx`; the bundle lives in `web/public/bundle/`.
  - Coordinates, verified exactly on 51 panos: **Showcase (x, y, z) = (X, Z, −Y)** from the Model API/mesh frame;
    pano id == SDK sweep sid; yaw = atan2(dY, dX) unchanged.
  - Fix that made components run: give each component at least one input and pass initial inputs to `addComponent`,
    and build buffers/canvases with the iframe's realm. Details in `docs/research/matterport-stage.md`.
- **Stream D (voice), done:**
  - Endpoint and code: `/ws/concierge`, `agents/concierge.py` (Claude Opus 5.5, effort low, tools only, refusal fallback).
  - Voice: `voice/` = Scribe v2 Realtime proxied through the server (the scoped key supports it), falling back to batch,
    plus flash TTS per sentence.
  - First audio ~1.7 s on the server, ~2.2 s in the browser.
  - The simulation gate is enforced in code (it needs the user's confirmation in a later turn).
  - Numbers it can't trace to a tool are reported in `unverified_numbers`.
  - UI: `web/src/voice/VoiceOrb.tsx`, hold Space to talk, Esc interrupts, with a text box fallback.

## Integrated app (verified end to end by the lead, 2026-11-14 night)
- **Run:**
  1. `cd backend && ../.venv/bin/uvicorn proinspect.api:app --port 8010`
  2. `cd web && npm run dev` (port 8080)
  3. Regenerate hero runs after ANY sim code change: `cd backend && ../.venv/bin/python -m proinspect.precompute`
     (run ids hash the code).
- **The flow works with no page errors:** map → plain search → chips + ranked rail → warehouse card (evidence with source
  chips) → Simulate (KPI P50 + P10–P90) → "Watch it inside the scan" (Bundle SDK replay).
- **Demo-ready heroes** (`build/sim/heroes.json`), both in 12896757 / scan QHMtBN5Nnrc:
  - pallet_3pl: 12/12 trucks, 156/156 pallets, unload 20 min, congestion 31%
  - food_beverage: 14/14 trucks, 130 in / 122 out, unload 21 min, congestion 20%
  - Both have doors ASSUMED (registration confidence 0.03); 15 doors from CoStar's 23.
- **Not demo-ready (sanity checks fail):** 238885 (density) and 237474 (capacity: 350 reachable positions).
- **Traffic fixes that made heroes stable (10/10 seeds):**
  - idle vehicles park off the travel lanes and release their zone;
  - a 14 ft two-way main aisle between staging and racks;
  - after a deadlock is detected, the vehicle passes through to its goal (counted once per trip);
  - no hold-and-wait on aisle locks.
- **CoStar photo URLs return 403**, so the UI falls back to the Vexcel ortho.
- **Hero dock wall:** the automatic longest-wall pick placed the 12896757 docks on a block wall with windows. Seen in the
  Matterport walk view, the real overhead doors are on the 'E' side (Showcase z ≈ 64).
  - `twin/fuse.py SCAN_VERIFIED_DOCK_WALL` records this; `doors_source="scan_verified_wall"`.
  - Door positions along the wall are still assumed (22 doors, 4 m apart; the real pitch is ~4.1–4.2 m).
  - Detecting doors from the bumper bollards in the mesh FAILED: the only usable mesh is 50k faces and the OBJ has no
    UVs, so there's no colour. Doors stay assumed and are shown with "?" markers. The untried route is a bollard
    detector on the 360° panos.
- **Stale-cache hazard:** sim run ids hash the source files on disk, so a backend process started BEFORE a code change can
  store old-code results under the new key. Always restart the backend after changing sim/fuse code, then purge and
  rerun precompute.
- **Current demo hero runs:** pallet_3pl `f95f82d1748a4718`, food_beverage `39c25474dce98382`.

## FEATURE FREEZE (2026-11-14, before demo recording)
- **State at freeze:** all 4 streams done; 101 backend tests pass; `npm run build` passes; end-to-end flow verified in
  headless Chromium (map → search → card → simulate → Matterport replay with a loading cover, Walk to the real dock doors).
- **Demo path:** "Demos" in the results rail → 3PL or Food & beverage at 3200 Sam Houston Pky (12896757).
- **After any further code change:** restart the backend, purge stale runs, and run `python -m proinspect.precompute`.

## v2 refinement (from 2026-11-14 late; supersedes the freeze above)
- **User feedback:**
  - Assumed door panels landed on blank walls; doors must be marked by a person in the scan.
  - The map was black.
  - The agent was weak: no Vexcel, refused "biggest parking lot"; it must use LangChain deepagents and act as a GUIDE
    (CoStar sells properties: find → shortlist → inspect → test → decide).
  - The 12 h random shift is replaced by two focused tests: the **Yard test** (outside, trucks turn/back in/queue) and
    the **Equipment test** (inside, will my machines fit and move).
- **Vexcel WMTS works** with our key (layers urban/urban-r/urban-g/wide-area/graysky). Its GetCapabilities EMBEDS THE
  API KEY: proxy tiles via `/api/tiles`; never forward capabilities.
- **S1 Vexcel intelligence, done:** `/api/tiles` (Vexcel WMTS proxy; transparent at z ≤ 12, so keep Esri underneath),
  `/api/vexcel/{id}` profiles for all 110 (`build/vexcel/`), `/api/vexcel/rank` (returns metric definitions + results),
  `/api/vexcel/search`, `metric_for(phrase)`.
  - `hardscapes.area` is m² (patios/walks only), so it is NOT used as paved area.
  - Parking stall estimates are an upper bound (~1.6× CoStar).
  - Embedding thresholds are calibrated per term ("grocery" = big-box store with a large car park).
  - **Parcel check (profile v4):**
    - `parcel_mismatch` applies only when Vexcel buildings are < 30% of CoStar RBA/GBA (11175666, 4013358). These are low
      confidence and excluded from parcel-based rankings.
    - When the parcel acres are > 2× apart from CoStar's lot, it is only a caveat (`acres_note`, medium confidence, still
      ranked; 26 properties).
- **S2 guide, done:** `agents/guide.py` is a LangChain deepagents orchestrator (Opus 5.5, effort low), with subagents
  costar_analyst, vexcel_analyst, interior_analyst and test_engineer, plus fast tools.
  - Buyer profile, shortlist and funnel stage persist per session.
  - Enforced in code: a test confirmation gate, one comment per event, suggestion chips every turn.
  - The ws adds `event` in and `suggestions` out.
  - First audio is ~1.2–2.8 s.
  - Verified live by the lead: "biggest parking lot" → parking_area_sqft ranking + rankBy/flyTo;
    "closest to a grocery" → showPlace + caveat. `PROINSPECT_GUIDE=v1` restores the old concierge.
- **S3 yard test, done:** `yard/kinematics.py`, a WB-67 off-axle hitch model.
  - Validation: outer turning radius 45.0 ft; semi needs ~120 ft of court with both neighbouring doors occupied (golden:
    fails at 110, passes at 125); box truck needs 57–70 ft.
  - `yard/queue.py`: SimPy queue, 20 seeds.
  - `/api/yard/test`, `/api/yard/{run_id}`; `web/src/yard/` (deck.gl on Vexcel tiles).
  - 12896757 docks are on the SOUTH facade (aerial truck court), which matches the scan-verified high-z wall.
  - Doors stay assumed until they are marked AND `doors.to_enu` resolves the 180° registration flip (S4 is fixing this
    using the court depth).
- **S4 equipment test + doors, done:**
  - `doors.py`, `/api/warehouses/{id}/doors`: save is validated, and `to_enu` breaks the 180° flip using the marked doors
    plus the deepest court (QHMtBN5Nnrc → SOUTH facade, confidence 0.44).
  - `fit/`, `/api/fit/test`, `/api/fit/{run_id}`: checks for corridor, turn, door, clear height, lift and aisle. A
    missing input gives `ok: null` ("?"), never a fail; results carry `counts`.
  - Marking UX: 2 clicks for the first door, then 1 click (or "+ next at same spacing").
  - `build/doors/` is EMPTY: the user marks the real doors.
  - The real-mouse click path still needs one manual check on 8080.
- **Lead end-to-end (headless, real UI clicks), no page errors:**
  - opening a card triggers the guide's proactive comment with chips (first audio 2.0 s);
  - the yard test runs from the Test tab, and the guide summarises it with caveats (1.8 s).
  - The only 404 is the missing ortho for 7362797 (its twin failed earlier; the card falls back).
- **S5 web, done:** Esri imagery under Vexcel tiles; a "Test it" tab; the Vexcel card section; shortlist/compare;
  showPlace/rankBy; a guide event bus. The old shift UI is behind `?devsim`.
- Five streams S1–S5 (Vexcel intelligence, deepagents guide, yard test, equipment test + door marking, web app). The
  contract is `docs/CONTRACTS.md` v2. deepagents 0.7.38 / langchain 1.5.0 / langchain-anthropic 1.7.10 are installed in `.venv`.

## v2 FREEZE (2026-11-14 night)
- **State:** all streams S1–S5 done; 173 backend tests pass; `npm run build` passes.
- **Lead end-to-end with real UI clicks:**
  - Vexcel map;
  - search → card → the guide's proactive brief;
  - Test tab → yard test (swept paths, "fails by / clears by"), with the guide docked over the rail;
  - equipment test and door marking verified by S4 on 8080.
- **Before the demo, the user must:**
  - mark the 12896757 dock doors in "Mark doors" (`build/doors/` is empty);
  - check by hand that a real mouse click on a door edge registers.
- **After ANY backend edit:** restart 8010. Yard and fit run ids hash their code, so stale results aren't served.

## Research memos (docs/research/, written by subagents; items marked [U] are unverified)
- `measurement.md`: methods for truck court depth, dock-high vs drive-in doors, the interior, validation, and ADA/FHA numbers for the stretch goal.
- `simulation.md`:
  - SimPy 4.1.2 for discrete events.
  - Our own rolling-horizon prioritized planner with a space-time reservation table, on a ~1.25 m lane graph (PIBT as an alternative).
  - WB-67 swept path by playing the forward pull-out in reverse.
  - Bartholdi & Hackman formulas, and 30 seeded runs reported as P10/P50/P90.
- `frontend-voice.md`, **front-end decision: Vite + React 19 (not Next.js).**
  - Map: MapLibre 6 + deck.gl 9.4 on one canvas, with OpenFreeMap tiles (no key needed).
  - Interior: R3F with the mesh as GLB (meshopt, not Draco, so it works offline).
  - Voice: our own pipeline (Scribe realtime → Claude → ElevenLabs flash TTS over WebSocket), with push-to-talk as the default.

## Gotchas that will bite

- Texas is non-disclosure: the 929 sold Texas home sales have **no sale price**, only asking. LA sales have prices.
- Residential: split sale (`transactionType` 1) from rent (2), and current from sold. Count homes by `propertyId` or `residentialListingId`, not by listing.
  `permissionLevels.homes` = 10 means public. `mls.id` 800000 = CoStar photo-shoot placeholder listings.
- Commercial: RBA (`rentableBuildingAreaSquareFeet`) is on only 550 properties, while GBA is on 3,334. Retail uses GLA. `lotSize` needs `lotSizeMeasurementType`.
- Rents: normalize to $/SF/yr and the same `serviceType`. A rate of 0 means no rate. Asking ≠ contract ≠ effective.
- Sale comps: always carry `salePriceStatus`, and watch portfolio-allocated prices and pro forma cap rates.
- Construction years come from `constructionEvents[]` (the year can be approximate). Renovations are in `renovations[]`.
- Agent `role` on residential listings is a bit flag (`& 1` = listing agent, `& 2` = buyer's agent).

## v3 (2026-11-15): operational intelligence, aerial-first docks, multi-truck yard (supersedes v2 where they overlap)
- Contract: `docs/CONTRACTS_v3.md`. Plan: `~/.claude/plans/okay-i-m-thinking-of-linear-minsky.md`. 230 backend tests pass;
  `npm run build` passes. Lead walkthrough screenshots: `build/ui/v3/lead/`.
- **Dock store v3** (`docks.py`, `build/docks/{property_id}.json`, `GET/PUT /api/warehouses/{id}/docks`,
  `/api/docks/evidence`): doors live on the Vexcel footprint (ENU of the exterior twin), with evidence and status
  (proposed/inferred/confirmed/rejected/user_added). Facade-crop SAM3 is the big win, not the GSD fix (the GSD bug made
  doors SMALLER, not bigger). Validation (39 CoStar-dock sites): MAE 21.2 → 16.7, sites with ≥1 dock-high 10% → 82%,
  but corr still ≈ 0 → detections are a lower bound only. The estimate counts only the property's own building and is
  set to unknown when implausible (> 4 doors/10k SF, or more than the facades can hold). Claude vision POC: NO-GO
  (MAE 9.2 vs 7.8 for SAM3).
- **Doors into the scan:** `doors.from_dock_store` / `doors_for_scan` map aerial doors into the Matterport scan through
  the registration. Order of trust: user flip in the dock editor > `SCAN_VERIFIED_DOCK_WALL` > automatic fit (flagged).
  The equipment test and `GET /api/warehouses/{id}/doors` use them when no doors were marked in 3D.
  GOTCHA: in `sim/site._rotate_dock_wall_down`, 'E' brings the LOW-x side down and 'W' the HIGH-x side (the docstring is
  mirrored). QHMtBN5Nnrc 'E' → Showcase z ≈ 64, and the aerial south docks land there.
- **Yard maneuver test** (`yard/plan.py`, `pursuit.py`, `reserve.py`, `route.py`; `POST /api/yard/maneuver`): all
  motion is kinematic (pure pursuit on the WB-67 model, kinematic back-in, pull-out). Trucks are coordinated by a
  space-time reservation table and wait at the gate. Issue classes: geometric (only buildings or DSM objects) /
  assumption (incl. the colour-based "unpaved" mask) / planner / congestion. 12896757 with 5 semis: all done in 16.7 min,
  2 at once; only k5 and k6 are reachable from the inferred EAST gate. Placing the real gate and doors in the dock editor
  is the main lever. The 24 h queue UI is only behind `?devsim`.
- **Analytics** (`analytics.py`, `/api/rank/attribute`, `/api/compare`, `/api/comparables/{id}`, `library/criteria.yaml`):
  verified / estimated / unknown tiers (unknown is never 0); criteria are chosen by operation type; a winner is named
  only beyond each criterion's tolerance. The agent is analysis-first (tools `rank_attribute`, `dock_evidence`,
  `compare_warehouses`, `find_comparables`, `yard_maneuver_test`), with ws `step` messages and a text-first Analyst
  thread. The rail handles `rankAttribute` via `rank('attr:<key>')`.
- **Web:** to-scale SVG equipment glyphs and cards (`EquipmentGlyph.tsx`, `EquipmentCard.tsx`); the dock editor
  (`web/src/docks/`) opens from the card's Docks row; the maneuver player autoplays (`window.__yardPlayer.seek(t)` for
  shots); the compare panel sits in the right column.
- **Aerial doors in 3D (2026-11-15 fix):** `doors.from_dock_store` places doors in a scan ONLY when the alignment is
  confirmed by a person, comes from a scan-verified wall, or the automatic fit is strong (`TRUST_REG` 0.5); the doors
  must sit on the OUTER wall (holes filled) and face outward. Before this fix, 9851 Fallbrook (fit 0.07) got panels on
  the battery containers. The dock editor shows the scan outline (`registration.scan_outline`, added by the API) with
  Flip and Move-scan (`registration.shift`, ENU m) controls.
- **Voice:** "TTS HTTP 401" = ElevenLabs credits used up (quota_exceeded). The server blocks TTS for 10 min and sends
  `tts_fallback` sentences; VoiceOrb speaks them with the browser voice. Add credits or a new key for the real voice.
- **Yard planner on tight sites:** gates inside the property's own notches are rejected; pull-past distances shrink to
  fit; tracking scales with wheelbase. 242089: vans and box trucks complete, semis fail in the notches as `planner`.
  Cold runs take 11–21 s (cached runs are instant).
- **v3.1 (2026-11-15):**
  - **Planner:** Hybrid A* approach/exit (`yard/hybrid.py`).
  - **Hard property bounds** (`plan._bounded_grid`): free ground outside the property becomes kind 5. The property is the
    parcel line only when it matches CoStar's lot size (0.7–1.6×). Otherwise it is the ground within D m of the
    building, with D chosen so the area equals CoStar's lot. Vexcel parcels were 44–51% of the lot at 242089 and
    9235035.
  - **OpenStreetMap** (`yard/roads.py`, Overpass, cached in `cache/osm`): driveways joining a public street are the
    first gate candidates and are forced drivable, so snapshot clutter on them doesn't block. Parked vehicles are
    treated as movable.
  - **Verified:** 0 of 20k truck poses outside the property; 12896757 5/5, 9235035 4/4, 242089 4/4.
  - **Dock proof:** `GET /api/docks/{pid}/proof` and `/proof/{crop}.jpg`, real Vexcel oblique crops with the SAM3 box,
    fetched on demand.
  - **Main map:** a 3D site from the Vexcel nDSM plus ortho (`api_terrain.py`, deck.gl `TerrainLayer`), with docks drawn
    x-ray on top.
  - **Dock editor:** Select / Add / Move-gate tools, and the main map is locked while it is open.
- **Storage capacity planner (2026-11-15):** `racks.py` + `api_racks.py` (`/api/racks/{pid}`, `/trucks`, `/summary`).
  - **Layout:** reuses `sim/layout.generate`, now with `sprinkler_ft` and `dock_zone` parameters. Levels = scan P5 − the
    sprinkler clearance (CoStar ESFR 3 ft, wet 1.5 ft, unknown 3 ft), capped by the truck's lift height. "Parallel"
    orientation transposes the site.
  - **Columns:** positions lost to columns = the same layout with the small blocked components removed.
  - **Dock wall:** from the dock-store doors, using the corrected E/W label mapping (`racks._dock_side`).
  - **Outputs:** the whole-building estimate is a ratio to CoStar RBA (labelled est.); $/position only when CoStar has
    a rent.
  - **3D:** the scene is written to `/media/racks/{key}_scene.json` and rendered by MatterportStage, with replay
    controls hidden for static scenes.
  - **UI:** Test tab "Storage capacity" (`web/src/racks/`); `StorageRow` on the card; a Storage section in Compare;
    the analyst tool `storage_capacity`; UI command `storageTest`.
  - **12896757:** reach 5,076 / VNA 5,460 / counterbalance 2,082 positions (scanned); 552 lost to columns.
- **Dock doors in the scan (2026-11-15):**
  - **Labels:** `docks.label_doors` gives each door a label (S1…, counted left to right as seen from OUTSIDE that wall),
    used on the map, editor, popup and scan.
  - **Alignment:** `doors.align_to_click` + `POST /api/warehouses/{id}/docks/align` = one click on a real door slides
    the aerial doors along the wall (`registration.shift`, confirmed). Scan stage mode `align`; "Align these doors"
    on the card's Docks row.
  - **Marked doors:** doors marked in the scan sync to the dock store (`sync_marked_to_dock_store`, after alignment).
  - **Measured on 12896757:** the rotation is right; the aerial is ±2 m along the wall. SAM3 finds no doors on the west
    segments (the oblique shows blank wall and tanks there), and the scan covers x −78..+13 of a 126 m facade.
- **Interior planner (2026-11-15):** `interior.py` + `api_interior.py` (`/api/interior/{pid}`, `/summary`,
  `/operations`) + `library/layouts.yaml`.
  - **Recipes:** operation → zones (selective / dense double-deep / shelving / open), measured as depth bands BEHIND
    the dock zone. A main aisle runs through pick/open zones and a cross aisle between zones.
  - **Scanned contents:** become objects (keep/remove).
  - **Forklift proofs:** reachable %, trip m, min/pallet, pallets/h, machines for the profile's volume, from a grid
    Dijkstra on the floor eroded by the machine's width.
  - **3D:** realistic instanced racks (`sceneRenderer.rackStructure`), forks lifting via key[5], removed-object
    outlines.
  - **UI:** `web/src/racks/InteriorWorkspace.tsx` (2D left, live Matterport right), opened from the Test tab or the
    `storageTest` command. The card/Compare verdict comes from `/api/interior/{id}/summary`.
  - **12896757, 3PL:** 5,076 positions, 100% reachable, 1.64 min/pallet, 2 reach trucks for 461 moves/shift.
- **Two tests only (2026-11-15):**
  - **Test tab:** Yard test (outside) + Interior planner (inside; it contains the forklift checks). The separate
    Equipment test and the old storage-capacity numbers are gone from the UI.
  - **Routing:** the `equipmentTest` UI command opens the planner, and the analyst prompt says so. `fit/` and
    `racks.py` remain as backend modules.
  - **Coverage honesty:** `interior.plan` returns `scan_coverage_pct`. Below 30%, the whole-building estimate is
    flagged unreliable and not shown as a result (e.g. 242089: the scan covers 10%).
  - **Rack types:** selective, dense (double-deep), shelving, cantilever (lumber, pipe), open.
  - **Comparison images:** `build/ui/compare_real_vs_racks/`.
- **UI polish (2026-11-15):**
  - **Layout:** map full-screen. Results are a bottom strip (`components/ResultsCarousel.tsx`); "Full list" opens the old
    `ResultsRail` (store `listOpen`). The selected property is a no-scroll panel (`components/PropertyHud.tsx`) with
    6 tiles, 3 actions and drawers; the full card is the "Evidence & sources" / "Test results" drawer
    (store `cardDetail`).
  - **Analyst:** speaks only after the user has used voice (within 2 min). Unprompted remarks come only on test
    results, or on openCard once the profile is known, at most one per 30 s. They show as a "new note" badge, never
    auto-opening the panel. Clicking outside or pressing Esc collapses it.
  - **Skill pick:** `ui-ux-pro-max` (checklist; keep the existing tokens). Not the personal KrX brand.
- **ui-ux-pro-max pass (2026-11-15, styling and accessibility only, no simulation/API/store changes):**
  - `--color-ink-3` changed #6b7587 → #8a94a6: it was 3.3–3.9:1, now ≥ 5:1.
  - Global `:focus-visible` ring, pointer cursor, `prefers-reduced-motion`.
  - Text floor 11px: 87 sub-11px sizes fixed.
  - aria-labels on icon-only buttons (from their titles).
  - Voice orb moved onto the teal palette and made 52px.
  - Map markers: flat dots (size = building SF or rank, colour = availability, white ring = 3D scan), a name label on
    hover/selected, and a legend above the strip; the 3D tower bars are gone.
  - Houston = 95 warehouses: 50 available, 40 not listed, 5 off market; 52 have a usable 3D scan.
- **Small buildings (2026-11-15):**
  - A scan counts as warehouse floor if it is ≥ 5,000 SF, OR ≥ 25% of the building with ≥ 1,500 SF and ≥ 16 ft
    (`catalog.py`). 10 scans on 9 small buildings newly qualify; their interior twins are built.
  - The dock zone is reserved only when dock doors fall inside the scanned area.
  - Without doors, forklifts start from the largest open floor.
  - 10111 Grant Rd: 18 positions, but 0% reachable. The 1,942 SF scanned room splits into 8 pockets for a reach
    truck, and the existing racks against the walls are kept as structure.
- **Analyst UX (2026-11-15):**
  - **Draggable dock** (`components/DraggableDock.tsx`): grip above the analyst; arrow keys or Home; double-click
    resets; position saved in localStorage `pi.analyst.pos`; the thread height is capped to the room above it
    (`--dock-room`).
  - **Answers:** 1–2 sentences / ~35 words with the key number and source; explain how or why only when asked;
    event remarks are one sentence.
  - **Lead-in fillers** ("One moment.") only on spoken turns (`guide.speaking`, set per turn by `api_concierge`).
- **Guided buying flow (2026-11-15):**
  - **Greeting:** on load the analyst shows "What are we looking for today?" (VoiceOrb `vo-greet`, input + 3 example
    chips; once per browser session via sessionStorage `pi.greeted`). Text only, never auto-spoken.
  - **Need → search:** the prompt makes the first need call `update_buyer_profile` + `search_warehouses` in one step,
    asking at most one question. `update_buyer_profile` emits ui `profile`, and the strip shows it as chips
    (store `buyerProfile`).
  - **Strip:** the top 10 when ranked ("Top 10 for you"), numbered. Its order goes to the analyst (store `visibleIds` →
    ws `context.visible` → `set_context(visible=)` → session note "#1 …"), so "compare the first and second" = strip
    #1/#2.
  - **Head to head:** `components/CompareDuel.tsx`, over the map when exactly 2 ids are compared (3–4 still use
    CompareView).
    - A balance per criterion from `/api/compare`.
    - Advantages = criteria won, disadvantages = criteria lost + missed deal-breakers; unknowns are listed separately.
    - The yard grade and interior reach/min-per-pallet differences count only when both buildings have a result.
    - It is curated by the buyer profile (ops_text) when no operation key is set.
  - **Opening and closing:** tick two cards (compare toggle on hover), or say it. ui `closeCompare` closes.
  - **Verified headless (real Claude):** need → top 10 in ~8 s; "compare the first and second property" opens the
    duel in 2.8 s with the correct ids; "close the comparison" closes it. No page errors. Shots in
    `build/ui/v3.1/guided/`.
- **The strip follows every ask (2026-11-15):**
  - `rank_attribute` always sends `order`; the web key is `attr:<key>:<asc|desc>`.
  - "smallest/fewest/oldest…" in the words → asc (`analytics.rank_attribute`).
  - `closest_to`, `find_comparables` and a ranking `among` a set emit ui `results` {title, ids, values} → store
    `curated`, shown first by the carousel. A new search or ranking clears it.
  - Rankings default to the market tab on screen (ws `context.market` → `g.context["market"]`). Before this, an
    unnamed market ranked both cities while the strip ranked one, so flyTo flipped the market label.
- **Restart gotcha (2026-11-15):** never run `lsof -ti tcp:8010 | xargs kill`. It also kills the Vite dev server,
  which holds proxy connections to 8010, so 8080 dies and cards, docks and scans stop loading in an open tab.
  Kill only the listener: `kill $(lsof -ti tcp:8010 -sTCP:LISTEN)`.
- **Tests start only when asked (code gate):**
  - `guide.ASK_YARD` / `ASK_INSIDE` + `_asked()`: `yard_maneuver_test`, ui `yardTest` / `storageTest` /
    `equipmentTest`, and the planner opening in `storage_capacity` run only when the user's own message in this turn
    asks for a test (a truck count, "test", "run it", "yes"…). Never from an event or on the agent's initiative.
  - "Take me to X" → ui openCard.
- **Declutter + voice control (2026-11-15):**
  - **Top bar:** the search chips are one scrollable row (fade edge). The dock summary ("Docks: N seen on aerials · …")
    moved from under the search box to above the map legend.
  - **Movable strip:** grip in the strip header (drag, arrows, Home / double-click resets; localStorage
    `pi.strip.pos`). The map legend and dock summary follow it via the CSS vars `--strip-dx` / `--strip-dy`.
  - **When the analyst speaks:**
    - only for (a) the FIRST open of each property (repeat opens are silent; any profile, EVENT_GAP_S 8), and
      (b) questions the user types or speaks;
    - chips and buttons answer in text; test results are text-only notes.
  - **Controls beside the orb:** a persistent voice switch (localStorage `pi.voice.off` → ws `{type:"voice", on}`;
    off = text only, server-enforced) and a Stop button while speaking. A stop (barge_in during a reply) silences
    unprompted speech for 5 min (`QUIET_AFTER_STOP_S`).
- **Door review in the proof popup (2026-11-15):**
  - "What is it?" Dock-high / Drive-in / Door (type unsure): sets `kind` (kind_conf 1) and confirms.
  - "Not a door": Window / Wall / vent / Duplicate / Other building: rejects with `reason`. `docks._validate_door`
    keeps `reason`; `docksApi.reviewDoor`.
  - The type feeds the yard test (semis use only dock-high or unknown doors).
- **Page-shift bug (2026-11-15):**
  - **Symptom:** the whole UI slid left, with a black band on the right.
  - **Cause:** `overflow: hidden` still allows programmatic scrolling. The greeting input's `autoFocus` (with the
    analyst dragged near the right edge) and the strip's `scrollIntoView` scrolled the page.
  - **Fix:**
    - `html, body, #root` and the App root use `overflow: clip`;
    - the strip uses `scrollTo` on itself;
    - the greeting focuses with `preventScroll`;
    - DraggableDock clamps fully on screen on load, resize and size change.
  - **Rule:** never use `scrollIntoView` / `autoFocus` in overlays.
- **Door popup + analyst drag (2026-11-15):**
  - **Door popup:** placed above the door (else below), then clamped fully on screen, with max-height and an internal
    scroll. It re-places on resize (ResizeObserver), because the proof photo loads after the first placement.
  - **DraggableDock:**
    - the drag offset is measured from the VISIBLE content (default layout right-aligns it in the 380 px box; when
      moved it is left-aligned);
    - the clamp uses the content bounds, so the collapsed pill and orb can reach the screen edge.
- **Wrong-parcel docks fixed (2026-11-15):**
  - **Fallback:** `docks.osm_footprint` + `roads.buildings` (Overpass, cached, retries plus a mirror). When the
    exterior twin has `parcel_mismatch`, the dock build uses the OSM building under CoStar's point (≤ 60 m away,
    footprint × stories 0.6–1.6× CoStar). Sets `store.footprint_source = "osm"` and a note.
  - **15550 Milner Rd (11175666):**
    - before: 0 doors, because the search ran on two 16,777 SF sheds;
    - now: OSM way 1544621969 (1,205,372 SF) → 89 seen on aerials + 39 inferred (CoStar 194).
    - Only the dock store uses it; the yard test still uses the exterior twin's parcel.
  - **Door labels:** deck.gl `CollisionFilterExtension` (`@deck.gl/extensions` 9.4.1, now in package.json). Priority:
    confirmed > seen > inferred.
- **Wrong parcel fixed at the source (2026-11-15, supersedes the docks-only OSM fallback):**
  - **`exterior._site`:** when the Vexcel structures are < 30% of CoStar's size, find the OSM building
    (`_osm_building_ll`), re-query Vexcel at its centre, and otherwise use the OSM outline. The property area always
    contains the outline + 40 m.
    `vexcel_profile._site` delegates to it, so the twin, yard, docks and profile share one parcel.
  - **Window:** the frame cap is 420 m (was 250).
  - **`_fix_swallowed_roof`:** when the ground model takes a giant flat roof for ground (the footprint reads < 2 m
    tall), the ground is refitted as a plane from the lower 40% of the DSM.
  - **`_snap_to_roof`:** the OSM outline is snapped to the measured roof blob (the OSM outline sat about 6 m inside the
    real roof edge).
  - **`_vehicles`:** tiled over 280 m tiles (one 840 m call found 0 trailers; tiled: 43 trucks, 570 cars).
  - **15550 Milner Rd results:**
    - footprint 1,220,667 SF vs CoStar 1,230,097;
    - doors 178 seen on aerials + 26 inferred (CoStar 194);
    - 5-semi yard test 0/5 → 4/5 in 14.2 min (1 is a planner limit).
- **The Yard test button only opens the test** (PropertyHud `openYard`). The "Yard · 5 semis" tile's Run is the
  explicit standard check.
- **Dimensions + pallet (2026-11-15):**
  - **Pallet input:** `interior.pallet_spec` + body `pallet` {preset, width_in, depth_in, load_in}. Presets: GMA
    48×40 (default, ASSUMED), 42×42, 48×48, Euro 1200×800 (3 per bay). It derives:
    - frame depth = pallet depth − 6 in;
    - bay = per_bay × width + clearances (default reproduces 42 in / 8.25 ft);
    - level pitch = load + 12 in.
  - **Load-aware levels:** `generate(load_ft=)` makes the top load (top beam + load) stay under clear − sprinkler.
    The old rule ignored the load: at 12896757 it gave 6 levels with the top load at 32 ft over a 29.2 ft limit.
    3PL capacity there is now **4,230** (5 levels), not 5,076.
    `racks.py` / sim keep the old rule (no load_ft).
  - **Provenance:** `plan()` returns `dims` (each value {value, unit, source: measured|assumed|calculated|yours,
    basis}), `feasibility` (6 checks) and `machine_pos`. The pallet source is part of the cache key.
  - **UI:**
    - `racks/PlanProps.tsx`: selection kinds row / obj / zone / lane / col / machine / pallet / bay / building /
      aisle; the properties panel with source tags; `BayElevation`.
    - `RackPlan`: everything selectable when `dims` is passed (objects select, keep/remove moved into the panel); a
      to-scale forklift; dimension lines (solid = measured, dashed amber ≈ = assumed / calculated).
    - `InteriorWorkspace`: `PalletBar` (presets, W / D / load, debounced) + spec chips + layout checks.
  - **Object heights** are only measured bounds (< 6.6 ft, or full height ≥ deck − 4.9 ft); exact tops are not
    extracted.
- **True cost per pallet (2026-11-15, additive; `PROINSPECT_COST=0` turns it off):**
  - **`rent_comps.py`:** builds `build/economics/rent_comps.json` from CoStar lease comps
    (`leaseDeal.rateActual`, industrial / flex, 2020+, monthly × 12, $1–80/SF/yr). Tiers:
    - asking (30) > own signed lease (13) > submarket comps ≥ 3 (37) > market-wide comps (30, "rough");
    - market medians: Houston $10.59, LA $18.48 /SF/yr.
  - **`economics.py`** + `library/costs.yaml` (assumed unit costs, [U]):
    - rent × SF (available SF or RBA);
    - racking amortised by zone type;
    - forklift leases + operators from `interior.plan()` (read only; scaled to the building and the leased share);
    - results: $ per position per month, $ per pallet moved.
  - **API and UI:** `GET/POST /api/economics/{pid}`; analyst tool `cost_per_pallet`. UI: `web/src/economics/`
    (HUD line, `CostPanel` with what-if, duel row when > 10% apart). New `SrcTag` kinds recorded / estimate /
    unknown.
- **Flood exposure (2026-11-15, additive; `PROINSPECT_FLOOD=0` turns it off):** `flood.py`.
  - **Facts from the Vexcel DTM:**
    - pad vs the 30–300 m surroundings;
    - lowest ground at the walls;
    - ponding (priority-flood sink fill, ≥ 25 cm, ≥ 50 m², storm drains not modelled);
    - dock wells (usually "not resolved": the DTM smooths them);
    - HAND above the nearest OSM waterway (`roads.waterways`, ≤ 1.6 km; a 2 m DTM, retried at 4 m).
  - **FEMA zone:** the Esri Living Atlas NFHL mirror (fast), with FEMA's own MapServer/28 as fallback (it often
    times out). No polygon = "Outside FEMA's mapped flood areas".
  - **Indicator:**
    - Higher = SFHA, or HAND < 5, or at grade (pad < 0.5) with HAND < 15;
    - Moderate = 0.2% zone, or HAND 5–15, or pad < 3, or wells + ponding;
    - missing inputs → partial / "Not enough data", never safe.
  - **Cache, API, analyst:** `build/flood/{pid}.json`, keyed by the flood.py code hash.
    `GET /api/flood/{pid}` (cache only) / `?run=true`. Analyst tool `flood_exposure` + UI command `showFlood`.
  - **UI:** `web/src/flood/FloodPanel.tsx` (HUD line, panel with cross-section, ponding + waterway map layers while
    open, duel row). Cost and flood panels close each other.
  - **3200 Sam Houston:** Zone AE, pad 0.2 ft, 11.3 ft above Clear Creek → Higher.
  - **Precompute:** after editing flood.py, rerun the scratchpad `flood_precompute.py` (or open the panel).
