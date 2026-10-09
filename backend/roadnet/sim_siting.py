"""Simulation-based signal siting (refines the structural pre-filter, docs/SITING.md).

1. Candidates = the top <= MAX_CANDIDATES structurally ranked intersections.
2. Baseline: every candidate unsignalised (priority + gap acceptance).
3. Greedy: each round, simulate "current set + one more candidate" for every remaining candidate
   (in parallel, one process per run; signals run max-pressure). Add the candidate that lowers
   avg_wait_s the most. Stop at diminishing returns: when the best improvement is below
   max(MIN_GAIN_S, MIN_REL_GAIN * current wait).
4. sim_gain_s per intersection = its marginal reduction of avg_wait_s (s) when it was added; for
   candidates never added, their best marginal value in the last round (may be <= 0).

Each run: WARMUP_S + MEASURE_S sim-seconds (1 + 3 simulated minutes), same seed and demand for every
run (common random numbers: differences come from the signals, not from demand noise).
Results are cached per (area_id, seed, level) in <STATE_DIR>/siting/; the demo area's result is
precomputed and committed in backend/data/siting/.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable

from backend.contract.models import AreaResponse, DemandLevel, SitingResult
from backend.traffic.demand import build_profile

MAX_CANDIDATES = 8
WARMUP_S = 60.0
MEASURE_S = 180.0
MIN_GAIN_S = 1.0
MIN_REL_GAIN = 0.03
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "siting"


def cache_name(area_id: str, seed: int, level: str) -> str:
    return f"{area_id}_seed{seed}_{level}.json"


def evaluate(args: tuple) -> tuple[float, int]:
    """Top-level (picklable) worker: avg_wait_s and deadlocks for one signalised set."""
    area_json, profile_json, seed, signalized, seconds = args
    from backend.contract.models import AreaResponse as _Area, DemandProfile
    from backend.control.runner import run_headless
    from backend.sim import SimEngine

    area = _Area.model_validate_json(area_json)
    profile = DemandProfile.model_validate_json(profile_json)
    engine = SimEngine(area.network, area.intersections, seed, profile, list(signalized))
    res = run_headless(engine, "max_pressure", seconds)
    return res.metrics.avg_wait_s, res.metrics.deadlocks


def run_siting(
    area: AreaResponse,
    seed: int = 42,
    level: DemandLevel = "rush",
    max_candidates: int | None = None,
    seconds: float | None = None,
    workers: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> SitingResult:
    t0 = time.perf_counter()
    max_candidates = MAX_CANDIDATES if max_candidates is None else max_candidates
    seconds = WARMUP_S + MEASURE_S if seconds is None else seconds
    candidates = [i.id for i in area.intersections[:max_candidates]]
    profile = build_profile(area_id=area.area_id, entry_nodes=list(area.network.entry_nodes), level=level,
                            profile_id=f"dp_siting_{level}")
    area_json, profile_json = area.model_dump_json(), profile.model_dump_json()
    workers = workers if workers is not None else min(len(candidates) or 1, os.cpu_count() or 1)
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None

    def run_many(sets: list[list[str]]) -> list[tuple[float, int]]:
        jobs = [(area_json, profile_json, seed, tuple(s), seconds) for s in sets]
        return list(pool.map(evaluate, jobs)) if pool else [evaluate(j) for j in jobs]

    try:
        selected: list[str] = []
        current, _ = run_many([[]])[0]
        baseline = current
        gains: dict[str, float] = {}
        remaining = list(candidates)
        while remaining:
            results = run_many([selected + [c] for c in remaining])
            marginal = {c: current - wait for c, (wait, _) in zip(remaining, results)}
            best = max(remaining, key=lambda c: (marginal[c], -candidates.index(c)))
            if progress:
                progress(f"round {len(selected) + 1}: best {best} gain {marginal[best]:.1f} s")
            if marginal[best] < max(MIN_GAIN_S, MIN_REL_GAIN * current):
                for c in remaining:
                    gains[c] = round(marginal[c], 2)
                break
            selected.append(best)
            gains[best] = round(marginal[best], 2)
            current -= marginal[best]
            remaining.remove(best)
    finally:
        if pool:
            pool.shutdown()
    return SitingResult(area_id=area.area_id, seed=seed, status="done", demand_level=level,
                        candidates=candidates, order=selected, gains=gains,
                        baseline_wait_s=round(baseline, 2), final_wait_s=round(current, 2),
                        runtime_s=round(time.perf_counter() - t0, 1),
                        message=f"{len(selected)} signal(s) chosen from {len(candidates)} candidates; "
                                f"{2 * seconds / 60:.0f} sim-min per run pair, {workers} worker(s)")


def apply_to_area(area: AreaResponse, result: SitingResult) -> AreaResponse:
    """sim_gain_s filled in; recommended_ids = greedy order (if any signal was worth adding).
    The structural ranking order of `intersections` is kept."""
    ixs = [i.model_copy(update={"sim_gain_s": result.gains.get(i.id, i.sim_gain_s)}) for i in area.intersections]
    rec = list(result.order) or list(area.recommended_ids)
    return area.model_copy(update={"intersections": ixs, "recommended_ids": rec})


class SitingCache:
    def __init__(self, state_dir: Path, committed_dir: Path = DATA_DIR):
        self.dirs = [state_dir, committed_dir]
        self.state_dir = state_dir

    def get(self, area_id: str, seed: int, level: str) -> SitingResult | None:
        for d in self.dirs:
            p = d / cache_name(area_id, seed, level)
            if p.exists():
                return SitingResult.model_validate_json(p.read_text("utf-8"))
        return None

    def put(self, result: SitingResult, committed: bool = False) -> Path:
        d = self.dirs[1] if committed else self.state_dir
        d.mkdir(parents=True, exist_ok=True)
        p = d / cache_name(result.area_id, result.seed, result.demand_level)
        p.write_bytes((json.dumps(result.model_dump(mode="json"), indent=1, sort_keys=True) + "\n").encode("utf-8"))
        return p
