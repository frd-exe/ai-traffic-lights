# SignalFlow frontend

React + Vite + TypeScript + MapLibre GL. It opens on the **synthetic demo city** (dark background,
no map tiles, works offline) with the simulation-based top 3 junctions preselected, and shows a
**Fixed vs AI** split on the same seed and the same frozen demand profile.

- Normal use: `python run_demo.py` from the repo root builds this app and serves it with the backend
  on one port (http://127.0.0.1:8000).
- Development with hot reload: `python run_demo.py --dev` (Vite on :5173, API proxied to :8000), or
  `npm run dev` with a backend already running.
- Mock backend: `python run_demo.py --mock`. Its scenarios are `?scenario=ai_limit` and `?scenario=ai_replay`.
  On the real backend, use `GEMINI_FAKE_FAIL=limit` instead.

Demand controls: level (120/200/280/380 veh/h per entry), a multiplier from 0.2 to 3.0, **Apply live**,
and **Surge** (×2, max ×3). Live changes call `POST /api/sim/demand` for both sessions with the same `at_t`.

Checks: `npm run gen:types -- --check`, `npm run lint`, `npm run typecheck`, `npm run build`.
Browser tests: `npx playwright install chromium`, then `npm test` (uses the mock backend; offline).

Types in `src/types/contract.gen.ts` are generated from `docs/schemas` (`npm run gen:types`); don't edit them by hand.
