"""Every mock endpoint must validate against the contract models."""

import json

import pytest
from fastapi.testclient import TestClient

import backend.mock_server as mock
from backend.contract.constants import GRID_AREA_ID
from backend.contract.models import (
    AiResetResponse,
    AreaResponse,
    DemandProfile,
    DemandResolveResponse,
    ErrorResponse,
    GeocodeResponse,
    HealthResponse,
    MetricsResponse,
    SimDemandResponse,
    SimStartResponse,
    SimStopResponse,
    SimTick,
)

BBOX = {"west": 2.16, "south": 41.385, "east": 2.17, "north": 41.395}


@pytest.fixture()
def client():
    return TestClient(mock.app)


def _setup(client, mode="ai", level="high", scenario=None, speed=1.0, multiplier=1.0):
    area = AreaResponse.model_validate(client.post("/api/area", json={"bbox": BBOX}).json())
    dem = client.post("/api/demand/resolve", json={"area_id": area.area_id, "level": level, "multiplier": multiplier})
    dem = DemandResolveResponse.model_validate(dem.json())
    q = f"?scenario={scenario}" if scenario else ""
    r = client.post(f"/api/sim/start{q}", json={
        "area_id": area.area_id, "mode": mode, "demand_profile_id": dem.demand_profile.id, "seed": 7,
        "selected_intersections": area.recommended_ids, "speed": speed})
    assert r.status_code == 200, r.text
    return area, dem, SimStartResponse.model_validate(r.json()).session_id


def test_health(client):
    HealthResponse.model_validate(client.get("/api/health").json())


def test_area_is_synthetic_grid(client):
    area = AreaResponse.model_validate(client.post("/api/area", json={"bbox": BBOX}).json())
    assert area.source == "synthetic_grid" and area.area_id == GRID_AREA_ID
    assert len(area.intersections) == 9 and len(area.recommended_ids) == 3
    scores = [i.structural_score for i in area.intersections]
    assert scores == sorted(scores, reverse=True)
    for path in ("/api/demo-area", f"/api/area/{GRID_AREA_ID}"):
        again = AreaResponse.model_validate(client.get(path).json())
        assert again == area
    ErrorResponse.model_validate(client.get("/api/area/ar_nope").json())


def test_geocode(client):
    GeocodeResponse.model_validate(client.get("/api/geocode", params={"q": "Barcelona"}).json())


def test_demand_resolve_baseline(client):
    r = DemandResolveResponse.model_validate(
        client.post("/api/demand/resolve", json={"area_id": GRID_AREA_ID, "level": "rush", "multiplier": 1.5}).json())
    assert r.demand_profile.source == "baseline_only" and r.data_status.state == "baseline_only"
    assert r.data_status.message == "Simulated demand"
    assert r.demand_profile.multiplier == 1.5
    got = DemandProfile.model_validate(client.get(f"/api/demand/{r.demand_profile.id}").json())
    assert got == r.demand_profile


@pytest.mark.parametrize("source", ["google_live", "google_snapshot"])
def test_demand_resolve_rejects_google(client, source):
    r = client.post("/api/demand/resolve", json={"area_id": GRID_AREA_ID, "source": source})
    assert r.status_code == 400
    assert ErrorResponse.model_validate(r.json()).error.code == "source_not_supported"


def test_sim_lifecycle(client):
    _, _, sid = _setup(client)
    MetricsResponse.model_validate(client.get("/api/metrics", params={"session_id": sid}).json())
    stop = SimStopResponse.model_validate(client.post("/api/sim/stop", json={"session_id": sid}).json())
    assert stop.stopped
    AiResetResponse.model_validate(client.post("/api/ai/reset").json())


def test_sim_demand_change(client):
    _, dem, sid = _setup(client, level="medium")
    r = client.post("/api/sim/demand", json={"session_id": sid, "level": "rush", "multiplier": 2.0, "at_t": 1000})
    assert r.status_code == 200, r.text
    res = SimDemandResponse.model_validate(r.json())
    assert res.applies_at_t == 1000
    assert res.demand_profile.parent_id == dem.demand_profile.id
    assert res.demand_profile.level == "rush" and res.demand_profile.multiplier == 2.0
    # the original profile is frozen
    orig = DemandProfile.model_validate(client.get(f"/api/demand/{dem.demand_profile.id}").json())
    assert orig == dem.demand_profile
    # in the past -> clear error; unknown session -> 404
    past = client.post("/api/sim/demand", json={"session_id": sid, "multiplier": 1.0, "at_t": 0})
    assert past.status_code == 400 and ErrorResponse.model_validate(past.json()).error.code == "at_t_in_past"
    nope = client.post("/api/sim/demand", json={"session_id": "s_nope", "multiplier": 1.0})
    assert nope.status_code == 404
    bad = client.post("/api/sim/demand", json={"session_id": sid, "multiplier": 9})
    assert bad.status_code == 422


def test_errors_use_contract_format(client):
    for r in (
        client.get("/api/metrics", params={"session_id": "nope"}),
        client.post("/api/area", json={"bbox": {"west": 3, "south": 1, "east": 2, "north": 2}}),
        client.post("/api/sim/start", json={"area_id": "x"}),
        client.get("/api/does-not-exist"),
        client.post("/api/demand/resolve", json={"area_id": GRID_AREA_ID, "entry_overrides": {"n_nope": 2}}),
    ):
        assert r.status_code >= 400
        ErrorResponse.model_validate(r.json())


def test_ws_two_sessions(client):
    _, _, s1 = _setup(client, mode="ai")
    _, _, s2 = _setup(client, mode="fixed")
    with client.websocket_connect(f"/ws/sim?session_id={s1}") as w1, \
         client.websocket_connect(f"/ws/sim?session_id={s2}") as w2:
        for w, sid, state in ((w1, s1, "ai_active"), (w2, s2, "traditional")):
            ticks = [SimTick.model_validate_json(w.receive_text()) for _ in range(3)]
            assert all(t.session_id == sid for t in ticks)
            assert ticks[-1].controller_status.state == state
            assert ticks[-1].data_status.state == "baseline_only"
            assert len(ticks[0].signals) == 3 and ticks[0].vehicles
            assert ticks[0].t <= ticks[-1].t


def test_ws_demand_change_reaches_stream(client, monkeypatch):
    _, _, sid = _setup(client, mode="fixed", level="low", speed=10)
    r = client.post("/api/sim/demand", json={"session_id": sid, "level": "rush", "multiplier": 3.0, "at_t": 3})
    assert r.status_code == 200, r.text
    with client.websocket_connect(f"/ws/sim?session_id={sid}") as w:
        first = SimTick.model_validate_json(w.receive_text())
        seen = False
        for _ in range(30):
            tick = SimTick.model_validate_json(w.receive_text())
            seen |= any("Demand changed" in e.text for e in tick.explanations)
            if tick.t > 4:
                break
        assert seen
        assert len(tick.vehicles) > len(first.vehicles)


def test_ws_unknown_session(client):
    with client.websocket_connect("/ws/sim?session_id=nope") as w:
        ErrorResponse.model_validate(json.loads(w.receive_text()))


@pytest.mark.parametrize("scenario,check", [
    ("ai_limit", lambda t: t.controller_status.state == "ai_limit_reached"
        and t.controller_status.effective_controller == "fixed"
        and t.metrics.effective_controller == "fixed"),
    ("ai_replay", lambda t: t.controller_status.state == "ai_replay"),
])
def test_ws_scenarios_switch(client, monkeypatch, scenario, check):
    monkeypatch.setenv("MOCK_SWITCH_AFTER_S", "3")
    _, _, sid = _setup(client, speed=10)
    with client.websocket_connect(f"/ws/sim?session_id={sid}&scenario={scenario}") as w:
        first = SimTick.model_validate_json(w.receive_text())
        assert not check(first)
        seen_notice = False
        for _ in range(30):
            tick = SimTick.model_validate_json(w.receive_text())
            seen_notice |= any(e.intersection_id is None for e in tick.explanations)
            if check(tick):
                break
        assert check(tick), f"{scenario} never switched (t={tick.t})"
        assert seen_notice, "switch must add a session-wide explanation"


def test_unknown_scenario_rejected(client):
    _, _, sid = _setup(client)
    with client.websocket_connect(f"/ws/sim?session_id={sid}&scenario=google_down") as w:
        assert ErrorResponse.model_validate(json.loads(w.receive_text())).error.code == "unknown_scenario"
