"""Engine-owned safety state machine; controllers only request phases."""
from backend.contract.constants import YELLOW_S, ALL_RED_S, MIN_GREEN_S, MAX_RED_S
from backend.contract.models import SignalState


class Signal:
    def __init__(self, intersection, phases):
        self.intersection = intersection
        self.phases = phases
        self.current = phases[0].id
        self.request = self.current
        self.target = self.current
        self.stage = "green"
        self.since = 0.0
        self.red_since = {a.id: 0.0 for a in intersection.approaches}

    def green_ids(self):
        return next(p.approach_ids for p in self.phases if p.id == self.current)

    def advance(self, t, command=None):
        if command is not None:
            if command not in {p.id for p in self.phases}:
                raise ValueError(f"unknown phase {command}")
            self.request = command
        if self.stage != "green":
            if t - self.since >= YELLOW_S + ALL_RED_S - 1e-9:
                self.current = self.target
                self.stage = "green"
                self.since = t
                for aid in self.green_ids():
                    self.red_since[aid] = t
            return
        green = self.green_ids()
        for aid in green:
            self.red_since[aid] = t
        # Reserve enough service time for every pending phase, not just the next
        # deadline (important for T/five-arm and irregular singleton phases).
        waiting = sorted((min(self.red_since[a] for a in p.approach_ids), p.id)
                         for p in self.phases if p.id != self.current)
        clearance = YELLOW_S + ALL_RED_S
        force = any(t + clearance + k * (MIN_GREEN_S + clearance) >= since + MAX_RED_S - 1e-9
                    for k, (since, _) in enumerate(waiting))
        wanted = self.request
        if force and waiting:
            wanted = waiting[0][1]
        if wanted != self.current and t - self.since >= MIN_GREEN_S - 1e-9:
            self.target = wanted
            self.stage = "transition"
            self.since = t

    def state(self, t):
        transition = self.stage != "green"
        color = "yellow" if transition and t - self.since < YELLOW_S - 1e-9 else "red"
        if not transition:
            color = "green"
        return SignalState(intersection_id=self.intersection.id,
                           phase_id=self.target if transition else self.current,
                           color_per_approach={a.id: color if a.id in self.green_ids() else "red"
                                               for a in self.intersection.approaches},
                           time_in_phase_s=max(0, t - self.since), is_transition=transition)
