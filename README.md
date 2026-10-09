# AI-controlled traffic lights

Hackathon project. Pick an area on a map (or use the built-in demo city); the system finds the
intersections, ranks the best ones to signalise, simulates cars, and an AI (Gemini + max-pressure)
controls the lights. Compare against a fixed-timer baseline side by side. Demand is simulated
(levels, a multiplier and per-entry overrides; no external traffic data). If the AI limit is
reached, signals revert to fixed timers and the UI says so.

> Status: contract 0.3.0. **Real backend** (areas, OSM parser, structural + simulation-based siting,
> demand, geocode, headless engine, fixed/Webster/max-pressure/Gemini controllers with safety layer and
> AI-limit fallback, live sessions over WebSocket) + **mock backend** + frontend shell (full UI: Codex).

## Requirements

Python 3.11+, Node.js 20+, Git.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/sim/requirements.txt
cd frontend && npm ci && cd ..
cp .env.example .env    # then add your keys (Windows: copy .env.example .env)
```

## Run

```bash
python run_demo.py                       # mock backend :8000 + frontend http://localhost:5173
python run_demo.py --scenario ai_limit   # also: ai_replay (or pick in the UI)
python run_demo.py --real                # real backend + real simulation (AI mode needs GEMINI_API_KEY)
python -m uvicorn backend.app:app --port 8000   # real backend alone
```

The demo city (`backend/data/grid_network.json`) is used automatically when there is no OSM sample.

## Optional: a real OSM area

```bash
python scripts/fetch_sample_area.py --bbox WEST,SOUTH,EAST,NORTH   # e.g. --bbox 2.160,41.385,2.172,41.395
python scripts/fetch_sample_area.py --offline-check
```

If Overpass keeps failing (504s are common), wait and re-run (partial results are cached), use a
smaller box, or just keep the demo city.

## Experiments and AI

```bash
python -m backend.experiments.compare --skip-ai        # fixed / webster / max_pressure -> docs/results.md
python -m backend.experiments.compare --only-ai --ai-pace 10   # add the AI rows (uses Gemini)
python scripts/check_gemini_plan.py                    # one live Gemini call, prints the plans
python scripts/precompute_siting.py                    # simulation-based siting for the demo city
python -m scripts.benchmark_sim --seconds 60 --vehicles 500 --maintain
```

## Checks

```bash
python -m pytest                              # contract, roadnet, siting, demand, area cache, geocode, mock, scripts
python scripts/export_schemas.py --check      # docs/schemas up to date?
cd frontend && npm run lint && npm run typecheck && npm run build
cd frontend && npm run gen:types              # after any contract change
```

## Scripts (run locally; they need internet)

- `python scripts/check_keys.py`: one Gemini models-list call; prints OK/FAIL, never the keys.
- `python scripts/fetch_sample_area.py`: Overpass → `backend/data/sample_area.json` (retries, mirrors, tiling, cache).

## Docs

[Contract](docs/CONTRACT.md) · [Architecture](docs/ARCHITECTURE.md) · [Siting](docs/SITING.md) ·
[Data sources](docs/DATA_SOURCES.md) · [Handoff](handoff/README.md)
