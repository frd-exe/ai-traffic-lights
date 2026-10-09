# AI-controlled traffic lights

Hackathon project. Drag a box over a real area on a map; the system finds the intersections,
picks the best ones to signalise, simulates cars, and an AI (Gemini + max-pressure) controls the
lights. Compare against a fixed-timer baseline side by side. Traffic demand is scaled from Google
Routes API congestion. If the AI limit is reached, signals revert to fixed timers and the UI says so.

> Status: **contract + mock backend + frontend shell.** The real engine, controllers, OSM import and
> Google/Gemini integration come next (see `handoff/`).

## Requirements

Python 3.11+, Node.js 20+, Git.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd frontend && npm ci && cd ..
cp .env.example .env    # then add your keys (Windows: copy .env.example .env)
```

## Run

```bash
python run_demo.py                       # mock backend :8000 + frontend http://localhost:5173
python run_demo.py --scenario ai_limit   # also: ai_replay, google_down (or pick in the UI)
```

## Checks

```bash
python -m pytest                              # contract, schemas, mock server, scripts
python scripts/export_schemas.py --check      # docs/schemas up to date?
cd frontend && npm run lint && npm run typecheck && npm run build
cd frontend && npm run gen:types              # after any contract change
```

## Scripts (run locally; they need internet)

- `python scripts/check_keys.py`: one Gemini models-list call + one Routes API call; prints OK/FAIL, never the keys.
- `python scripts/fetch_sample_area.py --bbox west,south,east,north`: Overpass → `backend/data/sample_area.json` (cached).

## Docs

[Contract](docs/CONTRACT.md) · [Architecture](docs/ARCHITECTURE.md) · [Data sources](docs/DATA_SOURCES.md) · [Handoff](handoff/README.md)
