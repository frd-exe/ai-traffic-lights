"""Poisson OD demand, independent of simulation and driver RNG consumption."""
from dataclasses import dataclass
import hashlib
import heapq
import json

import numpy as np

from backend.contract.constants import LEVEL_FLOW_VEH_PER_H


def flow_veh_per_h(profile, origin):
    """Contract 0.2.0: entry flow = scale * LEVEL_FLOW_VEH_PER_H['medium'] (scale already folds in
    level x multiplier x entry override). Entries missing from the profile use level x multiplier."""
    for entry in profile.entries:
        if entry.entry_node_id == origin:
            return entry.scale * LEVEL_FLOW_VEH_PER_H["medium"]
    return LEVEL_FLOW_VEH_PER_H[profile.level or "medium"] * getattr(profile, "multiplier", 1.0)


@dataclass(frozen=True)
class Arrival:
    time: float
    origin: str
    destination: str
    speed_factor: float
    route: tuple[str, ...]


class Demand:
    def __init__(self, network, profile, seed):
        self.profile = profile.model_copy(deep=True)
        self.routes = {}
        outgoing = {}
        for edge in network.edges:
            outgoing.setdefault(edge.from_node, []).append(edge)
        # Dijkstra weighted by free-flow travel time; stable lexical ties.
        for origin in sorted(network.entry_nodes):
            todo = [(0.0, origin, ())]
            seen = set()
            while todo:
                cost, node, route = heapq.heappop(todo)
                if node in seen:
                    continue
                seen.add(node)
                if node in network.exit_nodes and node != origin:
                    self.routes[origin, node] = route
                for edge in sorted(outgoing.get(node, []), key=lambda e: e.id):
                    if edge.to_node not in seen:
                        heapq.heappush(todo, (cost + edge.length_m / edge.speed_limit,
                                              edge.to_node, route + (edge.id,)))
        profile_seed = int.from_bytes(hashlib.sha256(profile.id.encode()).digest()[:8], "little")
        children = np.random.SeedSequence([seed, profile_seed]).spawn(len(network.entry_nodes))
        self.rngs = {}
        self.rates = {}
        self.destinations = {}
        self.pending = []
        self.schedule = []
        self.changes = []  # heap of (at_t, seq, profile): scheduled mid-run demand changes
        self.applied_changes = []  # (at_t, profile id) in application order
        self.horizon = 0.0
        for origin, child in zip(sorted(network.entry_nodes), children):
            destinations = sorted(d for o, d in self.routes if o == origin)
            if not destinations:
                continue
            self.rates[origin] = flow_veh_per_h(self.profile, origin) / 3600
            self.destinations[origin] = destinations
            self.rngs[origin] = np.random.default_rng(child)
            self._next(origin, 0.0)

    def _next(self, origin, after):
        rng = self.rngs[origin]
        t = after + float(rng.exponential(1 / self.rates[origin]))
        destination = self.destinations[origin][int(rng.integers(len(self.destinations[origin])))]
        factor = float(rng.uniform(0.85, 1.15))
        arrival = Arrival(t, origin, destination, factor, self.routes[origin, destination])
        heapq.heappush(self.pending, (t, origin, arrival))

    def schedule_change(self, profile, at_t):
        """Switch to `profile` (rates per entry) from sim time `at_t` (contract 0.2.0).

        Deterministic: arrivals up to at_t use the old rates; at at_t every origin's pending
        (not yet released) arrival is discarded and redrawn from at_t with the new rate, from
        the same per-origin RNG stream. So the schedule depends only on seed + profiles +
        change times, never on when (in wall time) the change was requested or on the controller.
        """
        if at_t < self.horizon - 1e-9:
            raise ValueError(f"at_t={at_t} is before the demand horizon {self.horizon}")
        heapq.heappush(self.changes, (float(at_t), len(self.applied_changes) + len(self.changes),
                                      profile.model_copy(deep=True)))

    def _apply_change(self, at_t, profile):
        self.profile = profile
        for origin in self.rates:
            self.rates[origin] = flow_veh_per_h(profile, origin) / 3600
        # Record what changed (time + rates), not the profile id: compare sessions get distinct
        # child-profile ids for the same change and must still produce equal digests.
        self.applied_changes.append((at_t, [(o, round(self.rates[o] * 3600, 6)) for o in sorted(self.rates)]))
        self.pending = [item for item in self.pending if item[1] not in self.rates]
        heapq.heapify(self.pending)
        for origin in sorted(self.rates):
            self._next(origin, at_t)

    def _release(self, t):
        arrivals = []
        while self.pending and self.pending[0][0] <= t:
            _, origin, arrival = heapq.heappop(self.pending)
            arrivals.append(arrival)
            self.schedule.append(arrival)
            self._next(origin, arrival.time)
        return arrivals

    def until(self, t):
        arrivals = []
        while self.changes and self.changes[0][0] <= t + 1e-9:
            at_t, _, profile = heapq.heappop(self.changes)
            arrivals += self._release(at_t)
            self._apply_change(at_t, profile)
        arrivals += self._release(t)
        self.horizon = t
        return arrivals

    def digest(self):
        return hashlib.sha256(json.dumps([[(a.time, a.origin, a.destination, a.speed_factor) for a in self.schedule],
                                          self.applied_changes], separators=(",", ":")).encode()).hexdigest()

    def approach_flows(self, intersections):
        """Expected veh/h at each approach under uniform reachable destination OD."""
        edge_flow = {}
        for (origin, _), route in self.routes.items():
            flow = self.rates[origin] * 3600 / len(self.destinations[origin])
            for edge in route:
                edge_flow[edge] = edge_flow.get(edge, 0.0) + flow
        return {i.id: {a.id: edge_flow.get(a.in_edge, 0.0) for a in i.approaches}
                for i in intersections}
