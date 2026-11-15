# ProInspect v3 contracts (2026-11-14, late night). Supersedes v2 where they overlap; v2 stays valid otherwise.

Plan: `/Users/kritazyaupreti/.claude/plans/okay-i-m-thinking-of-linear-minsky.md`.

**Goal.** Help a buyer or operator decide "will this warehouse work for my operation?"
- Every number carries a source and a confidence.
- **Unknown is never 0.** Missing CoStar fields stay null and are reported as "unknown".
- An estimate is always labelled as an estimate (a range or a lower bound).
- Simulations never fake a maneuver: everything animated is produced by the motion model.
- A planner failure is never called physically impossible.

## Runtime (unchanged)
- **Backend:** `cd backend && ../.venv/bin/uvicorn proinspect.api:app --port 8010`. RESTART after any backend edit.
- **Web:** `cd web && npm run dev` (port 8080, the only domain verified for the Matterport key).
- **Never touch port 8000.** Never print secrets from `.env`. No git commits.
- **Tests:** `cd backend && ../.venv/bin/python -m pytest -q tests`. All 173 existing tests must stay green; update a test only
  when the contract intentionally changed, and say so.
- **Routers:** `api.py` auto-includes `proinspect.api_docks`, `proinspect.api_analytics` (new) alongside the existing ones.
  Each stream adds routes only in its own module.

## Ownership (strict: don't edit files you don't own; ask the lead via your final report if you need a change)

| Stream | Owns |
|---|---|
| **S1 Docks backend** | `twin/exterior.py` (fixes), NEW `docks.py`, NEW `api_docks.py`, NEW `precompute_docks.py`, `tests/test_docks*.py`, `build/docks/`, `build/validation/docks.md` |
| **S2 Yard planner** | `yard/**` (new `plan.py`, `pursuit.py`, `reserve.py`; existing files may be extended), `api_yard.py`, `tests/test_yard*.py`, `build/yard/` |
| **S3 Web** | `web/src/yard/**`, NEW `web/src/docks/**`, NEW `web/src/components/EquipmentGlyph.tsx` + `EquipmentCard.tsx`, `web/src/matterport/FitForm.tsx`, `web/src/components/TestPanel.tsx`, `web/src/components/WarehouseCard.tsx`, `web/src/App.tsx`, NEW `web/src/lib/docksApi.ts` |
| **S4 Analytics + agent + compare** | NEW `analytics.py` (rank_attribute, compare, find_comparables, select_criteria), NEW `api_analytics.py`, NEW `library/criteria.yaml`, `agents/**`, `api_concierge.py`, `tests/test_analytics*.py` + agent tests, `web/src/components/CompareView.tsx` (rewrite), NEW `web/src/components/AnalystPanel.tsx`, `web/src/voice/**`, NEW `web/src/lib/compareApi.ts`, the compare part of `web/src/lib/store.ts` |

- **Shared, read-only for everyone:** `catalog.py`, `rank.py`, `facts.py`, `library/equipment.yaml`, `vexcel_rank.py`,
  `twin/fuse.py`, `doors.py` (v2 scan doors), `web/src/lib/api.ts`, `web/src/index.css` tokens.
- **Exception:** S4 may touch `store.ts` only for compare state; S3 may add one `docksOpen` field. Keep both diffs minimal.

## 1. Dock store v3 (S1 owns; S2, S3 and S4 read through `proinspect.docks` or the API)
The file is `build/docks/{property_id}.json`. It lives in the **exterior twin frame** (local ENU metres about
`frame.lat0/lon0` of `build/twins/exterior/{id}.json`; x east, y north), so the yard test needs no registration.
```
{ "property_id": 12896757, "version": 3, "updated_at": ISO8601,
  "frame": {"lat0":..,"lon0":..},
  "buildings": [{"id": 0, "outline": [[x,y],..], "edges": [{"id": 3, "a":[x,y], "b":[x,y], "facing":"S", "len_m":..}]}],
  "gate": {"x":..,"y":..,"lon":..,"lat":..,"source":"inferred"|"user"} | null,
  "registration": {"model_id": "QHMtBN5Nnrc"|null, "flip": 0|180, "confirmed_by_user": false,
                   "confidence": 0.44, "note": ".."},
  "doors": [ { "id": "k1", "building": 0, "edge": 3, "s_m": 12.4,           # offset along edge from a
     "x":.., "y":.., "lon":.., "lat":.., "normal":[nx,ny],                   # outward unit normal (ENU)
     "width_ft": 9.0 | null, "height_ft": 10.0 | null,
     "kind": "dock_high"|"drive_in"|"unknown", "kind_conf": 0..1,
     "status": "proposed"|"inferred"|"confirmed"|"rejected"|"user_added",
     "evidence": [ {"type": "oblique"|"trailer"|"well"|"costar_span"|"vision", "conf": 0..1, "ref": "..", "note": ".."} ] } ],
  "summary": { "costar_docks": 23|null, "costar_drive_ins": 2|null,
               "detected_doors": 9, "docked_trailers": 6, "confirmed": 0,
               "estimate": {"value": 14|null, "lo": 9, "hi": 23, "status": "verified"|"estimated"|"lower_bound"|"unknown",
                            "basis": "plain sentence"} } }
```

**Python API, `proinspect.docks`:**
- `load(pid) -> dict` builds the proposals when there is no file and caches them.
- `save(pid, body)` validates the body: a door must lie within 1.5 m of its edge, and status and kind must come from the enums.
- `usable_doors(pid, kinds=("dock_high","unknown")) -> [dict]` returns the confirmed, user_added, proposed and inferred
  doors, never the rejected ones. Each carries `x, y, normal, width_ft, kind, status`, so the yard can use it directly.
- `evidence(pid) -> summary`.

**API:**
- `GET /api/warehouses/{id}/docks`
- `PUT /api/warehouses/{id}/docks`, taking `{doors:[...], gate?, registration?}`, which the user edits in the 2D editor
- `GET /api/docks/evidence?ids=1,2,3` → `{id: summary}`

## 2. Analytics (S4)
- `GET /api/rank/attribute?attr=<key>&order=desc|asc&market=Houston|Los Angeles&top=20` returns
  ```
  { attr, label, unit, order,
    verified:[{id, value, source:"CoStar", confidence}],
    estimated:[{id, value|null, lo, hi, source, confidence, basis}],
    unknown:[{id, why}],
    coverage:{verified:n, estimated:n, unknown:n} }
  ```
  - Supported attrs (at least): docks_total, docks_per_10ksf, drive_ins, clear_height_ft, rba_sqft, truck_court_ft,
    column_spacing_ft, power_amps, parking_spaces, lot_acres, year_built, asking_rent.
  - Dock estimates come from `proinspect.docks.evidence`.
- `POST /api/compare {ids:[..2-4], profile?: <profiles.yaml key>, ops_text?: str}` returns
  ```
  { profile, criteria:[{key, label, weight, why}],
    rows:[{key, label, unit, better:"higher"|"lower", values:[{id, value|null, lo?, hi?, source, confidence, kind:"verified"|"estimated"|"unknown"}],
           winner:id|null, verdict:"winner"|"tie"|"unknown"}],
    tradeoffs:[str], fit:{id:"pass"|"flag"|"fail"}, recommendation:{id, text, basis:[criterion keys]} }
  ```
  - The `recommendation.text` is deterministic (template). The agent may rephrase it but adds no numbers.
- `GET /api/comparables/{id}?profile=` → `{ids:[..], why}`.
- `library/criteria.yaml` maps operation types (cross_dock, pallet_storage, ecommerce, cold_storage, last_mile,
  manufacturing, plus each profiles.yaml key) to 5–7 weighted criteria with a `why` line each.

## 3. Yard maneuver test (S2)
`POST /api/yard/maneuver`
```
{property_id, trucks:[{type:"semi_53"|"box_truck_26"|"last_mile_van", count}], dwell_s?: 90,
 door_ids?:[..], gate?: {lon, lat}, seed?: 0}
```
returns
```
{ run_id, frame:{lat0,lon0}, gate:{lon,lat,source},
  doors_used:[{id, lon, lat, heading_deg, kind, status, source:"confirmed"|"proposed"|"assumed"}],
  vehicle_geometry:{<type>: {...}},                       # same as /api/yard/test
  vehicles:[{id:"T1", type, target:{kind:"door"|"staging"|"street", id}, color_idx,
             keys:[[t_s, lon, lat, hdg_tractor_deg, hdg_trailer_deg, gear(+1|-1|0), phase]],   # dt = 0.2 s
             path:[[lon,lat,gear]],                        # the planned path, for drawing
             status:"done"|"waiting"|"failed", fail?:{class, text}}],
  events:[{t_s, truck, kind:"wait"|"conflict"|"arrive"|"dock"|"depart", where:[lon,lat], blocked_by?, seconds?, text}],
  issues:[{class:"geometric"|"assumption"|"planner"|"congestion", severity:"high"|"med"|"low", where:[lon,lat],
           text, trucks:[..], door?}],
  summary:{trucks:n, completed:n, concurrent_max:n, makespan_s, total_wait_s, worst_wait:{truck, s},
           headline:"plain sentence"},
  assumptions:[str], cached:bool }
```
- The phase is one of `enter`, `to_door`, `reverse`, `docked`, `pull_out`, `exit`, `wait` or `staging`.
- Headings are compass degrees (0 = N, clockwise), as in `/api/yard/test`.
- `/api/yard/test` remains for regression; the UI switches to `/maneuver`.

## 4. Front-end (S3, plus S4 for compare and analyst)
- **Right column = 432 px**, at `right-4`. The WarehouseCard, CompareView and DockEditor side panel all use it, and only one
  is shown at a time; CompareView covers the card while it is open.
- **DockEditor** (`web/src/docks/DockEditor.tsx`) is opened from the card's dock summary.
  - It uses the map overlay (ortho + footprint) with snapping, plus a side list.
  - It calls the store only through `web/src/lib/docksApi.ts`.
- **Agent UI commands:** in addition to v2, `showDocks {id}`, `rankAttribute {attr, market?}` and `compare {ids, profile?}`.
  - The `compare` command now also carries `profile` and is handled by the new CompareView.
- **The Analyst thread** (S4) is text-first: messages, the steps the agent took (tool → one-line finding) and evidence chips.
  - Voice is optional (the orb remains).
