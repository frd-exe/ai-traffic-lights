"""Safety layer between ANY controller and the engine. Every override is logged.

The engine (backend/sim/signals.py) is the last line of defence: it alone drives yellow/all-red,
enforces min green and the max-red starvation bound, and only ever shows one phase green. This
layer adds a second, controller-facing guard so that bad requests never even reach it:

  - unknown intersection or phase id      -> dropped / replaced by "hold current phase"
  - change requested during a transition  -> keep the transition target
  - change requested before MIN_GREEN_S   -> hold current phase (re-requested next second)
  - starvation: an approach has been RED (not in a green phase) for >= STARVATION_S, so that
    clearance would push it past MAX_RED_S -> force that approach's phase. Red time is tracked
    from the observations themselves (not from vehicle stopped time: under saturation a car can
    be stopped for a minute behind a queue on an approach that did get green).
  - structural check at start: phases must not share approaches (no conflicting greens), and
    `check_signals` verifies at runtime that the green approaches always form exactly one phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.contract.constants import ALL_RED_S, MAX_RED_S, MIN_GREEN_S, YELLOW_S
from backend.contract.models import Observation, Phase, SignalState

log = logging.getLogger("backend.control.safety")
STARVATION_S = MAX_RED_S - YELLOW_S - ALL_RED_S - 2.0  # 53 s: leaves time for clearance before 60 s


class SafetyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Override:
    t: float
    intersection_id: str
    requested: str | None
    applied: str | None
    reason: str


class SafetyLayer:
    def __init__(self, phases: dict[str, list[Phase]], max_log: int = 5000):
        self.phases = {iid: list(p) for iid, p in phases.items() if p}
        for iid, ph in self.phases.items():
            seen: set[str] = set()
            for p in ph:
                overlap = seen & set(p.approach_ids)
                if overlap:
                    raise SafetyViolation(f"{iid}: approaches {sorted(overlap)} appear in two phases")
                seen |= set(p.approach_ids)
        self.overrides: list[Override] = []
        self.max_log = max_log
        self.counts: dict[str, int] = {}
        self.last_green: dict[str, dict[str, float]] = {}  # iid -> approach -> last sim t seen green

    def _override(self, t: float, iid: str, requested: str | None, applied: str | None, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.overrides) < self.max_log:
            self.overrides.append(Override(t, iid, requested, applied, reason))
        log.info("safety override t=%.1f %s: requested=%s applied=%s (%s)", t, iid, requested, applied, reason)

    def filter(self, commands: dict[str, str], observation: Observation) -> dict[str, str]:
        t = observation.t
        safe: dict[str, str] = {}
        for iid, phase in commands.items():
            if iid not in self.phases or iid not in observation.intersections:
                self._override(t, iid, phase, None, "unknown_intersection")
                continue
            safe[iid] = phase
        for iid, state in observation.intersections.items():
            if iid not in self.phases:
                continue
            seen = self.last_green.setdefault(iid, {a: t for p in self.phases[iid] for a in p.approach_ids})
            if not state.is_transition:
                for p in self.phases[iid]:
                    if p.id == state.current_phase:
                        for a in p.approach_ids:
                            seen[a] = t
        for iid, state in observation.intersections.items():
            if iid not in self.phases:
                continue
            ids = {p.id for p in self.phases[iid]}
            current = state.current_phase
            requested = safe.get(iid)
            if requested is None:
                continue
            if requested not in ids:
                self._override(t, iid, requested, current, "unknown_phase")
                requested = current
            if state.is_transition and requested != current:
                self._override(t, iid, requested, current, "transition_locked")
                requested = current
            elif requested != current and state.time_in_phase_s < MIN_GREEN_S - 1e-9:
                self._override(t, iid, requested, current, "min_green")
                requested = current
            if not state.is_transition and state.time_in_phase_s >= MIN_GREEN_S - 1e-9:
                starving = self._starving_phase(iid, state, t)
                if starving and starving != requested:
                    self._override(t, iid, requested, starving, "starvation")
                    requested = starving
            safe[iid] = requested
        return safe

    def _starving_phase(self, iid: str, state, t: float) -> str | None:
        """Phase of the approach that has been red longest, if that is >= STARVATION_S and it
        has demand (an empty approach can't starve)."""
        worst, worst_red = None, STARVATION_S
        for p in self.phases[iid]:
            if p.id == state.current_phase:
                continue
            for a in p.approach_ids:
                obs = state.approaches.get(a)
                if obs is None or obs.vehicles == 0:
                    continue
                red = t - self.last_green[iid].get(a, t)
                if red >= worst_red:
                    worst, worst_red = p.id, red
        return worst

    def check_signals(self, signals: list[SignalState]) -> None:
        """Raise if green approaches don't form exactly one phase (conflicting greens)."""
        for s in signals:
            green = {a for a, c in s.color_per_approach.items() if c == "green"}
            if not green:
                continue
            phases = [set(p.approach_ids) for p in self.phases.get(s.intersection_id, [])]
            if green not in phases:
                raise SafetyViolation(f"{s.intersection_id}: green set {sorted(green)} is not a single phase")
