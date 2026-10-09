## Summary

Built SignalFlow, a dark map-based React/Vite/TypeScript/MapLibre frontend.
Scope (a) and (b) complete; (c) split compare and polish complete.
Demo loads the mock sample area, snapshot and paired runs; snapshot rush is a backend limitation below.
Added Enter-only search, rectangle/keyboard area selection and ranked signal siting.
Resolve demand separately; both compared sessions share one frozen profile and seed.
Buffered shared simulation clock drives both maps, metrics, chart and explanations.
Implemented tick-driven AI/Google banners, one-time toast, fallback chart and daily-cap reset.
OpenFreeMap verified; OSM raster fallback and offline dark network rendering tested.
Generated schema types; build, lint, schema freshness and 10 Playwright tests pass.
Screenshots include AI, Google, split demo, and both fallback banners together.

## contract_version used

`0.1.0`. Read `docs/CONTRACT.md` and all `docs/schemas/*.json` after
`git pull --rebase origin main`. Created `codex/step-3` from `origin/main`,
independent of the previous simulation branch. Ran `npm run gen:types`; output
already matched the schemas. No contract/schema/model/backend files were changed.

## Files

- `frontend/src/App.tsx`: area, demand and run orchestration; live compare workspace; demo.
- `frontend/src/api/index.ts`: typed REST/WS client with `VITE_API_URL`, timeouts and connection errors.
- `frontend/src/api/playback.ts`: shared clock, bracketing buffers, geometry/metric interpolation and ordered explanations.
- `frontend/src/api/useSimulation.ts`: transactional session lifecycle, UI snapshots, toast deduplication and chart history.
- `frontend/src/map/MapPane.tsx`: basemap fallbacks, geometry layers, rectangle tool, junction popups and animation.
- `frontend/src/map/geometry.ts`: bbox dimensions, polyline position and adjacent-edge interpolation.
- `frontend/src/map/search.ts`: geocode map focus.
- `frontend/src/panels/SetupPanel.tsx`: area/siting/data/controllers/seed/speed/scenario controls.
- `frontend/src/panels/SearchBox.tsx`: Enter-only backend geocoding and search states.
- `frontend/src/panels/StatusPanel.tsx`: controller and data status badges.
- `frontend/src/panels/Dashboard.tsx`: all requested metrics and shared-t-axis wait chart with fallback segments.
- `frontend/src/panels/ExplanationFeed.tsx`: time-aligned explanations, latest first.
- `frontend/src/status.ts`: contract banner wording and daily-cap reset eligibility.
- `frontend/src/styles.css`, `frontend/index.html`: responsive dark UI, focus states and metadata.
- `frontend/src/types/contract.gen.ts`: regenerated without content changes.
- `frontend/playwright.config.ts`, `frontend/tests/*.spec.ts`: browser and playback/status/geometry tests.
- `frontend/package.json`, `frontend/package-lock.json`: Playwright dependency and test command.
- `frontend/.env.example`, `frontend/README.md`: API configuration and setup instructions.
- `.gitignore`: excludes transient browser reports.
- `handoff/screens/*.png`, `handoff/codex_3.md`: screenshots and this handoff.

Replaced the starter `frontend/src/api.ts` and `frontend/src/MapView.tsx` with
the modular API and map directories. Reused the existing Vite proxy and ID-bearing
contract payloads; no client-side phase derivation or simulation physics.

## How to run

From repository root (Python 3.11+, Node 20+):

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m uvicorn backend.mock_server:app --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run gen:types
npm run dev
```

Open `http://localhost:5173`. Analyze the default small area (or select a
rectangle / Use center / edit coordinates), customize top-N, Resolve data, Start.
Split compare defaults to Fixed vs AI. Demo mode automatically prepares the
mock sample grid, resolves one snapshot, and starts both sessions.

Use `http://localhost:5173/?scenario=ai_limit` and
`http://localhost:5173/?scenario=google_down`, or Mock scenarios in the sidebar.
At ×4 speed AI fallback occurs after about five wall seconds / 20 sim seconds.
Resolve data during a run only stages the next profile; current map entry colors
and signals continue to use the active run's frozen profile and selection.

```powershell
npm run gen:types -- --check
npm run build
npm run lint
npx playwright install chromium
npm test
```

Results: schema freshness, production build and lint pass; **10 Playwright tests
pass** using actual mock REST/WS and two concurrent sessions. Tests cover
Enter-only search, oversized/drag/keyboard selection, top-N/toggling, profile
identity, frozen running demand, alignment, offline basemap, both scenarios,
persistent banner vs expiring toast, dashed chart, feed, reset eligibility,
demo live numbers, error retry, partial-start rollback and geometry interpolation.
Playwright starts both servers and captures screenshots automatically. Tests
reuse existing local servers; use the unmodified mock and its default 20 s switch.

Set `VITE_API_URL` to the backend root before dev/build; empty uses same-origin
REST/WS via Vite's `BACKEND_URL` proxy (default `http://127.0.0.1:8000`). Remote
backends need CORS. Production needs a reverse proxy for `/api` and `/ws`, or an
explicit backend URL. Fullscreen is available per map. Mobile controls stack;
keyboard users can select coordinates and toggle ranked junction checkboxes.

OpenFreeMap `https://tiles.openfreemap.org/styles/liberty` returned **HTTP 200,
43,079 bytes** during validation. Original data attribution is retained with the
dark palette. On failure, use light-use OSM raster, then plain dark network view.
Browser tests intentionally block external tiles/fonts while REST/WS keep working.

## Contract deviations

No wire/schema/type changes. UI display metrics interpolate continuous fields
between bracketing ticks at the common t; discrete counts, signals and status
retain the earlier frame until their next observed sample. Vehicle rendering
interpolates offsets along real edge polylines, not straight lines across bends.
There is no extrapolation past the slower session; pending explanations are
released only when common t reaches their event time.

Demo requests `source: google_snapshot, level: rush`. The existing contract
assigns demand levels to baseline_only, and the mock ignores level for snapshots.
Therefore the demo actually uses the returned frozen snapshot scales, explicitly
discloses the limitation, and does not claim a fabricated rush profile. Guaranteed
rush is available through Manual level / Rush. This part of item 12 needs the
backend change requested below; all other demo behavior is implemented.

## Contract change requests

1. To support a Google-snapshot rush demo, define an optional demand multiplier
   or level override for non-baseline profiles and persist it during resolve.
   Frontend cannot alter the backend's frozen profile through this contract.
2. Add an optional typed fallback trigger/reason to ControllerStatus (daily_cap,
   429, unavailable). The UI currently infers daily-cap eligibility from
   ai_limit_reached plus calls_today >= daily_cap; schema has no explicit cause.
3. Mock `/api/ai/reset` resets usage but its forced ai_limit scenario stays in
   fallback. Add a mock recovery path if backend-driven recovery must be demoed
   without restarting. UI correctly keeps the banner until a future tick recovers.

## Known issues

- Mock area/geocode always return the Barcelona grid, regardless of the real-map
  rectangle/place. Real extraction and actual Google/Gemini calls belong to backend.
- Snapshot rush override is unsupported; the UI explains it instead of silently
  pretending the profile is rush. Snapshot flow follows returned per-entry scales.
- WebGL is required for MapLibre. If unavailable, setup, accessible intersection
  list, metrics and charts still work, with an explicit map error.
- Lost WS connections freeze both views and require Stop/Start; no automatic
  reconnect that might hide missing status/explanation intervals. A prolonged
  comparison stall exceeding 4,096 retained ticks fails visibly rather than
  displaying mismatched simulation times.
- Unknown multi-edge jumps and the mock's same-edge offset wraps snap at the next
  frame. Connected edge transitions and bends interpolate along network geometry.
- Chart retains the latest 900 points; feed latest 60 visible entries. During long
  sessions chart x-axis slides using the same interval for both lines.
- MapLibre makes the production JS roughly 1.30 MB / 364 kB gzip plus its worker.
  Optional Google fonts fall back to system fonts offline. OSM attribution remains
  visible even on the offline dark style.
- Mock metrics are canned illustrations. Every result and demo difference is
  labeled simulated, and before/after numbers are read from live ticks.

## Screenshots

Captured from Chromium against the real mock server, with external tiles blocked
to demonstrate graceful offline network rendering. All shown metrics came from
WebSocket ticks; images are not mocked UI data.

- [AI limit banner and synchronized fallback](screens/ai-limit.png)
- [Google unavailable snapshot banner](screens/google-unavailable.png)
- [Split demo with live simulated before/after metrics](screens/split-demo.png)
- [Both persistent fallback banners together](screens/both-banners.png)
