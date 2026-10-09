"""Live simulation sessions for the real backend (/api/sim/*, /api/metrics, /ws/sim).

Each session owns an engine, a controller (by mode), a SafetyLayer and an asyncio task:
  - every 1/WS_HZ wall-seconds the task advances the engine to elapsed_wall * speed sim-seconds
    (stepping runs in a worker thread so the event loop stays responsive; controller every
    1 sim-second), then publishes the latest tick (latest-only: older ticks are simply replaced);
  - in `ai` mode it launches the Gemini supervisor as a background task whenever it is due on
    the WALL clock (>= GEMINI_MIN_INTERVAL_S, independent of sim speed) - never blocking stepping.
All modes use the request's selected_intersections; same seed + profile => same demand schedule.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from backend.ai.gemini_client import GeminiClient
from backend.ai.replay import PlanRecorder, PlanReplay
from backend.api_common import ApiError
from backend.contract.constants import CONTROLLER_PERIOD_S, FIXED_PHASE_S, SIM_DT_S, WS_HZ
from backend.contract.models import (
    AreaResponse,
    ControllerStatus,
    DemandProfile,
    Explanation,
    Metrics,
    SimStartRequest,
    SimTick,
)
from backend.control.ai_gemini import GeminiSupervisorController
from backend.control.runner import effective_of, make_controller
from backend.control.safety import SafetyLayer
from backend.sim import SimEngine
from backend.traffic.demand import DemandStore, derive_profile, simulated_data_status

log = logging.getLogger("backend.sessions")
MAX_CHUNK_SIM_S = 2.0  # max sim time advanced per worker-thread hop
TRADITIONAL = {"fixed": f"fixed timers ({FIXED_PHASE_S:.0f} s per phase)", "webster": "Webster timing",
               "max_pressure": "max-pressure"}


@dataclass
class TickSnapshot:
    version: int
    tick: SimTick  # explanations are attached per WebSocket connection (see explanations_since)


@dataclass
class Session:
    session_id: str
    req: SimStartRequest
    area: AreaResponse
    engine: SimEngine
    controller: object
    safety: SafetyLayer
    client: GeminiClient | None
    started_wall: float = field(default_factory=time.monotonic)
    stopped: bool = False
    task: asyncio.Task | None = None
    snapshot: TickSnapshot | None = None
    explanations: list[Explanation] = field(default_factory=list)
    # `lock` guards the engine and is held by worker threads while stepping: NEVER take it on the
    # event-loop thread. `expl_lock` only guards the explanation list (held for microseconds).
    lock: threading.Lock = field(default_factory=threading.Lock)
    expl_lock: threading.Lock = field(default_factory=threading.Lock)
    next_decide: float = 0.0
    commands: dict = field(default_factory=dict)
    error: str | None = None

    # ------------------------------------------------------------------ status
    @property
    def mode(self) -> str:
        return self.req.mode

    def effective(self) -> str:
        return effective_of(self.mode, self.controller)

    def controller_status(self) -> ControllerStatus:
        if self.mode == "ai":
            return self.controller.status()
        c = self.client
        return ControllerStatus(state="traditional", effective_controller=self.mode,
                                message=f"Traditional controller: {TRADITIONAL[self.mode]}", since_t=0.0,
                                calls_last_min=c.calls_last_min() if c else 0,
                                calls_today=c.usage.calls_today() if c else 0, daily_cap=c.daily_cap if c else 0)

    def metrics(self) -> Metrics:
        with self.lock:
            return self.engine.metrics(self.mode, self.effective())

    # ------------------------------------------------------------------ stepping (worker thread)
    def advance_to(self, target_t: float) -> None:
        with self.lock:
            e = self.engine
            while e.t < target_t - 1e-9:
                if e.t >= self.next_decide - 1e-9:
                    obs = e.observe()
                    self.commands = self.safety.filter(self.controller.decide(obs), obs)
                    self.next_decide += CONTROLLER_PERIOD_S
                e.step(SIM_DT_S, self.commands)
            self.safety.check_signals(e.signals())
            if self.mode == "ai":
                new = self.controller.drain_explanations()
                if new:
                    with self.expl_lock:
                        self.explanations.extend(new)

    def observe_locked(self):
        with self.lock:
            return self.engine.observe()

    def add_explanation(self, ex: Explanation) -> None:
        with self.expl_lock:
            self.explanations.append(ex)

    def build_tick(self, version: int) -> TickSnapshot:
        with self.lock:
            e = self.engine
            tick = SimTick(session_id=self.session_id, t=round(e.t, 3), vehicles=e.vehicles(), signals=e.signals(),
                           metrics=e.metrics(self.mode, self.effective()), controller_status=self.controller_status(),
                           data_status=simulated_data_status(), explanations=[])
        return TickSnapshot(version=version, tick=tick)

    def explanations_since(self, index: int) -> tuple[list[Explanation], int]:
        with self.expl_lock:
            return self.explanations[index:], len(self.explanations)


class SessionManager:
    def __init__(self, demand: DemandStore, client: GeminiClient | None, record_path=None, replay_path=None):
        self.sessions: dict[str, Session] = {}
        self.demand, self.client = demand, client
        self.record_path, self.replay_path = record_path, replay_path

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise ApiError(404, "unknown_session", f"session {sid} not found")
        return s

    def start(self, req: SimStartRequest, area: AreaResponse, profile: DemandProfile) -> Session:
        known = {i.id for i in area.intersections}
        bad = sorted(set(req.selected_intersections) - known)
        if bad:
            raise ApiError(400, "unknown_intersection", "selected_intersections contains unknown ids", {"ids": bad})
        entry = set(area.network.entry_nodes)
        if any(e.entry_node_id not in entry for e in profile.entries):
            raise ApiError(400, "profile_area_mismatch",
                           f"demand profile {profile.id} was resolved for a different area")
        engine = SimEngine(area.network, area.intersections, req.seed, profile, req.selected_intersections)
        sid = "s_" + uuid.uuid4().hex[:8]
        ai = None
        if req.mode == "ai":
            replay = PlanReplay(self.replay_path, req.seed) if self.replay_path else None
            if replay is None and self.client is None:
                raise ApiError(503, "ai_not_configured", "AI mode needs GEMINI_API_KEY (or a replay file)")
            ai = GeminiSupervisorController(self.client if replay is None else None, seed=req.seed,
                                            recorder=PlanRecorder(self.record_path) if self.record_path else None,
                                            replay=replay, session_label=sid)
        controller = make_controller(req.mode, engine, ai)
        s = Session(session_id=sid, req=req, area=area, engine=engine, controller=controller,
                    safety=SafetyLayer(engine.phases), client=self.client)
        s.snapshot = s.build_tick(0)
        self.sessions[sid] = s
        s.task = asyncio.get_running_loop().create_task(self._run(s))
        log.info("session %s started: mode=%s seed=%s profile=%s signals=%d", sid, req.mode, req.seed,
                 profile.id, len(req.selected_intersections))
        return s

    async def _run(self, s: Session) -> None:
        version = 0
        ai_tasks: set[asyncio.Task] = set()
        try:
            while not s.stopped:
                target = (time.monotonic() - s.started_wall) * s.req.speed
                while s.engine.t < target - 1e-9 and not s.stopped:
                    await asyncio.to_thread(s.advance_to, min(target, s.engine.t + MAX_CHUNK_SIM_S))
                if s.mode == "ai":
                    now = time.monotonic()
                    ctl: GeminiSupervisorController = s.controller
                    if ctl.due_request(now) or ctl.due_probe(now):
                        obs = await asyncio.to_thread(s.observe_locked)
                        task = asyncio.get_running_loop().create_task(ctl.request(obs, now))
                        ai_tasks.add(task)
                        task.add_done_callback(ai_tasks.discard)
                version += 1
                s.snapshot = await asyncio.to_thread(s.build_tick, version)
                await asyncio.sleep(1 / WS_HZ)
        except Exception as e:  # keep the server alive; surface the error on the session
            s.error = f"{type(e).__name__}: {e}"
            s.stopped = True
            log.exception("session %s crashed", s.session_id)
        finally:
            for t in ai_tasks:
                t.cancel()

    async def stop(self, sid: str) -> Session:
        s = self.get(sid)
        if not s.stopped:
            s.stopped = True
            if s.task:
                try:
                    await asyncio.wait_for(s.task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    s.task.cancel()
        return s

    def change_demand(self, sid: str, level, multiplier, entry_overrides, at_t) -> tuple[DemandProfile, float]:
        s = self.get(sid)
        if s.stopped:
            raise ApiError(409, "session_stopped", f"session {sid} is stopped")
        with s.lock:
            now = s.engine.t
            at = now if at_t is None else float(at_t)
            if at < now - 1e-9:
                raise ApiError(400, "at_t_in_past", f"at_t={at} is before the current sim time t={now:.1f}", {"t": now})
            child = derive_profile(s.engine.latest_profile(), list(s.area.network.entry_nodes),
                                   level=level, multiplier=multiplier, entry_overrides=entry_overrides)
            child = self.demand.put(child)
            s.engine.apply_profile(child, at)
            s.add_explanation(Explanation(t=at, intersection_id=None,
                                              text=f"Demand changed: level {child.level}, multiplier {child.multiplier:g}"
                                                   + (f", {len(child.entry_overrides)} entry override(s)"
                                                      if child.entry_overrides else "")))
        return child, at
