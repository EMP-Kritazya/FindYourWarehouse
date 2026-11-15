# Measurement methods: truck court, docks, interior, validation, accessibility

Research memo, 2026-11-14. Every number we publish has the shape `{value, unit, source, method, confidence, interval, qc_flags}`.
**[U]** marks something uncertain: unverified, from memory, or from a secondary source. Check it before quoting it to a user.
Frames: aerial work uses `elevation.grid` local ENU metres (0.5 m DSM grid). Mesh work uses the Matterport frame (z up).
`register.py` (Co-STAR) links the two frames.

---

## 0. Ground truth and sensor limits (read first)

| Quantity | Typical / standard value | Source |
|---|---|---|
| Truck court depth, 53' trailer | 120 ft min, 130 ft common, 135-150 ft preferred, ~185 ft with a trailer-parking row | Link Logistics loading-dock guide; industry blogs **[U: no code-level standard; these are industry norms]** |
| AASHTO WB-67 (53' interstate semi) | Min design turning radius 45 ft, centreline ~41 ft, min inside radius 4.4 ft **[U: inside radius differs between DOT tables]** | AASHTO Green Book, via Chester County and Iowa DOT tables |
| Dock height (dock-high door) | 48-52 in above the truck court (48 in most common) | Rite-Hite; Link Logistics |
| Dock door size | 8-9 ft wide x 9-10 ft high (10 ft fits high-cube trailers) | Blue Giant door-size guide |
| Drive-in (grade-level) door | 10-14 ft wide x 12-16 ft high, sill at grade **[U: vendor norms, not a standard]** | vendor pages |
| Dock door spacing | 12-14 ft on centre **[U: from memory]** | dock-equipment vendors |
| Clear height | Finished floor to the lowest fixed overhead obstruction (sprinkler deflector, joist or bottom chord, light, duct). Ceiling height, by contrast, goes to the deck. | Link Logistics; Cisco-Eagle |
| Vexcel DSM vertical accuracy | ~15 cm RMSE at 5.5-7.5 cm GSD, ~1 m at 15-20 cm GSD **[U: reseller listing]** | UP42 Vexcel Elevate DSM listing |
| Matterport accuracy | Pro2: ±50-70 mm per setup, about 1% at room scale. Error accumulates across setups, and in long, repetitive spaces scans can be misplaced. | ISPRS 2019/2020 tests; Geo Week News |
| Matterport depth range | Pro2 structured-light depth reaches only ~4.5 m, while Pro3 LiDAR reaches ~100 m. Deck geometry on Pro2 warehouse scans may be photogrammetric fill or missing **[U: check camera per model]**. | Matterport specs |

Our Phase 0 finding is that the mesh's roof deck sits 7-10 ft above CoStar's clear height. So CoStar measures to an obstruction (or
uses a "minimum clear" convention), not to the deck. We have to measure both, and calibrate.

CoStar fields, as mapped in `catalog.py`:
- `ceilingHeightFeet` + `ceilingHeightInches`
- `columnWidthFeet` x `columnDepthFeet`
- `numExternalLoadingDocks`, `numInternalLoadingDocks`, `hasCrossDocks`
- `driveInBays`
- `truckWells.truckCourtLengthInFeet`

---

## 1. Truck court depth from aerial data

**Inputs:** the footprint (`structures[].footprint.wkt`), the parcel polygon, DSM and DTM (newest year), the ortho, SAM3 masks on the ortho,
and dock-door detections on the obliques (Phase 0: obliques, all 4 directions, threshold 0.3-0.5).

**Steps**

1. **Find the dock facade.**
   - Project each oblique door detection to the footprint edge it faces.
   - The oblique direction gives the facade normal. The bbox centre, ray-cast to the DSM, gives a position along the edge.
   - Group doors per edge, and keep edges with ≥2 doors as dock edges.
   - Refine the facade line. Take the footprint edge and slide it ±3 m to the strongest nDSM step (`DSM-DTM`, roof to ground) along its normal.
     This corrects footprint offset and canopy overhang. Record the shift as a QC value.
2. **Build the paved mask** on the 0.5 m grid as `paved = (SAM3 "asphalt"|"concrete pavement"|"parking lot") ∧ (nDSM < 0.5 m)`.
   - Make vehicles transparent: `SAM3 "truck"|"semi trailer"|"car"` with nDSM < 4.5 m counts as paved, because parked trailers stand on the court.
   - Remove `SAM3 "grass"|"tree"|"shrub"` and cells with nDSM ≥ 0.5 m that are not vehicles.
   - Close the mask morphologically (1 m) to fill paint stripes and cracks.
3. **Cast rays.**
   - Spacing: every 2 m along the dock span, from the first door to the last door ±3 m.
   - Direction: along the outward facade normal, marching 0.25 m per step.
   - A ray stops at the first of these:
     - (a) ≥3 m of consecutive non-paved cells (landscaping or curb island)
     - (b) nDSM > 1.0 m that is not a vehicle (wall, fence, building, tree)
     - (c) another footprint (shared court: record `shared=True` and the opposite building id)
     - (d) the parcel edge (record `limited_by="parcel"`; access drives often cross it, so this is a soft stop)
     - (e) 80 m (no limit found)
4. **Compute depth.**
   - Depth is the median ray length, reported in ft (`m × 3.2808`). Report P10 and P90 too, and the stop-reason histogram.
   - For a shared court, also report the half-depth. CoStar may record either one **[U]**. Test both against CoStar and keep the one that fits better.
5. **Compute confidence** from these factors:
   - ray agreement: IQR / median < 0.15 is good
   - stop reasons: (a) or (b) give high confidence, (d) or (e) give low
   - facade-shift magnitude
   - SAM mask score
   - the year of the DSM against the year of the ortho (they must agree: compare `heightgrids` across years)

**Parameters to tune:** the nDSM thresholds of 0.5 and 1.0 m, the 3 m landscape run, and the ray spacing.

**Failure modes**
- Trailers parked nose-out exceed 4.5 m or merge with the building in the DSM.
- Trees overhang the court and create false stops. Fix: use the DTM-only slope plus the ortho, and ignore canopy when the SAM label is tree and the DTM is flat.
- The court continues into a drive aisle or a public street. The depth is then "to the curb", but the usable depth includes the aisle, so flag it.
- Truck wells are depressed and the DTM dips. That is fine for depth and useful for step 2 of the door classifier below.
- Footprint misregistration of 1-3 m. Vexcel footprints are usually good, but check them against the DSM.
- Cross-dock buildings have 2 courts. Report each one, and compare the max with CoStar **[U: CoStar convention]**.

**Validation**
- Compare with `truckCourtLengthInFeet` where it is present.
- Hand-measure 10 sites in the ortho (two clicks) as a second reference, because CoStar values are often rounded or marketing numbers.
- Target: |error| ≤ 10 ft on 80% of sites.

**Sources**
- https://www.linklogistics.com/news-insights/industrial-real-estate-101/what-is-a-loading-dock-a-guide-to-types-configurations-and-key-features/
- https://industrialspaces.net/learn/logistics-operations/fleet-access-considerations-warehouse-sites
- https://chescoplanning.org/MuniCorner/MultiModal/28-Vehicle.cfm
- https://iowadot.gov/design-manual/chapter-6-geometric-design/6a-2-intersection-design-vehicle
- https://www.txdot.gov/manuals/des/rdw/chapter-4--basic-design-criteria/4-3-traffic-characteristics/4-3-3-control-vehicles.html
- https://up42.com/marketplace/data/archive/vexcel-elevate-dsm

**WB-67 sanity check (optional):** a 90° back-in needs about trailer length + tractor + swing. Industry guidance converges on 120-135 ft,
so flag `<110 ft` as "tight for 53' trailers" and `≥130 ft` as "full 53' maneuvering". This is a rule, not a simulation.
For a better check, sweep a WB-67 path with a simple kinematic bicycle model (tractor wheelbase ~19.5 ft, trailer kingpin to axle ~40-41 ft) **[U: AASHTO dims]**.

### 1b. Dock-high vs grade-level (drive-in) doors, from the obliques

There are four independent cues. Combine them with a simple logistic or vote rule, and report each cue in the explanation.

1. **Sill ratio (scale-free).**
   - In the oblique, a door's vertical edges and the ground gap below it lie in roughly the same facade plane, so the oblique tilt cancels out.
   - Compute `r = gap_px / door_h_px`, where the gap runs from the door bottom down to the pavement line. Use SAM masks for the door and for the pavement or wall base.
   - Dock-high: gap ≈ 4 ft and door ≈ 9-10 ft, so r ≈ 0.4-0.55. Drive-in: r < 0.1.
   - Metric version: estimate pixels per metre on the facade from the eave height (DSM roof minus DTM) divided by the eave height in pixels.
     That gives the door height and the sill height in ft. Drive-in doors are usually ≥12 ft tall.
2. **Width and aspect.** At the same facade scale, a door ≥11 ft wide or ≥12 ft tall suggests drive-in. 8-9 ft x 9-10 ft doors in a regular row suggest dock-high.
3. **Dock equipment.** Use SAM3 labels `"dock seal"`, `"dock shelter"`, `"dock bumper"` and `"dock leveler"` (open-vocabulary, so precision is unknown **[U]**).
   Seals or shelters around the door mean dock-high. A ramp or apron sloping up to the sill means drive-in.
4. **DTM profile.**
   - Sample the DTM 0-20 m out from the door.
   - A truck well shows a ~1.0-1.3 m dip toward the wall: a depressed dock (counts toward `truck_wells`).
   - A ramp rising toward the door means a grade-level door with a ramp.
   - A flat profile is ambiguous, so use cues 1-3.
   - The DTM vertical error is ~15 cm, so a 1.2 m dip is detectable **[U: depends on DTM filtering near walls]**.

**Dedupe and count:**
- Merge detections from all 4 obliques by their projected position on the footprint edge, using 1D clustering with 2.5 m tolerance.
- Count dock-high and drive-in doors separately.
- Compare with `numExternalLoadingDocks` and `driveInBays`.
- Failure modes:
  - a closed door and the wall look alike in low-contrast metal siding
  - trailers parked in the docks hide the doors (count trailer noses at the wall as occupied docks)
  - canopies hide the sills

---

## 2. Warehouse interior from the Matterport mesh

**Preprocessing**
- Use the 500k mesh for anything overhead. The 50k mesh decimates sprinklers and joists away.
- Sample the surface uniformly: area-weighted points at ≤5 cm spacing (reuse `geometry._sample_surface`). Keep per-point normals.
- Voxelise at 10 cm. Drop connected components < 0.05 m³ (floaters).
- Align to Manhattan axes with `matterport.wall_axes` (area-weighted 4θ circular mean). Report its residual spread as an axis-QC value.

### 2a. Floor extraction
1. Build a histogram of the z of upward-facing area (`n_z > 0.95`) in 2 cm bins.
   - The largest peak is the main floor. Secondary peaks are mezzanines, dock pits, office floors, or yard paving.
2. RANSAC-fit a plane to points within ±10 cm of the peak (Open3D `segment_plane`, distance 2 cm, 1000 iterations).
   - Record the tilt. More than 0.5° is a mesh warp: flag it.
   - Record the inlier RMS. Large slabs have real flatness of about ±1-2 cm.
3. The floor mask is the 10 cm cells with inlier area, closed at 30 cm.
4. **Indoor vs yard.** Phase 0 found yards inside some scans. Keep only floor cells that have mesh surface overhead (> floor + 4 m) and that
   fall inside the registered footprint (`register.py`, 4-rotation + translation search).
   - Yard cells are "floor with sky", so they have no overhead hits.
5. Reconcile the floor area with the sum of `rooms[].areaFloor` and with CoStar RBA/GBA. Report the ratio.

### 2b. Ceiling, roof deck and clear height
Work per 1 m XY cell over the indoor floor.
- **Deck z** is the highest surface in the cell. Take its median over cells, robust to skylights and monitors. It gives *ceiling (deck) height*.
- **Obstruction z** is the lowest downward-facing or horizontal surface (`n_z < -0.3` or thin horizontal) inside the **overhead band**.
  The band runs from `deck - 4.5 m` up to the deck.
  - Exclude the cells of columns (2c) and of floor-connected stacks (racks or walls). A cell is floor-connected when its vertical occupancy is continuous from 0.3 m up to the band.
  - Lights, sprinkler mains and joist bottom chords appear as sparse low hits in the band.
- **Clear height** is the P5 of obstruction z over overhead-band cells, minus floor z. Report P5, P50 and the deck as a triple.
  - CoStar "clear" should be close to P5-P20. The fit will tell us which **[U]**.
- **Empirical fallback.** Fit `costar_clear ≈ deck - δ` on the scans that have CoStar values (Phase 0: δ ≈ 7-10 ft). Use it only when the band has too few hits.
  Mark the result `method="deck-minus-offset"` with a wide interval.
- **Coverage QC.** Record the fraction of indoor floor cells with any overhead hit. If it is below 60%, the deck is not reliably captured (a Pro2 range limit).
  In that case downgrade the confidence, or fall back to `rooms[].height`, the Matterport room height (its definition is unclear **[U]**).

**Failure modes**
- Mesh hole-filling makes smooth fake ceilings.
- Pro2 depth range.
- High-bay racking reaches the deck. Racks are floor-connected, so they are excluded.
- Ducts or HVAC units are real obstructions. Keep them.
- Sloped roof: the deck varies by ±0.5-1 m. Report min and max, and report clear at the eave side.

### 2c. Column detection and bay spacing
1. Take two horizontal slices: S1 at floor + [1.2, 2.0] m and S2 at deck - [1.5, 0.5] m. Rasterise occupancy at 5 cm.
2. Candidates are connected components in S1 with an equivalent diameter of 0.2-0.8 m and a near-square or circular shape (W-shape or HSS steel, concrete tilt-up pilasters).
   They must also appear in S2 within 0.3 m, which means floor-to-deck.
   - Rack uprights are 8-10 cm, sit in long rows at 2.4-3 m pitch and stop below the deck, so they are rejected by size and by S2.
   - Mark components touching walls as pilasters (perimeter columns).
3. **Grid fit.**
   - Rotate the centres to the Manhattan axes. For each axis, cluster the 1D coordinates (tolerance 0.4 m) to get grid lines.
   - Fit the spacing s by searching s ∈ [6, 22] m in 0.05 m steps, minimising robust residuals of `x_i - (x0 + k_i s)` (Huber loss) on the line positions.
     Allow one wider "speed bay" at the dock wall (common at 60 ft) **[U: industry norm]**.
   - Report `bay_x × bay_y` in ft, the support (number of lines), the residual RMS and the fraction of predicted grid nodes that have a detected column (completeness).
4. **Confidence** is high when there are ≥3 lines per axis, the residual is < 0.3 m and completeness is > 60%.

**Failure modes**
- Occlusion by inventory.
- Partial scans with too few lines.
- Mezzanine posts.
- Rows of free-standing pallets read as columns: the S2 test fixes this.

### 2d. Doors and openings in exterior walls
- Reuse the `geometry._windows` idea: from the floor boundary, look outward 0.6 m for wall voxels over z ∈ [0.1, 3.5] m.
- An opening with its bottom at floor level and width ≥ 2.4 m is an overhead door candidate. Width 0.8-1.2 m and height ~2.1 m is a man door.
- Closed overhead doors get scanned as surfaces. Detect them from the panos instead: run SAM3 or Claude vision "overhead door" on skybox faces, then ray-march the bbox into the voxels.
  This is the `windows.py` pattern, with face calibration and merging across panos.
- Interior evidence cannot tell dock-high from drive-in, because both sit at the inside floor. Exception: a visible dock leveler pit, a 1.8-2.4 m long rectangular depression in the floor at the door.
  Fuse the interior count with the oblique count. Treat agreement as a confidence boost.

### 2e. Free floor area
- Free area = indoor floor cells (2a) whose occupancy in z ∈ [0.1, 2.0] m is empty.
- Subtract a 0.15 m dilation around columns, racks and offices.
- Report the gross indoor floor, the clear floor and the % occupied.
- The scan only shows the floor on the day of scanning: label it `as_scanned`.

**Sources (point cloud / scan-to-BIM)**
- Schnabel, Wahl, Klein 2007, *Efficient RANSAC for point-cloud shape detection*, CGF. https://doi.org/10.1111/j.1467-8659.2007.01016.x
- Okorn et al. 2010, *Toward automated modeling of floor plans* (height histograms for floor and ceiling, wall density maps). https://www.ri.cmu.edu/publications/toward-automated-modeling-of-floor-plans/ **[U: URL from memory]**
- Adan & Huber 2011, *3D reconstruction of interior wall surfaces under occlusion and clutter* (openings via ray occupancy). https://doi.org/10.1109/3DIMPVT.2011.42 **[U: DOI from memory]**
- Xiong, Adan, Akinci, Huber 2013, *Automatic creation of semantically rich 3D building models from laser scanner data*, Autom. Constr. https://doi.org/10.1016/j.autcon.2012.10.006 **[U: DOI from memory]**
- Coughlan & Yuille 1999, Manhattan World (ICCV). Review of indoor scan-to-BIM, including histogram methods and their clutter sensitivity (ISPRS 2022): https://isprs-archives.copernicus.org/articles/XLIII-B2-2022/193/2022/isprs-archives-XLIII-B2-2022-193-2022.pdf
- Manhattan-world RANSAC for walls, floors and ceilings: https://core.ac.uk/download/35280467.pdf
- Indoor element detection including columns (MDPI IJGI 2025): https://www.mdpi.com/2220-9964/14/11/428
- Column segmentation for scan-vs-BIM: https://isprs-archives.copernicus.org/articles/XLVIII-G-2025/1455/2025/
- Clear-height definitions: https://www.linklogistics.com/news-insights/industrial-real-estate-101/what-is-clear-height-a-guide-to-warehouse-ceilings-and-vertical-space/
  and https://www.cisco-eagle.com/blog/?p=14371
- Open3D plane segmentation: https://www.open3d.org/docs/release/tutorial/geometry/pointcloud.html#Plane-segmentation
- Matterport accuracy: https://isprs-archives.copernicus.org/articles/XLII-2-W11/1059/2019/isprs-archives-XLII-2-W11-1059-2019.pdf
  and https://www.geoweeknews.com/articles/testing-matterports-pro-2-professional-3d-scanning/

---

## 3. Validation protocol

**Reference set**
- Use the 57 properties / 74 scans with warehouse floor (Phase 0) plus all properties with obliques (truck court, docks).
- Split by **property**, not by scan: several scans share a property, and one model can map to several PropertyIds. Otherwise residuals leak.
- Hold out the hero sites (238885, 237474, 278605) as a demo set that we never tune on.

**Treat CoStar as a noisy label.**
- Clear height is rounded to the foot, may be "minimum" or "maximum" clear, and may be stale.
- Dock counts may include doors on other buildings.
- `truckCourtLengthInFeet` is sparse.
- For each metric, also hand-label 10-15 sites (ortho clicks or the Matterport measure tool). Report agreement against both, plus CoStar-vs-hand (the label noise floor).

**Metrics per field**

| Field | Our value | Metric | Target **[U: our proposal]** |
|---|---|---|---|
| `ceilingHeightFeet` | clear P5 (and deck) | MAE, bias, % within ±1 ft and ±2 ft | 70% within ±2 ft |
| `columnWidthFeet × columnDepthFeet` | bay_x × bay_y (unordered pair match) | % within ±2 ft on both axes | 70% |
| `numExternalLoadingDocks` | dock-high count | exact %, within ±1 or ±10%, signed error | 60% within ±10% |
| `driveInBays` | drive-in count | confusion matrix (dock-high vs drive-in), per-door precision and recall on 5 hand-labelled sites | precision ≥ 0.8 |
| `truckCourtLengthInFeet` | median ray depth | MAE, % within ±10 ft and ±20 ft | 80% within ±10 ft |

**Calibration and intervals**
1. **Systematic bias.** Fit `y_costar = a + b·ŷ` on the calibration split. For clear height, this learns the deck-to-obstruction offset if P5 is biased.
   Apply the correction only if it reduces held-out MAE. Report raw and corrected values.
2. **Split conformal intervals** (distribution-free, finite-sample).
   - On the calibration properties, compute residuals `r_i = |y_i - ŷ_i|`.
   - Set `q = r_(k)` with `k = ⌈(n+1)(1-α)⌉`, and report `ŷ ± q` at 80% and 90%.
   - With n ≈ 25-30 per field, use α = 0.2 (k ≈ 25 of 30). Say so in the UI.
   - **Normalised (locally adaptive) conformal:** use the score `r_i / σ̂_i`, where σ̂ comes from internal QC (ray IQR, grid residual, coverage fraction).
     Then intervals widen where QC is poor. This turns our QC flags into calibrated intervals.
   - **Mondrian conformal:** compute quantiles separately per QC tier (high, medium, low) once n allows.
   - Small n: use **jackknife+** (leave-one-property-out) to use all data.
3. **Confidence label.**
   - Map the conformal interval width to high, medium or low.
   - Check that the label means something: Spearman(width, |error|) > 0 on held-out data, and empirical coverage ≈ nominal (report it).
   - Counts: give an interval on the count, or a per-door detection probability calibrated by isotonic regression on hand-labelled doors.
4. **Disagreement reporting.**
   - If |ŷ − CoStar| exceeds the interval, show both values with a "measured differs from listing" badge (rank.py already keeps CoStar in `alt`).
   - Do not silently override when our confidence is low.

**Sources**
- Angelopoulos & Bates, *A Gentle Introduction to Conformal Prediction*: https://arxiv.org/abs/2107.07511
- Barber et al. 2021, jackknife+: https://arxiv.org/abs/1905.02928
- Lei et al. 2018, distribution-free predictive inference (normalised residuals): https://arxiv.org/abs/1604.04173
- MAPIE (Python implementation): https://mapie.readthedocs.io

---

## 4. STRETCH: accessibility from residential Matterport scans

**Scope caveats**
- Private single-family homes are generally *not* covered by the ADA or the FHA design requirements.
- FHA design and construction rules cover multifamily buildings with 4+ units first occupied after 13 Mar 1991: all ground-floor units, plus all units in elevator buildings (24 CFR 100.205).
- ADA Title III covers public accommodations. ANSI A117.1 Type A/B units come in through IBC Ch. 11.
- Position the output as a **"usability screen"**, never as a compliance certification.

**2010 ADA Standards** (section numbers per the 2010 Standards; spot-check them against the Access Board guide):

| Criterion | Value | Section |
|---|---|---|
| Door clear width | ≥ 32 in, open 90°, face of door to stop | 404.2.3 |
| Clear width of the accessible route | ≥ 36 in (may narrow to 32 in for ≤ 24 in length) | 403.5.1 |
| Passing space | 60 in circle or a 60 in T every 200 ft | 403.5.3 |
| Turning space | 60 in diameter circle, or a T-turn within a 60 in square with 36 in arms | 304.3.1, 304.3.2 |
| Clear floor space | 30 x 48 in | 305.3 |
| Change in level | ≤ 1/4 in vertical; 1/4-1/2 in beveled at ≤ 1:2; > 1/2 in must be a ramp | 303.2, 303.3, 303.4 |
| Door threshold | ≤ 1/2 in (3/4 in for existing or altered thresholds) | 404.2.5 |
| Ramp slope | ≤ 1:12; cross slope ≤ 1:48; rise ≤ 30 in per run; clear width ≥ 36 in; landings ≥ 60 in long | 405.2, 405.3, 405.6, 405.5, 405.7 |
| Running slope above which a route counts as a ramp | > 1:20 | 402.2, 403.3 |
| Reach range, forward unobstructed | 15-48 in high | 308.2.1 |
| Reach range, side unobstructed | 15-48 in high | 308.3.1 |
| Door maneuvering clearance | Front approach, pull side: 18 in beyond the latch side, 60 in deep (table 404.2.4.1) | 404.2.4 |
| Toilet clearance | 60 in wide x 56 in deep (wall-hung); centreline 16-18 in from the side wall | 604.3.1, 604.2 |
| Grab bars | 33-36 in above the floor | 609.4 |
| Kitchen clearance | 40 in between opposing elements (pass-through); 60 in for U-shaped | 804.2.1, 804.2.2 |
| Work surface height | 28-34 in | 902.3 |
| Accessible dwelling units | Mobility features | 809 (references 804 kitchens, 1003-style features) |

**FHA Accessibility Guidelines (24 CFR 100, 1991) and the HUD Fair Housing Act Design Manual (1998)** set seven requirements.
The measurable ones are Req 3, 4, 5 and 7:

- **Req 3, usable doors:** a "nominal 32 in" clear width. The Design Manual accepts a 2'-10" door, which gives about 31-5/8 in **[U: verify page]**.
- **Req 4, route into and through the unit:**
  - ≥ 36 in route, which may narrow to 32 in at doors (≤ 24 in deep)
  - interior thresholds ≤ 1/4 in, or ≤ 1/2 in if beveled
  - exterior door thresholds ≤ 3/4 in, beveled ≤ 1:2
  - balcony or patio step-downs allowed up to 4 in onto impervious surfaces **[U: exact exception wording]**
- **Req 5, controls:** switches, outlets and thermostats at 15-48 in.
- **Req 6:** grab-bar reinforcement. It is not observable in a scan.
- **Req 7, kitchens and baths:**
  - 30 x 48 in clear floor space at each appliance
  - ≥ 40 in between opposing counters, cabinets, appliances or walls in a galley; 60 in turning circle in a U-shaped kitchen
  - 30 x 48 in clear floor space outside the swing of the bathroom door
  - fixture clearances per the Design Manual figures
- ANSI A117.1 Type B units are roughly equivalent to FHA (a FHAG safe harbor). Type A units are stricter, close to ADA.
  The ICC standard text is paywalled, so cite section numbers only after checking **[U]**.

**Algorithm (on the `geometry.py` voxel model, 2.5 cm XY grid for this task)**
1. **Walkable mask:** floor cells with no occupancy in z ∈ [0.05, 2.0] m. Remove furniture separately: report "as furnished" and "structural" (only walls and fixed cabinets, detected as floor-connected objects that are coplanar with walls or sit ≥ 0.85 m high with a counter-top plane).
2. **Route width:** compute the distance transform of the walkable mask. A path is ≥ 36 in wide where the medial-axis radius is ≥ 18 in.
   Compute the max-min-width path (widest path, a bottleneck shortest path) from the entry door to each room. Report the bottleneck width and where it occurs.
3. **Door clear width:** at each opening (2d), the free width between jambs at z ∈ [0.3, 1.8] m. Report the minimum across heights. Doors scanned half-open make this unreliable: use the panos to confirm.
4. **Turning space:** morphological erosion of the walkable mask by a 30 in radius disk. Any surviving cell means a 60 in circle fits.
   T-turn: template-match a T shape (60 in square, 36 in arms, 4 orientations) per room.
5. **Thresholds and level changes:** floor-height discontinuities across door lines from the floor z field.
   Matterport noise is ±1-2 cm, while a 1/2 in threshold is 1.27 cm. **Do not report pass or fail below about 2 cm.** Report "possible step" with confidence (mark this as a resolution limit).
6. **Kitchen and bath:** find counter planes at 0.85-0.95 m via RANSAC horizontal planes, and clearance between opposing counters along their normal. Check for a 30x48 clear floor space rectangle at the sink, range and toilet with an oriented box fit on the walkable mask.
7. **Reach and controls:** detect switches and outlets in the panos (Claude vision), then ray-march to get the height. Check 15-48 in.
8. **Validation:** hand-measure 5 units with the Matterport measure tool. Report width error (expect ±2-5 cm) and use conformal intervals as in §3.
   Report "passes", "fails" or "indeterminate (within measurement error of the threshold)". The indeterminate band is ± the interval half-width.

**Sources**
- 2010 ADA Standards: https://www.ada.gov/law-and-regs/design-standards/2010-stds/
- Access Board guide to the ADA Standards, chapters 3-4: https://www.access-board.gov/ada/guides/
- FHA Design Manual chapters: https://www.huduser.gov/publications/pdf/fairhousing/fairch3.pdf and https://www.huduser.gov/publications/pdf/fairhousing/fairch4.pdf
- FHAG Requirement 4 text: https://corada.com/documents/24-cfr-100-fhag/requirement-4-accessible-route-into-and-through-the-covered-dwelling-unit
- Exterior-threshold discussion: https://www.corada.com/documents/24-cfr-100-fhag-preamble/thresholds-at-exterior-doors-thresholds-to-balconies-or-decks
- HUD Requirement 3 workbook: https://www.hud.gov/sites/dfiles/FHEO/documents/The-Fair-Housing-Act-Requirement-3-Usable-Doors-Participant-Workbook.pdf
- HUD Requirement 4 workbook: https://hud.gov/sites/dfiles/FHEO/documents/Fair-Housing-Act-Requirement-4-Accessible-Route-Into-and-Through-the-Dwelling-Unit-Participant-Workbook.pdf
- 24 CFR 100.205: https://www.ecfr.gov/current/title-24/subtitle-B/chapter-I/subchapter-A/part-100/subpart-D/section-100.205
- ANSI/ICC A117.1 (paywalled): https://codes.iccsafe.org/content/ICCA117_12017

**Prior work**
- RASSAR (CHI 2024): AR room scanning that flags ADA-informed issues, 3.5x faster than manual audits. https://makeabilitylab.cs.washington.edu/media/publications/Su_RassarRoomAccessibilityAndSafetyScanningInAugmentedReality_CHI2024.pdf
- RAIS, a robotic indoor accessibility scanner: https://makeabilitylab.cs.washington.edu/project/RAIS
- OmniPath (2026), aerial LiDAR plus OSM for ADA slope checks outdoors: https://arxiv.org/pdf/2606.24129
- Point-cloud accessibility digital twin (FIG 2025, ~90% agreement with manual measurements): https://fig.net/resources/proceedings/fig_proceedings/fig2025/papers/ts05d/TS05D_khoshelham_radanovic_et_al_13488_abs.pdf
- Accessibility extracted from floor plans (Kostić, Münster): https://run.unl.pt/bitstreams/6889f4b2-b451-40a2-91be-c8bb7bb880bf/download
- Indoor automated assessment is under-studied compared with sidewalks. A Matterport-based usability screen with calibrated "indeterminate" bands would be novel.

---

## Build order (suggested)
1. Floor + deck + clear-height triple, with coverage QC (2a/2b). It is cheap and validates against `ceilingHeightFeet` on 57 properties.
2. Dock dedupe and dock-high/drive-in classifier (1b), validated against dock counts.
3. Truck-court rays (1), validated against `truckCourtLengthInFeet` plus 10 hand clicks.
4. Columns and grid fit (2c).
5. Conformal wrapper shared by all fields (§3).
6. Accessibility screen (§4), only if time allows.
