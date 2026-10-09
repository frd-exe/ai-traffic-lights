"""Webster: C=(1.5 L+5)/(1-Y), critical lane flows / saturation flow.

L=5 seconds/phase. Uniform reachable-destination OD expected flows are taken
from engine.demand, not observed queues. C clipped to [n*12, MAX_CYCLE_S] seconds;
oversaturated Y>=1 uses MAX_CYCLE_S. Green splits proportional to critical
flow with a seven-second floor. Safety/fairness remains engine-owned.

Calibration (see docs/results.md): Webster needs the saturation flow of the system it
controls. This engine's single-lane approaches with permissive left turns discharge far
below the textbook 1800 veh/h/lane, so 1800 made Y too small, cycles too short (26-39 s,
28-38% lost time) and Webster lost to fixed 30 s phases. 900 veh/h/lane with a 90 s cap
(the usual practical limit; Webster's formula overshoots near Y=1) beat fixed at every level.
"""
from backend.contract.constants import MIN_GREEN_S
from .fixed import FixedController

SATURATION_FLOW_VEH_PER_H_LANE = 900.0
MAX_CYCLE_S = 90.0


class WebsterController(FixedController):
    def __init__(self, engine, saturation_flow=SATURATION_FLOW_VEH_PER_H_LANE, max_cycle=MAX_CYCLE_S):
        super().__init__()
        self.saturation_flow = saturation_flow
        self.max_cycle = max_cycle
        self.splits = {}
        self.cycles = {}
        flows = engine.demand.approach_flows(engine.intersections)
        for intersection in engine.intersections:
            phases = engine.phases[intersection.id]
            if not phases:
                continue
            lanes = {a.id: a.lanes for a in intersection.approaches}
            critical = [max((flows[intersection.id][a] / (self.saturation_flow * lanes[a])
                             for a in p.approach_ids), default=0) for p in phases]
            n, total = len(phases), sum(critical)
            lost = 5 * n
            cycle = max(n * 12, min(self.max_cycle, (1.5 * lost + 5) / (1 - total) if total < 1 else self.max_cycle))
            budget = cycle - lost - n * MIN_GREEN_S
            self.cycles[intersection.id] = cycle
            for phase, ratio in zip(phases, critical):
                self.splits[intersection.id, phase.id] = MIN_GREEN_S + budget * (ratio / total if total else 1 / n)

    def duration(self, iid, phase):
        return self.splits[iid, phase]
