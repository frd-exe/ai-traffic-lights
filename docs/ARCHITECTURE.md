# Architecture

```
 Browser (React + Vite + MapLibre GL)
   │  REST /api/*                 WS /ws/sim (5 Hz, latest-only)
   ▼
 FastAPI backend (Python 3.11)            backend/app.py  (real)   backend/mock_server.py (mock)
   ├─ area/        AreaService: demo city or OSM sample → network + ranking, cached per area_id (memory + disk)
   ├─ roadnet/     Overpass JSON → RoadNetwork (drivable classes, 20 m consolidation, bbox clip) → Intersections
   ├─ siting/      structural pre-filter score (docs/SITING.md)
   ├─ traffic/     simulated demand: level × multiplier × per-entry overrides → frozen DemandProfile
   ├─ geocode.py   Nominatim proxy (1 req/s, cache)
   ├─ sim/         SimEngine (0.1 s step), phases.py, unsignalised priority, metrics      [Codex, in progress]
   ├─ control/     fixed · webster · max_pressure · gemini+max_pressure (plan executor)   [todo]
   ├─ ai/          Gemini supervisor, quota/failure tracking, fallback + probe            [todo]
   └─ contract/    models.py · constants.py · helpers.py · interfaces.py   ← everyone imports this
```

**Flow:** pick an area → `POST /api/area` (demo city, or the OSM sample clipped to the bbox; ranked junctions) →
the user selects junctions → `POST /api/demand/resolve` (a frozen DemandProfile) → `POST /api/sim/start` ×2
(AI vs fixed, same profile + seed) → WS ticks render vehicles, signals, metrics, banners and AI explanations.
`POST /api/sim/demand` changes demand mid-run (same `at_t` for both sessions).

**Loops per session:**
- *Sim loop* (asyncio task): `step(0.1)` × N per wall tick, scaled by `speed`.
- *Controller* every 1 sim-s: `decide(observe())` → `SignalCommands`.
- *Supervisor* (ai mode only), wall-clock ≥6 s, never blocks the sim loop: `plan(observe())` → `Plan`s.
- *Broadcaster*: 5 Hz, latest-only.

**State on disk** (`backend/data/state/`, gitignored): `areas/<area_id>.json`, `demand/<id>.json`,
`geocode_cache.json`. The server never calls Overpass; OSM data comes only from `scripts/fetch_sample_area.py`.

**Fallbacks:** AI → fixed timers with a banner (CONTRACT §9); no OSM sample → demo city.
