"""Mock backend: schema-valid canned data for every endpoint + a fake 5 Hz WS stream.

    python -m uvicorn backend.mock_server:app --port 8000

Scenarios (WS query ?scenario=..., POST /api/sim/start?scenario=..., or env MOCK_SCENARIO):
    ai_limit     after ~20 sim-s an `ai` session switches to ai_limit_reached / fixed timers
    ai_replay    after ~20 sim-s an `ai` session switches to ai_replay
    google_down  after ~20 sim-s DataStatus switches to google_snapshot; google_live resolves
                 return google_snapshot immediately
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.contract.constants import (
    ALL_RED_S,
    FIXED_PHASE_S,
    GEMINI_MIN_INTERVAL_S,
    LEVEL_FLOW_VEH_PER_H,
    BASE_LEVEL,
    WS_HZ,
    YELLOW_S,
)
from backend.contract.helpers import approach_id, area_id as make_area_id, demand_scale, intersection_id
from backend.contract.models import (
    AiResetResponse,
    Approach,
    AreaRequest,
    AreaResponse,
    ControllerStatus,
    DataStatus,
    DemandEntry,
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
    RoadNetwork,
    SignalColor,
    SignalState,
    SimMode,
    SimStartRequest,
    SimStartResponse,
    SimStopRequest,
    SimStopResponse,
    SimTick,
    Vehicle,
    BBox,
)

log = logging.getLogger("mock_server")
Scenario = Literal["none", "ai_limit", "ai_replay", "google_down"]
SCENARIOS = ("none", "ai_limit", "ai_replay", "google_down")
DATA_DIR = Path(__file__).resolve().parent / "data"
GEMINI_DAILY_CAP = 500
GOOGLE_DAILY_CAP = 200
SNAPSHOT_TIME = datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc)


def switch_after_s() -> float:
    return float(os.environ.get("MOCK_SWITCH_AFTER_S", "20"))


def resolve_scenario(explicit: str | None) -> Scenario:
    s = explicit or os.environ.get("MOCK_SCENARIO") or "none"
    if s not in SCENARIOS:
        raise ApiError(400, "unknown_scenario", f"scenario must be one of {SCENARIOS}")
    return s  # type: ignore[return-value]


# ------------------------------------------------------------------ errors
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def _err(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


# ------------------------------------------------------------------ canned network
def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = lat2 - lat1
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dx, dy)) % 360


def load_network() -> RoadNetwork:
    return RoadNetwork.model_validate_json((DATA_DIR / "grid_network.json").read_text("utf-8"))


def derive_intersections(net: RoadNetwork) -> list[Intersection]:
    """Mock ranking: every non-boundary node is a junction; row 2 (major street) ranks highest."""
    nodes = {n.id: n for n in net.nodes}
    boundary = set(net.entry_nodes) | set(net.exit_nodes)
    out: list[Intersection] = []
    for n in net.nodes:
        if n.id in boundary:
            continue
        iid = intersection_id(n.lat, n.lon)
        approaches = []
        for e in (e for e in net.edges if e.to_node == n.id):
            up_lon, up_lat = e.geometry[-2]
            b = _bearing(n.lat, n.lon, up_lat, up_lon)
            outs = [o.id for o in net.edges if o.from_node == n.id and o.to_node != e.from_node]
            approaches.append(Approach(id=approach_id(iid, b), bearing=round(b, 2), lanes=e.lanes,
                                       in_edge=e.id, out_edges=outs))
        approaches.sort(key=lambda a: a.bearing)
        major = n.id[1] == "2"
        score = (0.95 if n.id == "n22" else 0.85) if major else (0.6 if n.id[2] == "2" else 0.45)
        out.append(Intersection(
            id=iid, node_id=n.id, lat=n.lat, lon=n.lon, approaches=approaches,
            has_signal_in_osm=major, structural_score=score,
            sim_gain_s=round(score * 12, 1) if major else None,
        ))
    out.sort(key=lambda i: (-i.structural_score, i.id))
    return out


def phases_for(ix: Intersection) -> list[Phase]:
    """Mock of backend/sim/phases.py: north-south vs east-west."""
    ns = [a.id for a in ix.approaches if a.bearing < 45 or 135 <= a.bearing < 225 or a.bearing >= 315]
    ew = [a.id for a in ix.approaches if a.id not in ns]
    return [Phase(id=f"{ix.id}:p0", approach_ids=ns), Phase(id=f"{ix.id}:p1", approach_ids=ew)]


NETWORK = load_network()
INTERSECTIONS = derive_intersections(NETWORK)
INTERSECTIONS_BY_ID = {i.id: i for i in INTERSECTIONS}
EDGES = NETWORK.edges


# ------------------------------------------------------------------ state
@dataclass
class Area:
    area_id: str
    bbox: BBox


@dataclass
class Session:
    session_id: str
    req: SimStartRequest
    scenario: Scenario
    started: float = field(default_factory=time.monotonic)
    stopped_at_t: float | None = None

    def t(self) -> float:
        if self.stopped_at_t is not None:
            return self.stopped_at_t
        return round((time.monotonic() - self.started) * self.req.speed, 2)


AREAS: dict[str, Area] = {}
PROFILES: dict[str, DemandProfile] = {}
SESSIONS: dict[str, Session] = {}
COUNTERS = {"gemini_calls_today": 37, "google_calls_today": 4}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stable_frac(key: str) -> float:
    return int(hashlib.sha1(key.encode()).hexdigest()[:6], 16) / 0xFFFFFF


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


def data_status_for(source: str, scenario_down: bool) -> DataStatus:
    calls = COUNTERS["google_calls_today"]
    if scenario_down or source == "google_snapshot":
        return DataStatus(state="google_snapshot",
                          message=f"Google live data unavailable: using recorded snapshot from {SNAPSHOT_TIME:%Y-%m-%d %H:%M} UTC",
                          last_update=SNAPSHOT_TIME, calls_today=calls, daily_cap=GOOGLE_DAILY_CAP)
    if source == "google_live":
        return DataStatus(state="google_live", message="Live Google traffic", last_update=_now(),
                          calls_today=calls, daily_cap=GOOGLE_DAILY_CAP)
    if source == "google_cached":
        return DataStatus(state="google_cached", message="Google data from cache (< 30 min old)",
                          last_update=_now() - timedelta(minutes=12), calls_today=calls, daily_cap=GOOGLE_DAILY_CAP)
    return DataStatus(state="baseline_only", message="No Google data: using baseline demand",
                      last_update=None, calls_today=calls, daily_cap=GOOGLE_DAILY_CAP)


def session_data_status(s: Session, t: float) -> DataStatus:
    profile = PROFILES[s.req.demand_profile_id]
    return data_status_for(profile.source, _switched(s, t, "google_down"))


def effective(s: Session, t: float) -> EffectiveController:
    return controller_status(s, t).effective_controller


def metrics(s: Session, t: float) -> Metrics:
    eff = effective(s, t)
    base = {"gemini+max_pressure": 14.0, "max_pressure": 17.0, "webster": 21.0, "fixed": 26.0}[eff]
    wobble = 1 + 0.08 * math.sin(t / 7 + s.req.seed)
    ramp = min(1.0, t / 30) if t > 0 else 0.0
    return Metrics(
        mode=s.req.mode, effective_controller=eff,
        trip_delay_s=round(base * 1.4 * wobble * ramp, 2), avg_wait_s=round(base * wobble * ramp, 2),
        avg_queue=round(base / 6 * wobble * ramp, 2), throughput_per_min=round(38 - base / 2 + 2 * wobble, 2) if t > 5 else 0.0,
        blocked_spawns=int(t // 90) if eff == "fixed" else 0, deadlocks=0, t=t, population=int(40 + 10 * wobble))


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


def vehicles(s: Session, t: float, n: int = 40) -> list[Vehicle]:
    out = []
    for i in range(n):
        e = EDGES[(i * 7 + s.req.seed) % len(EDGES)]
        speed = max(0.0, e.speed_limit * (0.55 + 0.45 * math.sin(t / 5 + i)))
        offset = (i * 37.0 + t * e.speed_limit * 0.6) % e.length_m
        (lon1, lat1), (lon2, lat2) = e.geometry[0], e.geometry[-1]
        f = offset / e.length_m
        out.append(Vehicle(id=f"v{i}", edge_id=e.id, offset_m=round(offset, 2),
                           lat=lat1 + (lat2 - lat1) * f, lon=lon1 + (lon2 - lon1) * f,
                           heading=round(_bearing(lat1, lon1, lat2, lon2), 1), speed=round(speed, 2)))
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
    if t0 < sw <= t1:
        if s.scenario == "ai_limit" and s.req.mode == "ai":
            out.append(Explanation(t=sw, intersection_id=None,
                                   text="AI limit reached: signals reverted to traditional fixed timers"))
        elif s.scenario == "ai_replay" and s.req.mode == "ai":
            out.append(Explanation(t=sw, intersection_id=None, text="Switched to AI replay (recorded plans)"))
        elif s.scenario == "google_down":
            out.append(Explanation(t=sw, intersection_id=None,
                                   text="Google live data unavailable: demand stays frozen; status shows snapshot"))
    return out


def build_tick(s: Session, t: float, since_t: float) -> SimTick:
    return SimTick(session_id=s.session_id, t=t, vehicles=vehicles(s, t), signals=signals(s, t),
                   metrics=metrics(s, t), controller_status=controller_status(s, t),
                   data_status=session_data_status(s, t), explanations=explanations_between(s, since_t, t))


# ------------------------------------------------------------------ app
app = FastAPI(title="AI traffic lights - mock backend")


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _err(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _err(422, "validation_error", "request failed validation",
                {"errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})})


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _err(exc.status_code, "http_error", str(exc.detail))


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", mock=True)


@app.post("/api/area", response_model=AreaResponse)
def post_area(req: AreaRequest) -> AreaResponse:
    b = req.bbox
    aid = make_area_id(b.west, b.south, b.east, b.north)
    AREAS[aid] = Area(area_id=aid, bbox=b)
    return AreaResponse(area_id=aid, network=NETWORK, intersections=INTERSECTIONS,
                        recommended_ids=[i.id for i in INTERSECTIONS[:3]])


@app.get("/api/geocode", response_model=GeocodeResponse)
def geocode(q: str = Query(min_length=1)) -> GeocodeResponse:
    bb = NETWORK.bbox
    return GeocodeResponse(results=[
        GeocodeResult(display_name="Eixample, Barcelona, Catalonia, Spain (mock)", lat=41.39, lon=2.165, bbox=bb),
        GeocodeResult(display_name=f"{q} (mock result)", lat=41.3874, lon=2.1686),
    ])


@app.post("/api/demand/resolve", response_model=DemandResolveResponse)
def resolve_demand(req: DemandResolveRequest, scenario: str | None = None) -> DemandResolveResponse:
    if req.area_id not in AREAS:
        raise ApiError(404, "unknown_area", f"area {req.area_id} not found; POST /api/area first")
    sc = resolve_scenario(scenario)
    entries: list[DemandEntry] = []
    level = None
    if req.source == "baseline":
        source = "baseline_only"
        level = req.level or "medium"
        scale = LEVEL_FLOW_VEH_PER_H[level] / LEVEL_FLOW_VEH_PER_H[BASE_LEVEL]
        entries = [DemandEntry(entry_node_id=n, scale=scale) for n in NETWORK.entry_nodes]
    else:
        source = "google_snapshot" if (req.source == "google_snapshot" or sc == "google_down") else "google_live"
        for n in NETWORK.entry_nodes:
            ratio = round(1.0 + _stable_frac(n), 3)
            entries.append(DemandEntry(entry_node_id=n, congestion_ratio=ratio, scale=demand_scale(ratio)))
    profile = DemandProfile(id="dp_" + uuid.uuid4().hex[:8], area_id=req.area_id, source=source, level=level,
                            created_at=_now(), departure_time=req.departure_time, entries=entries)
    PROFILES[profile.id] = profile
    return DemandResolveResponse(demand_profile=profile, data_status=data_status_for(source, False))


@app.post("/api/sim/start", response_model=SimStartResponse)
def sim_start(req: SimStartRequest, scenario: str | None = None) -> SimStartResponse:
    if req.area_id not in AREAS:
        raise ApiError(404, "unknown_area", f"area {req.area_id} not found")
    if req.demand_profile_id not in PROFILES:
        raise ApiError(404, "unknown_demand_profile", f"demand profile {req.demand_profile_id} not found")
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
            await ws.send_text(build_tick(s, t, max(last_sent, -1.0)).model_dump_json())
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
