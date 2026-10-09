"""Controller factory + headless run loop (experiments, siting, tests).

Live sessions (backend/sessions.py) use the same pieces but schedule the AI supervisor on the
wall clock; headless runs schedule it on the SIM clock (default every 20 sim-s) and simply wait
for each call, so results don't depend on machine speed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.contract.constants import CONTROLLER_PERIOD_S, SIM_DT_S
from backend.contract.models import Metrics

from .ai_gemini import GeminiSupervisorController
from .fixed import FixedController
from .max_pressure import MaxPressureController
from .safety import SafetyLayer
from .webster import WebsterController

HEADLESS_AI_INTERVAL_S = 20.0


def make_controller(mode: str, engine: Any, ai: GeminiSupervisorController | None = None):
    if mode == "fixed":
        return FixedController()
    if mode == "webster":
        return WebsterController(engine)
    if mode == "max_pressure":
        return MaxPressureController()
    if mode == "ai":
        if ai is None:
            raise ValueError("mode 'ai' needs a GeminiSupervisorController")
        return ai
    raise ValueError(f"unknown mode {mode!r}")


def effective_of(mode: str, controller: Any) -> str:
    return controller.effective_controller if mode == "ai" else mode


@dataclass
class RunResult:
    metrics: Metrics
    max_avg_queue: float = 0.0
    max_external: int = 0
    max_queue: int = 0  # most stopped vehicles on any single approach edge (sampled every 10 sim-s)
    exits: int = 0  # genuine trip completions during the run (teleports excluded)
    overrides: dict[str, int] = field(default_factory=dict)
    limit_reached_at_t: float | None = None
    ai_calls_ok: int = 0
    ai_calls_failed: int = 0


def run_headless(
    engine: Any,
    mode: str,
    seconds: float,
    controller: Any | None = None,
    safety: SafetyLayer | None = None,
    on_second: Callable[[Any, float], None] | None = None,
    ai_interval_s: float = HEADLESS_AI_INTERVAL_S,
    check_safety: bool = False,
) -> RunResult:
    controller = controller or make_controller(mode, engine)
    safety = safety or SafetyLayer(engine.phases)
    loop = asyncio.new_event_loop() if mode == "ai" else None
    if mode == "ai":
        controller.interval_s = ai_interval_s
    commands: dict[str, str] = {}
    end = engine.t + seconds
    next_decide = engine.t
    max_q, max_ext, max_lane_q = 0.0, 0, 0
    exits0 = engine.completed
    approach_edges = {a.in_edge for i in engine.intersections for a in i.approaches}
    try:
        while engine.t < end - 1e-9:
            if engine.t >= next_decide - 1e-9:
                obs = engine.observe()
                if mode == "ai" and (controller.due_request(obs.t) or controller.due_probe(obs.t)):
                    loop.run_until_complete(controller.request(obs, obs.t))
                commands = safety.filter(controller.decide(obs), obs)
                if check_safety:
                    safety.check_signals(engine.signals())
                if on_second:
                    on_second(engine, obs.t)
                if int(round(engine.t)) % 10 == 0:
                    m = engine.metrics(mode, effective_of(mode, controller))
                    max_q = max(max_q, m.avg_queue)
                    max_ext = max(max_ext, sum(map(len, engine.external.values())))
                    stopped: dict[str, int] = {}
                    for car in engine.cars.values():
                        if car.speed < 0.5 and car.edge in approach_edges:
                            stopped[car.edge] = stopped.get(car.edge, 0) + 1
                    max_lane_q = max(max_lane_q, max(stopped.values(), default=0))
                next_decide += CONTROLLER_PERIOD_S
            engine.step(SIM_DT_S, commands)
    finally:
        if loop:
            loop.close()
    res = RunResult(metrics=engine.metrics(mode, effective_of(mode, controller)), max_avg_queue=max_q,
                    max_external=max_ext, overrides=dict(safety.counts), max_queue=max_lane_q,
                    exits=engine.completed - exits0)
    if mode == "ai":
        res.limit_reached_at_t = controller.limit_reached_at_t
        res.ai_calls_ok, res.ai_calls_failed = controller.calls_ok, controller.calls_failed
    return res
