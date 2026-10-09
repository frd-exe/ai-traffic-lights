"""IDM on directed per-edge lane queues, no server or wall-clock dependencies.

Cars are 4.5m with 2m minimum bumper gap. Lanes do not change. Junction
reservations are per MOVEMENT (in-edge -> out-edge): a crossing blocks, for
RESERVATION_S, every movement that conflicts with it. Compatible (may cross
together): movements from the same in-edge (car following is handled by IDM),
and opposing non-left movements that don't share an exit. Everything else
(crossing paths, permissive left turns vs. opposing traffic, merges into the
same out-edge) is serialized. Downstream space is reserved before crossing,
preventing junction overlap.
"""
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math

import numpy as np

from backend.contract.constants import (
    SIM_DT_S, STOPPED_SPEED_MPS, QUEUE_DISTANCE_M,
    METRICS_FINISHED_WINDOW_S, THROUGHPUT_WINDOW_S,
)
from backend.contract.models import (
    ApproachObservation, IntersectionObservation, Metrics, Observation, Vehicle,
)
from .demand import Demand
from .phases import derive_phases
from .signals import Signal

CAR_LENGTH = 4.5
MIN_GAP = 2.0
SPACING = CAR_LENGTH + MIN_GAP
RESERVATION_S = 2.0  # a crossing blocks conflicting movements at the node for this long
OPPOSING_TOLERANCE_DEG = 30.0
ROAD_PRIORITY = {name: rank for rank, name in enumerate(
    ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
     "residential", "living_street", "service"])}


@dataclass
class Car:
    id: str
    route: tuple[str, ...]
    born: float
    factor: float
    headway: float
    index: int = 0
    lane: int = 0
    x: float = 0.0
    speed: float = 0.0
    wait: float = 0.0
    stopped: float = 0.0
    free_flow: float = 0.0
    finished: float | None = None
    blocked: bool = False
    teleported: bool = False

    @property
    def edge(self):
        return self.route[self.index]


class SimEngine:
    def __init__(self, network, intersections, seed, demand_profile, signalized_ids):
        self.seed = seed
        self.network = network.model_copy(deep=True)
        self.intersections = [i.model_copy(deep=True) for i in intersections]
        self.edges = {e.id: e for e in self.network.edges}
        self.phases = {i.id: derive_phases(i) for i in self.intersections}
        known = {i.id for i in self.intersections}
        if set(signalized_ids) - known:
            raise ValueError("unknown signalized intersection")
        self.signal_map = {i.id: Signal(i, self.phases[i.id]) for i in self.intersections
                           if i.id in signalized_ids and self.phases[i.id]}
        self.approach_map = {a.in_edge: (i, a) for i in self.intersections for a in i.approaches}
        self.demand = Demand(self.network, demand_profile, seed)
        self.driver_rng = np.random.default_rng(np.random.SeedSequence([seed, 0xD12A]))
        self.t = 0.0
        self.cars = {}
        self.external = defaultdict(deque)
        self.finished = deque()
        self.arrivals = defaultdict(deque)
        self.reservations = {}  # node -> [(expiry, in_edge, out_edge)] active movement reservations
        self.crossing_moves = deque(maxlen=10000)  # (time, node, in_edge, out_edge) for audits/tests
        self.blocked_spawns = 0
        self.deadlocks = 0
        self.generated = 0
        self.completed = 0
        self.teleported = 0
        self.no_motion_since = 0.0
        self.crossings = deque(maxlen=10000)  # (time, vehicle, incoming edge, color)

    def _lanes(self):
        lanes = defaultdict(list)
        for car in self.cars.values():
            lanes[car.edge, car.lane].append(car)
        for queue in lanes.values():
            queue.sort(key=lambda c: (-c.x, c.id))
        return lanes

    def _space(self, edge_id, lanes):
        edge = self.edges[edge_id]
        options = []
        for lane in range(edge.lanes):
            queue = lanes.get((edge_id, lane), [])
            space = queue[-1].x - SPACING if queue else edge.length_m
            if space >= -1e-9:
                options.append((space, -lane))
        if not options:
            return None
        space, minus_lane = max(options)
        return -minus_lane, max(0.0, space)

    # ------------------------------------------------------------------ movement conflicts
    def _heading(self, edge_id, at_end):
        key = (edge_id, at_end)
        cache = self.__dict__.setdefault("_heading_cache", {})
        if key not in cache:
            g = self.edges[edge_id].geometry
            (ax, ay), (bx, by) = (g[-2], g[-1]) if at_end else (g[0], g[1])
            cache[key] = math.degrees(math.atan2((bx - ax) * math.cos(math.radians(ay)), by - ay)) % 360
        return cache[key]

    def _turn(self, in_edge, out_edge):
        """'straight' | 'right' | 'left' (right-hand traffic; U-turns count as left)."""
        delta = (self._heading(out_edge, False) - self._heading(in_edge, True)) % 360
        if delta <= 30 or delta >= 330:
            return "straight"
        if delta < 160:
            return "right"
        return "left"

    def conflicts(self, a, b):
        """Do movements a=(in_edge, out_edge) and b conflict inside the junction?"""
        (ai, ao), (bi, bo) = a, b
        if ai == bi:
            return False  # same approach: following / parallel lanes
        if ao is None or bo is None:
            return False  # leaving the network at a boundary node
        if ao == bo:
            return True  # merge into the same exit
        diff = abs(((self._heading(ai, True) - self._heading(bi, True)) % 360) - 180)
        opposing = diff <= OPPOSING_TOLERANCE_DEG
        if opposing and self._turn(ai, ao) != "left" and self._turn(bi, bo) != "left":
            return False
        return True

    def _blocked_by_reservation(self, node, movement):
        return any(expiry > self.t + 1e-9 and self.conflicts(movement, (ri, ro))
                   for expiry, ri, ro in self.reservations.get(node, ()))

    def _record_arrival(self, car):
        if car.edge in self.approach_map:
            _, approach = self.approach_map[car.edge]
            self.arrivals[approach.id].append(self.t)

    def _spawn(self, lanes):
        for arrival in self.demand.until(self.t):
            car = Car(id=f"v{self.generated}", route=arrival.route, born=arrival.time,
                      factor=arrival.speed_factor, headway=float(self.driver_rng.uniform(1.1, 1.8)))
            self.generated += 1
            self.external[car.route[0]].append(car)
        for edge_id in sorted(self.external):
            pending = self.external[edge_id]
            while pending:
                space = self._space(edge_id, lanes)
                if space is None:
                    # Count one deferred initial attempt per vehicle, not every retry.
                    for car in pending:
                        if not car.blocked:
                            self.blocked_spawns += 1
                            car.blocked = True
                    break
                car = pending.popleft()
                car.lane = space[0]
                car.wait = self.t - car.born
                car.stopped = car.wait
                self.cars[car.id] = car
                lanes[edge_id, car.lane].append(car)
                self._record_arrival(car)

    def _permission(self, car, lanes):
        edge = self.edges[car.edge]
        pair = self.approach_map.get(car.edge)
        if pair:
            intersection, approach = pair
            signal = self.signal_map.get(intersection.id)
            if signal and (signal.stage != "green" or approach.id not in signal.green_ids()):
                return False
        out_edge = car.route[car.index + 1] if car.index + 1 < len(car.route) else None
        if self._blocked_by_reservation(edge.to_node, (car.edge, out_edge)):
            return False
        if car.index + 1 < len(car.route) and self._space(car.route[car.index + 1], lanes) is None:
            return False
        if pair and pair[0].id not in self.signal_map:
            intersection, approach = pair
            own_rank = ROAD_PRIORITY[edge.road_class]
            for other in intersection.approaches:
                if other.id == approach.id:
                    continue
                other_edge = self.edges[other.in_edge]
                rank = ROAD_PRIORITY[other_edge.road_class]
                delta = (other.bearing - approach.bearing) % 360
                yields = rank < own_rank or (rank == own_rank and 180 < delta < 360)
                if not yields:
                    continue
                for lane in range(other_edge.lanes):
                    queue = lanes.get((other.in_edge, lane), [])
                    if not queue:
                        continue
                    lead = queue[0]
                    gap_time = (other_edge.length_m - lead.x) / max(lead.speed, 1.0)
                    # Per-driver headway variation from the independent driver RNG.
                    if gap_time < 3.0 + car.headway:
                        return False
        return True

    def step(self, dt, commands):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be positive and finite")
        for iid, phase in commands.items():
            if iid not in self.signal_map:
                raise ValueError(f"unknown signalized intersection {iid}")
            if phase not in {p.id for p in self.phases[iid]}:
                raise ValueError(f"unknown phase {phase}")
        remaining = dt
        while remaining > 1e-9:
            h = min(SIM_DT_S, remaining)
            self._tick(h, commands)
            remaining -= h

    def _tick(self, dt, commands):
        for iid, signal in self.signal_map.items():
            signal.advance(self.t, commands.get(iid))
        lanes = self._lanes()
        self._spawn(lanes)
        moved = 0.0
        processed = set()
        # Front-to-back updates make hard spacing bounds independent of dt.
        for edge_lane in sorted(list(lanes)):
            queue = list(lanes[edge_lane])
            for car in queue:
                if car.id in processed or car.id not in self.cars or (car.edge, car.lane) != edge_lane:
                    continue
                processed.add(car.id)
                edge = self.edges[car.edge]
                current_queue = lanes[edge_lane]
                pos = current_queue.index(car)
                leader = current_queue[pos - 1] if pos else None
                allowed = leader is None and self._permission(car, lanes)
                cap = leader.x - SPACING if leader else (edge.length_m if allowed else edge.length_m - 0.1)
                gap = max(0.1, leader.x - car.x - CAR_LENGTH if leader else
                          (1e6 if allowed else cap - car.x))
                lead_speed = leader.speed if leader else 0.0
                desired = edge.speed_limit * min(1.0, car.factor)
                s_star = MIN_GAP + max(0, car.speed * car.headway +
                                      car.speed * (car.speed - lead_speed) / (2 * math.sqrt(1.4 * 2)))
                acceleration = 1.4 * (1 - (car.speed / desired) ** 4 - (s_star / gap) ** 2)
                speed = max(0.0, car.speed + max(-8.0, acceleration) * dt)
                distance = min(speed * dt, max(0.0, cap - car.x))
                # At most one edge transfer per integration substep.
                distance = min(distance, edge.length_m - car.x)
                car.speed = distance / dt
                car.x += distance
                car.free_flow += distance / edge.speed_limit
                moved += distance
                if car.speed < STOPPED_SPEED_MPS:
                    car.wait += dt
                    car.stopped += dt
                else:
                    car.stopped = 0.0
                if allowed and car.x >= edge.length_m - 1e-9:
                    # Recheck after earlier movements: only one reservation can win.
                    if not self._permission(car, lanes):
                        car.x = edge.length_m - 0.1
                        car.speed = 0.0
                        continue
                    color = "unsignalized"
                    if car.edge in self.approach_map:
                        intersection, approach = self.approach_map[car.edge]
                        if intersection.id in self.signal_map:
                            color = self.signal_map[intersection.id].state(self.t).color_per_approach[approach.id]
                    self.crossings.append((self.t, car.id, car.edge, color))
                    out_edge = car.route[car.index + 1] if car.index + 1 < len(car.route) else None
                    self.crossing_moves.append((self.t, edge.to_node, car.edge, out_edge))
                    active = [r for r in self.reservations.get(edge.to_node, []) if r[0] > self.t + 1e-9]
                    active.append((self.t + RESERVATION_S, car.edge, out_edge))
                    self.reservations[edge.to_node] = active
                    current_queue.remove(car)
                    if car.index + 1 == len(car.route):
                        self._finish(car, self.t + dt)
                    else:
                        next_edge = car.route[car.index + 1]
                        space = self._space(next_edge, lanes)
                        car.index += 1
                        car.lane, car.x = space[0], 0.0
                        lanes[next_edge, car.lane].append(car)
                        self._record_arrival(car)
        self.t = round(self.t + dt, 10)
        self._spawn(self._lanes())
        for pending in self.external.values():
            for car in pending:
                car.wait = self.t - car.born
                car.stopped = car.wait
        for signal in self.signal_map.values():
            signal.advance(self.t)
        if moved > 1e-6 or not self.cars:
            self.no_motion_since = self.t
        elif self.t - self.no_motion_since >= 60 - 1e-9:
            self.deadlocks += 1
            self._teleport(min(self.cars.values(), key=lambda c: (c.born, c.id)))
            self.no_motion_since = self.t
        self._resolve_cycle(lanes)
        while self.finished and self.finished[0].finished <= self.t - METRICS_FINISHED_WINDOW_S:
            self.finished.popleft()
        for arrivals in self.arrivals.values():
            while arrivals and arrivals[0] <= self.t - 60:
                arrivals.popleft()

    def _finish(self, car, t):
        car.finished = t
        self.finished.append(car)
        self.cars.pop(car.id)
        self.completed += 1

    def _teleport(self, car):
        car.teleported = True
        self._finish(car, self.t)
        self.completed -= 1
        self.teleported += 1

    def _resolve_cycle(self, lanes):
        # Functional wait-for graph: lane leaders blocked by downstream tail.
        blocked = {}
        for queue in lanes.values():
            if not queue:
                continue
            for follower, leader in zip(queue[1:], queue):
                if (follower.id in self.cars and leader.id in self.cars
                        and follower.stopped > 120 and leader.stopped > 120):
                    blocked[follower.id] = leader.id
            car = queue[0]
            if (car.id not in self.cars or car.stopped <= 120 or car.index + 1 >= len(car.route)
                    or self.edges[car.edge].length_m - car.x > SPACING):
                continue
            nxt = car.route[car.index + 1]
            if self._space(nxt, lanes) is not None:
                continue
            tails = [lanes[nxt, lane][-1] for lane in range(self.edges[nxt].lanes)
                     if lanes.get((nxt, lane))]
            if tails:
                blocked[car.id] = min(tails, key=lambda c: (c.born, c.id)).id
        for start in sorted(blocked):
            path = []
            current = start
            while current in blocked and current not in path:
                path.append(current)
                current = blocked[current]
            if current in path:
                cycle = [self.cars[c] for c in path[path.index(current):] if c in self.cars]
                if cycle:
                    self.deadlocks += 1
                    self._teleport(min(cycle, key=lambda c: (c.born, c.id)))
                    return

    def _cars_by_edge(self):
        by_edge = defaultdict(list)
        for c in self.cars.values():
            by_edge[c.edge].append(c)
        return by_edge

    def _approach_stats(self, approach, by_edge=None):
        by_edge = self._cars_by_edge() if by_edge is None else by_edge
        edge = self.edges[approach.in_edge]
        cars = by_edge.get(edge.id, [])
        stopped = [c for c in cars if c.speed < STOPPED_SPEED_MPS]
        outs = approach.out_edges
        return ApproachObservation(queue=sum(edge.length_m - c.x <= QUEUE_DISTANCE_M for c in stopped),
                                   wait_s=max((c.stopped for c in stopped), default=0.0),
                                   arrival_rate=len(self.arrivals[approach.id]) / 60,
                                   vehicles=len(cars),
                                   downstream_vehicles=(sum(len(by_edge.get(o, [])) for o in outs) / len(outs))
                                   if outs else 0.0)

    def observe(self):
        states = {}
        by_edge = self._cars_by_edge()
        for i in self.intersections:
            if i.id not in self.signal_map:
                continue
            state = self.signal_map[i.id].state(self.t)
            states[i.id] = IntersectionObservation(phases=self.phases[i.id], current_phase=state.phase_id,
                time_in_phase_s=state.time_in_phase_s, is_transition=state.is_transition,
                approaches={a.id: self._approach_stats(a, by_edge) for a in i.approaches})
        return Observation(t=self.t, intersections=states)

    def signals(self):
        return [self.signal_map[iid].state(self.t) for iid in sorted(self.signal_map)]

    def vehicles(self):
        result = []
        for car in sorted(self.cars.values(), key=lambda c: c.id):
            edge = self.edges[car.edge]
            geometry = edge.geometry
            lengths = [math.hypot((b[0] - a[0]) * math.cos(math.radians(a[1])), b[1] - a[1])
                       for a, b in zip(geometry, geometry[1:])]
            target = car.x / edge.length_m * sum(lengths)
            segment = len(lengths) - 1
            for k, length in enumerate(lengths):
                if target <= length:
                    segment = k
                    break
                target -= length
            a, b = geometry[segment:segment + 2]
            f = min(1.0, target / lengths[segment]) if lengths[segment] else 0.0
            heading = math.degrees(math.atan2((b[0] - a[0]) * math.cos(math.radians(a[1])), b[1] - a[1])) % 360
            result.append(Vehicle(id=car.id, edge_id=car.edge, offset_m=car.x,
                lat=a[1] + f * (b[1] - a[1]), lon=a[0] + f * (b[0] - a[0]), heading=heading, speed=car.speed))
        return result

    def metrics(self, mode, effective_controller):
        population = list(self.cars.values()) + list(self.finished)
        population += [c for pending in self.external.values() for c in pending]
        n = len(population)
        wait = sum(c.wait for c in population) / n if n else 0.0
        delay = max(0.0, sum((c.finished if c.finished is not None else self.t) - c.born - c.free_flow
                            for c in population) / n) if n else 0.0
        by_edge = self._cars_by_edge()
        queues = [self._approach_stats(a, by_edge).queue for i in self.intersections for a in i.approaches]
        # Teleports are retained in the metric population, but never throughput.
        throughput = sum(c.finished > self.t - THROUGHPUT_WINDOW_S and not c.teleported
                         for c in self.finished)
        return Metrics(mode=mode, effective_controller=effective_controller, avg_wait_s=wait,
            trip_delay_s=delay, avg_queue=sum(queues) / len(queues) if queues else 0.0,
            throughput_per_min=throughput, blocked_spawns=self.blocked_spawns,
            deadlocks=self.deadlocks, t=self.t, population=n)

    def state_digest(self):
        state = dict(t=self.t, cars=[asdict(c) for c in sorted(self.cars.values(), key=lambda c: c.id)],
            external={e: [asdict(c) for c in q] for e, q in sorted(self.external.items())},
            finished=[asdict(c) for c in self.finished], signals=[s.model_dump() for s in self.signals()],
            requests={iid: (s.request, s.current, s.target, s.stage, s.since, s.red_since)
                      for iid, s in sorted(self.signal_map.items())}, reservations=self.reservations,
            arrivals={a: list(q) for a, q in sorted(self.arrivals.items())},
            demand=self.demand.digest(), pending=[(t, o, asdict(a)) for t, o, a in self.demand.pending],
            demand_rng={o: r.bit_generator.state for o, r in self.demand.rngs.items()},
            driver_rng=self.driver_rng.bit_generator.state,
            counters=[self.generated, self.completed, self.teleported, self.blocked_spawns, self.deadlocks],
            no_motion_since=self.no_motion_since, crossings=list(self.crossings),
            profile=self.demand.profile.model_dump(mode="json"), seed=self.seed)
        return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def demand_schedule_digest(self):
        """Hash scheduled arrivals through current simulation time (compare equal t)."""
        return self.demand.digest()

    def latest_profile(self):
        """The profile in force after every change scheduled so far."""
        if self.demand.changes:
            return max(self.demand.changes, key=lambda c: (c[0], c[1]))[2]
        return self.demand.profile

    def apply_profile(self, profile, at_t=None):
        """Schedule a frozen DemandProfile from sim time at_t (None = now). Contract 0.2.0."""
        at = self.t if at_t is None else float(at_t)
        if at < self.t - 1e-9:
            raise ValueError(f"at_t={at} is in the past (t={self.t})")
        self.demand.schedule_change(profile, at)
        return at

    def set_demand(self, level=None, multiplier=None, entry_overrides=None, at_t=None):
        """Contract 0.2.0: change demand from at_t; omitted arguments keep the latest values.
        Returns the new (derived, frozen) profile."""
        from backend.traffic.demand import derive_profile
        child = derive_profile(self.latest_profile(), list(self.network.entry_nodes), level=level,
                               multiplier=multiplier, entry_overrides=entry_overrides)
        self.apply_profile(child, at_t)
        return child
