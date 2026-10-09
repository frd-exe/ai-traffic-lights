# Handoff: workstreams

Read `docs/CONTRACT.md` first. Import ids, constants and models from `backend/contract/`; don't
copy values. Contract changes are additive only: bump `CONTRACT_VERSION`, add a changelog row,
run `python scripts/export_schemas.py` and `npm run gen:types`, and commit the generated files.
Never call Overpass, Gemini or Google in tests; mock them.

| # | workstream | builds | done when |
|---|---|---|---|
| A | Road network | `backend/area/`: Overpass/sample_area.json → `RoadNetwork`, junction detection, approaches (ids via helpers), `structural_score` ranking, `/api/area` cache | the grid and sample_area produce valid `AreaResponse`; ids are stable across re-runs |
| B | Sim engine | `backend/sim/`: `SimEngineFactory`, `phases.py`, car following, signal rules, unsignalised priority + gap acceptance, spawn queue, metrics (§4) | determinism tests: same seed+profile ⇒ same `demand_schedule_digest` for every mode; metrics match the definitions |
| C | Controllers | `backend/control/`: fixed (30 s), webster, max_pressure with the rest-in-phase rule, plan executor | the no-flapping test passes (empty approaches ⇒ phase unchanged); AI vs fixed shows a gain on the grid |
| D | AI supervisor | `backend/ai/`: Gemini client, prompt → `Plan` validation, caps/counters (persisted), triggers, fallback, 60 s probe, `/api/ai/reset`, logs | every `GEMINI_FAKE_FAIL` mode leads to the right `ControllerStatus`; a single failure doesn't switch |
| E | Demand | `backend/demand/`: Routes client, ratio → scale, live → cached → snapshot → baseline chain, `GOOGLE_DAILY_CAP`, snapshot recorder script | `GOOGLE_FAKE_FAIL=1` falls back with the correct `DataStatus` |
| F | Frontend | box-drag selection, geocode search, split view, charts, polish | works against the mock and the real backend with no code changes |
| G | Real server | `backend/app.py` wiring A–E behind the same routes as `mock_server.py`; sessions; WS broadcaster | the mock tests also pass against the real app |
