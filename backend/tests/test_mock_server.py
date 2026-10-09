"""Every mock endpoint must validate against the contract models."""

import json

import pytest
from fastapi.testclient import TestClient

import backend.mock_server as mock
from backend.contract.models import (
    AiResetResponse,
    AreaResponse,
    DemandResolveResponse,
    ErrorResponse,
    GeocodeResponse,
    HealthResponse,
    MetricsResponse,
    SimStartResponse,
    SimStopResponse,
    SimTick,
)

BBOX = {"west": 2.16, "south": 41.385, "east": 2.17, "north": 41.395}


@pytest.fixture()
def client():
    return TestClient(mock.app)


def _setup(client, mode="ai", source="baseline", scenario=None, speed=1.0):
    area = AreaResponse.model_validate(client.post("/api/area", json={"bbox": BBOX}).json())
    dem = client.post("/api/demand/resolve", json={"area_id": area.area_id, "source": source, "level": "high"})
    dem = DemandResolveResponse.model_validate(dem.json())
    q = f"?scenario={scenario}" if scenario else ""
    r = client.post(f"/api/sim/start{q}", json={
        "area_id": area.area_id, "mode": mode, "demand_profile_id": dem.demand_profile.id, "seed": 7,
        "selected_intersections": area.recommended_ids, "speed": speed})
    assert r.status_code == 200, r.text
    return area, dem, SimStartResponse.model_validate(r.json()).session_id


def test_health(client):
    HealthResponse.model_validate(client.get("/api/health").json())


def test_area(client):
    area = AreaResponse.model_validate(client.post("/api/area", json={"bbox": BBOX}).json())
    assert len(area.intersections) == 9 and len(area.recommended_ids) == 3
    scores = [i.structural_score for i in area.intersections]
    assert scores == sorted(scores, reverse=True)
    again = client.post("/api/area", json={"bbox": BBOX, "ignore_osm_signals": True}).json()
    assert again["area_id"] == area.area_id


def test_geocode(client):
    GeocodeResponse.model_validate(client.get("/api/geocode", params={"q": "Barcelona"}).json())


@pytest.mark.parametrize("source,expected", [
    ("baseline", "baseline_only"), ("google_live", "google_live"), ("google_snapshot", "google_snapshot")])
def test_demand_resolve(client, source, expected):
    aid = client.post("/api/area", json={"bbox": BBOX}).json()["area_id"]
    r = DemandResolveResponse.model_validate(
        client.post("/api/demand/resolve", json={"area_id": aid, "source": source}).json())
    assert r.demand_profile.source == expected and r.data_status.state == expected


def test_demand_resolve_google_down(client):
    aid = client.post("/api/area", json={"bbox": BBOX}).json()["area_id"]
    r = client.post("/api/demand/resolve?scenario=google_down", json={"area_id": aid, "source": "google_live"})
    assert DemandResolveResponse.model_validate(r.json()).data_status.state == "google_snapshot"


def test_sim_lifecycle(client):
    _, _, sid = _setup(client)
    MetricsResponse.model_validate(client.get("/api/metrics", params={"session_id": sid}).json())
    stop = SimStopResponse.model_validate(client.post("/api/sim/stop", json={"session_id": sid}).json())
    assert stop.stopped
    AiResetResponse.model_validate(client.post("/api/ai/reset").json())


def test_errors_use_contract_format(client):
    for r in (
        client.get("/api/metrics", params={"session_id": "nope"}),
        client.post("/api/area", json={"bbox": {"west": 3, "south": 1, "east": 2, "north": 2}}),
        client.post("/api/sim/start", json={"area_id": "x"}),
        client.get("/api/does-not-exist"),
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
            assert len(ticks[0].signals) == 3 and ticks[0].vehicles
            assert ticks[0].t <= ticks[-1].t


def test_ws_unknown_session(client):
    with client.websocket_connect("/ws/sim?session_id=nope") as w:
        ErrorResponse.model_validate(json.loads(w.receive_text()))


@pytest.mark.parametrize("scenario,check", [
    ("ai_limit", lambda t: t.controller_status.state == "ai_limit_reached"
        and t.controller_status.effective_controller == "fixed"
        and t.metrics.effective_controller == "fixed"),
    ("ai_replay", lambda t: t.controller_status.state == "ai_replay"),
    ("google_down", lambda t: t.data_status.state == "google_snapshot"),
])
def test_ws_scenarios_switch(client, monkeypatch, scenario, check):
    monkeypatch.setenv("MOCK_SWITCH_AFTER_S", "3")
    _, _, sid = _setup(client, source="google_live", speed=10)
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
