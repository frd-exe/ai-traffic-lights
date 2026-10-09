# Architecture

```
 Browser (React + Vite + MapLibre GL)
   │  REST /api/*                 WS /ws/sim (5 Hz, latest-only)
   ▼
 FastAPI backend (Python 3.11)
   ├─ area/        OSM fetch (Overpass) → RoadNetwork → junction detection → ranking
   ├─ sim/         SimEngine (0.1 s step), phases.py, unsignalised priority, metrics
   ├─ control/     fixed · webster · max_pressure · gemini+max_pressure (plan executor)
   ├─ ai/          Gemini supervisor (async, ≥6 s), quota/failure tracking, fallback + probe
   ├─ demand/      Google Routes congestion → DemandProfile (live → cached → snapshot → baseline)
   └─ contract/    models.py · constants.py · helpers.py · interfaces.py   ← everyone imports this
```

**Flow:** the user drags a box → `POST /api/area` (OSM roads, ranked junctions) → the user selects junctions →
`POST /api/demand/resolve` (a frozen DemandProfile) → `POST /api/sim/start` ×2 (AI vs fixed, same
profile + seed) → WS ticks render vehicles, signals, metrics, status banners and AI explanations.

**Loops per session:**
- *Sim loop* (asyncio task): `step(0.1)` × N per wall tick, scaled by `speed`.
- *Controller* every 1 sim-s: `decide(observe())` → `SignalCommands`.
- *Supervisor* (ai mode only), wall-clock ≥6 s, never blocks the sim loop: `plan(observe())` → `Plan`s.
- *Broadcaster*: 5 Hz, latest-only.

**Fallbacks** (CONTRACT §9, §10): AI → fixed timers with a banner; Google → cached → snapshot → baseline with a banner.

**Today:** only `contract/` and `mock_server.py` exist. The frontend runs fully against the mock (`python run_demo.py`).
Each workstream replaces mock pieces behind the same contract (see `handoff/`).
