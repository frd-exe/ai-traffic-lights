"""Mock backend: schema-valid canned data for every endpoint + a fake 5 Hz WS stream.

    python -m uvicorn backend.mock_server:app --port 8000

Areas, siting and demand use the real code (backend/area, backend/siting, backend/traffic) on the
synthetic grid; only the simulation is faked.

Scenarios (WS query ?scenario=..., POST /api/sim/start?scenario=..., or env MOCK_SCENARIO):
    ai_limit     after ~20 sim-s an `ai` session switches to ai_limit_reached / fixed timers
    ai_replay    after ~20 sim-s an `ai` session switches to ai_replay
The switch time is MOCK_SWITCH_AFTER_S (env, default 20).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from backend.api_common import ApiError, install_error_handlers
from backend.contract.constants import (
    ALL_RED_S,
    BASE_LEVEL,
    FIXED_PHASE_S,
    GEMINI_MIN_INTERVAL_S,
    GRID_AREA_ID,
    LEVEL_FLOW_VEH_PER_H,
    WS_HZ,
    YELLOW_S,
)
from backend.contract.models import (
    AiResetResponse,
    AreaRequest,
    AreaResponse,
    ControllerStatus,
    DemandProfile,
    DemandResolveRequest,
    DemandResolveResponse,
    EffectiveController,
    ErrorDetail,
    ErrorResponse,
    Explanation,
    GeocodeResponse,
    GeocodeResult,
    HealthResponse,
    Intersection,
    Metrics,
    MetricsResponse,
    Phase,
    SignalColor,
    SignalState,
    SimDemandRequest,
    SimDemandResponse,
    SimStartRequest,
    SimStartResponse,
    SimStopRequest,
    SimStopResponse,
    SimTick,
    Vehicle,
)
from backend.roadnet.geo import bearing_deg
from backend.roadnet.intersections import build_intersections
from backend.siting.prefilter import rank
from backend.traffic.demand import DemandStore, derive_profile, resolve, simulated_data_status, total_flow

log = logging.getLogger("mock_server")
Scenario = Literal["none", "ai_limit", "ai_replay"]
SCENARIOS = ("none", "ai_limit", "ai_replay")
GEMINI_DAILY_CAP = 500


def switch_after_s() -> float:
    return float(os.environ.get("MOCK_SWITCH_AFTER_S", "20"))


def resolve_scenario(explicit: str | None) -> Scenario:
    s = explicit or os.environ.get("MOCK_SCENARIO") or "none"
    if s not in SCENARIOS:
        raise ApiError(400, "unknown_scenario", f"scenario must be one of {SCENARIOS}")
    return s  # type: ignore[return-value]


# ------------------------------------------------------------------ canned area (real code on the grid)
def _load_grid_area() -> AreaResponse:
    from pathlib import Path

    from backend.contract.models import RoadNetwork

    net = RoadNetwork.model_validate_json(
        (Path(__file__).resolve().parent / "data" / "grid_network.json").read_text("utf-8"))
    ranked, recommended = rank(net, build_intersections(net))
    return AreaResponse(area_id=GRID_AREA_ID, source="synthetic_grid", network=net,
                        intersections=ranked, recommended_ids=recommended)


def phases_for(ix: Intersection) -> list[Phase]:
    """Mock of backend/sim/phases.py: north-south vs east-west."""
    ns = [a.id for a in ix.approaches if a.bearing < 45 or 135 <= a.bearing < 225 or a.bearing >= 315]
    ew = [a.id for a in ix.approaches if a.id not in ns]
    return [Phase(id=f"{ix.id}:p0", approach_ids=ns), Phase(id=f"{ix.id}:p1", approach_ids=ew)]


AREA = _load_grid_area()
NETWORK = AREA.network
INTERSECTIONS = AREA.intersections
INTERSECTIONS_BY_ID = {i.id: i for i in INTERSECTIONS}
EDGES = NETWORK.edges


# ------------------------------------------------------------------ state
@dataclass
class Session:
    session_id: str
    req: SimStartRequest
    scenario: Scenario
    started: float = field(default_factory=time.monotonic)
    stopped_at_t: float | None = None
    demand_changes: list[tuple[float, DemandProfile]] = field(default_factory=list)  # (at_t, profile)

    def t(self) -> float:
        if self.stopped_at_t is not None:
            return self.stopped_at_t
        return round((time.monotonic() - self.started) * self.req.speed, 2)


AREAS: dict[str, AreaResponse] = {}
DEMAND = DemandStore()  # memory only in the mock
SESSIONS: dict[str, Session] = {}
COUNTERS = {"gemini_calls_today": 37}


def _stable_frac(key: str) -> float:
    return int(hashlib.sha1(key.encode()).hexdigest()[:6], 16) / 0xFFFFFF


def profile_at(s: Session, t: float) -> DemandProfile:
    current = DEMAND.get(s.req.demand_profile_id)
    for at_t, p in s.demand_changes:
        if at_t <= t:
            current = p
    return current


# ------------------------------------------------------------------ fake dynamics
def _switched(s: Session, t: float, scenario: Scenario) -> bool:
    return s.scenario == scenario and t >= switch_after_s()


def controller_status(s: Session, t: float) -> ControllerStatus:
    mode = s.req.mode
    calls = COUNTERS["gemini_calls_today"]
    if mode != "ai":
        eff: EffectiveController = mode  # type: ignore[assignment]
        names = {"fixed": f"fixed timers ({FIXED_PHASE_S:.0f} s per phase)", "webster": "Webster timing",
                 "max_pressure": "max-pressure"}
        return ControllerStatus(state="traditional", effective_controller=eff,
                                message=f"Traditional controller: {names[mode]}", since_t=0.0,
                                calls_last_min=0, calls_today=calls, daily_cap=GEMINI_DAILY_CAP)
    sw = switch_after_s()
    if _switched(s, t, "ai_limit"):
        return ControllerStatus(
            state="ai_limit_reached", effective_controller="fixed",
            message=f"AI limit reached: signals reverted to traditional fixed timers (since t={sw:.0f} s)",
            since_t=sw, calls_last_min=0, calls_today=GEMINI_DAILY_CAP, daily_cap=GEMINI_DAILY_CAP)
    if _switched(s, t, "ai_replay"):
        return ControllerStatus(
            state="ai_replay", effective_controller="gemini+max_pressure",
            message="AI replay: replaying recorded Gemini plans (no live calls)",
            since_t=sw, calls_last_min=0, calls_today=calls, daily_cap=GEMINI_DAILY_CAP)
    made = int(t // GEMINI_MIN_INTERVAL_S)
    return ControllerStatus(
        state="ai_active", effective_controller="gemini+max_pressure",
        message="AI active: Gemini supervisor + max-pressure", since_t=0.0,
        calls_last_min=min(made, 10), calls_today=calls + made, daily_cap=GEMINI_DAILY_CAP)


def effective(s: Session, t: float) -> EffectiveController:
    return controller_status(s, t).effective_controller


def metrics(s: Session, t: float) -> Metrics:
    eff = effective(s, t)
    base = {"gemini+max_pressure": 14.0, "max_pressure": 17.0, "webster": 21.0, "fixed": 26.0}[eff]
    p = profile_at(s, t)
    load = total_flow(p) / (LEVEL_FLOW_VEH_PER_H[BASE_LEVEL] * max(1, len(p.entries)))  # 1.0 = medium
    base *= 0.5 + 0.5 * load ** 1.5  # congestion grows faster than demand
    wobble = 1 + 0.08 * math.sin(t / 7 + s.req.seed)
    ramp = min(1.0, t / 30) if t > 0 else 0.0
    return Metrics(
        mode=s.req.mode, effective_controller=eff,
        trip_delay_s=round(base * 1.4 * wobble * ramp, 2), avg_wait_s=round(base * wobble * ramp, 2),
        avg_queue=round(base / 6 * wobble * ramp, 2),
        throughput_per_min=round(max(0.0, 30 * load - base / 4 + 2 * wobble), 2) if t > 5 else 0.0,
        blocked_spawns=int(t // 90) if eff == "fixed" else 0, deadlocks=0, t=t,
        population=int((40 + 10 * wobble) * load))


def signals(s: Session, t: float) -> list[SignalState]:
    cycle = 2 * (FIXED_PHASE_S + YELLOW_S + ALL_RED_S)
    half = cycle / 2
    out = []
    for iid in s.req.selected_intersections:
        ix = INTERSECTIONS_BY_ID[iid]
        ph = phases_for(ix)
        offset = 0.0 if effective(s, t) == "fixed" else _stable_frac(iid) * cycle
        tc = (t + offset) % cycle
        k, tin = int(tc // half), tc % half
        cur, nxt = ph[k], ph[1 - k]
        colors: dict[str, SignalColor] = {a.id: "red" for a in ix.approaches}
        if tin < FIXED_PHASE_S:
            colors.update({a: "green" for a in cur.approach_ids})
            out.append(SignalState(intersection_id=iid, phase_id=cur.id, color_per_approach=colors,
                                   time_in_phase_s=round(tin, 2), is_transition=False))
        else:
            if tin < FIXED_PHASE_S + YELLOW_S:
                colors.update({a: "yellow" for a in cur.approach_ids})
            out.append(SignalState(intersection_id=iid, phase_id=nxt.id, color_per_approach=colors,
                                   time_in_phase_s=round(tin - FIXED_PHASE_S, 2), is_transition=True))
    return out


def vehicles(s: Session, t: float) -> list[Vehicle]:
    p = profile_at(s, t)
    n = max(5, min(150, int(40 * total_flow(p) / (LEVEL_FLOW_VEH_PER_H[BASE_LEVEL] * max(1, len(p.entries))))))
    out = []
    for i in range(n):
        e = EDGES[(i * 7 + s.req.seed) % len(EDGES)]
        speed = max(0.0, e.speed_limit * (0.55 + 0.45 * math.sin(t / 5 + i)))
        offset = (i * 37.0 + t * e.speed_limit * 0.6) % e.length_m
        (lon1, lat1), (lon2, lat2) = e.geometry[0], e.geometry[-1]
        f = offset / e.length_m
        out.append(Vehicle(id=f"v{i}", edge_id=e.id, offset_m=round(offset, 2),
                           lat=lat1 + (lat2 - lat1) * f, lon=lon1 + (lon2 - lon1) * f,
                           heading=round(bearing_deg(lat1, lon1, lat2, lon2), 1) % 360, speed=round(speed, 2)))
    return out


def explanations_between(s: Session, t0: float, t1: float) -> list[Explanation]:
    """Deterministic explanations with t in (t0, t1]."""
    out: list[Explanation] = []
    sw = switch_after_s()
    if s.req.mode == "ai":
        k0, k1 = int(t0 // GEMINI_MIN_INTERVAL_S), int(t1 // GEMINI_MIN_INTERVAL_S)
        for k in range(k0 + 1, k1 + 1):
            te = k * GEMINI_MIN_INTERVAL_S
            if effective(s, te) != "gemini+max_pressure" or not s.req.selected_intersections:
                continue
            iid = s.req.selected_intersections[k % len(s.req.selected_intersections)]
            q1, q2 = 3 + k % 6, 1 + k % 3
            out.append(Explanation(t=te, intersection_id=iid,
                                   text=f"Holding phase p{k % 2} green: busy approach queue {q1} vs {q2} on cross street"))
    if t0 < sw <= t1 and s.req.mode == "ai":
        if s.scenario == "ai_limit":
            out.append(Explanation(t=sw, intersection_id=None,
                                   text="AI limit reached: signals reverted to traditional fixed timers"))
        elif s.scenario == "ai_replay":
            out.append(Explanation(t=sw, intersection_id=None, text="Switched to AI replay (recorded plans)"))
    for at_t, p in s.demand_changes:
        if t0 < at_t <= t1:
            out.append(Explanation(t=at_t, intersection_id=None,
                                   text=f"Demand changed: level {p.level}, multiplier {p.multiplier:g}"))
    return out


def build_tick(s: Session, t: float, since_t: float) -> SimTick:
    return SimTick(session_id=s.session_id, t=t, vehicles=vehicles(s, t), signals=signals(s, t),
                   metrics=metrics(s, t), controller_status=controller_status(s, t),
                   data_status=simulated_data_status(), explanations=explanations_between(s, since_t, t))


# ------------------------------------------------------------------ app
app = FastAPI(title="AI traffic lights - mock backend")
install_error_handlers(app)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", mock=True)


@app.post("/api/area", response_model=AreaResponse)
def post_area(req: AreaRequest) -> AreaResponse:
    # The mock always serves the synthetic grid (as the real backend does without an OSM sample).
    AREAS[AREA.area_id] = AREA
    return AREA


@app.get("/api/demo-area", response_model=AreaResponse)
def demo_area() -> AreaResponse:
    AREAS[AREA.area_id] = AREA
    return AREA


@app.get("/api/area/{area_id}", response_model=AreaResponse)
def get_area(area_id: str) -> AreaResponse:
    if area_id != AREA.area_id:
        raise ApiError(404, "unknown_area", f"area {area_id} not found")
    return AREA


@app.get("/api/geocode", response_model=GeocodeResponse)
def geocode(q: str = Query(min_length=1)) -> GeocodeResponse:
    return GeocodeResponse(results=[
        GeocodeResult(display_name="Demo city (synthetic grid, mock)", lat=41.39, lon=2.165, bbox=NETWORK.bbox),
        GeocodeResult(display_name=f"{q} (mock result)", lat=41.3874, lon=2.1686),
    ])


@app.post("/api/demand/resolve", response_model=DemandResolveResponse)
def resolve_demand(req: DemandResolveRequest) -> DemandResolveResponse:
    if req.area_id not in AREAS and req.area_id != AREA.area_id:
        raise ApiError(404, "unknown_area", f"area {req.area_id} not found; POST /api/area first")
    profile = resolve(req, list(NETWORK.entry_nodes), DEMAND)
    return DemandResolveResponse(demand_profile=profile, data_status=simulated_data_status())


@app.get("/api/demand/{profile_id}", response_model=DemandProfile)
def get_demand(profile_id: str) -> DemandProfile:
    return DEMAND.get(profile_id)


@app.post("/api/sim/start", response_model=SimStartResponse)
def sim_start(req: SimStartRequest, scenario: str | None = None) -> SimStartResponse:
    if req.area_id != AREA.area_id:
        raise ApiError(404, "unknown_area", f"area {req.area_id} not found")
    DEMAND.get(req.demand_profile_id)  # 404 if unknown
    bad = [i for i in req.selected_intersections if i not in INTERSECTIONS_BY_ID]
    if bad:
        raise ApiError(400, "unknown_intersection", "selected_intersections contains unknown ids", {"ids": bad})
    sid = "s_" + uuid.uuid4().hex[:8]
    SESSIONS[sid] = Session(session_id=sid, req=req, scenario=resolve_scenario(scenario))
    log.info("session %s started mode=%s scenario=%s", sid, req.mode, SESSIONS[sid].scenario)
    return SimStartResponse(session_id=sid)


def _session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise ApiError(404, "unknown_session", f"session {sid} not found")
    return s


@app.post("/api/sim/demand", response_model=SimDemandResponse)
def sim_demand(req: SimDemandRequest) -> SimDemandResponse:
    s = _session(req.session_id)
    if s.stopped_at_t is not None:
        raise ApiError(409, "session_stopped", f"session {s.session_id} is stopped")
    now = s.t()
    at_t = now if req.at_t is None else req.at_t
    if at_t < now:
        raise ApiError(400, "at_t_in_past", f"at_t={at_t} is before the current sim time t={now}",
                       {"t": now})
    parent = profile_at(s, at_t)  # changes build on whatever is active at at_t
    child = DEMAND.put(derive_profile(parent, list(NETWORK.entry_nodes), level=req.level,
                                      multiplier=req.multiplier, entry_overrides=req.entry_overrides))
    s.demand_changes.append((at_t, child))
    s.demand_changes.sort(key=lambda x: x[0])
    return SimDemandResponse(session_id=s.session_id, demand_profile=child, applies_at_t=at_t)


@app.post("/api/sim/stop", response_model=SimStopResponse)
def sim_stop(req: SimStopRequest) -> SimStopResponse:
    s = _session(req.session_id)
    if s.stopped_at_t is None:
        s.stopped_at_t = s.t()
    return SimStopResponse(session_id=s.session_id, stopped=True, final_metrics=metrics(s, s.t()))


@app.get("/api/metrics", response_model=MetricsResponse)
def get_metrics(session_id: str) -> MetricsResponse:
    s = _session(session_id)
    t = s.t()
    return MetricsResponse(session_id=session_id, t=t, metrics=metrics(s, t))


@app.post("/api/ai/reset", response_model=AiResetResponse)
def ai_reset() -> AiResetResponse:
    COUNTERS["gemini_calls_today"] = 0
    return AiResetResponse(reset=True, message="AI re-enabled; daily counter reset (mock)",
                           calls_today=0, daily_cap=GEMINI_DAILY_CAP)


@app.websocket("/ws/sim")
async def ws_sim(ws: WebSocket, session_id: str, scenario: str | None = None) -> None:
    await ws.accept()
    s = SESSIONS.get(session_id)
    if s is None:
        await ws.send_text(json.dumps(ErrorResponse(error=ErrorDetail(
            code="unknown_session", message=f"session {session_id} not found")).model_dump(mode="json")))
        await ws.close(code=4404)
        return
    if scenario:
        try:
            s.scenario = resolve_scenario(scenario)
        except ApiError as e:
            await ws.send_text(json.dumps(ErrorResponse(error=ErrorDetail(code=e.code, message=e.message)).model_dump(mode="json")))
            await ws.close(code=4400)
            return

    latest: dict[str, float] = {}
    ready = asyncio.Event()

    async def producer() -> None:  # samples sim time at WS_HZ; overwrites the slot (latest only)
        while True:
            latest["t"] = s.t()
            ready.set()
            await asyncio.sleep(1 / WS_HZ)

    async def sender() -> None:
        last_sent = -1.0
        while True:
            await ready.wait()
            ready.clear()
            t = latest["t"]
            if t <= last_sent and s.stopped_at_t is not None:
                await ws.close(code=1000)
                return
            await ws.send_text(build_tick(s, t, last_sent).model_dump_json())
            last_sent = t

    async def receiver() -> None:
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(c()) for c in (producer, sender, receiver)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
