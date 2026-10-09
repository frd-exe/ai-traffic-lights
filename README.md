# SignalFlow: AI-controlled traffic lights

A live, side-by-side simulation: the same synthetic city runs on **fixed timers** (left) and on **AI control** (right), with the same cars and the same demand.

## Start

**Windows:** double-click **`start.bat`**. It sets up Python packages on the first run, starts the app and opens your browser at http://127.0.0.1:8000.
Requirement: Python 3.11+ (`winget install Python.Python.3.12`). The release ZIP already contains the built web app, so Node.js isn't needed.
**Other OS:** `python -m venv .venv`, `.venv/bin/pip install -r backend/requirements.txt`, then `.venv/bin/python run_demo.py`.

## Try it (4 steps)

1. Pick **Rush** as the demand level (left panel).
2. Press **▶ Start**: Fixed timers (left) vs AI (right) start on the same seed and demand.
3. Press **⚡ Surge**: both sides get double the traffic at the same simulation time.
4. Watch the **metrics** under each map (average wait, trip delay, queue, throughput), the chart, and the AI explanation feed.

## What is simulated

Everything you see is simulated:
- **City:** a synthetic 3 × 3 grid with 150 m blocks and one primary street. It isn't a real place, and no map tiles are used.
- **Demand:** simulated, with no real traffic data. Each entry road gets low 120 / medium 200 / high 280 / rush 380 vehicles per hour, times a multiplier.
- **Cars:** a car-following model (IDM). Signals run with yellow 3 s, all-red 2 s and a minimum green of 7 s. Unsignalised junctions use priority rules.

## How the AI works

- **Gemini suggests plans:** every 15 s it receives a compact snapshot of the queues and returns, for each junction, which phase to hold green and for how long (5–40 s), with a one-line reason. At most 40 calls per session.
- **Max-pressure fills in between:** an adaptive controller runs every second. It serves the approaches with the most waiting cars, and it ends a Gemini plan early when traffic clearly disagrees with it.
- **Safety rules decide:** a safety layer and the engine enforce yellow, all-red and minimum green, ensure no conflicting greens, and keep every red under 60 s, whatever the AI suggests.
- **Fallback:** if the AI limit is hit (daily quota, rate limit, or the 40-call session budget), the AI side switches to the **adaptive max-pressure fallback** and shows **"Live AI quota reached. Using adaptive fallback."** Fixed timers are used only as the last resort.

## Results (from `docs/results.md`)

Average wait in seconds (lower is better). Mean of 3 seeds, 10 simulated minutes, the same 3 signalised junctions in every mode.

| demand | fixed (30 s) | Webster | max-pressure | Gemini + max-pressure |
|---|---|---|---|---|
| low | 10.9 | 6.9 | 7.0 | 7.5 |
| medium | 21.6 | 19.2 | 26.8 | 21.5 |
| high | 77.8 | 41.6 | 81.1 | 58.4 |
| rush | 138.5 | 145.7 | 106.1 | 120.8 |

The surge scenario (rush plus 2× on one entry) is in `docs/results.md`. Throughput and max queue are there too. At high and rush demand the results vary a lot between seeds (sd up to ±46 s).

**Why the difference between Fixed and AI can look small:** the demo city is a small, uniform grid with balanced demand and single-lane roads without turn pockets. Fixed timers only lose a lot when traffic is uneven or changing, because they keep giving green to empty roads.
- At medium demand, Gemini only ties fixed timers.
- At rush, Gemini is about 13% better and max-pressure about 23% better, with a large spread between seeds.
- At low demand the gain is about 30–35%.

Real roads with uneven, changing traffic would show larger gains.

**AI mode uses a shared API key with a daily quota.** If the banner appears, the app continues with the adaptive fallback.

## Limitations

- **Model:** no lane changing and no turn pockets, so left turners block single-lane approaches.
- **Demand:** the levels are plausible guesses, not calibrated, and there is only one synthetic city.
- **Gemini:** its output varies run to run (n = 3 per demand), and calls take a few seconds, so plans apply with a delay.
- **Siting:** the simulation-based choice of the 3 signals used one seed and short runs.

## For developers

- **Checks:** `python -m pytest`; `cd frontend && npm run build`; `python run_demo.py --check` (headless end-to-end test).
- **Docs:** [contract](docs/CONTRACT.md) · [architecture](docs/ARCHITECTURE.md) · [siting](docs/SITING.md) · [results](docs/results.md) · [pitch](docs/PITCH.md)
