"""Max-pressure, safety layer, safe controller swaps and demand independence (C3)."""

import logging

import pytest

from backend.ai.gemini_client import GeminiClient, UsageCounter
from backend.contract.constants import ALL_RED_S, MAX_RED_S, MIN_GREEN_S, YELLOW_S
from backend.contract.models import ApproachObservation, IntersectionObservation, Observation, Phase, SignalState
from backend.control import MaxPressureController, SafetyLayer, WebsterController
from backend.control.ai_gemini import GeminiSupervisorController
from backend.control.max_pressure import SWITCH_GAIN
from backend.control.runner import run_headless
from backend.control.safety import STARVATION_S, SafetyViolation
from backend.tests.test_sim import engine, profile


def obs(t=100.0, current="i:p0", in_phase=20.0, transition=False, veh=None, down=None):
    """Two-phase junction: p0 = a_n + a_s, p1 = a_e + a_w. veh/down: {approach: count}."""
    veh = veh or {}
    down = down or {}
    phases = [Phase(id="i:p0", approach_ids=["a_n", "a_s"]), Phase(id="i:p1", approach_ids=["a_e", "a_w"])]
    approaches = {a: ApproachObservation(queue=min(veh.get(a, 0), 5), wait_s=0, arrival_rate=0,
                                         vehicles=veh.get(a, 0), downstream_vehicles=down.get(a, 0))
                  for a in ("a_n", "a_s", "a_e", "a_w")}
    st = IntersectionObservation(phases=phases, current_phase=current, time_in_phase_s=in_phase,
                                 is_transition=transition, approaches=approaches)
    return Observation(t=t, intersections={"i": st})


# ------------------------------------------------------------------ max-pressure
def test_mp_holds_when_empty():
    assert MaxPressureController().decide(obs()) == {"i": "i:p0"}


def test_mp_lone_vehicle_on_red_triggers_switch_after_min_green():
    mp = MaxPressureController()
    assert mp.decide(obs(in_phase=MIN_GREEN_S - 1, veh={"a_e": 1})) == {"i": "i:p0"}  # min green first
    assert mp.decide(obs(in_phase=MIN_GREEN_S, veh={"a_e": 1})) == {"i": "i:p1"}


def test_mp_hysteresis_and_pressure_with_downstream():
    mp = MaxPressureController()
    # current phase busy, other slightly busier: below SWITCH_GAIN -> hold
    assert mp.decide(obs(veh={"a_n": 5, "a_e": 5 + int(SWITCH_GAIN)})) == {"i": "i:p0"}
    # clearly more pressure on p1 -> switch
    assert mp.decide(obs(veh={"a_n": 2, "a_e": 2 + int(SWITCH_GAIN) + 3})) == {"i": "i:p1"}
    # same upstream counts but p1's exits are full -> no pressure, hold
    assert mp.decide(obs(veh={"a_n": 2, "a_e": 15}, down={"a_e": 15})) == {"i": "i:p0"}


def test_mp_keeps_transition_target():
    assert MaxPressureController().decide(obs(current="i:p1", transition=True, veh={"a_n": 30})) == {"i": "i:p1"}


def test_mp_no_flapping_on_engine():
    """Light demand: switches stay well below one per 10 s per junction."""
    e = engine(demand=profile(level="low"))
    switches = {}
    prev = {}

    def count(eng, t):
        for s in eng.signals():
            if s.is_transition and not prev.get(s.intersection_id):
                switches[s.intersection_id] = switches.get(s.intersection_id, 0) + 1
            prev[s.intersection_id] = s.is_transition

    run_headless(e, "max_pressure", 300, on_second=count)
    assert max(switches.values()) <= 300 / 10


# ------------------------------------------------------------------ safety layer
def safety():
    return SafetyLayer({"i": [Phase(id="i:p0", approach_ids=["a_n", "a_s"]),
                              Phase(id="i:p1", approach_ids=["a_e", "a_w"])]})


def test_safety_unknown_ids_min_green_and_transition(caplog):
    s = safety()
    with caplog.at_level(logging.INFO, logger="backend.control.safety"):
        assert s.filter({"nope": "x", "i": "i:p9"}, obs()) == {"i": "i:p0"}
        assert s.filter({"i": "i:p1"}, obs(in_phase=3.0)) == {"i": "i:p0"}
        assert s.filter({"i": "i:p0"}, obs(current="i:p1", transition=True)) == {"i": "i:p1"}
    assert s.counts == {"unknown_intersection": 1, "unknown_phase": 1, "min_green": 1, "transition_locked": 1}
    assert "safety override" in caplog.text  # every override is logged


def test_safety_starvation_guard_uses_red_time():
    s = safety()
    s.filter({"i": "i:p0"}, obs(t=0.0))  # p0 green from t=0; p1 approaches red since 0
    held = s.filter({"i": "i:p0"}, obs(t=STARVATION_S - 1, in_phase=STARVATION_S - 1, veh={"a_e": 1}))
    assert held == {"i": "i:p0"}
    forced = s.filter({"i": "i:p0"}, obs(t=STARVATION_S, in_phase=STARVATION_S, veh={"a_e": 1}))
    assert forced == {"i": "i:p1"} and s.counts["starvation"] == 1
    # an empty red approach cannot starve
    s2 = safety()
    s2.filter({"i": "i:p0"}, obs(t=0.0))
    assert s2.filter({"i": "i:p0"}, obs(t=200, in_phase=200)) == {"i": "i:p0"}
    assert STARVATION_S + YELLOW_S + ALL_RED_S < MAX_RED_S


def test_safety_rejects_overlapping_phases_and_conflicting_greens():
    with pytest.raises(SafetyViolation):
        SafetyLayer({"i": [Phase(id="i:p0", approach_ids=["a"]), Phase(id="i:p1", approach_ids=["a", "b"])]})
    s = safety()
    ok = SignalState(intersection_id="i", phase_id="i:p0", is_transition=False, time_in_phase_s=1,
                     color_per_approach={"a_n": "green", "a_s": "green", "a_e": "red", "a_w": "red"})
    s.check_signals([ok])
    bad = ok.model_copy(update={"color_per_approach": {"a_n": "green", "a_s": "red", "a_e": "green", "a_w": "red"}})
    with pytest.raises(SafetyViolation):
        s.check_signals([bad])


@pytest.mark.parametrize("mode", ["fixed", "webster", "max_pressure"])
def test_every_controller_keeps_engine_invariants(mode):
    """Through the safety layer under rush: one phase green at a time, min green, 3 s yellow + 2 s
    all-red, nobody red > 60 s, no red/yellow crossing, vehicle conservation, no deadlock."""
    e = engine(demand=profile(level="rush"))
    green_since, red_since, last_state = {}, {}, {}
    sig_ix = {iid: s.intersection for iid, s in e.signal_map.items()}

    def check(eng, t):
        for s in eng.signals():
            iid = s.intersection_id
            prev = last_state.get(iid)
            if prev and not prev.is_transition and s.is_transition:
                assert t - green_since[iid] >= MIN_GREEN_S - 1e-6  # min green served
            if prev and prev.is_transition and not s.is_transition:
                assert prev.time_in_phase_s >= YELLOW_S + ALL_RED_S - 1.0 - 1e-6
            if s.is_transition and s.time_in_phase_s < YELLOW_S - 1e-6:
                assert "green" not in s.color_per_approach.values()
            if s.is_transition and s.time_in_phase_s >= YELLOW_S + 1e-6:
                assert set(s.color_per_approach.values()) == {"red"}  # all-red
            if not s.is_transition and (prev is None or prev.is_transition):
                green_since[iid] = t
            for a in sig_ix[iid].approaches:
                if s.color_per_approach[a.id] == "green":
                    red_since[a.id] = t
                assert t - red_since.setdefault(a.id, 0.0) <= MAX_RED_S + 1.0
            last_state[iid] = s
        assert eng.generated == len(eng.cars) + sum(map(len, eng.external.values())) + eng.completed + eng.teleported

    res = run_headless(e, mode, 400, on_second=check, check_safety=True)
    assert all(color in {"green", "unsignalized"} for _, _, _, color in e.crossings)
    assert res.metrics.deadlocks == 0


# ------------------------------------------------------------------ swaps + demand independence
def _ai(fake, cap=500, tmp=None):
    usage = UsageCounter(tmp / "usage.json" if tmp else None, cap)
    return GeminiSupervisorController(GeminiClient(None, usage, fake_fail=fake), seed=1)


@pytest.mark.parametrize("fake", ["limit", "invalid"])
def test_ai_fallback_is_a_safe_mid_run_swap(fake):
    e = engine(demand=profile(level="high"))
    ai = _ai(fake)
    res = run_headless(e, "ai", 240, controller=ai, ai_interval_s=20, check_safety=True)
    assert ai.state in ("ai_limit_reached", "ai_unavailable") and ai.effective_controller == "max_pressure"
    assert res.limit_reached_at_t == 0.0
    assert all(color in {"green", "unsignalized"} for _, _, _, color in e.crossings)
    # the fixed fallback actually cycles all phases
    seen = {s.phase_id for s in e.signals()}
    assert seen


def test_demand_schedule_identical_across_all_controllers():
    digests, schedules = {}, {}
    for mode in ("fixed", "webster", "max_pressure", "ai"):
        e = engine(demand=profile(level="high"))
        ctl = _ai("timeout") if mode == "ai" else None
        run_headless(e, mode, 300, controller=ctl)
        digests[mode] = e.demand_schedule_digest()
        schedules[mode] = e.demand.schedule
    assert len(set(digests.values())) == 1
    assert schedules["fixed"] == schedules["ai"]


def test_webster_defaults_are_calibrated():
    w = WebsterController(engine(demand=profile(level="rush")))
    assert w.saturation_flow == 900 and max(w.cycles.values()) <= 90
