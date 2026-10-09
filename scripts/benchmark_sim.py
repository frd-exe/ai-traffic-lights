"""Run: python -m scripts.benchmark_sim --seconds 60 --vehicles 500.

500 moving/queued initial cars evenly distributed across fixture lane queues;
normal rush arrivals continue. --maintain replenishes exited cars to keep at
least 500 active; these are workload injections, not scheduled OD demand.
Timing excludes setup and serialization, includes controller and replenishment.
"""
import argparse
from datetime import datetime, timezone
import json
from time import perf_counter

from backend.contract.models import DemandProfile
from backend.control.fixed import FixedController
from backend.sim import SimEngine
from backend.sim.engine import Car, SPACING
from backend.sim.fixtures import load_network, derive_intersections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--vehicles", type=int, default=500)
    parser.add_argument("--network", default=None)
    parser.add_argument("--maintain", action="store_true")
    args = parser.parse_args()
    network = load_network(args.network)
    intersections = derive_intersections(network)
    profile = DemandProfile(id="benchmark", source="baseline_only", level="rush",
                            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), entries=[])
    engine = SimEngine(network, intersections, 42, profile, [i.id for i in intersections])
    queues = [(e, lane) for e in network.edges for lane in range(e.lanes)]
    edge_routes = {e.id: next((r[r.index(e.id):] for r in engine.demand.routes.values()
                              if e.id in r), (e.id,)) for e in network.edges}
    for k in range(args.vehicles):
        edge, lane = queues[k % len(queues)]
        rank = k // len(queues)
        x = edge.length_m - 1 - SPACING * rank
        if x < 0:
            raise ValueError("requested vehicles exceed fixture capacity")
        # Find a reachable destination continuation, excluding immediate U-turn.
        route = edge_routes[edge.id]
        car = Car(id=f"bench{k}", route=route, born=0, factor=1, headway=1.5,
                  lane=lane, x=x, speed=0)
        engine.cars[car.id] = car
    engine.generated = args.vehicles
    controller = FixedController()
    commands = controller.decide(engine.observe())
    start = perf_counter()
    steps = round(args.seconds / 0.1)
    minimum = len(engine.cars)
    maximum = minimum
    replenish_cursor = 0
    for step in range(steps):
        if step % 10 == 0:
            commands = controller.decide(engine.observe())
        engine.step(0.1, commands)
        if args.maintain and len(engine.cars) < args.vehicles:
            lanes = engine._lanes()
            for _ in range(len(network.edges)):
                if len(engine.cars) >= args.vehicles:
                    break
                edge = network.edges[replenish_cursor % len(network.edges)]
                replenish_cursor += 1
                space = engine._space(edge.id, lanes)
                if space is None:
                    continue
                car = Car(id=f"replenish{engine.generated}", route=edge_routes[edge.id],
                    born=engine.t, factor=1, headway=1.5, lane=space[0])
                engine.cars[car.id] = car
                lanes[edge.id, car.lane].append(car)
                engine.generated += 1
                engine._record_arrival(car)
        minimum = min(minimum, len(engine.cars))
        maximum = max(maximum, len(engine.cars))
    elapsed = perf_counter() - start
    print(json.dumps(dict(simulated_s=engine.t, wall_s=round(elapsed, 4),
        realtime_factor=round(engine.t / elapsed, 2), initial_vehicles=args.vehicles,
        maintain=args.maintain,
        min_active=minimum, max_active=maximum, final_active=len(engine.cars),
        generated=engine.generated, completed=engine.completed, teleported=engine.teleported,
        metrics=engine.metrics("fixed", "fixed").model_dump()), indent=2))


if __name__ == "__main__":
    main()
