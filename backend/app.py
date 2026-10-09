"""Real backend: areas, siting, demand, geocode, live simulation sessions, AI supervisor.

    python -m uvicorn backend.app:app --port 8000

Env (also read from .env): STATE_DIR (default backend/data/state), SAMPLE_AREA_PATH,
NOMINATIM_URL, NOMINATIM_CONTACT, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_DAILY_CAP, GEMINI_FAKE_FAIL,
GEMINI_MIN_INTERVAL_S, GEMINI_RPM, AI_RECORD (JSONL path), AI_REPLAY (JSONL path).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect

from backend.ai.gemini_client import GeminiClient
from backend.api_common import ApiError, install_error_handlers
from backend.area.service import AreaService
from backend.contract.constants import (
    AI_SESSION_MAX_CALLS,
    AREA_RATE_LIMIT_PER_MIN,
    GRID_AREA_ID,
    LIVE_AI_INTERVAL_S,
    WS_HZ,
)
from backend.contract.models import (
    AiResetResponse,
    AreaRequest,
    AreaResponse,
    DemandLevel,
    DemandProfile,
    DemandResolveRequest,
    DemandResolveResponse,
    ErrorDetail,
    ErrorResponse,
    GeocodeResponse,
    HealthResponse,
    MetricsResponse,
    SimDemandRequest,
    SimDemandResponse,
    SimStartRequest,
    SimStartResponse,
    SimStopRequest,
    SimStopResponse,
    SitingResult,
)
from backend.geocode import DEFAULT_URL, Geocoder
from backend.roadnet.sim_siting import SitingCache, apply_to_area, run_siting, siting_top
from backend.sessions import SessionManager
from backend.static_frontend import mount_frontend
from backend.traffic.demand import DemandStore, resolve, simulated_data_status

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
log = logging.getLogger("backend.app")
DEFAULT_SITING_SEED, DEFAULT_SITING_LEVEL = 42, "rush"


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text("utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("STATE_DIR", DATA_DIR / "state")))
    sample_path: Path = field(default_factory=lambda: Path(os.environ.get("SAMPLE_AREA_PATH", DATA_DIR / "sample_area.json")))
    grid_path: Path = DATA_DIR / "grid_network.json"
    nominatim_url: str = field(default_factory=lambda: os.environ.get("NOMINATIM_URL", DEFAULT_URL))
    geocode_transport: httpx.AsyncBaseTransport | None = None
    area_rate_limit_per_min: int = AREA_RATE_LIMIT_PER_MIN
    env: dict[str, str] = field(default_factory=lambda: {**load_env_file(ROOT / ".env"), **os.environ})
    gemini_transport: httpx.AsyncBaseTransport | None = None
    siting_committed_dir: Path | None = None  # default backend/data/siting
    siting_workers: int | None = None
    serve_frontend: bool = True  # mount frontend/dist at / when it has been built


def _configure_logging() -> None:
    """uvicorn only configures its own loggers; give ours a level-tagged handler (once)."""
    root = logging.getLogger("backend")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    _configure_logging()
    s = settings or Settings()
    areas = AreaService(s.grid_path, s.sample_path, s.state_dir / "areas", s.area_rate_limit_per_min)
    demand = DemandStore(s.state_dir / "demand")
    geocoder = Geocoder(cache_path=s.state_dir / "geocode_cache.json", transport=s.geocode_transport,
                        url=s.nominatim_url)
    client = GeminiClient.from_env(s.state_dir, s.env, transport=s.gemini_transport)
    rec, rep = s.env.get("AI_RECORD"), s.env.get("AI_REPLAY")
    max_calls = int(s.env.get("GEMINI_SESSION_MAX_CALLS") or AI_SESSION_MAX_CALLS)
    sessions = SessionManager(demand, client, Path(rec) if rec else None, Path(rep) if rep else None,
                              live_interval_s=float(s.env.get("GEMINI_LIVE_INTERVAL_S") or LIVE_AI_INTERVAL_S),
                              max_calls=max_calls if max_calls > 0 else None)
    siting_cache = SitingCache(s.state_dir / "siting", *( [s.siting_committed_dir] if s.siting_committed_dir else []))
    siting_jobs: dict[tuple[str, int, str], SitingResult] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for sid in list(sessions.sessions):
            await sessions.stop(sid)

    app = FastAPI(title="AI traffic lights - backend", lifespan=lifespan)
    install_error_handlers(app)
    app.state.areas, app.state.demand, app.state.geocoder = areas, demand, geocoder
    app.state.sessions, app.state.gemini, app.state.siting = sessions, client, siting_cache

    def with_siting(area: AreaResponse) -> AreaResponse:
        """Fill sim_gain_s from a cached siting result (precomputed for the demo area)."""
        res = siting_cache.get(area.area_id, DEFAULT_SITING_SEED, DEFAULT_SITING_LEVEL)
        if res is None:
            return area
        gains = {i.id: i.sim_gain_s for i in area.intersections if i.id in res.gains}
        if gains == res.gains and list(area.recommended_ids) == siting_top(res):
            return area  # already up to date (a newer siting result must replace a stale cached one)
        area = apply_to_area(area, res)
        areas.update(area)
        return area

    def cache_header(response: Response, hit: str) -> None:
        response.headers["X-Area-Cache"] = hit  # memory | disk | computed (debug aid, not contract)

    # ------------------------------------------------------------------ areas
    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", mock=False)

    @app.post("/api/area", response_model=AreaResponse)
    def post_area(req: AreaRequest, request: Request, response: Response) -> AreaResponse:
        area, hit = areas.analyze(req, request.client.host if request.client else "local")
        cache_header(response, hit)
        return with_siting(area)

    @app.get("/api/demo-area", response_model=AreaResponse)
    def demo_area(response: Response) -> AreaResponse:
        area, hit = areas.demo_area()
        cache_header(response, hit)
        return with_siting(area)

    @app.get("/api/area/{area_id}", response_model=AreaResponse)
    def get_area(area_id: str) -> AreaResponse:
        return with_siting(areas.get(area_id))

    @app.post("/api/area/{area_id}/refine", response_model=SitingResult)
    async def refine(area_id: str, seed: int = DEFAULT_SITING_SEED,
                     level: DemandLevel = DEFAULT_SITING_LEVEL) -> SitingResult:
        """Start (or return) simulation-based siting. Returns immediately: status=running until done."""
        area = areas.get(area_id)
        key = (area_id, seed, level)
        cached = siting_cache.get(*key)
        if cached is not None:
            return cached
        job = siting_jobs.get(key)
        if job is not None and job.status == "running":
            return job
        job = SitingResult(area_id=area_id, seed=seed, status="running", demand_level=level,
                           candidates=[i.id for i in area.intersections[:8]], message="started")
        siting_jobs[key] = job

        async def work() -> None:
            try:
                res = await asyncio.to_thread(run_siting, area, seed, level, workers=s.siting_workers)
                siting_cache.put(res)
                areas.update(apply_to_area(areas.get(area_id), res))
                siting_jobs[key] = res
                log.info("siting %s done in %.1f s: %s", area_id, res.runtime_s, res.order)
            except Exception as e:  # report, don't crash the server
                log.exception("siting %s failed", area_id)
                siting_jobs[key] = job.model_copy(update={"status": "failed", "message": f"{type(e).__name__}: {e}"})

        asyncio.get_running_loop().create_task(work())
        return job

    @app.get("/api/area/{area_id}/refine", response_model=SitingResult)
    def refine_status(area_id: str, seed: int = DEFAULT_SITING_SEED,
                      level: DemandLevel = DEFAULT_SITING_LEVEL) -> SitingResult:
        key = (area_id, seed, level)
        res = siting_jobs.get(key) or siting_cache.get(*key)
        if res is None:
            raise ApiError(404, "siting_not_started", f"no siting for {area_id}; POST this URL to start it")
        return res

    @app.get("/api/geocode", response_model=GeocodeResponse)
    async def geocode(q: str = Query(min_length=1, max_length=200)) -> GeocodeResponse:
        return await geocoder.search(q)

    # ------------------------------------------------------------------ demand
    @app.post("/api/demand/resolve", response_model=DemandResolveResponse)
    def resolve_demand(req: DemandResolveRequest) -> DemandResolveResponse:
        profile = resolve(req, areas.entry_nodes(req.area_id), demand)
        return DemandResolveResponse(demand_profile=profile, data_status=simulated_data_status())

    @app.get("/api/demand/{profile_id}", response_model=DemandProfile)
    def get_demand(profile_id: str) -> DemandProfile:
        return demand.get(profile_id)

    # ------------------------------------------------------------------ simulation
    @app.post("/api/sim/start", response_model=SimStartResponse)
    async def sim_start(req: SimStartRequest) -> SimStartResponse:
        area = with_siting(areas.get(req.area_id))
        profile = demand.get(req.demand_profile_id)
        return SimStartResponse(session_id=sessions.start(req, area, profile).session_id)

    @app.post("/api/sim/stop", response_model=SimStopResponse)
    async def sim_stop(req: SimStopRequest) -> SimStopResponse:
        sess = await sessions.stop(req.session_id)
        final = await asyncio.to_thread(sess.metrics)  # engine lock: never on the event-loop thread
        return SimStopResponse(session_id=sess.session_id, stopped=True, final_metrics=final)

    @app.post("/api/sim/demand", response_model=SimDemandResponse)
    def sim_demand(req: SimDemandRequest) -> SimDemandResponse:
        child, at = sessions.change_demand(req.session_id, req.level, req.multiplier, req.entry_overrides, req.at_t)
        return SimDemandResponse(session_id=req.session_id, demand_profile=child, applies_at_t=at)

    @app.get("/api/metrics", response_model=MetricsResponse)
    def get_metrics(session_id: str) -> MetricsResponse:
        sess = sessions.get(session_id)
        m = sess.metrics()
        return MetricsResponse(session_id=session_id, t=m.t or 0.0, metrics=m)

    @app.get("/api/debug/demand_digest")
    def demand_digest(session_id: str, until_t: float) -> dict:
        """Debug (not contract): hash of the demand schedule up to until_t + applied changes, so two
        sessions can be compared at the same sim time from outside the process."""
        import hashlib
        import json as _json

        sess = sessions.get(session_id)
        with sess.lock:
            d = sess.engine.demand
            if d.horizon < until_t:
                raise ApiError(409, "not_reached", f"session is at t={d.horizon:.1f} < until_t")
            arrivals = [(a.time, a.origin, a.destination, a.speed_factor) for a in d.schedule if a.time <= until_t]
            changes = [c for c in d.applied_changes if c[0] <= until_t]
        blob = _json.dumps([arrivals, changes], separators=(",", ":")).encode()
        return {"session_id": session_id, "until_t": until_t, "arrivals": len(arrivals),
                "changes": len(changes), "digest": hashlib.sha256(blob).hexdigest()}

    @app.post("/api/ai/reset", response_model=AiResetResponse)
    def ai_reset() -> AiResetResponse:
        client.usage.reset()
        log.warning("AI daily counter reset via POST /api/ai/reset; daily-cap fallbacks resume at their next tick")
        return AiResetResponse(reset=True, message="AI re-enabled: daily counter reset; sessions in daily-cap "
                                                   "fallback resume at their next controller tick",
                               calls_today=client.usage.calls_today(), daily_cap=client.daily_cap)

    @app.websocket("/ws/sim")
    async def ws_sim(ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        sess = sessions.sessions.get(session_id)
        if sess is None:
            await ws.send_text(ErrorResponse(error=ErrorDetail(
                code="unknown_session", message=f"session {session_id} not found")).model_dump_json())
            await ws.close(code=4404)
            return

        # anyio task group (Starlette is anyio-based): cancellation stays inside the handler's scope.
        async with anyio.create_task_group() as tg:
            async def sender() -> None:
                last_version, idx = -1, 0
                while True:
                    snap = sess.snapshot  # latest only: intermediate versions are skipped
                    if snap is not None and snap.version != last_version:
                        new, idx = sess.explanations_since(idx)
                        await ws.send_text(snap.tick.model_copy(update={"explanations": new}).model_dump_json())
                        last_version = snap.version
                    elif sess.stopped:
                        await ws.close(code=1000)
                        tg.cancel_scope.cancel()
                        return
                    await anyio.sleep(1 / WS_HZ)

            async def receiver() -> None:
                try:
                    while True:
                        await ws.receive_text()
                except WebSocketDisconnect:
                    pass
                tg.cancel_scope.cancel()

            tg.start_soon(sender)
            tg.start_soon(receiver)

    served = mount_frontend(app) if s.serve_frontend else False
    log.info("backend ready (state=%s, sample=%s, grid area=%s, gemini model=%s, fake_fail=%s, frontend=%s)",
             s.state_dir, "present" if s.sample_path.exists() else "absent", GRID_AREA_ID, client.model,
             client.fake_fail, "served" if served else "not served")
    return app


app = create_app()
