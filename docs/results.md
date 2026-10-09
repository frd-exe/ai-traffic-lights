# Results: controller comparison (demo city)

_Generated 2026-10-09 13:25 UTC by `python -m backend.experiments.compare`. Raw rows: `docs/results.json`._

Setup: synthetic 3×3 demo grid (`area_grid_mock`), **all 9 junctions signalised in every mode**, 10 simulated minutes per run, contract metrics at the end of the run (population = trips finished in the last 120 s + vehicles in the network + external spawn queue). Demand is simulated: per-entry flow low 120 / medium 200 / high 280 / rush 380 veh/h; **surge** = rush, then from t=300 s a 2× override on entry `n20` (west end of the primary street) via a demand segment. Same seed ⇒ identical demand schedule across controllers. Values are mean ± sd over n seeds.

Row status: **filled** = measured here · **INVALID** = AI run hit the limit/fallback (excluded from means; `limit_reached_at_t` recorded) · **USER MUST RUN** = needs Gemini access (run locally).

## low

| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|---|
| fixed | 3 | 22.9 ± 0.6 | 43.0 ± 0.8 | 0.7 | 1 | 0 | filled |
| webster | 3 | 13.6 ± 0.8 | 34.0 ± 2.1 | 0.4 | 1 | 0 | filled |
| max_pressure | 3 | 14.2 ± 4.7 | 30.7 ± 5.2 | 0.5 | 1 | 0 | filled |
| ai | 0 | - | - | - | - | - | USER MUST RUN (optional: `--only-ai --ai-demands low`) |

## medium

| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|---|
| fixed | 3 | 28.3 ± 0.9 | 53.6 ± 1.3 | 1.3 | 2 | 0 | filled |
| webster | 3 | 24.0 ± 0.3 | 49.9 ± 0.7 | 0.9 | 2 | 0 | filled |
| max_pressure | 3 | 28.8 ± 3.7 | 51.5 ± 4.6 | 1.2 | 2 | 0 | filled |
| ai | 0 | - | - | - | - | - | USER MUST RUN |

## high

| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|---|
| fixed | 3 | 39.7 ± 1.3 | 70.1 ± 1.5 | 1.9 | 3 | 0 | filled |
| webster | 3 | 35.4 ± 2.2 | 64.8 ± 3.0 | 1.9 | 3 | 0 | filled |
| max_pressure | 3 | 36.9 ± 1.6 | 65.0 ± 2.0 | 1.6 | 3 | 0 | filled |
| ai | 0 | - | - | - | - | - | USER MUST RUN (optional: `--only-ai --ai-demands high`) |

## rush

| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|---|
| fixed | 3 | 80.8 ± 3.4 | 122.8 ± 3.1 | 3.7 | 24 | 0 | filled |
| webster | 3 | 76.8 ± 7.7 | 119.4 ± 11.3 | 3.4 | 14 | 0 | filled |
| max_pressure | 3 | 76.2 ± 9.1 | 116.9 ± 11.5 | 3.5 | 5 | 0 | filled |
| ai | 0 | - | - | - | - | - | USER MUST RUN |

## surge

| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |
|---|---|---|---|---|---|---|---|
| fixed | 3 | 82.9 ± 1.7 | 125.0 ± 3.3 | 4.2 | 25 | 0 | filled |
| webster | 3 | 82.0 ± 7.0 | 124.3 ± 12.2 | 3.8 | 15 | 0 | filled |
| max_pressure | 3 | 78.4 ± 5.1 | 119.1 ± 7.8 | 3.6 | 8 | 0 | filled |
| ai | 0 | - | - | - | - | - | USER MUST RUN (optional: `--only-ai --ai-demands surge`) |

## Ablation: max_pressure vs ai (same seeds and demands)

| scenario | seed | max_pressure avg_wait_s | ai avg_wait_s | Δ (ai − mp) | ai status |
|---|---|---|---|---|---|
| medium, rush | 1 | see above | - | - | USER MUST RUN |

The LLM is non-deterministic: repeated AI runs with the same seed differ. Report n and sd; do not read a single AI run as a result.

## Optional baseline: existing OSM signals + fixed timing

NOT RUN: not applicable on the demo city (a synthetic grid has no OSM signals), and compare.py only runs the demo city so far. Extending it to an OSM sample is future work.

## Summary (computed from the rows above)

- **low**: fixed 22.9 s; webster 13.6 s (-41% vs fixed); max_pressure 14.2 s (-38% vs fixed)
- **medium**: fixed 28.3 s; webster 24.0 s (-15% vs fixed); max_pressure 28.8 s (+2% vs fixed)
- **high**: fixed 39.7 s; webster 35.4 s (-11% vs fixed); max_pressure 36.9 s (-7% vs fixed)
- **rush**: fixed 80.8 s; webster 76.8 s (-5% vs fixed); max_pressure 76.2 s (-6% vs fixed)
- **surge**: fixed 82.9 s; webster 82.0 s (-1% vs fixed); max_pressure 78.4 s (-5% vs fixed)

Caveats: the max-pressure hysteresis (10 vehicles) and Webster's saturation flow (900) were tuned on seed 1 before these runs (see the controller docstrings), so seed 1 is not out-of-sample. Differences smaller than the sd are not meaningful with n = 3.

## Saturation check (C8)

`max avg_queue` = largest network-mean stop-line queue sampled every 10 s; `max ext. queue` = largest number of vehicles waiting outside the network to enter. Rush/surge should show growing queues and a non-empty external queue, with **0 deadlocks** (no gridlock).

## Notes

- Runs: 45 rows; wall time of the last invocation 47.9 s.
- Max-pressure uses a switch hysteresis of 10 vehicles; Webster uses 900 veh/h/lane saturation flow and a 90 s cycle cap, both calibrated for this engine (see their module docstrings).
- All controllers pass through the same safety layer (min green, transition lock, max-red guard); override counts are in `docs/results.json`.
