# Shift simulation: research memo (2026-11-14)

Scope: a deterministic, seeded simulation of ONE shift (6 to 10 h) of a business profile (`library/profiles.yaml`) inside a real
building (Matterport mesh voxelized to a 25 cm occupancy grid) plus its truck court (Vexcel ortho), using the vehicles in
`library/equipment.yaml`. Tags: **[V]** = verified this session from a primary source, **[U]** = uncertain or a rule of thumb, so check it
before presenting it as fact.

## 0. TL;DR (decisions)

1. **The engine is SimPy 4.1.2 (MIT).** Events cover docks, trucks, waves and labor. A single `TrafficController` process ticks every
   0.5 s and moves all floor vehicles. Do not model space with SimPy resources.
2. **Floor traffic runs on a lane graph** built from the generated layout (aisle centerlines plus cross-aisles plus the dock aisle, nodes about 1.25 m
   apart), **not on the raw 25 cm grid**. The raw grid is used only for layout, clearance and swept-path checks.
3. **MAPF uses rolling-horizon prioritized planning with a space-time reservation table** (Cooperative/WHCA*-style), written in about 200
   lines of our own Python. It handles mixed speeds and multi-cell footprints. PIBT (vendored from `Kei18/pypibt`, MIT) is the
   fallback or "AMR mode" for homogeneous robots.
4. **Forklift motion uses trapezoidal speed profiles** on graph edges, a turn-speed cap, and additive lift time from `lift_fpm`.
   No Hybrid A* is needed indoors.
5. **For trucks**, a kinematic tractor-semitrailer model generates the back-in path by driving the rig *forward out of the dock*
   and reversing time. The polygon is rasterized against the yard grid for the swept-path check, and the result is validated against the
   AASHTO WB-67 turning numbers.
6. **The layout comes from a rule-based generator.** It searches rack orientation and offset in 25 cm steps so that columns land in flues or in the
   rack line. Aisle widths come from `min_aisle_ft`, staging sits 60 to 70 ft deep at the docks, and slotting is ABC by distance to the dock.
7. **Trust comes from 30 seeded replications.** Report P10/P50/P90, show analytic cross-checks (Bartholdi travel models, Little's law, dock
   utilization) next to the simulated numbers, and show an event-log hash so the same seed gives the same log.

## 1. Recommended stack (Python 3.12, all on PyPI; versions checked on PyPI 2026-11-14) [V]

| Package | Version | License | Use |
|---|---|---|---|
| `simpy` | 4.1.2 | MIT | DES kernel (Resource, PriorityResource, Store/FilterStore, Container) |
| `numpy` | 2.5.4 | BSD-3 | grids, `Generator(PCG64)` + `SeedSequence.spawn` RNG streams |
| `scipy` | 1.18.1 | BSD-3 | `ndimage` (distance transform, labeling columns), `optimize.linear_sum_assignment` (task to vehicle), stats |
| `networkx` | 3.7 | BSD-3 | lane graph, all-pairs/single-source Dijkstra heuristics (precompute per goal) |
| `shapely` | 2.2.0 | BSD-3 | rack and vehicle polygons, swept-path union, clearance buffers |
| `scikit-image` | 0.26.0 | BSD-3 | morphology on the occupancy grid (column/wall extraction, skeletonize for yard lanes) |
| `rsplan` | 1.0.10 | MIT | Reeds-Shepp paths (yard truck and box truck, optional); pure Python, stable but last release in 2023 |
| `pyyaml` | 6.0.3 | MIT | library files |
| `numba` | 0.68.1 | BSD | *optional*, only if space-time A* gets slow |

Considered and not chosen:
- `salabim` 26.0.9 (MIT, active, Nov 2026) has built-in monitors and 2D/3D animation, but our web scene does the rendering, so
  SimPy's smaller surface wins.
- `ciw` 3.2.7 (MIT) is for queueing networks only and has no space. It is useful only as an independent analytic check of the dock queue.
- `cbs-mapf` 0.5 is stale (2020).
- `pogema` 1.4.0 (MIT) is an RL environment, which is overkill here.
- `python-motion-planning` 2.1 is **GPL-3**, so avoid it.
- No pip package of LaCAM or PIBT exists (`pypibt` and `pylacam` return 404 on PyPI). Vendor the code from GitHub instead.

Sources: https://pypi.org/project/simpy/ , https://simpy.readthedocs.io/en/4.1.2/ , https://pypi.org/project/salabim/ ,
https://github.com/Kei18/pypibt , https://github.com/Kei18/pylacam , https://github.com/builtrobotics/rsplan

## 2. Discrete-event model (question 1)

**Why SimPy:** it is mature, small and MIT-licensed, and its deterministic event ordering (events at the same time fire in scheduling order) is what we
need for reproducibility. Salabim is the only serious alternative, and it is better if you want built-in animation or monitoring, which we do not
(https://www.theoj.org/joss-papers/joss.00767/10.21105.joss.00767.pdf). Both are process-based, so switching later is cheap.

The model, as one `Shift` object holding one `env` and one seed:
- **Truck arrivals:** each truck is one process. The schedule comes from the profile's `inbound`/`outbound` counts, spread over the shift by appointment slots
  plus lateness noise (`N(0, 10 min)`, clipped) [U]. Flow: gate, then yard queue, then `docks.request(priority=...)`. Use
  `PriorityResource`, with one resource per door *type* (dock-high vs drive-in, from the Vexcel door classification; vans need drive-in).
  The truck then backs in (time = path length / 5 mph from `approach_speed_mph`, plus about 60 s to set the dock lock [U]), unloads or loads,
  and departs. The unload is **not** a fixed `dock_turn_min`. It ends when the last pallet task finishes, and `dock_turn_min` is the
  validation target (35 min for a 26-pallet 53 ft trailer, i.e. about 81 s per pallet).
- **Pallets and tasks:** each pallet is a task in a `FilterStore` (putaway, replenish, retrieve, or load). Staging lanes are a `simpy.Container`
  per door (capacity about 26 pallets, one trailer). When a lane is full, unloading blocks, which is the congestion signal users care about.
- **Pick waves:** waves are released at fixed times (every 60 to 120 min, keyed to outbound departures) [U]. The order lines in a wave are
  `units / units_per_line`, sized by profile. The picker pool is a `Resource(capacity=pickers)`. Each pick trip batches k lines, and its
  time is travel plus k * t_pick. For each-pick (e-commerce) and last-mile profiles, picking is a *rate* model (Section 5 formula), not
  per-vehicle MAPF. Only pallet-moving vehicles, AMRs and tuggers go through traffic.
- **Task assignment:** every tick, idle vehicles are assigned to open tasks with `linear_sum_assignment` on (graph distance + age penalty), with
  `dock_ok`/`lift_height_ft` used as hard filters (a reach truck never enters a trailer). Prefer dual-cycle pairing of a putaway and a retrieval
  (Bartholdi section 6.2.1). Under single-cycle operation, a forklift deadheads at least 50% of the time.
- **Coupling to space:** a vehicle process calls `yield controller.move(vehicle, goal)`, which returns an event that the traffic
  controller succeeds on arrival. The controller is one SimPy process looping `yield env.timeout(DT)` with DT = 0.5 s.
- **Breaks and charging:** add one 30 min break per 8 h shift per operator [U]. Battery swaps or charging come from `charge.type`. These are second-order,
  so add them last.
- **KPIs logged per event:** truck dwell (gate to depart), door utilization, yard queue length, staging occupancy, vehicle utilization
  (loaded / empty / waiting-in-traffic), congestion delay per vehicle-hour, units shipped vs plan, and last-truck-out time.

## 3. Multi-agent path finding (question 2)

State of the art (2023 to 2026):
- **PIBT** (Okumura et al., AIJ 2022) gives one-step priority inheritance with backtracking. It is extremely fast and is the core of every top lifelong
  solver. It guarantees that each agent reaches its goal only on biconnected graphs, and dead-end rack aisles break that.
- **LaCAM / LaCAM\*** (Okumura, AAAI-23, IJCAI-23, and `lacam3` 2024 to 2026) runs a lazy search over configurations with PIBT as the successor generator. It is
  complete, eventually optimal for one-shot MAPF, and scales to thousands of agents in C++.
- **CBS/ECBS/EECBS** are optimal or bounded-suboptimal. They are too slow and too fragile for lifelong replanning in Python.
- **Lifelong:** RHCR (Li et al., AAAI-21) plans a window of w steps every h steps (https://github.com/Jiaoyang-Li/RHCR).
- **League of Robot Runners:** the 2023 winner was WPPL, Windowed Parallel PIBT + LNS (https://github.com/DiligentPanda/MAPF-LRR2023). The 2024
  combined-track winner was "No Man's Sky", with enhanced PIBT, guidance graphs and parallel LNS
  (https://expo25.leagueofrobotrunners.org/resources/No-Man-Sky.pdf).
- **LoRR action model:** forward, rotate in place, and wait. Rotation costs time. The 2026 edition adds stochastic execution delays
  (https://www.leagueofrobotrunners.org/problem , ICAPS-24 demo paper https://harabor.net/data/papers/lorr-demo-icaps24.pdf).
  [U] We could not confirm what Amazon, Locus or Geek+ deploy in production. The public evidence (RHCR, WPPL, the "Deploying ten thousand robots" paper by
  Jiang et al., arXiv 2410.21415) points to windowed, PIBT-family planners plus guidance or one-way highways.

**Our choice: rolling-horizon prioritized planning (WHCA\*-style) on the lane graph.**
For 20 to 60 heterogeneous vehicles (a 14 ft VNA, a 30 ft tugger, a 3.5 ft AMR, speeds of 3 to 11 mph), the binding problems are footprint
and speed heterogeneity, not agent count. Prioritized planning handles both naturally:
- Each vehicle runs space-time A* (state = node, heading, t) against a reservation table keyed `(node, t)` and `(edge, t)`.
- A vehicle reserves every node its *footprint polygon* covers, plus a 1-tick safety buffer.
- The window is W = 30 s, and the vehicle replans every 5 s or on a task change.
- The heuristic is the precomputed reverse-Dijkstra distance to the goal, on the static graph.
- The priority order is: loaded > deadline-critical (outbound wave) > longest-waiting. This is deterministic, with ties broken by vehicle id.

The method is incomplete. The fallbacks are:
- **Waiting:** if no path is found, the vehicle waits in place and its priority rises next replan.
- **One-way aisles:** make narrow aisles one-way, or single-occupancy "critical sections" (VNA and order-picker aisles have room for only one truck
  anyway, as in real operations).
- **Cross-aisle direction:** add a "highway" direction convention on cross-aisles (as in guidance graphs and LoRR solvers).

At 60 agents, a graph of about 5k nodes and W = 30 ticks, pure Python space-time A* should be roughly 5 to 50 ms per agent replan [U, benchmark early].

**When to use PIBT instead:** in AMR goods-to-person mode (25 or more identical robots, unit moves, a 1.25 m grid). Vendor
`pypibt` (MIT, about 150 lines, https://github.com/Kei18/pypibt) and add rotation actions as LoRR does. If you need one-shot completeness for a
demo, the reference is `pylacam` (MIT, Python, not on PyPI, https://github.com/Kei18/pylacam).

**Pitfalls:**
- MAPF on the raw 400x800 grid at 25 cm makes footprints 5 to 56 cells long, and nothing works. Coarsen the grid first.
- Edge-swap and following conflicts matter for long vehicles, so reserve the swept cells between t and t+1.
- Per-agent speeds must be expressed as edge traversal times in integer ticks (no float time keys).

## 4. Vehicle kinematics (question 3)

**Forklifts and AMRs (indoors):**
- **Travel:** the edge time follows a trapezoidal profile. The cruise speed is v = 0.8 * `speed_mph[loaded]`, as `equipment.yaml` already says.
  Acceleration is about 0.5 m/s^2 for manned trucks and 0.25 to 0.5 m/s^2 for AGVs and AMRs [U; the AGV figure comes from arXiv 2408.14527, and the manned figure is a placeholder to replace from an OEM sheet].
  Corners and rack entry are capped at about 0.25 m/s, which is the VDI 2198 cycle convention (https://engmechx.it.cas.cz/improc/2017/1094.pdf).
- **Turning:** a turn adds a rotation time of about 2 to 4 s per 90 degrees [U]. A node accepts a turn only if the aisle width is at least `min_aisle_ft` for that equipment.
  Otherwise the edge is forbidden for that class, so build one graph mask per equipment class.
- **Storage and retrieval cycle:**
  t = t_travel + 2 * (h / lift_fpm) + t_fork (about 15 to 20 s for positioning, fork entry and exit) [U].
  Travel and lift are *not* simultaneous for forklifts (forks are lowered while traveling for safety). AS/RS-style Chebyshev models therefore do not apply,
  so add the times together.
- **Trailer unload:** one pallet takes a travel round-trip from dock to staging plus about 30 s inside the trailer [U]. Calibrate against `dock_turn_min`.

**Trucks (yard):** a tractor-semitrailer kinematic model (standard off-axle hitch model, e.g. arXiv 2504.00315).
- **Tractor:** rear-axle pose is (x, y, th0), wheelbase is L0 and steer angle is delta.
  - th0' = v * tan(delta) / L0
  - x' = v * cos(th0)
  - y' = v * sin(th0)
- **Trailer:** kingpin offset is M ahead of the tractor's rear axle (M is about 1 to 2 ft for a fifth wheel) and L1 is kingpin to trailer axle. The trailer heading is th1, with gamma = th0 - th1:
  - th1' = (v / L1) * sin(gamma) + (M / L1) * cos(gamma) * th0'
  - The sign of the M term flips with the hitch-position convention [U, verify the sign by simulation].
- **WB-67 parameters:**
  - [V] Overall length 73.5 ft, width 8.5 ft, front overhang 4 ft, rear overhang 4.5 ft, minimum design turning radius 44.8 to 45 ft,
    centerline turning radius 40.9 ft, minimum inside radius 1.9 ft (TxDOT Roadway Design Manual 4.3.3, AASHTO Green Book).
  - [U] WB1 is about 21.5 ft and WB2 (kingpin to tandem center) about 45.5 ft. This is consistent with the 73.5 ft length but must be checked against Green Book Table 2-1b.
  - In California, the kingpin-to-rear-axle distance is limited to 40 ft, so LA trucks slide their tandems forward.
- **Backing into a dock (hackathon trick):** reversing a trailer is unstable, but the kinematic model has no dynamics and is time-reversible.
  1. Start from the docked pose (trailer square to the door, rear at the dock face).
  2. Drive *forward* with a simple steering law: straight 1.5 trailer lengths, then turn toward the truck-court lane at delta_max.
  3. Stop when the rig reaches the drive lane.
  4. Play that path backwards. That is the classic "blind-side free" 90-degree back-in.
- **Swept path:** at each 0.1 s step, union the tractor and trailer polygons (`shapely`) into a swept path. Rasterize it onto the yard occupancy grid with a
  2 ft clearance buffer. A conflict means the door is unusable for a WB-67 at that court depth. Report it as a flag, the way an AutoTURN review would.
  This replaces Hybrid A* for docks.
- **Moving between gate, yard and door:** use a hand- or skeleton-derived lane graph. Use Hybrid A* (PythonRobotics `PathPlanning/HybridAStar`; MIT
  per its LICENSE file [U], since GitHub reports NOASSERTION) with `rsplan` analytic expansion only for the box truck, van or yard truck, which use one rigid body.
- **How traffic engineers do it:** they check against AASHTO turning templates or AutoTURN (Transoft) or AutoTrack swept-path simulation of the design vehicle at
  low speed, which uses the same kinematic tractrix model. A minimum 120 to 135 ft truck court is the site-planning norm (`semi_53.truck_court_depth_ft`).

## 5. Layout generation and slotting (question 4)

Inputs: the occupancy grid (walls, columns, obstacles), the dock doors (wall positions from the Vexcel and Matterport fusion), clear height, and the profile.

**Algorithm (deterministic):**
1. **Usable floor:** free cells minus a perimeter aisle of at least 44 to 48 in (fire-code perimeter aisle; IFC 2021 section 3206.10.1 has the value, and local AHJs differ, e.g.
   Santa Clara requires 48 in) [U].
2. **Dock zone:** reserve 15 to 20 ft for the dock apron or main aisle plus 45 to 50 ft of staging lanes along the dock wall, about 60 to 70 ft in total [U, industry
   rule of thumb]. Use one staging lane per door: 8 ft wide (2 pallets) by 13 pallets deep, so one lane equals one 26-pallet trailer.
3. **Rack modules:**
   - Selective rack frames are 42 in deep for 48 in pallets. Back-to-back double rows are 42 + 6 to 12 in flue + 42, about 7.5 to 8 ft.
   - Bay pitch is about 8.25 ft (two 40 in pallets on 96 in beams plus the upright).
   - The aisle face-to-face width is `min_aisle_ft` of the chosen put-away truck: reach 9 ft, counterbalance 12 to 12.5 ft, VNA 6 ft (VNA also needs transfer aisles of about 14 ft).
   - Module pitch is aisle + double row.
4. **Cross-aisles:** every 100 to 150 ft of rack run, at least 1 bay wide plus the turn clearance of the widest truck [U]. Roodbergen and de Koster show that
   one or two middle cross-aisles cut picker travel substantially (https://www.roodbergen.com/warehouse/background.php).
5. **Search:** try both orientations (rows parallel or perpendicular to the dock wall; rows *perpendicular* to the dock wall are the usual default
   for unit-load flow) and a lateral offset from 0 to the module pitch in 25 cm steps. Reject any rack bay that intersects an obstacle plus a 6 in buffer.
   Score = pallet positions - lambda * (columns inside an aisle). Columns in the flue or inside a rack bay footprint are fine (racks are usually
   set out so that column lines fall in the back-to-back flue). About 2 x 40 candidates is cheap with numpy.
6. **Levels:** levels = floor((clear_height - 3 ft) / pitch) + 1 with a ground level, where pitch is about 5.5 ft (a 54 in load plus 6 in lift-off plus a 5 in beam) [U].
   The 3 ft is an ESFR deflector-to-storage clearance assumption; check NFPA 13. Cap the levels by `lift_height_ft`.
7. **Check:** positions / 1,000 SF should land near the profile's `positions_per_ksf` (9 to 16). If it does not, flag it as a validation failure.

**Slotting:**
- **Pallets:** use class-based storage. Rank locations by the expected travel cost from the dock (dual-command: inbound door to location to outbound door, plus level
  height). Rank SKUs by velocity, or by COI (cube-per-order index, space / picks; lowest COI goes closest). Fill A, B and C by the profile's `velocity` shares.
- **Forward-pick (case/each) space:** use Bartholdi's fluid model. The optimal space fraction for SKU i is v_i* = sqrt(f_i) / sum_j sqrt(f_j), where f_i is flow
  (W&DS v0.98.1, Theorem 8.1, p.104). The SKUs with the strongest claim are those with the highest picks / sqrt(flow) ("bang-for-buck", Theorem 8.7).
- **SKU data:** we have no SKU data. Synthesize a deterministic Pareto SKU population matched to A/B/C shares, and label it "synthetic".

**Analytic travel and pick-rate models (used as validation baselines):**
- **Unit-load single command, random storage:** E[one-way] = W/4 + D/2 for a dock at the middle of a W-wide wall of a W x D rectangle (rectilinear, uniform storage;
  standard result, see Tompkins et al., *Facilities Planning* 4th ed., Wiley 2010, chapter on storage) [U on the exact chapter]. Round trip = 2x. Add the
  lift time from Section 4.
- **AS/RS-style Chebyshev (VNA turret trucks only, if they lift while traveling):** E[T_SC] = T(1 + Q^2/3) and E[T_DC] = (T/30)(40 + 15Q^2 - Q^3), where
  T = max(H, V) and Q = min(H, V) / T (Bozer and White 1984, W&DS section 12.3, p.207).
- **Picker tour, S-shape, n aisles of length L, m picks uniform:** E[aisles visited] = n * (1 - (1 - 1/n)^m). E[distance] is about
  E[visited] * L + 2 * (distance to the farthest visited aisle) (Hall 1993; Roodbergen's site above).
- **Pick rate:** lines/hr = 3600 / (t_setup / k + t_travel / k + t_pick). Travel is the largest share of picker labor (W&DS ch. 10; Tompkins
  gives about 50%). Calibrate t_pick so the model reproduces the profile's `lines_per_hour`. W&DS ch. 4 gives 50 to 100 picks/person-hour for bin shelving
  and 150 to 500 for flow rack.
- **Dock capacity:** rho = lambda_trucks * E[turn] / c_doors. If rho > 0.85, expect yard queues. As a cross-check, use Erlang-C (M/M/c), computed by hand or with `ciw`.

Book: Bartholdi and Hackman, *Warehouse & Distribution Science* v0.98.1 (free):
https://www.warehouse-science.com/book/editions/wh-sci-0.98.1.pdf

## 6. Validation and uncertainty (question 5)

Practice follows Sargent's V&V framework (WSC tutorials, https://www.informs-sim.org/wsc03papers/006.pdf) and Law, *Simulation Modeling and
Analysis* (5th ed.): conceptual validity, then verification, then operational validity. With no client data, validation means face validity, analytic checks and
extreme-condition tests.

**Checklist (automate as pytest; show in the UI as a "model checks" panel):**
- [ ] **Determinism:** the same seed and inputs give a byte-identical event log. Store a SHA-256 of the log in the result. Use integer ticks, no
      `set` iteration of strings (PYTHONHASHSEED), and explicit tie-breaks in heaps.
- [ ] **Conservation:** pallets in = pallets stored + staged + in-transit at every tick. Trucks arrived = docked + queued + departed.
- [ ] **Little's law** (W&DS Theorem 2.1). Time-averaged trucks on site L must equal arrival rate x mean dwell, and staging WIP must equal flow x
      staging time, within 5% (exact up to edge effects, so measure over the full shift including drain).
- [ ] **Analytic travel:** the mean simulated single-command cycle on an empty floor (one vehicle, congestion off) is within 10% of W/4 + D/2 plus lift.
      The S-shape tour matches E[aisles visited].
- [ ] **Single-vehicle calibration:** the 53 ft unload with the default fleet is about `dock_turn_min` (35 min). Pick rate is about `lines_per_hour`.
- [ ] **Congestion sanity:** throughput vs fleet size is concave and saturates, and congestion delay rises monotonically with fleet size. A single
      vehicle has 0 congestion delay.
- [ ] **Extreme conditions:** with 0 doors nothing ships. With 10x inbound the yard queue grows linearly and the run is flagged. With an infinite fleet, the limit is
      docks or staging. With a blocked aisle, traffic reroutes or the run reports a deadlock (never a silent hang; add a watchdog for no
      progress in 120 s).
- [ ] **Kinematic checks:** WB-67 steady turn at CTR 40.9 ft, where the inside trailer track approaches the AASHTO minimum inside radius of 1.9 ft, within
      +/- 2 ft [U, because AASHTO templates are 180-degree turns and not steady state]. A docked trailer ends within 0.3 m and 2 degrees of the door axis.
- [ ] **Layout checks:** positions per KSF are near the profile. No rack intersects a column or wall. Every rack face is reachable on the lane graph by the
      assigned equipment class (BFS).
- [ ] **Face validity:** a 2x speed replay video reviewed by someone who has worked a dock. Utilizations land in plausible bands (forklifts 60
      to 85% [U]).

**Uncertainty presentation:**
- Sources of randomness: arrival lateness, unload time per pallet (lognormal, CV of about 0.3 [U]), and pick time per line. Layout and routing
  are deterministic.
- Run R = 30 replications with `SeedSequence(base).spawn(R)` and separate streams per source (common random numbers across scenarios, so A/B
  comparisons are paired).
- Report each KPI as **P10 / P50 / P90 across replications**, plus a 95% t-CI on the mean (sigma / sqrt(R)). With R = 30, P10 is the third-lowest run, so say
  "approx." or use R = 50 if runtime allows.
- One shift is a *terminating* simulation: no warm-up deletion is needed, but you must start from a realistic initial state (stock pre-filled to
  about 80% of positions [U], yard empty).
- The UI must show inputs as "typical" (per `equipment.yaml` `basis`). Every KPI shows its band, and every number derived from a [U] parameter gets an
  "assumption" chip.

## 7. Pitfalls (summary)

- MAPF on the 25 cm grid. Coarsen to a lane graph, and keep 25 cm for collision and layout geometry only.
- Mixing SimPy's continuous time with the MAPF tick. Keep one clock: the controller ticks at DT, and all MAPF times are integer ticks.
- Prioritized planning deadlocks in dead-end aisles. Use critical-section aisles and a watchdog, and log deadlocks as a KPI rather than hiding them.
- Using `dock_turn_min` as a constant. It must emerge from the model, or validation becomes circular.
- Matterport floors include yards and mezzanines (CLAUDE.md, Phase 0). Mask out non-warehouse floor before the layout step.
- Clear height: CoStar clear height and scan roof-deck height differ by 7 to 10 ft. Use the CoStar clear height (or measured clear height to the lowest obstruction) for rack levels.
- GPL contamination (`python-motion-planning`). Stay on MIT/BSD/Apache packages.
- Presenting synthetic SKUs, waves or arrival times as data. Label them as synthetic everywhere.
