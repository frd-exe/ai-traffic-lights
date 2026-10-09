"""Real backend wiring: /api/sim/*, /api/metrics, /ws/sim, /api/ai/reset, refine. No network."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.contract.constants import GRID_AREA_ID
from backend.contract.models import (
    AiResetResponse,
    AreaResponse,
    DemandResolveResponse,
    ErrorResponse,
    MetricsResponse,
    SimDemandResponse,
    SimStartResponse,
    SimStopResponse,
    SimTick,
    SitingResult,
)
from backend.roadnet import sim_siting
from backend.tests import osm_fixture as fx


@pytest.fixture()
def app_client(tmp_path):
    def make(env=None, sample=False):
        sample_path = tmp_path / "sample_area.json"
        if sample:
            sample_path.write_text(json.dumps(fx.wrapped()), "utf-8")
        settings = Settings(state_dir=tmp_path / "state", sample_path=sample_path, env=env or {},
                            siting_committed_dir=tmp_path / "committed", siting_workers=1)
        return TestClient(create_app(settings))
    return make


def _start(c, mode, profile_id, area, speed=20.0, seed=7):
    r = c.post("/api/sim/start", json={"area_id": area.area_id, "mode": mode, "demand_profile_id": profile_id,
                                       "seed": seed, "selected_intersections": area.recommended_ids, "speed": speed})
    assert r.status_code == 200, r.text
    return SimStartResponse.model_validate(r.json()).session_id


def _setup(c, level="medium"):
    area = AreaResponse.model_validate(c.get("/api/demo-area").json())
    dem = DemandResolveResponse.model_validate(c.post("/api/demand/resolve", json={"area_id": area.area_id,
                                                                                    "level": level}).json())
    return area, dem.demand_profile.id


def _ticks(c, sid, until, limit=200):
    with c.websocket_connect(f"/ws/sim?session_id={sid}") as ws:
        for _ in range(limit):
            tick = SimTick.model_validate_json(ws.receive_text())
            yield tick
            if until(tick):
                return
    raise AssertionError("condition never reached")


def test_two_concurrent_sessions_stream_ticks(app_client):
    with app_client() as c:
        area, pid = _setup(c)
        s_fixed, s_mp = _start(c, "fixed", pid, area), _start(c, "max_pressure", pid, area)
        for sid, mode in ((s_fixed, "fixed"), (s_mp, "max_pressure")):
            ticks = list(_ticks(c, sid, lambda t: t.t > 20))
            last = ticks[-1]
            assert last.session_id == sid and last.metrics.mode == mode
            assert last.controller_status.state == "traditional"
            assert last.data_status.state == "baseline_only" and last.data_status.message == "Simulated demand"
            assert {s.intersection_id for s in last.signals} == set(area.recommended_ids)
            assert ticks[0].t <= last.t
        m = MetricsResponse.model_validate(c.get("/api/metrics", params={"session_id": s_fixed}).json())
        assert m.metrics.mode == "fixed" and m.t > 0
        for sid in (s_fixed, s_mp):
            stop = SimStopResponse.model_validate(c.post("/api/sim/stop", json={"session_id": sid}).json())
            assert stop.stopped and stop.final_metrics is not None


def test_live_demand_change_same_at_t_keeps_schedules_identical(app_client):
    with app_client() as c:
        area, pid = _setup(c)
        sids = [_start(c, "fixed", pid, area), _start(c, "max_pressure", pid, area)]
        list(_ticks(c, sids[0], lambda t: t.t > 5))
        at_t = 40.0
        for sid in sids:
            r = c.post("/api/sim/demand", json={"session_id": sid, "level": "rush", "multiplier": 1.5, "at_t": at_t})
            res = SimDemandResponse.model_validate(r.json())
            assert res.applies_at_t == at_t and res.demand_profile.parent_id == pid
        for sid in sids:
            notes = [e.text for t in _ticks(c, sid, lambda t: t.t > 70) for e in t.explanations]
            assert any("Demand changed" in n for n in notes)
        mgr = c.app.state.sessions
        for sid in sids:
            c.post("/api/sim/stop", json={"session_id": sid})
        e1, e2 = (mgr.sessions[s].engine for s in sids)
        horizon = min(e1.demand.horizon, e2.demand.horizon)
        cut = lambda e: [a for a in e.demand.schedule if a.time <= horizon]  # noqa: E731
        assert cut(e1) == cut(e2) and len(cut(e1)) > 20
        assert e1.demand.applied_changes == e2.demand.applied_changes
        assert e1.demand.rates == e2.demand.rates
        past = c.post("/api/sim/demand", json={"session_id": sids[0], "multiplier": 1.0, "at_t": 1})
        assert past.status_code == 409  # stopped session
        r = c.post("/api/sim/demand", json={"session_id": "s_nope", "multiplier": 1.0})
        assert r.status_code == 404 and ErrorResponse.model_validate(r.json()).error.code == "unknown_session"


def test_demand_change_in_the_past_is_rejected(app_client):
    with app_client() as c:
        area, pid = _setup(c)
        sid = _start(c, "fixed", pid, area)
        list(_ticks(c, sid, lambda t: t.t > 10))
        r = c.post("/api/sim/demand", json={"session_id": sid, "multiplier": 2.0, "at_t": 1.0})
        assert r.status_code == 400 and ErrorResponse.model_validate(r.json()).error.code == "at_t_in_past"


@pytest.mark.parametrize("fake,state", [("limit", "ai_limit_reached"), ("invalid", "ai_unavailable"),
                                        ("timeout", "ai_unavailable")])
def test_ai_session_fallback_through_real_wiring(app_client, fake, state):
    env = {"GEMINI_FAKE_FAIL": fake, "GEMINI_MIN_INTERVAL_S": "0.05", "GEMINI_RPM": "0"}
    with app_client(env) as c:
        area, pid = _setup(c)
        sid = _start(c, "ai", pid, area, speed=10)
        ctl = c.app.state.sessions.sessions[sid].controller
        ctl.interval_s = 0.05  # test speed-up: 3 failed calls quickly for the timeout case
        notices = []
        for tick in _ticks(c, sid, lambda t: t.controller_status.state == state, limit=400):
            notices += [e for e in tick.explanations if e.intersection_id is None]
        cs = tick.controller_status
        assert cs.state == state and cs.effective_controller == "fixed" and cs.since_t >= 0
        assert cs.message.startswith(("AI limit reached", "AI unavailable"))
        assert tick.metrics.mode == "ai" and tick.metrics.effective_controller == "fixed"
        assert notices and "signals reverted to traditional fixed timers" in notices[-1].text


def test_ai_reset_endpoint_resumes_daily_cap_sessions(app_client):
    env = {"GEMINI_FAKE_FAIL": "limit", "GEMINI_DAILY_CAP": "1"}
    with app_client(env) as c:
        gem = c.app.state.gemini
        gem.usage.increment()  # budget exhausted
        r = AiResetResponse.model_validate(c.post("/api/ai/reset").json())
        assert r.reset and r.calls_today == 0 and r.daily_cap == 1
        assert gem.usage.allowed()


def test_start_validation(app_client):
    with app_client() as c:
        area, pid = _setup(c)
        bad = c.post("/api/sim/start", json={"area_id": area.area_id, "mode": "fixed", "demand_profile_id": pid,
                                             "seed": 1, "selected_intersections": ["i_nope"], "speed": 1})
        assert bad.status_code == 400 and ErrorResponse.model_validate(bad.json()).error.code == "unknown_intersection"
        nop = c.post("/api/sim/start", json={"area_id": area.area_id, "mode": "fixed", "demand_profile_id": "dp_x",
                                             "seed": 1, "selected_intersections": [], "speed": 1})
        assert nop.status_code == 404


# ------------------------------------------------------------------ siting
def test_run_siting_on_grid_in_process(tmp_path):
    from backend.app import Settings as S
    from backend.area.service import AreaService

    s = S()
    area = AreaService(s.grid_path, tmp_path / "missing.json", tmp_path / "areas").demo_area()[0]
    res = sim_siting.run_siting(area, seed=1, level="high", max_candidates=3, seconds=60, workers=1)
    assert res.status == "done" and res.candidates == [i.id for i in area.intersections[:3]]
    assert set(res.order) <= set(res.candidates) and set(res.gains) == set(res.candidates)
    assert res.baseline_wait_s is not None and res.runtime_s is not None
    applied = sim_siting.apply_to_area(area, res)
    assert {i.id: i.sim_gain_s for i in applied.intersections if i.id in res.gains} == res.gains
    if res.order:
        assert applied.recommended_ids == res.order


def test_committed_demo_siting_is_served(app_client):
    with app_client() as c:  # committed dir is empty in this fixture -> nothing applied
        assert all(i.sim_gain_s is None for i in AreaResponse.model_validate(c.get("/api/demo-area").json()).intersections)
    committed = sim_siting.SitingCache(sim_siting.DATA_DIR, sim_siting.DATA_DIR).get(GRID_AREA_ID, 42, "rush")
    assert committed is not None and committed.status == "done", "run scripts/precompute_siting.py"


def test_refine_returns_immediately_then_finishes(app_client, monkeypatch):
    monkeypatch.setattr(sim_siting, "WARMUP_S", 10.0)
    monkeypatch.setattr(sim_siting, "MEASURE_S", 30.0)
    monkeypatch.setattr(sim_siting, "MAX_CANDIDATES", 2)
    with app_client(sample=True) as c:
        area = AreaResponse.model_validate(c.post("/api/area", json={"bbox": fx.BBOX}).json())
        assert area.source == "osm"
        missing = c.get(f"/api/area/{area.area_id}/refine")
        assert missing.status_code == 404
        t0 = time.perf_counter()
        first = SitingResult.model_validate(c.post(f"/api/area/{area.area_id}/refine").json())
        assert first.status == "running" and time.perf_counter() - t0 < 2.0  # structural answer is immediate
        for _ in range(300):
            res = SitingResult.model_validate(c.get(f"/api/area/{area.area_id}/refine").json())
            if res.status != "running":
                break
            time.sleep(0.1)
        assert res.status == "done", res.message
        refined = AreaResponse.model_validate(c.get(f"/api/area/{area.area_id}").json())
        assert any(i.sim_gain_s is not None for i in refined.intersections)
        again = SitingResult.model_validate(c.post(f"/api/area/{area.area_id}/refine").json())
        assert again.status == "done"  # cached by (area_id, seed)
