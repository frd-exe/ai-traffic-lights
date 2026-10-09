"""Python interfaces between workstreams (see docs/CONTRACT.md 'Python interfaces')."""

from typing import Protocol, runtime_checkable

from .models import (
    DemandLevel,
    DemandProfile,
    EffectiveController,
    Intersection,
    Metrics,
    Observation,
    Plan,
    RoadNetwork,
    SignalState,
    SimMode,
    Vehicle,
)

# {intersection_id: phase_id}. Missing intersections keep their current request.
SignalCommands = dict[str, str]


@runtime_checkable
class SimEngine(Protocol):
    def step(self, dt: float, commands: SignalCommands) -> None:
        """Advance dt sim-seconds. The engine enforces yellow / all-red / min green / max red."""

    def observe(self) -> Observation:
        """Per signalised intersection: phases (from backend/sim/phases.py), current phase, approach stats."""

    def vehicles(self) -> list[Vehicle]: ...

    def signals(self) -> list[SignalState]: ...

    def metrics(self, mode: SimMode, effective_controller: EffectiveController) -> Metrics: ...

    def state_digest(self) -> str:
        """Hash of full engine state (determinism tests)."""

    def demand_schedule_digest(self) -> str:
        """Hash of the demand schedule (spawn t, origin, destination, speed factor).
        Depends only on seed + demand profile, never on controller or driver behaviour."""

    def set_demand(self, level: DemandLevel) -> None:
        """Dev/testing only: rescale baseline demand. Never called on a running compare session."""


class SimEngineFactory(Protocol):
    def __call__(
        self,
        network: RoadNetwork,
        intersections: list[Intersection],
        seed: int,
        demand_profile: DemandProfile,
        signalized_ids: list[str],
    ) -> SimEngine: ...


@runtime_checkable
class Controller(Protocol):
    """Called every CONTROLLER_PERIOD_S sim-seconds. Must be fast and deterministic."""

    def decide(self, observation: Observation) -> SignalCommands: ...


@runtime_checkable
class Supervisor(Protocol):
    """LLM supervisor: async, wall-clock >= GEMINI_MIN_INTERVAL_S between calls.
    Returns Plans that a plan executor applies on top of max_pressure between calls.
    Raises on failure; the caller counts failures for the AI-limit rules."""

    async def plan(self, observation: Observation) -> list[Plan]: ...


# Re-exported for convenience.
__all__ = [
    "Controller", "SignalCommands", "SignalState", "SimEngine", "SimEngineFactory", "Supervisor",
]
