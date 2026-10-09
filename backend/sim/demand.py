"""Poisson OD demand, independent of simulation and driver RNG consumption."""
from dataclasses import dataclass
import hashlib
import heapq
import json

import numpy as np

from backend.contract.constants import LEVEL_FLOW_VEH_PER_H


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
        entries = {e.entry_node_id: e.scale for e in profile.entries}
        self.rngs = {}
        self.rates = {}
        self.destinations = {}
        self.pending = []
        self.schedule = []
        self.horizon = 0.0
        for origin, child in zip(sorted(network.entry_nodes), children):
            destinations = sorted(d for o, d in self.routes if o == origin)
            if not destinations:
                continue
            flow = (LEVEL_FLOW_VEH_PER_H[profile.level or "medium"]
                    if profile.source == "baseline_only" else
                    LEVEL_FLOW_VEH_PER_H["medium"] * entries.get(origin, 1.0))
            self.rates[origin] = flow / 3600
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

    def until(self, t):
        arrivals = []
        while self.pending and self.pending[0][0] <= t:
            _, origin, arrival = heapq.heappop(self.pending)
            arrivals.append(arrival)
            self.schedule.append(arrival)
            self._next(origin, arrival.time)
        self.horizon = t
        return arrivals

    def digest(self):
        return hashlib.sha256(json.dumps([(a.time, a.origin, a.destination, a.speed_factor)
                                          for a in self.schedule], separators=(",", ":")).encode()).hexdigest()

    def approach_flows(self, intersections):
        """Expected veh/h at each approach under uniform reachable destination OD."""
        edge_flow = {}
        for (origin, _), route in self.routes.items():
            flow = self.rates[origin] * 3600 / len(self.destinations[origin])
            for edge in route:
                edge_flow[edge] = edge_flow.get(edge, 0.0) + flow
        return {i.id: {a.id: edge_flow.get(a.in_edge, 0.0) for a in i.approaches}
                for i in intersections}
