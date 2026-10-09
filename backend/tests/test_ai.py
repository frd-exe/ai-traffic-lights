"""Gemini client + supervisor + AI limit behavior (CONTRACT §9). No network: GEMINI_FAKE_FAIL or
httpx.MockTransport only."""

import asyncio
import json
import logging

import httpx
import pytest

from backend.ai.gemini_client import GeminiClient, GeminiError, UsageCounter, parse_plans
from backend.ai.replay import PlanRecorder, PlanReplay
from backend.contract.models import Plan
from backend.control.ai_gemini import EARLY_TERMINATION_GAIN, GeminiSupervisorController, compact_observation
from backend.tests.test_control import obs

ALLOWED = {"i": ["i:p0", "i:p1"]}
KEY = "AIzaTESTKEY-do-not-print"


def gemini_ok(plans):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(plans)}]}}]})


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, s):
        self.now += s


def client(handler=None, fake=None, cap=500, tmp=None, key=KEY, clock=None):
    clock = clock or FakeClock()
    usage = UsageCounter(tmp / "usage.json" if tmp else None, cap)
    return GeminiClient(key, usage, fake_fail=fake, transport=httpx.MockTransport(handler) if handler else None,
                        clock=clock, sleep=clock.sleep)


def supervisor(c, **kw):
    return GeminiSupervisorController(c, seed=1, session_label="test", **kw)


def call(ctl, o, now=0.0):
    asyncio.run(ctl.request(o, now))


# ------------------------------------------------------------------ client
def test_client_parses_schema_output_and_never_leaks_key():
    seen = []

    def handler(req):
        seen.append(req)
        return gemini_ok([{"intersection_id": "i", "phase": "i:p1", "hold_s": 90, "reason": "queue on east"},
                          {"intersection_id": "zzz", "phase": "x", "hold_s": 10, "reason": "unknown -> dropped"}])

    plans = asyncio.run(client(handler).plan({"t": 0}, ALLOWED))
    assert plans == [Plan(intersection_id="i", phase="i:p1", hold_s=40.0, reason="queue on east")]  # clamped
    req = seen[0]
    assert KEY not in str(req.url) and req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    gc = body["generationConfig"]
    assert gc["responseMimeType"] == "application/json" and gc["responseSchema"]["type"] == "ARRAY"
    assert gc["thinkingConfig"] == {"thinkingLevel": "low"}  # default model is Gemini 3.x


def test_thinking_config_per_model_family():
    c = client(lambda r: gemini_ok([]))
    c.model = "gemini-2.0-flash"  # any Gemini 2.x model uses thinkingBudget
    assert c._thinking_config() == {"thinkingConfig": {"thinkingBudget": 0}}
    c.model = "gemini-3.8-flash"
    assert c._thinking_config() == {"thinkingConfig": {"thinkingLevel": "low"}}
    c.thinking_level = None
    assert c._thinking_config() == {}


@pytest.mark.parametrize("status,payload,kind", [
    (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "limit"),
    (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"API key not valid {KEY}",
                     "details": [{"reason": "API_KEY_INVALID"}]}}, "invalid_key"),
    (403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "denied"}}, "invalid_key"),
    (503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}}, "transient"),
])
def test_client_error_classification(status, payload, kind):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=payload)

    with pytest.raises(GeminiError) as e:
        asyncio.run(client(handler).plan({}, ALLOWED))
    assert e.value.kind == kind
    assert KEY not in e.value.message
    assert len(calls) == (2 if kind == "transient" else 1)  # one retry for transient only


def test_client_timeout_is_transient_and_retried_once():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(GeminiError) as e:
        asyncio.run(client(handler).plan({}, ALLOWED))
    assert e.value.kind == "transient" and len(calls) == 2


def test_parse_plans_rejects_non_arrays():
    with pytest.raises(GeminiError):
        parse_plans({"plans": []}, ALLOWED)
    with pytest.raises(GeminiError):
        parse_plans([{"intersection_id": "nope", "phase": "x", "hold_s": 5, "reason": ""}], ALLOWED)
    assert parse_plans([], ALLOWED) == []


@pytest.mark.parametrize("fake,kind", [("limit", "limit"), ("429", "limit"), ("invalid", "invalid_key"),
                                       ("timeout", "transient"), ("badjson", "invalid_output")])
def test_fake_fail_modes_need_no_network(fake, kind):
    with pytest.raises(GeminiError) as e:
        asyncio.run(client(fake=fake, key=None).plan({}, ALLOWED))
    assert e.value.kind == kind


def test_missing_key_is_invalid_key():
    with pytest.raises(GeminiError) as e:
        asyncio.run(client(key=None).plan({}, ALLOWED))
    assert e.value.kind == "invalid_key"


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    c = client(lambda r: gemini_ok([]), clock=clock)
    c.min_interval_s, c.rpm = 6.0, 2

    async def run():
        for _ in range(3):
            await c.plan({}, ALLOWED)

    start = clock.now
    asyncio.run(run())
    assert clock.now - start >= 60  # 3rd call waits for the per-minute window (rpm=2)


# ------------------------------------------------------------------ budget guard
def test_budget_guard_persists_and_rolls_over(tmp_path):
    day = {"d": "2026-10-09"}
    u = UsageCounter(tmp_path / "usage.json", 3, today=lambda: day["d"])
    u.increment(), u.increment()
    again = UsageCounter(tmp_path / "usage.json", 3, today=lambda: day["d"])  # "restart"
    assert again.calls_today() == 2 and again.allowed()
    again.increment()
    assert not again.allowed()
    day["d"] = "2026-10-10"
    assert again.allowed() and again.calls_today() == 0
    again.increment()
    again.reset()
    assert UsageCounter(tmp_path / "usage.json", 3, today=lambda: day["d"]).calls_today() == 0


def test_daily_cap_blocks_before_network(tmp_path):
    calls = []
    c = client(lambda r: (calls.append(r), gemini_ok([]))[1], cap=1, tmp=tmp_path)
    asyncio.run(c.plan({}, ALLOWED))
    with pytest.raises(GeminiError) as e:
        asyncio.run(c.plan({}, ALLOWED))
    assert e.value.kind == "daily_cap" and len(calls) == 1


# ------------------------------------------------------------------ supervisor: limit behavior
def test_limit_falls_back_to_adaptive_with_all_notifications(caplog, capsys):
    ctl = supervisor(client(fake="limit"))
    ctl.decide(obs(t=42.0))
    with caplog.at_level(logging.WARNING, logger="backend.ai"):
        call(ctl, obs(t=42.0))
    st = ctl.status()
    assert st.state == "ai_limit_reached" and st.effective_controller == "max_pressure" and st.since_t == 42.0
    assert st.message == "Live AI quota reached. Using adaptive fallback."
    ex = ctl.drain_explanations()
    assert ex and ex[-1].intersection_id is None and ex[-1].text == "Live AI quota reached. Using adaptive fallback."
    assert "[AI fallback]" in caplog.text and "trigger=limit" in caplog.text
    assert "[AI fallback]" in capsys.readouterr().out
    assert KEY not in caplog.text
    # adaptive (max-pressure) now drives the junction, NOT fixed timers:
    assert ctl.decide(obs(t=43.0, in_phase=40)) == {"i": "i:p0"}  # empty junction: hold (fixed would switch)
    assert ctl.decide(obs(t=44.0, in_phase=40, veh={"a_e": 20})) == {"i": "i:p1"}  # busy approach: serve it


@pytest.mark.parametrize("fake", ["invalid"])
def test_invalid_key_is_immediate_unavailable_with_adaptive_fallback(fake):
    ctl = supervisor(client(fake=fake))
    call(ctl, obs())
    assert ctl.state == "ai_unavailable" and ctl.fallback_reason == "invalid_key"
    assert ctl.message() == "AI unavailable. Using adaptive fallback."
    assert ctl.effective_controller == "max_pressure"


def test_session_call_cap_switches_to_adaptive_without_probing():
    c = client(lambda r: gemini_ok([]))
    ctl = supervisor(c, max_calls=3)
    for k in range(3):
        assert ctl.due_request(k * 100.0)
        call(ctl, obs(t=k * 15.0), now=k * 100.0)
    assert ctl.calls_made == 3 and ctl.state == "ai_active"
    ctl.decide(obs(t=60.0))
    assert not ctl.due_request(1000.0)  # 4th call refused -> quota fallback
    st = ctl.status()
    assert st.state == "ai_limit_reached" and st.message == "Live AI quota reached. Using adaptive fallback."
    assert ctl.fallback_reason == "session_cap" and ctl.effective_controller == "max_pressure"
    assert not any(ctl.due_probe(now) for now in (2000.0, 5000.0, 9000.0))  # no probing after the session cap


def test_fixed_timers_only_as_last_resort(monkeypatch):
    ctl = supervisor(client(fake="limit"))
    call(ctl, obs(t=10.0))
    assert ctl.effective_controller == "max_pressure"

    def broken(_obs):
        raise RuntimeError("adaptive controller crashed")

    monkeypatch.setattr(ctl.adaptive, "decide", broken)
    assert ctl.decide(obs(t=11.0, in_phase=40)) == {"i": "i:p1"}  # fresh FixedController takes over
    assert ctl.effective_controller == "fixed" and "last resort" in ctl.message()


def test_three_consecutive_failures_rule():
    ctl = supervisor(client(fake="timeout"))
    call(ctl, obs(t=6))
    assert ctl.state == "ai_active" and ctl.consecutive_failures == 1  # one failure is NOT a trigger
    call(ctl, obs(t=12))
    assert ctl.state == "ai_active" and ctl.consecutive_failures == 2
    call(ctl, obs(t=18))
    assert ctl.state == "ai_unavailable" and ctl.fallback_reason == "failures"


def test_success_resets_failure_counter():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] <= 2:  # first plan() = 2 attempts (retry) -> one failed call
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return gemini_ok([])

    ctl = supervisor(client(handler))
    call(ctl, obs())
    assert ctl.consecutive_failures == 1
    call(ctl, obs())
    assert ctl.consecutive_failures == 0 and ctl.state == "ai_active"


def test_recovery_probe_resumes_after_one_success():
    c = client(fake="limit")
    ctl = supervisor(c, probe_interval_s=60)
    assert ctl.due_request(0.0)
    call(ctl, obs(t=10), now=0.0)
    assert ctl.state == "ai_limit_reached"
    assert not ctl.due_request(10.0) and not ctl.due_probe(30.0)  # no normal calls, probe after 60 s
    assert ctl.due_probe(60.0)
    call(ctl, obs(t=70), now=60.0)  # probe still failing
    assert ctl.state == "ai_limit_reached" and not ctl.due_probe(100.0) and ctl.due_probe(120.0)
    c.fake_fail = None
    c.transport = httpx.MockTransport(lambda r: gemini_ok([{"intersection_id": "i", "phase": "i:p1",
                                                             "hold_s": 20, "reason": "resume"}]))
    ctl.decide(obs(t=130))
    call(ctl, obs(t=130), now=120.0)
    assert ctl.state == "ai_active" and ctl.effective_controller == "gemini+max_pressure"
    assert any("AI resumed" in e.text for e in ctl.drain_explanations())


def test_daily_cap_no_auto_resume_until_reset(tmp_path):
    c = client(lambda r: gemini_ok([]), cap=1, tmp=tmp_path)
    ctl = supervisor(c)
    call(ctl, obs(t=6), now=0)  # uses the only call
    call(ctl, obs(t=12), now=6)
    assert ctl.state == "ai_limit_reached" and ctl.fallback_reason == "daily_cap"
    for now in (60, 600, 6000):
        assert not ctl.due_probe(now) and not ctl.due_request(now)  # never probes
        ctl.decide(obs(t=now))
        assert ctl.state == "ai_limit_reached"
    c.usage.reset()  # what POST /api/ai/reset does
    ctl.decide(obs(t=7000))
    assert ctl.state == "ai_active"


# ------------------------------------------------------------------ plan executor
def _with_plan(phase="i:p1", hold=20, t=100.0):
    c = client(lambda r: gemini_ok([{"intersection_id": "i", "phase": phase, "hold_s": hold, "reason": "test plan"}]))
    ctl = supervisor(c)
    ctl.decide(obs(t=t))
    call(ctl, obs(t=t))
    return ctl


def test_plan_applies_then_expires_to_max_pressure():
    ctl = _with_plan(hold=20, t=100)
    assert ctl.decide(obs(t=101)) == {"i": "i:p1"}  # plan overrides max-pressure (junction empty)
    assert ctl.decide(obs(t=119.5)) == {"i": "i:p1"}
    assert ctl.decide(obs(t=120.0)) == {"i": "i:p0"}  # expired -> max-pressure holds current
    assert "i" not in ctl.plans
    assert any(e.text == "test plan" and e.intersection_id == "i" for e in ctl.drain_explanations())


def test_plan_ends_early_when_max_pressure_strongly_disagrees():
    ctl = _with_plan(phase="i:p1", hold=40, t=100)
    mild = {"a_n": int(EARLY_TERMINATION_GAIN) - 2}
    assert ctl.decide(obs(t=105, veh=mild)) == {"i": "i:p1"}  # below threshold: keep plan
    strong = {"a_n": int(EARLY_TERMINATION_GAIN) + 5}
    assert ctl.decide(obs(t=106, veh=strong)) == {"i": "i:p0"}
    assert "i" not in ctl.plans
    assert any("Ended AI plan early" in e.text for e in ctl.drain_explanations())


def test_request_is_async_and_non_blocking():
    """While a slow Gemini call is in flight, decide() keeps answering immediately (max-pressure)."""
    started = asyncio.Event()

    async def slow(req):
        started.set()
        await asyncio.sleep(0.5)
        return gemini_ok([{"intersection_id": "i", "phase": "i:p1", "hold_s": 30, "reason": "late plan"}])

    ctl = supervisor(client(slow))

    async def scenario():
        task = asyncio.create_task(ctl.request(obs(t=10), 0.0))
        await started.wait()
        answers = []
        for k in range(5):  # sim keeps running on max-pressure
            answers.append(ctl.decide(obs(t=10 + k)))
            assert ctl.in_flight and not ctl.due_request(100.0)
            await asyncio.sleep(0.01)
        await task
        return answers

    answers = asyncio.run(scenario())
    assert all(a == {"i": "i:p0"} for a in answers)
    assert ctl.decide(obs(t=20)) == {"i": "i:p1"} and not ctl.in_flight


def test_interval_never_below_minimum():
    ctl = supervisor(client(fake="timeout"), interval_s=1.0)
    assert ctl.interval_s >= 6.0


# ------------------------------------------------------------------ record / replay
def test_record_and_replay_labeling(tmp_path):
    path = tmp_path / "plans.jsonl"
    c = client(lambda r: gemini_ok([{"intersection_id": "i", "phase": "i:p1", "hold_s": 15, "reason": "recorded"}]))
    rec_ctl = supervisor(c, recorder=PlanRecorder(path))
    call(rec_ctl, obs(t=20))
    assert path.exists() and json.loads(path.read_text().splitlines()[0])["seed"] == 1

    replay = PlanReplay(path, seed=1)
    assert len(replay) == 1
    ctl = GeminiSupervisorController(None, seed=1, replay=replay, interval_s=20)
    st = ctl.status()
    assert st.state == "ai_replay" and st.effective_controller == "gemini+max_pressure"
    assert st.message.startswith("AI replay") and st.calls_today == 0
    ctl.decide(obs(t=20))
    call(ctl, obs(t=20))
    assert ctl.decide(obs(t=21)) == {"i": "i:p1"}
    assert PlanReplay(path, seed=2).entries == []  # other seeds don't replay


def test_compact_observation_shape():
    payload = compact_observation(obs(veh={"a_e": 3}))
    ix = payload["intersections"][0]
    assert ix["id"] == "i" and ix["phases"]["i:p1"] == ["a_e", "a_w"]
    assert ix["approaches"]["a_e"] == {"q": 3, "n": 3, "w": 0, "down": 0.0}
    assert len(json.dumps(payload)) < 2000
