from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.contract.interfaces import Controller, SimEngine as SimProtocol
from backend.contract.models import DemandEntry, DemandProfile, Phase
from backend.control.fixed import FixedController
from backend.control.webster import WebsterController
from backend.sim import SimEngine
from backend.sim.demand import Demand
from backend.sim.engine import Car, SPACING
from backend.sim.fixtures import load_network, derive_intersections
from backend.sim.phases import derive_phases
from backend.sim.signals import Signal


def profile(source="baseline_only", level="medium", scale=1):
    return DemandProfile(id="test-profile", source=source, level=level,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entries=[DemandEntry(entry_node_id=n, scale=scale) for n in load_network().entry_nodes])


def engine(seed=123, selected=True, demand=None):
    net = load_network()
    intersections = derive_intersections(net)
    return SimEngine(net, intersections, seed, demand or profile(),
                     [i.id for i in intersections] if selected else [])


def run(e, seconds, controller=None):
    controller = controller or FixedController()
    commands = {}
    for tick in range(round(seconds / 0.1)):
        if tick % 10 == 0:
            commands = controller.decide(e.observe())
        e.step(0.1, commands)


def test_protocol_and_determinism():
    a, b = engine(), engine()
    assert isinstance(a, SimProtocol)
    assert isinstance(FixedController(), Controller)
    run(a, 120)
    run(b, 120)
    assert a.state_digest() == b.state_digest()
    assert a.generated > 50
    assert a.completed > 0
    assert a.vehicles() == b.vehicles()
    c = engine(seed=124)
    run(c, 120)
    assert c.state_digest() != a.state_digest()


def test_identical_demand_across_controllers_and_driver_consumption():
    a, b, c = engine(), engine(), engine(selected=False)
    b.driver_rng.random(1000)
    run(a, 90, FixedController(10))
    run(b, 90, WebsterController(b))
    run(c, 90)
    assert a.demand_schedule_digest() == b.demand_schedule_digest() == c.demand_schedule_digest()
    assert a.demand.schedule == b.demand.schedule
    assert a.state_digest() != b.state_digest()


def test_scale_and_baseline_level():
    low = Demand(load_network(), profile("google_snapshot", scale=0.5), 1)
    high = Demand(load_network(), profile("google_snapshot", scale=1.5), 1)
    lo, hi = low.until(36000), high.until(36000)
    assert len(hi) / len(lo) == pytest.approx(3, rel=0.04)
    origin = next(iter(low.rates))
    lo_entry = [a for a in lo if a.origin == origin]
    hi_entry = [a for a in hi if a.origin == origin]
    for a, b in zip(lo_entry[:20], hi_entry[:20]):
        assert a.time == pytest.approx(b.time * 3)
        assert (a.destination, a.speed_factor) == (b.destination, b.speed_factor)
    baseline = Demand(load_network(), profile(level="rush", scale=0.01), 1)
    assert all(rate == 700 / 3600 for rate in baseline.rates.values())


def test_profile_is_frozen_copy_and_set_demand():
    p = profile("google_snapshot", scale=1)
    e = engine(demand=p)
    p.entries[0].scale = 10
    assert next(iter(e.demand.rates.values())) == 300 / 3600
    e.set_demand("low")
    assert all(rate == 150 / 3600 for rate in e.demand.rates.values())
    e.step(0.1, {})
    with pytest.raises(RuntimeError):
        e.set_demand("high")


def test_geometry_four_three_five_arms():
    i = engine().intersections[0]
    phases = derive_phases(i)
    assert len(phases) == 2
    assert sorted(len(p.approach_ids) for p in phases) == [2, 2]
    t = i.model_copy(update={"approaches": i.approaches[:3]})
    assert sorted(len(p.approach_ids) for p in derive_phases(t)) == [1, 2]
    fifth = i.approaches[0].model_copy(update={"id": "fifth", "bearing": 45})
    five = i.model_copy(update={"approaches": i.approaches + [fifth]})
    assert sorted(len(p.approach_ids) for p in derive_phases(five)) == [1, 2, 2]


def test_min_green_yellow_all_red_and_queued_command():
    e = engine()
    iid, s = next(iter(e.signal_map.items()))
    target = s.phases[1].id
    e.step(6.9, {iid: target})
    assert not e.signals()[0].is_transition
    e.step(0.1, {})
    assert s.stage == "transition"
    assert set(s.state(e.t).color_per_approach.values()) == {"yellow", "red"}
    assert s.state(e.t).phase_id == target
    e.step(2.9, {})
    assert "yellow" in s.state(e.t).color_per_approach.values()
    e.step(0.1, {})
    assert set(s.state(e.t).color_per_approach.values()) == {"red"}
    original = s.current
    e.step(1.9, {iid: original})
    assert s.stage == "transition"
    e.step(0.1, {})
    assert s.current == target and s.stage == "green"
    e.step(6.9, {})
    assert s.stage == "green"
    e.step(0.1, {})
    assert s.stage == "transition" and s.target == original


def test_starvation_limit_and_no_conflicting_greens():
    e = engine()
    iid, signal = next(iter(e.signal_map.items()))
    served = {a.id: 0 for a in signal.intersection.approaches}
    for _ in range(1800):
        e.step(0.1, {iid: signal.phases[0].id})
        state = signal.state(e.t)
        green = {a for a, color in state.color_per_approach.items() if color == "green"}
        assert not green or green in [set(p.approach_ids) for p in signal.phases]
        for aid in served:
            if aid in green:
                served[aid] = e.t
            assert e.t - served[aid] <= 60.1


@pytest.mark.parametrize("swap_t", [1, 9, 10.5, 11.5, 40])
def test_safe_controller_swap(swap_t):
    e = engine()
    run(e, swap_t, FixedController(7))
    before = {iid: s.state(e.t) for iid, s in e.signal_map.items()}
    fallback = FixedController()
    command = fallback.decide(e.observe())
    for iid, state in before.items():
        if state.is_transition:
            assert command[iid] == state.phase_id
        else:
            assert command[iid] != state.phase_id
    transitions = set()
    for k in range(200):
        if k % 10 == 0:
            command = fallback.decide(e.observe())
        e.step(0.1, command)
        for state in e.signals():
            if state.is_transition:
                transitions.add(state.intersection_id)
    assert transitions == set(e.signal_map)
    assert all(color != "red" for _, _, _, color in e.crossings)


def test_red_yellow_stop_no_overlap_and_vehicle_conservation():
    e = engine(demand=profile(level="rush"))
    controller = FixedController(7)
    commands = {}
    for k in range(2400):
        if k % 10 == 0:
            commands = controller.decide(e.observe())
        e.step(0.1, commands)
        for queue in e._lanes().values():
            for front, back in zip(queue, queue[1:]):
                assert front.x - back.x >= SPACING - 1e-7
        assert all(0 <= c.x <= e.edges[c.edge].length_m for c in e.cars.values())
        assert e.generated == len(e.cars) + sum(map(len, e.external.values())) + e.completed + e.teleported
    assert e.crossings
    assert all(color == "green" for _, _, edge, color in e.crossings if edge in e.approach_map)


def test_red_vehicle_cannot_cross_stop_line():
    e = engine()
    s = next(iter(e.signal_map.values()))
    aid = s.phases[1].approach_ids[0]
    a = next(a for a in s.intersection.approaches if a.id == aid)
    edge = e.edges[a.in_edge]
    car = Car("red-test", (edge.id, a.out_edges[0]), 0, 1, 1.5, x=edge.length_m - 1, speed=10)
    e.cars[car.id] = car
    e.step(5, {})
    assert car.edge == edge.id
    assert car.x <= edge.length_m - 0.1 + 1e-8
    assert not any(c[1] == car.id for c in e.crossings)


def test_blocked_spawns_include_external_wait_and_count_once():
    e = engine()
    edge = next(x for x in e.network.edges if x.from_node in e.network.entry_nodes and x.lanes == 1)
    front = Car("front", (edge.id,), 0, 1, 1.5, x=0)
    pending = Car("pending", (edge.id,), 0, 1, 1.5)
    e.cars[front.id] = front
    e.external[edge.id].append(pending)
    e._spawn(e._lanes())
    e._spawn(e._lanes())
    assert e.blocked_spawns == 1
    e.t = 10
    pending.wait = 10
    front.wait = 10
    metric = e.metrics("fixed", "fixed")
    assert metric.avg_wait_s == 10
    assert metric.trip_delay_s == 10
    assert metric.population == 2


def test_hand_computed_metrics_and_window_boundaries():
    e = engine()
    e.t = 100
    approach = e.intersections[0].approaches[0]
    edge = e.edges[approach.in_edge]
    moving = Car("moving", (edge.id,), 80, 1, 1.5, x=100, speed=2, wait=3, free_flow=10)
    stopped = Car("stopped", (edge.id,), 70, 1, 1.5, x=edge.length_m - 10, wait=8, free_flow=5)
    queued = Car("queued", (edge.id,), 90, 1, 1.5, wait=10)
    done = Car("done", (edge.id,), 20, 1, 1.5, wait=9, free_flow=20, finished=60)
    e.cars = {c.id: c for c in [moving, stopped]}
    e.external[edge.id].append(queued)
    e.finished.append(done)
    m = e.metrics("fixed", "fixed")
    assert m.population == 4
    assert m.avg_wait_s == 7.5
    assert m.trip_delay_s == (10 + 25 + 10 + 20) / 4
    assert m.avg_queue == 1 / sum(len(i.approaches) for i in e.intersections)
    assert m.throughput_per_min == 1
    done.finished = 40
    assert e.metrics("fixed", "fixed").throughput_per_min == 0
    e.arrivals[approach.id].extend([41, 60, 99])
    obs = e.observe().intersections[e.intersections[0].id].approaches[approach.id]
    assert obs.queue == 1
    assert obs.arrival_rate == 3 / 60


def test_unsignalized_major_priority_and_gap_acceptance():
    e = engine(selected=False)
    i = next(i for i in e.intersections if any(e.edges[a.in_edge].road_class == "primary" for a in i.approaches))
    major = next(a for a in i.approaches if e.edges[a.in_edge].road_class == "primary")
    minor = next(a for a in i.approaches if e.edges[a.in_edge].road_class == "residential")
    m = Car("major", (major.in_edge, major.out_edges[0]), 0, 1, 1.5,
            x=e.edges[major.in_edge].length_m - 1, speed=3)
    n = Car("minor", (minor.in_edge, minor.out_edges[0]), 0, 1, 1.5,
            x=e.edges[minor.in_edge].length_m - 1, speed=3)
    e.cars = {c.id: c for c in [m, n]}
    assert e._permission(m, e._lanes())
    assert not e._permission(n, e._lanes())
    m.x = 0
    assert e._permission(n, e._lanes())
    assert not e.signals() and not e.observe().intersections


def test_unsignalized_right_hand_rule():
    e = engine(selected=False)
    i = next(i for i in e.intersections if i.node_id == "n11")
    south = min(i.approaches, key=lambda a: abs(a.bearing - 180))
    east = min(i.approaches, key=lambda a: abs(a.bearing - 90))
    cars = [Car(a.id, (a.in_edge, a.out_edges[0]), 0, 1, 1.5,
                x=e.edges[a.in_edge].length_m - 1, speed=3) for a in [south, east]]
    e.cars = {c.id: c for c in cars}
    assert not e._permission(cars[0], e._lanes())
    assert e._permission(cars[1], e._lanes())


def test_reservations_serialize_conflicting_turns():
    e = engine()
    run(e, 120, FixedController(7))
    by_node = {}
    for t, _, edge_id, color in e.crossings:
        node = e.edges[edge_id].to_node
        assert t - by_node.get(node, -100) >= 2 - 1e-8
        by_node[node] = t
        assert color in {"green", "unsignalized"}


def test_five_singleton_phases_do_not_starve():
    i = engine().intersections[0]
    arms = [i.approaches[0].model_copy(update={"id": f"arm{k}", "bearing": k * 20}) for k in range(5)]
    i = i.model_copy(update={"approaches": arms})
    phases = derive_phases(i)
    assert len(phases) == 5
    signal = Signal(i, phases)
    last_green = {a.id: 0.0 for a in arms}
    for k in range(2401):
        t = k / 10
        signal.advance(t, phases[0].id)
        state = signal.state(t)
        for aid, color in state.color_per_approach.items():
            if color == "green":
                last_green[aid] = t
            assert t - last_green[aid] <= 60.1


def test_global_deadlock_resolution_oldest_and_no_throughput():
    e = engine()
    s = next(iter(e.signal_map.values()))
    aid = s.phases[1].approach_ids[0]
    edge_id = next(a.in_edge for a in s.intersection.approaches if a.id == aid)
    car = Car("oldest", (edge_id,), 0, 1, 1.5, x=e.edges[edge_id].length_m - 0.1)
    e.cars[car.id] = car
    # Exercise contract-wide immobility branch without allowing starvation release.
    e.t = 59.9
    e.demand.pending.clear()
    s.since = 59.9
    s.red_since = {a.id: 59.9 for a in s.intersection.approaches}
    e.step(0.1, {})
    assert e.deadlocks == 1 and e.teleported == 1
    assert car.id not in e.cars
    assert e.metrics("fixed", "fixed").throughput_per_min == 0


def test_local_mutual_blocking_cycle_teleports_oldest():
    e = engine(selected=False)
    # Four directed edges forming an interior square with all lanes filled.
    nodes = ["n11", "n12", "n22", "n21", "n11"]
    edge_ids = [next(x.id for x in e.network.edges if x.from_node == a and x.to_node == b)
                for a, b in zip(nodes, nodes[1:])]
    for k, edge_id in enumerate(edge_ids):
        edge = e.edges[edge_id]
        for lane in range(edge.lanes):
            for depth in range(int((edge.length_m - 0.1) / SPACING) + 1):
                car = Car(f"cycle{k}-{lane}-{depth}", (edge_id, edge_ids[(k + 1) % 4]),
                          k + depth / 100, 1, 1.5, lane=lane,
                          x=edge.length_m - 0.1 - depth * SPACING, stopped=121)
                e.cars[car.id] = car
    e.t = 130
    e._resolve_cycle(e._lanes())
    assert e.deadlocks == 1 and e.teleported == 1
    assert "cycle0-0-0" not in e.cars


def test_webster_uses_demand_flows_and_minimum_splits():
    e = engine()
    w = WebsterController(e)
    assert isinstance(w, Controller)
    assert all(green >= 7 for green in w.splits.values())
    for i in e.intersections:
        assert sum(w.splits[i.id, p.id] for p in e.phases[i.id]) + 5 * len(e.phases[i.id]) == pytest.approx(w.cycles[i.id])
    run(e, 90, w)
    assert e.completed > 0


def test_optional_sample_area():
    path = Path("backend/data/sample_area.json")
    if not path.exists():
        pytest.skip("sample_area.json is not present")
    net = load_network(path)
    intersections = derive_intersections(net)
    e = SimEngine(net, intersections, 1, profile(), [i.id for i in intersections])
    run(e, 30)
    assert e.t == 30
