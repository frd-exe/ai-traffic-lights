## Summary

Implemented all task groups (a), (b), and (c) on `codex/step-2`.
Headless deterministic IDM with per-edge lane queues and frozen profile snapshots.
Independent Poisson OD demand and driver RNG streams; shortest free-flow routes.
External spawn queues, contract metrics, observations, and state/schedule hashes.
Engine-owned signal clearance, minimum green, starvation prevention, safe fallback.
Geometry phases with exclusive junction reservations for conflicting turns.
Unsignalized road-class/right-hand priority and stochastic-driver gap thresholds.
Global immobility and local mutual-blocking detection with oldest-car teleport.
Fixed and Webster controllers, offline fixture loader, tests and benchmark.
No contract, backend wiring, AI, server, or WebSocket files were changed.

## contract_version used

`0.1.0`, read from `docs/CONTRACT.md`, `backend/contract/models.py`, constants,
and interfaces after `git pull --rebase origin main` (main was already current).

## Files

- `backend/sim/__init__.py`: exports concrete `SimEngine` matching the factory signature.
- `backend/sim/engine.py`: IDM, lanes, admission, external queues, observations, metrics, hashes, deadlocks.
- `backend/sim/demand.py`: per-entry independent Poisson schedules, OD shortest paths, expected approach flows.
- `backend/sim/phases.py`: deterministic geometry phase derivation.
- `backend/sim/signals.py`: engine-owned signal state machine and pending requests.
- `backend/sim/fixtures.py`: offline network loader and fixture junction detection using contract ID helpers.
- `backend/sim/README.md`: flow levels, assumptions, physics, metric and timing semantics.
- `backend/sim/requirements.txt`: NumPy 2.3.3 (supports the repository's Python 3.11+ requirement).
- `backend/control/fixed.py`: `FixedController(cycle_s=30)`, green seconds per phase, safe mid-run attachment.
- `backend/control/webster.py`: `WebsterController(engine)`, expected demand flows and Webster splits.
- `backend/control/__init__.py`: exports both controllers.
- `backend/tests/test_sim.py`: determinism, demand, scale, timing, swap, safety, conservation, priority, deadlock, metrics.
- `scripts/benchmark_sim.py`: offline 500-car workload, optional sustained replenishment.
- `handoff/codex_2.md`: this handoff.

## How to run tests + benchmark

From the repository root, using Python 3.11+:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt -r backend/sim/requirements.txt
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m scripts.benchmark_sim --seconds 60 --vehicles 500 --maintain
```

Validation: **55 passed, 1 skipped**. The skip is optional
`backend/data/sample_area.json`, which is absent. Existing FastAPI mock tests emit
one Starlette/httpx deprecation warning. Simulation needs no server process.

Integration: instantiate with contract models; every simulation second call
`controller.decide(engine.observe())`, and pass those commands to each
`engine.step(0.1, commands)`. Observe/signals cover selected intersections only;
metrics also cover all supplied unsignalized approaches. To switch to fallback,
attach a new `FixedController`; it requests the next phase (or retains an ongoing
transition's target), and the engine applies minimum green and clearance.
Webster reads `engine.demand.approach_flows(engine.intersections)` at attachment.
Compare demand digests at the same simulation time. `set_demand` is pre-start only.

## Benchmark numbers

Windows 11, CPython 3.12.4, NumPy 2.3.3, provided 5x5 grid, seed 42,
0.1 s integration, controller every 1 s, rush demand:

| Workload | Sim seconds | Wall seconds | Real-time factor | Active cars |
| --- | ---: | ---: | ---: | --- |
| Sustained 500 (`--maintain`) | 60 | 1.2127 | 49.48x | 500–508, final 503 |

194 genuine exits; zero teleports. Setup and vehicle serialization are excluded;
controller observations and workload replenishment are included. Replenishment
adds benchmark-only cars outside the OD schedule to maintain the active count;
it is not used in the actual engine. Without `--maintain` the initial 500 cars
can drain naturally. Numbers are a single local measurement, not a CI threshold.

## Phase derivation description

Sort approaches by bearing modulo 180, bearing, then ID. Pair the closest
opposing approach if within 20 degrees of 180; otherwise use a singleton.
IDs are `<intersection_id>:p<k>`. Orthogonal four-arm junctions give NS/EW;
three arms normally give pair + singleton, five arms two pairs + singleton.
Irregular geometry can give additional singleton phases.

Green allows requesting an exclusive two-second junction reservation. Even
opposing green approaches serialize their crossing movements; therefore left
turns and intersecting paths cannot enter together. Downstream lane space is
required before admission. Yellow/all-red forbid new crossings. This is a
conservative safe approach-level model rather than a detailed turning-lane model.
Commands arriving during minimum green or clearance remain queued. The target
does not change midway through clearance. Starvation scheduling reserves enough
minimum-green/clearance time for every pending phase, including five singleton
phases, to serve approaches within the 60-second bound when feasible.

## Contract deviations

No schema/model/interface changes; all payloads import contract models.
Per the task, local mutual-blocking cycles after >120 s also increment
`deadlocks`; the prose metric defines only global 60 s immobility events.
Global immobility detection/resolution is implemented as well.
`blocked_spawns` counts each deferred scheduled vehicle once, rather than every
0.1-second retry; this interpretation is documented to avoid timestep inflation.
Teleported vehicles remain in the finished metric population for 120 s but
are excluded from genuine-exit throughput.

## Contract change requests

Owner clarification requested, with no contract edits made here:
1. Broaden the `deadlocks` definition to include the task's local >120 s cycles,
   or add an independent cycle-resolution counter.
2. Confirm unique deferred vehicles as the unit of `blocked_spawns`.
3. Specify teleport accounting in population/delay/throughput explicitly.
4. Document that phase greens are approach permission with engine conflict
   arbitration; future turn-specific protected phases need movement-level models.
5. More than five mutually exclusive phases cannot generally satisfy all of
   minimum green 7 s, clearance 5 s and max red 60 s; define the policy for such
   junctions. The engine always preserves clearance/minimum-green safety.

## Known issues

- No optional sample-area fixture was available; grid fixtures and synthetic
  three/five-arm geometry are tested.
- No lane changing; uniform reachable-exit OD, independent arrivals and fixed
  routes simplify real behavior. Caller should supply production intersection models.
- Opposing-through movements serialize too, so junction throughput is conservative;
  this can bias siting estimates. Webster's 1800 veh/h/lane assumption is consequently
  optimistic relative to the exclusive junction admission model.
- Headway variation is seeded; gap acceptance uses that per-driver variation,
  rather than drawing fresh randomness on every rejected gap.
- Speeds are capped at the legal limit; the scheduled factor above 1 does not
  cause speeding. Geometry positions approximate distance along polylines in
  a local planar projection.
- Full schedule history is retained for digest auditing; extremely long runs
  will grow memory with arrivals. Normal hackathon/siting runs are bounded.
- No wiring to Claude-owned controllers/backend sessions was changed.
