# Handoff: workstreams

Read `docs/CONTRACT.md` first (now **0.3.0**). Import ids, constants and models from `backend/contract/`;
don't copy values. Contract changes are additive only: bump `CONTRACT_VERSION`, add a changelog row,
run `python scripts/export_schemas.py` and `npm run gen:types`, and commit the generated files.
Never call Overpass, Gemini or Nominatim in tests; mock them (`GEMINI_FAKE_FAIL`, `httpx.MockTransport`).

| # | workstream | status |
|---|---|---|
| A | Road network (`backend/roadnet/`, `backend/area/`) | done |
| B | Sim engine (`backend/sim/`, Codex step 2) | merged + reviewed; movement-level conflicts, 0.2.0 demand (`scale`, mid-run changes) added |
| C | Controllers (`backend/control/`): fixed, webster, max_pressure, safety layer, runner | done |
| D | AI supervisor (`backend/ai/`, `backend/control/ai_gemini.py`) | done (live Gemini call: user must run `scripts/check_gemini_plan.py`) |
| E | Demand (`backend/traffic/`) | done |
| G | Real server (`backend/app.py`, `backend/sessions.py`) | done: all contract endpoints incl. `/ws/sim` |
| H | Siting: structural pre-filter + simulation-based refine | done; demo city precomputed |
| I | Experiments (`backend/experiments/compare.py` → `docs/results.md`) | non-AI rows filled; AI rows: user must run |
| F | **Frontend (Codex, next step)** | see below |

## F. Frontend tasks for Codex (step 4)

`codex/step-3` was **not merged**: it predates contract 0.2.0/0.3.0 (Google data banners and a traffic-source
picker no longer exist) and conflicts with `frontend/src/App.tsx` on main. Rebase it onto main, then:

1. `npm run gen:types` (contract 0.3.0) and fix type errors. Remove all Google wording/UI
   (`google-unavailable` screens, data-source select). `DataStatus` is always "Simulated demand"; no banner for it.
2. **Demand panel:** level (low/medium/high/rush = 120/200/280/380 veh/h per entry), multiplier 0.2–3.0,
   optional per-entry overrides (click an entry node). Start resolves ONE profile for both compare sessions.
   "Apply demand" sends `POST /api/sim/demand` to **both** sessions with the **same `at_t`** (current max t + 2 s);
   the explanation feed then shows "Demand changed …" at that t.
3. **Areas:** "Demo city" button (`GET /api/demo-area`); show `area.source` (synthetic grid vs OSM). Keep the
   search box (geocode) and add box-drag selection for `POST /api/area` (`bbox_too_large`, `area_not_available`,
   `rate_limited` errors shown in plain words).
4. **Siting:** show `structural_score` and `sim_gain_s` per junction (tooltip + size/color), a "Refine with
   simulation" button → `POST /api/area/{id}/refine`, poll `GET` every 2 s while `status=running`, then reload the
   area and preselect `recommended_ids`; show `runtime_s` and the baseline/final wait.
5. **AI status:** banners for `ai_limit_reached` / `ai_unavailable` (exact text from CONTRACT §9, with `since_t`),
   `ai_replay` (info). One-time toast per transition. When `calls_today >= daily_cap`, show a
   "Re-enable AI" button → `POST /api/ai/reset`. Show `calls_today/daily_cap` and `calls_last_min` small in the status panel.
6. **Explanation feed:** per-intersection AI reasons (click → highlight junction), session-wide notices styled
   differently (fallback, resume, demand change, "Ended AI plan early").
7. **Compare view:** two sessions side by side (same profile + seed): metrics table plus small time-series of
   `avg_wait_s` and `trip_delay_s`; label the AI session's `effective_controller` live (it becomes `fixed` on fallback).
8. Run against the **real** backend: `python run_demo.py --real` (AI mode works without a key: it shows the
   `ai_unavailable` fallback immediately, which is a good demo of the banner). Keep the mock working.
9. Playwright: demo city → start AI vs fixed → apply demand → refine; and `GEMINI_FAKE_FAIL=limit` real backend
   shows the banner + toast + feed entry.
