from backend.contract.constants import FIXED_PHASE_S


class FixedController:
    """cycle_s is green duration PER PHASE (default 30), excluding clearance.

    On first attachment request the next phase, safely via the engine, even when
    attached midway through another controller's green. During a transition keep
    its target; thereafter schedule from observed green age rather than wall time.
    """
    def __init__(self, cycle_s=FIXED_PHASE_S):
        if cycle_s <= 0:
            raise ValueError("cycle_s must be positive")
        self.cycle_s = cycle_s
        self.attached = set()
        self.pending = {}

    def duration(self, iid, phase):
        return self.cycle_s

    def decide(self, observation):
        commands = {}
        for iid, state in observation.intersections.items():
            ids = [p.id for p in state.phases]
            if not ids:
                continue
            first = iid not in self.attached
            self.attached.add(iid)
            phase = state.current_phase
            target = self.pending.get(iid)
            if target == phase:
                self.pending.pop(iid, None)
            elif target is not None:
                commands[iid] = target
                continue
            if not state.is_transition and (first or state.time_in_phase_s >= self.duration(iid, phase)):
                phase = ids[(ids.index(phase) + 1) % len(ids)]
                self.pending[iid] = phase
            commands[iid] = phase
        return commands
