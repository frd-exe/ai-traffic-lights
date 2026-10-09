# Results: controller comparison (demo city)

_Generated 2026-10-09 14:50 UTC by `python -m backend.experiments.compare`. Raw rows: `docs/results.json`._

**Setup.** Synthetic 3×3 demo grid (`area_grid_mock`). The **same 3 junctions are signalised in every mode**: the simulation-based siting top 3 (`i_1c42c10c`, `i_27451942`, `i_ac9b6377`), which the UI also preselects; the other 6 junctions are unsignalised (priority + gap acceptance). 10 simulated minutes per run, seeds 1–3, same seed ⇒ identical demand schedule across controllers. Simulated demand per entry: low 120 / medium 200 / high 280 / rush 380 veh/h; **surge** = rush, then from t=300 s a 2× override on entry `n20`.

**Gemini** = `gemini+max_pressure`: live Gemini API (`gemini-3.5-flash-lite`), one supervisor call every 20 **sim**-seconds (≈30 calls per run), plans applied on top of max-pressure. The LLM is non-deterministic, so the same seed does not give the same result twice.

**Columns.** avg wait = contract `avg_wait_s` at the end of the run (mean ± sd over seeds) · throughput = trips completed per minute over the whole run · max queue = most stopped vehicles on any single approach, sampled every 10 s (max over seeds) · status: **filled** = measured · **INVALID** = Gemini hit a limit/fallback (excluded from means) · **USER MUST RUN**.

## Main table

| demand | controller | n | avg wait (s) | throughput (veh/min) | max queue (veh) | status |
|---|---|---|---|---|---|---|
| low | fixed (30 s) | 3 | 10.9 ± 1.0 | 19.7 ± 0.8 | 5 | filled |
| low | webster | 3 | 6.9 ± 0.9 | 19.8 ± 0.9 | 5 | filled |
| low | max_pressure | 3 | 7.0 ± 0.9 | 19.9 ± 1.0 | 7 | filled |
| low | gemini | 3 | 7.5 ± 0.9 | 19.9 ± 0.8 | 5 | filled |
| medium | fixed (30 s) | 3 | 21.6 ± 1.7 | 31.8 ± 1.6 | 15 | filled |
| medium | webster | 3 | 19.2 ± 2.4 | 31.7 ± 1.3 | 11 | filled |
| medium | max_pressure | 3 | 26.8 ± 3.1 | 31.3 ± 1.6 | 15 | filled |
| medium | gemini | 3 | 21.5 ± 1.8 | 31.6 ± 1.6 | 13 | filled |
| high | fixed (30 s) | 3 | 77.8 ± 46.1 | 34.0 ± 5.6 | 23 | filled |
| high | webster | 3 | 41.6 ± 11.7 | 39.2 ± 1.5 | 23 | filled |
| high | max_pressure | 3 | 81.1 ± 36.2 | 33.8 ± 6.2 | 23 | filled |
| high | gemini | 3 | 58.4 ± 35.0 | 36.6 ± 4.9 | 23 | filled |
| rush | fixed (30 s) | 3 | 138.5 ± 36.7 | 36.0 ± 6.6 | 46 | filled |
| rush | webster | 3 | 145.7 ± 12.0 | 34.1 ± 3.1 | 46 | filled |
| rush | max_pressure | 3 | 106.1 ± 15.3 | 43.1 ± 4.0 | 24 | filled |
| rush | gemini | 1 | 127.3 | 37.3 | 24 | filled |

## Relative to fixed timers (avg wait)

- **low**: fixed 10.9 s; webster 6.9 s (-36%); max_pressure 7.0 s (-36%); gemini 7.5 s (-31%)
- **medium**: fixed 21.6 s; webster 19.2 s (-11%); max_pressure 26.8 s (+24%); gemini 21.5 s (-0%)
- **high**: fixed 77.8 s; webster 41.6 s (-46%); max_pressure 81.1 s (+4%); gemini 58.4 s (-25%)
- **rush**: fixed 138.5 s; webster 145.7 s (+5%); max_pressure 106.1 s (-23%); gemini 127.3 s (-8%)
- **surge**: fixed 145.6 s; webster 155.5 s (+7%); max_pressure 110.3 s (-24%)

## Details (incl. surge, trip delay, saturation)

| demand | controller | n | trip delay (s) | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|
| low | fixed (30 s) | 3 | 26.7 ± 0.6 | 1 | 0 | filled |
| low | webster | 3 | 23.3 ± 1.4 | 1 | 0 | filled |
| low | max_pressure | 3 | 22.0 ± 1.7 | 1 | 0 | filled |
| low | gemini | 3 | 22.4 ± 1.8 | 1 | 0 | filled |
| medium | fixed (30 s) | 3 | 45.9 ± 2.1 | 2 | 0 | filled |
| medium | webster | 3 | 43.6 ± 4.0 | 2 | 0 | filled |
| medium | max_pressure | 3 | 50.1 ± 3.1 | 2 | 0 | filled |
| medium | gemini | 3 | 45.0 ± 1.1 | 2 | 0 | filled |
| high | fixed (30 s) | 3 | 101.9 ± 40.8 | 28 | 0 | filled |
| high | webster | 3 | 73.7 ± 13.6 | 3 | 0 | filled |
| high | max_pressure | 3 | 103.8 ± 30.5 | 20 | 0 | filled |
| high | gemini | 3 | 87.0 ± 31.0 | 15 | 0 | filled |
| rush | fixed (30 s) | 3 | 171.2 ± 32.6 | 110 | 0 | filled |
| rush | webster | 3 | 175.9 ± 11.9 | 105 | 0 | filled |
| rush | max_pressure | 3 | 145.1 ± 10.5 | 77 | 0 | filled |
| rush | gemini | 1 | 154.5 | 93 | 0 | filled |
| surge | fixed (30 s) | 3 | 172.3 ± 29.4 | 121 | 0 | filled |
| surge | webster | 3 | 180.9 ± 8.2 | 126 | 0 | filled |
| surge | max_pressure | 3 | 147.5 ± 10.2 | 82 | 0 | filled |
| surge | gemini | 0 | - | - | - | NOT RUN (not in this experiment) |

## Gemini runs

| demand | seed | model | calls ok / failed | avg wait (s) | max_pressure same seed (s) | Δ vs max_pressure | status |
|---|---|---|---|---|---|---|---|
| low | 1 | gemini-3.5-flash-lite | 30 / 0 | 8.6 | 7.81 | +0.8 | filled |
| low | 2 | gemini-3.5-flash-lite | 30 / 0 | 7.18 | 7.2 | -0.0 | filled |
| low | 3 | gemini-3.5-flash-lite | 30 / 0 | 6.8 | 5.95 | +0.8 | filled |
| medium | 1 | gemini-3.5-flash-lite | 30 / 0 | 21.32 | 26.95 | -5.6 | filled |
| medium | 2 | gemini-3.5-flash-lite | 29 / 1 | 23.49 | 29.84 | -6.4 | filled |
| medium | 3 | gemini-3.5-flash-lite | 30 / 0 | 19.83 | 23.71 | -3.9 | filled |
| high | 1 | gemini-3.5-flash-lite | 30 / 0 | 42.76 | 54.91 | -12.1 | filled |
| high | 2 | gemini-3.5-flash-lite | 30 / 0 | 33.82 | 122.37 | -88.6 | filled |
| high | 3 | gemini-3.5-flash-lite | 30 / 0 | 98.47 | 65.89 | +32.6 | filled |
| rush | 1 | gemini-3.5-flash-lite | 30 / 0 | 127.35 | 120.75 | +6.6 | filled |

## Optional baseline: existing OSM signals + fixed timing

NOT RUN: not applicable on the demo city (a synthetic grid has no OSM signals).

## Caveats

- n = 3 seeds; differences smaller than the sd are not meaningful.
- Max-pressure's hysteresis (10 vehicles) and Webster's saturation flow (900 veh/h/lane) were tuned on seed 1 with all 9 junctions signalised; seed 1 is not out-of-sample.
- Demand is simulated (no real traffic data); results are relative to this model, not measurements.
- Rows: 55; wall time of the last invocation 1785.6 s.
