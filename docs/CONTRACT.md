# Contract

**contract_version: 0.1.0**

Machine-readable source of truth: `backend/contract/models.py` (pydantic v2), exported to
`docs/schemas/*.json` (`python scripts/export_schemas.py`; `--check` in CI/pytest). Frontend
types are generated from `docs/schemas/contract.bundle.json` (`npm run gen:types`).
Constants: `backend/contract/constants.py`. If this prose and the code disagree, the code wins
and this file gets fixed.

## Changelog

| version | date | change |
|---|---|---|
| 0.1.0 | 2026-10-09 | Initial contract. |

**Change rule (until further notice): additive only.** New optional fields, new models, new
endpoints, new enum values announced here. No renames, removals or meaning changes. Every change
bumps `CONTRACT_VERSION` (patch for docs-only, minor for additions) and adds a changelog row.
Models reject unknown fields (`extra="forbid"`) so typos fail fast; consumers of *newer* payloads
should regenerate types.

## 1. Conventions

- **Units:** SI everywhere. Seconds (`_s`), metres (`_m`), metres/second for speeds
  (`speed`, `speed_limit`), degrees for bearings/headings (0 = north, clockwise, `[0, 360)`).
  Flows are veh/h only where the name says so (`LEVEL_FLOW_VEH_PER_H`); `arrival_rate` is veh/s.
- **Coordinates:** WGS84. Objects use `lat`, `lon`. Geometries are lists of `[lon, lat]` (GeoJSON order).
- **Time:** `t`, `since_t`, `time_in_phase_s` are **simulation** seconds from session start.
  Timestamps (`created_at`, `last_update`, `departure_time`) are ISO-8601 UTC strings.
- **Ids:** always strings. Deterministic where it matters, so caches survive re-analysis:

  | id | recipe |
  |---|---|
  | intersection | `"i_" + sha1(f"{lat:.5f},{lon:.5f}")[:8]` with lat/lon rounded to 5 decimals (`-0.00000` normalised to `0.00000`) |
  | approach | `"a_" + sha1(f"{intersection_id}:{b}")[:8]` with `b = round(bearing/5)*5 % 360` as an int |
  | area | `"ar_" + sha1("w,s,e,n")[:8]`, each rounded to 5 decimals |
  | phase | `"<intersection_id>:p<k>"`, k from `backend/sim/phases.py` |
  | session / demand profile | `"s_…"` / `"dp_…"`, random |

  Implementation: `backend/contract/helpers.py` (`intersection_id`, `approach_id`, `area_id`). Never re-implement.
- **Approach bearing:** the direction **from the intersection toward the upstream end of `in_edge`**
  (traffic arriving from the south has bearing ≈ 180).
- **Errors:** every non-2xx REST response is
  `{"error": {"code": "snake_case", "message": "human readable", "details": {...} | null}}`
  (`ErrorResponse`). Codes so far: `validation_error` (422), `unknown_area`, `unknown_session`,
  `unknown_demand_profile` (404), `unknown_intersection`, `unknown_scenario` (400), `http_error`.
  WS errors send one `ErrorResponse` frame, then close (4404 unknown session, 4400 bad request).

## 2. Constants (`backend/contract/constants.py`)

| name | value | meaning |
|---|---|---|
| `YELLOW_S` | 3 | yellow |
| `ALL_RED_S` | 2 | all-red clearance |
| `MIN_GREEN_S` | 7 | minimum green |
| `MAX_RED_S` | 60 | no approach waits on red longer than this |
| `FIXED_PHASE_S` | 30 | green per phase for fixed timers / AI fallback |
| `SIM_DT_S` | 0.1 | engine step |
| `CONTROLLER_PERIOD_S` | 1 | controller cadence (sim-s) |
| `WS_HZ` | 5 | tick rate |
| `GEMINI_MIN_INTERVAL_S` | 6 | wall-clock gap between LLM calls |
| `STOPPED_SPEED_MPS` | 0.5 | stopped threshold |
| `QUEUE_DISTANCE_M` | 50 | queue window upstream of stop line |
| `METRICS_FINISHED_WINDOW_S` | 120 | finished trips kept in population |
| `THROUGHPUT_WINDOW_S` | 60 | throughput window |
| `AI_CONSECUTIVE_FAILURE_LIMIT` | 3 | failures before fallback |
| `AI_PROBE_INTERVAL_S` | 60 | recovery probe period (wall-clock) |
| `GOOGLE_CACHE_TTL_S` | 1800 | Google cache TTL |
| `DEMAND_SCALE_MIN/MAX`, `DEMAND_RATIO_LO/HI` | 0.4/1.6, 1.0/2.0 | demand heuristic |
| `LEVEL_FLOW_VEH_PER_H` | low 150, medium 300, high 500, rush 700 | base flow per entry node |

## 3. Schemas

Field-level truth is in `docs/schemas/`. Summary (`?` = optional/nullable):

- **BBox** `{west, south, east, north}`, west < east and south < north.
- **RoadNetwork** `{nodes[{id, lat, lon}], edges[], entry_nodes[], exit_nodes[], bbox?}`.
  **Edge** `{id, from_node, to_node, geometry[[lon,lat]...], length_m, speed_limit (m/s), lanes, road_class}`.
  Edges are directed; a two-way street is two edges. `road_class` uses OSM `highway` values
  (`motorway … service`). Entry/exit nodes are boundary nodes.
- **Intersection** `{id, node_id, lat, lon, approaches[{id, bearing, lanes, in_edge, out_edges[]}], has_signal_in_osm, structural_score (0..1), sim_gain_s?}`.
  `out_edges` excludes U-turns. `sim_gain_s` = estimated delay saved by signalising, null until computed.
- **Phase** `{id, approach_ids[]}`: approaches that are green together. Derived by the engine (`backend/sim/phases.py`), never by road-network code.
- **SignalState** `{intersection_id, phase_id, color_per_approach{approach_id: green|yellow|red}, time_in_phase_s, is_transition}`.
  During yellow/all-red `is_transition = true`, `phase_id` = the phase being switched **to**, and `time_in_phase_s` counts from the start of the transition.
- **Vehicle** `{id, edge_id, offset_m, lat, lon, heading, speed}`.
- **Metrics** `{mode, effective_controller, trip_delay_s, avg_wait_s, avg_queue, throughput_per_min, blocked_spawns, deadlocks, t?, population?}`.
- **ControllerStatus** `{state, effective_controller, message, since_t, calls_last_min, calls_today, daily_cap}`.
  `state`: `ai_active` (Gemini + max-pressure live), `ai_limit_reached` (quota: daily cap or 429),
  `ai_unavailable` (invalid/missing key or ≥3 consecutive failures), `ai_replay` (recorded plans
  replayed, no live calls), `traditional` (session mode is not `ai`).
  `effective_controller` is what actually drives the signals right now.
- **DemandProfile** `{id, area_id?, source, level?, created_at, departure_time?, entries[{entry_node_id, congestion_ratio?, scale}]}`.
- **DataStatus** `{state, message, last_update?, calls_today, daily_cap}`.
- **Explanation** `{t, intersection_id?, text}`; `intersection_id = null` is a session-wide notice.
- **Observation** `{t, intersections{id: {phases[], current_phase, time_in_phase_s, is_transition, approaches{approach_id: {queue, wait_s, arrival_rate}}}}}`.
  `queue` = stopped vehicles within 50 m of the stop line; `wait_s` = longest current continuous
  stopped time on the approach; `arrival_rate` = veh/s over the trailing 60 sim-s.
- **Plan** `{intersection_id, phase, hold_s (0..120), reason}`.

## 4. Metric definitions

Identical in engine, experiments and UI. Computed by the engine only (`SimEngine.metrics`).

- **Population** = vehicles that finished their trip in the trailing `120` sim-s **plus** all vehicles
  currently in the network **plus** all vehicles waiting in the external spawn queue.
- **avg_wait_s** = mean cumulative stopped time (speed < 0.5 m/s, *including* time in the external
  spawn queue) over the population.
- **trip_delay_s** = mean (actual − free-flow travel time) over the population; for unfinished
  vehicles "actual" is elapsed time so far (incl. spawn-queue time) and free-flow is for the
  distance covered so far.
- **avg_queue** = mean over approaches of stopped vehicles within 50 m upstream of the stop line
  (signalised and unsignalised approaches).
- **throughput_per_min** = vehicles that exited in the trailing 60 sim-s.
- **blocked_spawns** = cumulative spawn attempts deferred because the entry edge was full.
- **deadlocks** = cumulative gridlock events detected (no vehicle moved for 60 sim-s while vehicles are present).

## 5. REST

All JSON. Base path `/api`. Every error uses `ErrorResponse`.

| method + path | request | response |
|---|---|---|
| `GET /api/health` | – | `HealthResponse {status, contract_version, mock}` |
| `POST /api/area` | `AreaRequest {bbox, ignore_osm_signals?=false}` | `AreaResponse {area_id, network, intersections (ranked best first), recommended_ids}` |
| `GET /api/geocode?q=` | – | `GeocodeResponse {results[{display_name, lat, lon, bbox?}]}` (backend proxies Nominatim, with User-Agent and caching) |
| `POST /api/demand/resolve` | `DemandResolveRequest {area_id, source: google_live\|google_snapshot\|baseline, level?, departure_time?}` | `DemandResolveResponse {demand_profile, data_status}` |
| `POST /api/sim/start` | `SimStartRequest {area_id, mode, demand_profile_id, seed, selected_intersections, speed}` | `SimStartResponse {session_id}` |
| `POST /api/sim/stop` | `SimStopRequest {session_id}` | `SimStopResponse {session_id, stopped, final_metrics?}` |
| `GET /api/metrics?session_id=` | – | `MetricsResponse {session_id, t, metrics}` |
| `POST /api/ai/reset` | – | `AiResetResponse {reset, message, calls_today, daily_cap}` |

The backend caches areas by `area_id`. `ignore_osm_signals = true` ranks junctions without
favouring existing OSM signals. `speed` = sim-seconds per wall-second (0 < speed ≤ 20).

**Mock only:** `?scenario=ai_limit|ai_replay|google_down` on `/api/sim/start`, `/api/demand/resolve` and the WS URL (or env `MOCK_SCENARIO`).

## 6. WebSocket

`WS /ws/sim?session_id=…`. Server sends `SimTick` frames at 5 Hz:
`{type: "tick", session_id, t, vehicles[], signals[], metrics, controller_status, data_status, explanations[]}`.

- **Latest only:** if the client is slow, the server drops stale ticks and sends only the newest. Never queue.
- `explanations` holds entries with `t` in (previous delivered tick, this tick], so dropped ticks don't lose explanations.
- `signals` covers signalised (selected) intersections only.
- When the session stops, the server closes with code 1000.

## 7. Sessions, demand and determinism

- **All modes use the SAME `selected_intersections` as signalised**; every other junction is unsignalised
  (priority by road class with gap acceptance). Modes differ only in the controller.
- **≥2 concurrent sessions** are supported (split compare).
- **DemandProfile is resolved ONCE and frozen.** Split-compare sessions pass the same
  `demand_profile_id` (and seed) so they see identical demand. A running session ignores later
  resolves; a new resolve creates a new profile id.
- **Determinism:** same `seed` + same `demand_profile` ⇒ identical **demand schedule** (spawn time,
  origin, destination, speed factor). It comes from its own RNG stream (seeded from `seed` and the
  profile id), independent of controller and driver behaviour. `demand_schedule_digest()` must
  match across modes; `state_digest()` only has to match for the same mode and seed.
- **Ratio → scale (heuristic, tunable):** `congestion_ratio = duration / staticDuration` from the Routes
  API for a short route starting at the entry. `scale` is linear from 0.4 at ratio 1.0 to 1.6
  at ratio ≥ 2.0 (clamped to 0.4 below 1.0). Per-entry flow = `scale × LEVEL_FLOW_VEH_PER_H["medium"]`.
  For `baseline_only`, `scale = LEVEL_FLOW_VEH_PER_H[level] / LEVEL_FLOW_VEH_PER_H["medium"]` and `congestion_ratio = null`.
  Implementation: `helpers.demand_scale`.

## 8. Python interfaces (`backend/contract/interfaces.py`)

```python
SignalCommands = dict[str, str]            # {intersection_id: phase_id}

class SimEngineFactory(Protocol):
    def __call__(self, network, intersections, seed, demand_profile, signalized_ids) -> SimEngine: ...

class SimEngine(Protocol):
    def step(self, dt: float, commands: SignalCommands) -> None
    def observe(self) -> Observation        # includes phases
    def vehicles(self) -> list[Vehicle]
    def signals(self) -> list[SignalState]
    def metrics(self, mode, effective_controller) -> Metrics
    def state_digest(self) -> str
    def demand_schedule_digest(self) -> str
    def set_demand(self, level) -> None     # dev/testing only

class Controller(Protocol):
    def decide(self, observation: Observation) -> SignalCommands

class Supervisor(Protocol):               # LLM
    async def plan(self, observation: Observation) -> list[Plan]
```

- A command requests a phase; the **engine** enforces the transition (yellow 3 s → all-red 2 s),
  min green 7 s and max red 60 s (a starved approach is served even against the controller).
  Missing intersections keep their current request.
- **Cadence:** controllers are asked every 1 sim-second. In `ai` mode the controller is
  `gemini+max_pressure`: max-pressure runs every second, and a **plan executor** applies the latest
  Gemini `Plan`s on top of it (hold `phase` for up to `hold_s`). The Gemini supervisor runs
  asynchronously on wall-clock, at most every 6 s, in live mode; it never blocks the sim loop.
- **Rest-in-phase rule (all adaptive controllers):** busy approach → green, others red; if all
  approaches are empty, **keep the current phase** (no flapping).

## 9. AI limit behavior

**Triggers** (any one switches an `ai` session to fixed timers):
1. Our daily cap reached (`calls_today >= GEMINI_DAILY_CAP`), checked before each call → `ai_limit_reached`.
2. Gemini HTTP 429 / `RESOURCE_EXHAUSTED` → `ai_limit_reached`.
3. Invalid or missing API key (HTTP 400/401/403 with an `API_KEY_INVALID` / `PERMISSION_DENIED` reason) → `ai_unavailable`.
4. ≥ 3 consecutive failed calls (timeout, 5xx, network error, output that fails `Plan` validation) → `ai_unavailable`.

**A single failed call is NOT a trigger.** The plan executor keeps running on max-pressure plus the
last valid plans until they expire; the failure counter resets after any successful call.

**On trigger:**
- The session's effective controller becomes `fixed` (30 s per phase). The switch goes through the
  engine's normal safe transition: any green turns yellow (3 s) then all-red (2 s) before the
  fixed-timer phase starts; min green is respected. No signal jumps green→red.
- `ControllerStatus` = `{state: ai_limit_reached | ai_unavailable, effective_controller: "fixed",
  message, since_t: <sim t of the switch>, …}`. `Metrics.effective_controller` = `fixed` from then on
  (`Metrics.mode` stays `ai`).

**Notifications (all of them):**
- Frontend banner, persistent while the state holds:
  `AI limit reached: signals reverted to traditional fixed timers (since t=<since_t> s)`
  (for `ai_unavailable`: `AI unavailable: signals reverted to traditional fixed timers (since t=… s)`).
- A one-time toast per session per transition.
- An explanation-feed entry (`Explanation` with `intersection_id = null`).
- Backend `WARNING` log line and a console message (`print`) naming the session, the trigger and the counters.

**Recovery:**
- For triggers 2–4: probe Gemini every 60 s wall-clock (one minimal call, counted against the cap).
  After **one** successful call, resume AI (`ai_active`, `gemini+max_pressure`) via the same safe
  transition, with banner removal, a toast and an explanation entry.
- After a **daily-cap** fallback (trigger 1): no probing. Resume only after the counter rolls over
  (next UTC day) or via `POST /api/ai/reset`, which resets our counter and lets running sessions resume
  at their next controller tick.
- The daily counter is persisted (`backend/data/state/usage.json`) so restarts don't reset it.

**Testing:** `GEMINI_FAKE_FAIL` = `429` | `timeout` | `5xx` | `invalid` | `key` simulates each trigger. The mock
server's `?scenario=ai_limit` exercises the UI path.

## 10. Google data fallback

Chain, tried in order when resolving `source = google_live`:

1. **google_live**: Routes API `computeRoutes` (`TRAFFIC_AWARE`, field mask `routes.duration,routes.staticDuration`), one short route per entry node; ratio = duration/staticDuration.
2. **google_cached**: the last live result for the same `area_id` (and departure hour), if younger than 30 min. Persisted on disk (`backend/data/cache/`), so it survives restarts.
3. **google_snapshot**: a recorded result committed to the repo (`backend/data/snapshots/<area_id>.json`, recorded by a script the user runs).
4. **baseline_only**: level flows (`medium` unless a level is given).

`source = google_snapshot` starts at step 3. `source = baseline` goes straight to step 4.

- Every response carries `DataStatus`. `DemandProfile.source` records which step produced it.
- When `state != google_live` the UI shows a banner with `DataStatus.message` (e.g.
  "Google live data unavailable: using recorded snapshot from 2026-10-01 08:30 UTC").
- **GOOGLE_DAILY_CAP guard:** each Routes call increments a persisted counter (`backend/data/state/usage.json`, keyed by UTC date).
  At the cap, live calls are skipped and the chain continues at step 2. `calls_today` and `daily_cap` are in `DataStatus`.
- `GOOGLE_FAKE_FAIL=1` makes every live call fail (to test the chain).
- Because profiles are frozen, a Google outage *during* a session changes only `DataStatus`, never that session's demand.

## 11. Engine rules

- yellow 3 s, all-red 2 s, min green 7 s, max red wait 60 s (signalised junctions).
- Unsignalised junctions: priority by road class (higher class has priority, ties = right-hand rule)
  with gap acceptance on the minor approach.
- Vehicles that can't enter a full entry edge wait in an external spawn queue (counted in metrics).
