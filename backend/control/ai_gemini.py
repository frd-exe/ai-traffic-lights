"""AI supervisor controller: Gemini plans on top of max-pressure, with the contract's AI LIMIT
BEHAVIOR (docs/CONTRACT.md §9).

Design
------
- decide(observation) is called every sim-second (sync, cheap): max-pressure, overlaid by the
  plan executor. It NEVER waits for Gemini.
- The supervisor call is ONE async, non-blocking request on an interval (wall clock in live
  sessions, >= GEMINI_MIN_INTERVAL_S, never per tick and not faster at higher sim speeds; sim clock
  in headless experiments). The session asks `due_request(now)` and then runs `request(obs)` as a
  background task. Until a plan arrives the sim runs on max-pressure.
- Plan executor: a plan holds `phase` for AT MOST hold_s sim-seconds. It ends early when
  max-pressure's best phase beats the plan's phase by more than EARLY_TERMINATION_GAIN vehicles
  (the plan is clearly stale). On expiry the intersection returns to max-pressure.
- Limit fallback (contract 0.4.0): daily cap / 429 / the per-session live-call cap (max_calls,
  default 40 in live sessions) -> ai_limit_reached, "Live AI quota reached. Using adaptive fallback.";
  invalid key / >= 3 consecutive failed calls -> ai_unavailable. One failed call is NOT a trigger.
  On trigger the junctions switch to ADAPTIVE control (max-pressure; the engine performs any safe
  yellow/all-red transition); fixed timers are only the last resort if the adaptive controller
  itself fails. ControllerStatus updated, session-wide Explanation, WARNING log + console print.
  Probe every AI_PROBE_INTERVAL_S and resume after one success, except after a daily-cap or
  session-cap fallback: daily cap resumes only when the budget is available again (next UTC day, or
  POST /api/ai/reset); the session cap never resumes within that session.
- Replay: with a PlanReplay, plans come from a recording, no API calls; state = ai_replay.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal

from backend.ai.gemini_client import GeminiClient, GeminiError
from backend.ai.replay import PlanRecorder, PlanReplay
from backend.contract.constants import (
    AI_CONSECUTIVE_FAILURE_LIMIT,
    AI_PROBE_INTERVAL_S,
    GEMINI_MIN_INTERVAL_S,
)
from backend.contract.models import ControllerStatus, Explanation, Observation, Plan

from .fixed import FixedController
from .max_pressure import MaxPressureController, best_phase, phase_pressures

log = logging.getLogger("backend.ai")
EARLY_TERMINATION_GAIN = 15.0  # vehicles: max-pressure must beat the planned phase by this much
LIMIT_MESSAGE = "Live AI quota reached. Using adaptive fallback."
UNAVAILABLE_MESSAGE = "AI unavailable. Using adaptive fallback."
State = Literal["ai_active", "ai_limit_reached", "ai_unavailable", "ai_replay"]


@dataclass
class ActivePlan:
    phase: str
    started_t: float
    until_t: float
    reason: str


def compact_observation(obs: Observation) -> dict:
    """Small JSON for the prompt (see SYSTEM_PROMPT for the keys)."""
    return {
        "t": round(obs.t, 1),
        "intersections": [
            {
                "id": iid,
                "phase": st.current_phase,
                "in_phase_s": round(st.time_in_phase_s, 1),
                "transition": st.is_transition,
                "phases": {p.id: p.approach_ids for p in st.phases},
                "approaches": {aid: {"q": a.queue, "n": a.vehicles, "w": round(a.wait_s), "down": round(a.downstream_vehicles, 1)}
                               for aid, a in st.approaches.items()},
            }
            for iid, st in sorted(obs.intersections.items())
        ],
    }


class GeminiSupervisorController:
    def __init__(
        self,
        client: GeminiClient | None,
        *,
        seed: int = 0,
        interval_s: float = GEMINI_MIN_INTERVAL_S,
        probe_interval_s: float = AI_PROBE_INTERVAL_S,
        early_gain: float = EARLY_TERMINATION_GAIN,
        recorder: PlanRecorder | None = None,
        replay: PlanReplay | None = None,
        session_label: str = "",
        max_calls: int | None = None,
    ):
        if client is None and replay is None:
            raise ValueError("need a GeminiClient or a PlanReplay")
        self.client, self.seed, self.recorder, self.replay = client, seed, recorder, replay
        self.interval_s = max(interval_s, GEMINI_MIN_INTERVAL_S) if replay is None else interval_s
        self.probe_interval_s, self.early_gain, self.label = probe_interval_s, early_gain, session_label
        self.max_calls = max_calls  # live calls per session (incl. probes); None = unlimited
        self.calls_made = 0
        self.mp = MaxPressureController()
        self.adaptive = MaxPressureController()  # fallback when the AI is limited / unavailable
        self.fixed: FixedController | None = None  # last resort only: if the adaptive fallback itself fails
        self.state: State = "ai_replay" if replay is not None else "ai_active"
        self.fallback_reason: str | None = None  # "daily_cap" | "limit" | "invalid_key" | "failures"
        self.since_t = 0.0
        self.consecutive_failures = 0
        self.plans: dict[str, ActivePlan] = {}
        self.in_flight = False
        self.last_request: float | None = None
        self.last_probe: float | None = None
        self.limit_reached_at_t: float | None = None
        self.calls_ok = 0
        self.calls_failed = 0
        self._t = 0.0
        self._explanations: list[Explanation] = []
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- status
    @property
    def in_fallback(self) -> bool:
        return self.state in ("ai_limit_reached", "ai_unavailable")

    @property
    def effective_controller(self) -> str:
        if not self.in_fallback:
            return "gemini+max_pressure"
        return "fixed" if self.fixed is not None else "max_pressure"

    def message(self) -> str:
        if self.in_fallback and self.fixed is not None:
            return "AI fallback failed: signals on traditional fixed timers (last resort)"
        return {
            "ai_active": "AI active: Gemini supervisor + max-pressure",
            "ai_replay": "AI replay: replaying recorded Gemini plans (no live calls)",
            "ai_limit_reached": LIMIT_MESSAGE,
            "ai_unavailable": UNAVAILABLE_MESSAGE,
        }[self.state]

    def status(self) -> ControllerStatus:
        c = self.client
        return ControllerStatus(
            state=self.state, effective_controller=self.effective_controller, message=self.message(),
            since_t=self.since_t, calls_last_min=c.calls_last_min() if c else 0,
            calls_today=c.usage.calls_today() if c else 0, daily_cap=c.daily_cap if c else 0)

    def drain_explanations(self) -> list[Explanation]:
        with self._lock:
            out, self._explanations = self._explanations, []
            return out

    def _explain(self, t: float, iid: str | None, text: str) -> None:
        self._explanations.append(Explanation(t=round(t, 2), intersection_id=iid, text=text))

    # ---------------------------------------------------------------- per sim-second
    def decide(self, observation: Observation) -> dict[str, str]:
        with self._lock:
            t = self._t = observation.t
            if self.state == "ai_limit_reached" and self.fallback_reason == "daily_cap" \
                    and self.client is not None and self.client.usage.allowed():
                self._resume(t, "daily budget available again")
            if self.in_fallback:
                return self._fallback_decide(observation)
            commands = self.mp.decide(observation)
            for iid, plan in list(self.plans.items()):
                st = observation.intersections.get(iid)
                if st is None:
                    continue
                if t >= plan.until_t - 1e-9:
                    del self.plans[iid]  # expired: back to max-pressure
                    continue
                best, _ = best_phase(st)
                pressures = phase_pressures(st)
                margin = pressures.get(best, 0.0) - pressures.get(plan.phase, 0.0)
                if best != plan.phase and margin > self.early_gain:
                    del self.plans[iid]
                    self._explain(t, iid, f"Ended AI plan early: max-pressure prefers {best.split(':')[-1]} "
                                          f"by {margin:.0f} vehicles")
                    continue
                commands[iid] = plan.phase
            return commands

    def _fallback_decide(self, observation: Observation) -> dict[str, str]:
        """Adaptive fallback (max-pressure); fixed timers only if the adaptive controller fails."""
        if self.fixed is None:
            try:
                return self.adaptive.decide(observation)
            except Exception:  # noqa: BLE001 - last resort must never take the junctions down
                log.exception("adaptive fallback failed in session %s; using fixed timers (last resort)", self.label)
                self.fixed = FixedController()
                self._explain(observation.t, None, self.message())
        return self.fixed.decide(observation)

    # ---------------------------------------------------------------- scheduling (session calls these)
    def _session_cap_reached(self) -> bool:
        return self.max_calls is not None and self.calls_made >= self.max_calls

    def due_request(self, now: float) -> bool:
        with self._lock:
            if self.in_flight or self.in_fallback:
                return False
            if self._session_cap_reached():
                self._trigger(self._t, "ai_limit_reached", "session_cap",
                              f"{self.max_calls} live calls used in this session")
                return False
            return self.last_request is None or now - self.last_request >= self.interval_s - 1e-9

    def due_probe(self, now: float) -> bool:
        with self._lock:
            if self.in_flight or not self.in_fallback or self.fallback_reason in ("daily_cap", "session_cap"):
                return False
            if self._session_cap_reached():
                return False
            return self.last_probe is None or now - self.last_probe >= self.probe_interval_s - 1e-9

    async def request(self, observation: Observation, now: float) -> None:
        """One supervisor call (or one probe while in fallback). Never raises."""
        with self._lock:
            if self.replay is None:
                self.calls_made += 1
            probing = self.in_fallback
            if probing:
                self.last_probe = now
            else:
                self.last_request = now
            self.in_flight = True
        allowed = {iid: [p.id for p in st.phases] for iid, st in observation.intersections.items()}
        try:
            if self.replay is not None:
                plans = self.replay.take(observation.t) or []
            else:
                assert self.client is not None
                plans = await self.client.plan(compact_observation(observation), allowed)
            self._on_success(plans, observation.t, probing)
        except GeminiError as e:
            self._on_failure(e, observation.t, probing)
        finally:
            with self._lock:
                self.in_flight = False

    # ---------------------------------------------------------------- outcomes
    def _on_success(self, plans: list[Plan], t_obs: float, probing: bool) -> None:
        with self._lock:
            t = self._t
            self.calls_ok += 1
            self.consecutive_failures = 0
            if probing:
                self._resume(t, "probe call succeeded")
            if self.recorder is not None and self.replay is None:
                self.recorder.record(self.seed, t_obs, plans)
            for p in plans:
                self.plans[p.intersection_id] = ActivePlan(p.phase, t, t + p.hold_s, p.reason)
                self._explain(t, p.intersection_id, p.reason)

    def _on_failure(self, err: GeminiError, t_obs: float, probing: bool) -> None:
        with self._lock:
            t = self._t
            self.calls_failed += 1
            if probing:
                log.info("AI probe failed (%s): %s", err.kind, err.message)
                if err.kind == "daily_cap":
                    self.fallback_reason = "daily_cap"
                return
            if err.kind in ("daily_cap", "limit"):
                self._trigger(t, "ai_limit_reached", err.kind, err.message)
            elif err.kind == "invalid_key":
                self._trigger(t, "ai_unavailable", "invalid_key", err.message)
            else:
                self.consecutive_failures += 1
                log.info("AI call failed (%s, %d in a row): %s", err.kind, self.consecutive_failures, err.message)
                if self.consecutive_failures >= AI_CONSECUTIVE_FAILURE_LIMIT:
                    self._trigger(t, "ai_unavailable", "failures",
                                  f"{self.consecutive_failures} consecutive failed calls (last: {err.message})")

    def _trigger(self, t: float, state: State, reason: str, detail: str) -> None:
        if self.in_fallback:
            return
        self.state, self.fallback_reason, self.since_t = state, reason, t
        if self.limit_reached_at_t is None:
            self.limit_reached_at_t = t
        self.fixed = None  # adaptive (max-pressure) fallback; the engine keeps clearance/min-green safety
        self.plans.clear()
        self.last_probe = None if reason == "daily_cap" else self.last_request
        self._explain(t, None, self.message())
        calls = self.client.usage.calls_today() if self.client else 0
        cap = self.client.daily_cap if self.client else 0
        msg = (f"[AI fallback] session {self.label or '-'}: {self.message()} | trigger={reason} "
               f"({detail}) | calls_today={calls}/{cap}")
        log.warning(msg)
        print(msg, flush=True)

    def _resume(self, t: float, why: str) -> None:
        if not self.in_fallback:
            return
        self.state = "ai_replay" if self.replay is not None else "ai_active"
        self.fallback_reason, self.since_t, self.fixed = None, t, None
        self.consecutive_failures = 0
        self._explain(t, None, f"AI resumed ({why}): Gemini supervisor + max-pressure")
        log.warning("[AI resumed] session %s at t=%.0f s (%s)", self.label or "-", t, why)
        print(f"[AI resumed] session {self.label or '-'} at t={t:.0f} s ({why})", flush=True)
