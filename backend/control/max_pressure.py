"""Max-pressure controller (Varaiya 2013 style, counts-based).

pressure(phase) = sum over its approaches of (vehicles on the in-edge - mean vehicles on the
approach's out-edges). Upstream and downstream counts come from the engine's observation
(ApproachObservation.vehicles / .downstream_vehicles, contract 0.3.0).

Rules, evaluated every controller period (1 sim-s) per signalised intersection:
  1. During a transition (yellow / all-red): keep requesting the transition target.
  2. Before MIN_GREEN_S is served: hold the current phase (the engine would defer anyway).
  3. If the junction is empty (no vehicle on any approach): hold the current phase. No flapping.
  4. Lone-vehicle rule: if the current phase has no vehicles and another phase has >= 1,
     switch to the highest-pressure such phase (a single car must never wait for nothing).
  5. Otherwise switch only if pressure(best) - pressure(current) > SWITCH_GAIN.
The engine still owns safety (clearance, min green, max red); backend/control/safety.py sits
between controller and engine as well.
"""

from __future__ import annotations

from backend.contract.constants import MIN_GREEN_S
from backend.contract.models import IntersectionObservation, Observation

# Vehicles; hysteresis against flapping. Each switch costs YELLOW_S + ALL_RED_S = 5 s of lost
# time, so near-equal phases must not trade green. Tuned on the demo grid (150 m links): 2 caused
# 2.7 switches/junction/min and lost to fixed timing; 6-15 all beat fixed; 10 was best at
# medium..rush (seed 1, 600 s). See docs/results.md.
SWITCH_GAIN = 10.0


def phase_pressures(state: IntersectionObservation) -> dict[str, float]:
    out = {}
    for phase in state.phases:
        out[phase.id] = sum(state.approaches[a].vehicles - state.approaches[a].downstream_vehicles
                            for a in phase.approach_ids if a in state.approaches)
    return out


def phase_vehicles(state: IntersectionObservation) -> dict[str, int]:
    return {p.id: sum(state.approaches[a].vehicles for a in p.approach_ids if a in state.approaches)
            for p in state.phases}


def best_phase(state: IntersectionObservation) -> tuple[str, float]:
    """(phase, gain over current) the max-pressure rule would pick; gain 0 means hold."""
    current = state.current_phase
    if state.is_transition or state.time_in_phase_s < MIN_GREEN_S - 1e-9:
        return current, 0.0
    veh = phase_vehicles(state)
    if sum(veh.values()) == 0:
        return current, 0.0
    pressure = phase_pressures(state)
    ranked = sorted(pressure, key=lambda p: (-pressure[p], p))
    if veh.get(current, 0) == 0:
        waiting = [p for p in ranked if p != current and veh[p] > 0]
        if waiting:
            return waiting[0], pressure[waiting[0]] - pressure.get(current, 0.0)
    best = ranked[0]
    gain = pressure[best] - pressure.get(current, 0.0)
    if best != current and gain > SWITCH_GAIN:
        return best, gain
    return current, 0.0


class MaxPressureController:
    name = "max_pressure"

    def decide(self, observation: Observation) -> dict[str, str]:
        commands = {}
        for iid, state in observation.intersections.items():
            if not state.phases:
                continue
            commands[iid] = best_phase(state)[0]
        return commands
