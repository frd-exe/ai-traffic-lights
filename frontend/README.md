# SignalFlow frontend

React + Vite + TypeScript + MapLibre GL. Contract version 0.1.0; generated types
come from the committed JSON schemas. No API keys are required for this mock demo.

From the repository root:

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

Open http://localhost:5173. Search on Enter, select a rectangle or use the map
center, analyze, choose signal locations, resolve data, and Start. Demo mode
prepares a sample grid and starts Fixed vs AI against one snapshot profile.
Search and analysis are mocked by this backend: every area returns the sample grid.

Use `?scenario=ai_limit` or `?scenario=google_down` on the page, or the Mock
scenarios selector. At speed ×4 the mock AI fallback occurs after about five
wall seconds (20 simulation seconds). The fallback banner persists, a toast
appears once per transition, the feed records the event, and the AI chart becomes
dashed. The reset button appears only when calls_today reaches daily_cap;
the mock reset endpoint resets its counter but does not clear a forced scenario.
Stop and restart in Normal mode to exercise recovery in the mock.

The basemap uses https://tiles.openfreemap.org/styles/liberty (verified HTTP 200).
Failure falls back to OSM raster, then a plain dark background. Network, car,
signal and selection layers remain available independently of the basemap.
Visible attribution is retained in every mode. A fullscreen control is available
on each map. Keyboard area coordinates and ranked-junction checkboxes provide
alternatives to dragging and clicking map features.

`VITE_API_URL` sets the backend root for REST and WS. Empty uses same-origin;
Vite proxies `/api` and `/ws` to `BACKEND_URL` (default http://127.0.0.1:8000).
Cross-origin backends must configure CORS. For production serve `frontend/dist`
and proxy both routes to the backend, or set VITE_API_URL before building.

Validation:

```powershell
npm run gen:types -- --check
npm run build
npm run lint
npx playwright install chromium
npm test
```

Playwright starts the mock backend and Vite automatically (Python virtualenv at
the repo root; Node 20+). It uses live REST/WS, simulates external tile failures,
tests compare timing and fallback states, and saves screenshots to
`handoff/screens/`. `npm test -- tests/playback.spec.ts` also tests interpolation,
shared-clock buffering, and status eligibility. Tests reuse local servers when
available; stop other servers if their configuration differs from this mock.

Playback uses one requestAnimationFrame clock, capped at the slower session's
latest simulation time. Frames bracket that time; continuous metrics and edge
offsets interpolate, discrete counters/signals/controller status retain the
earlier sample until the next frame. Vehicles follow curved edge geometry and
adjacent-edge transitions; unknown multi-edge jumps snap instead of crossing
buildings. Both maps render at the display refresh rate; UI updates at 10 Hz,
and the chart samples approximately once per simulation second. Compare buffers
are bounded; prolonged stalls stop playback with a visible restart message.

Demand resolution stages a new profile for the next Start. Both compared runs
receive the exact same seed, profile ID and selected intersections. During a run,
the map uses the frozen active profile and signal selection. A partial start is
rolled back by stopping sessions that already started. Stop retains aligned final
playback results. A lost socket freezes both views and offers Stop/Start recovery.

Known backend limitation: the demo requests Google snapshot with `level: rush`,
but contract level semantics apply only to baseline demand, and the mock ignores
it for snapshots. The demo displays the returned snapshot scales and explicitly
states this limitation. To run guaranteed rush demand, resolve Manual level / Rush.
All demo improvements are calculated from live metrics and labeled simulated.
