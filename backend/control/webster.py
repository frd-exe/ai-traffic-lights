"""Webster: C=(1.5 L+5)/(1-Y), critical lane flows, 1800 veh/h/lane.

L=5 seconds/phase. Uniform reachable-destination OD expected flows are taken
from engine.demand, not observed queues. C clipped to [n*12, 120] seconds;
oversaturated Y>=1 uses 120 seconds. Green splits proportional to critical
flow with a seven-second floor. Safety/fairness remains engine-owned.
"""
from backend.contract.constants import MIN_GREEN_S
from .fixed import FixedController


class WebsterController(FixedController):
    def __init__(self, engine):
        super().__init__()
        self.splits = {}
        self.cycles = {}
        flows = engine.demand.approach_flows(engine.intersections)
        for intersection in engine.intersections:
            phases = engine.phases[intersection.id]
            if not phases:
                continue
            lanes = {a.id: a.lanes for a in intersection.approaches}
            critical = [max((flows[intersection.id][a] / (1800 * lanes[a])
                             for a in p.approach_ids), default=0) for p in phases]
            n, total = len(phases), sum(critical)
            lost = 5 * n
            cycle = max(n * 12, min(120, (1.5 * lost + 5) / (1 - total) if total < 1 else 120))
            budget = cycle - lost - n * MIN_GREEN_S
            self.cycles[intersection.id] = cycle
            for phase, ratio in zip(phases, critical):
                self.splits[intersection.id, phase.id] = MIN_GREEN_S + budget * (ratio / total if total else 1 / n)

    def duration(self, iid, phase):
        return self.splits[iid, phase]
