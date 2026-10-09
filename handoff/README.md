# Handoff: workstreams

Read `docs/CONTRACT.md` first. Import ids, constants and models from `backend/contract/`; don't
copy values. Contract changes are additive only: bump `CONTRACT_VERSION`, add a changelog row,
run `python scripts/export_schemas.py` and `npm run gen:types`, and commit the generated files.
Never call Overpass, Gemini or Nominatim in tests; mock them.

| # | workstream | status | builds | done when |
|---|---|---|---|---|
| A | Road network | **done (part 1)** | `backend/roadnet/`, `backend/area/`: Overpass → `RoadNetwork`, consolidation, intersections, area cache | parser + cache tests green; ids stable across re-analysis |
| B | Sim engine | in progress (Codex) | `backend/sim/`: `SimEngineFactory`, `phases.py`, car following, signal rules, unsignalised priority, spawn queue, metrics (§4), `set_demand(level, multiplier, entry_overrides, at_t)` | same seed+profile ⇒ same `demand_schedule_digest` for every mode; metrics match the definitions |
| C | Controllers | todo | `backend/control/`: fixed (30 s), webster, max_pressure with the rest-in-phase rule, plan executor | no-flapping test passes; AI vs fixed shows a gain on the grid |
| D | AI supervisor | todo | `backend/ai/`: Gemini client, `Plan` validation, caps/counters (persisted), triggers, fallback, 60 s probe, `/api/ai/reset` | every `GEMINI_FAKE_FAIL` mode leads to the right `ControllerStatus` |
| E | Demand | **done** | `backend/traffic/demand.py`: levels, multiplier, overrides, frozen profiles | tests green |
| F | Frontend | partial | demo-city button, demand controls, split view; todo: box-drag selection, geocode search box, charts | works against mock and real backend with no code changes |
| G | Real server | partial | `backend/app.py` has areas/demand/geocode; todo: `/api/sim/*`, `/api/metrics`, `/ws/sim` once B lands | the mock tests also pass against the real app |
| H | Siting | **pre-filter done** | `backend/siting/prefilter.py` (docs/SITING.md); todo: simulation-based `sim_gain_s` (Step 5) | top candidates improve delay in simulation |
