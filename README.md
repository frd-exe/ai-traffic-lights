# SignalFlow: AI-controlled traffic lights (hackathon demo)

**Run (Windows):** double-click `start.bat`. **Any OS:** `python -m venv .venv`, `pip install -r backend/requirements.txt`, then `python run_demo.py` and open http://127.0.0.1:8000 (one port; Python 3.11+ and Node 20+ needed).
**Use it:** the demo city loads with 3 signals preselected. Pick a demand level and multiplier, press **Start**, then compare **Fixed timers** (left) with **AI** (right). Press **Surge** to double the demand in both views at the same moment.
**AI:** set `GEMINI_API_KEY` in `.env` (copy `.env.example`). Without a key, or when the AI limit is hit, the AI side switches to fixed timers and a banner says so. `GEMINI_FAKE_FAIL=limit` shows this offline.
**What's simulated:** everything. The city is a synthetic 3×3 grid, demand comes from levels and multipliers (no real traffic data), and cars follow a car-following model with signal and priority rules. The AI is a real Gemini model advising a max-pressure controller.
**Results:** `docs/results.md` (fixed / Webster / max-pressure / Gemini, 3 seeds, 4 demand levels).
**Limitations:** no lane changing or turn pockets; demand levels are guesses, not calibrated; one synthetic grid; Gemini output varies run to run; at high demand, results vary a lot between seeds.
**Checks:** `python -m pytest`, `cd frontend && npm run build`, `python run_demo.py --check` (headless end-to-end self-test).
**More:** [contract](docs/CONTRACT.md) · [architecture](docs/ARCHITECTURE.md) · [siting](docs/SITING.md) · [pitch](docs/PITCH.md) · [handoff](handoff/README.md)
