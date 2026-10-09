"""Real backend (part 1: areas, siting, demand, geocode).

    python -m uvicorn backend.app:app --port 8000

Simulation endpoints (/api/sim/*, /ws/sim) are wired once the engine (backend/sim/) lands;
until then use the mock (backend/mock_server.py) for the full UI flow.

Env: STATE_DIR (default backend/data/state), SAMPLE_AREA_PATH (default backend/data/sample_area.json),
NOMINATIM_URL, NOMINATIM_CONTACT.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request, Response

from backend.api_common import install_error_handlers
from backend.area.service import AreaService
from backend.contract.constants import AREA_RATE_LIMIT_PER_MIN, GRID_AREA_ID
from backend.contract.models import (
    AreaRequest,
    AreaResponse,
    DemandProfile,
    DemandResolveRequest,
    DemandResolveResponse,
    GeocodeResponse,
    HealthResponse,
)
from backend.geocode import DEFAULT_URL, Geocoder
from backend.traffic.demand import DemandStore, resolve, simulated_data_status

DATA_DIR = Path(__file__).resolve().parent / "data"
log = logging.getLogger("backend.app")


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("STATE_DIR", DATA_DIR / "state")))
    sample_path: Path = field(default_factory=lambda: Path(os.environ.get("SAMPLE_AREA_PATH", DATA_DIR / "sample_area.json")))
    grid_path: Path = DATA_DIR / "grid_network.json"
    nominatim_url: str = field(default_factory=lambda: os.environ.get("NOMINATIM_URL", DEFAULT_URL))
    geocode_transport: httpx.AsyncBaseTransport | None = None
    area_rate_limit_per_min: int = AREA_RATE_LIMIT_PER_MIN


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    app = FastAPI(title="AI traffic lights - backend")
    install_error_handlers(app)
    areas = AreaService(s.grid_path, s.sample_path, s.state_dir / "areas", s.area_rate_limit_per_min)
    demand = DemandStore(s.state_dir / "demand")
    geocoder = Geocoder(cache_path=s.state_dir / "geocode_cache.json", transport=s.geocode_transport,
                        url=s.nominatim_url)
    app.state.areas, app.state.demand, app.state.geocoder = areas, demand, geocoder

    def _with_cache_header(area: AreaResponse, hit: str, response: Response) -> AreaResponse:
        response.headers["X-Area-Cache"] = hit  # memory | disk | computed (debug aid, not contract)
        return area

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", mock=False)

    @app.post("/api/area", response_model=AreaResponse)
    def post_area(req: AreaRequest, request: Request, response: Response) -> AreaResponse:
        client = request.client.host if request.client else "local"
        area, hit = areas.analyze(req, client)
        return _with_cache_header(area, hit, response)

    @app.get("/api/demo-area", response_model=AreaResponse)
    def demo_area(response: Response) -> AreaResponse:
        area, hit = areas.demo_area()
        return _with_cache_header(area, hit, response)

    @app.get("/api/area/{area_id}", response_model=AreaResponse)
    def get_area(area_id: str) -> AreaResponse:
        return areas.get(area_id)

    @app.get("/api/geocode", response_model=GeocodeResponse)
    async def geocode(q: str = Query(min_length=1, max_length=200)) -> GeocodeResponse:
        return await geocoder.search(q)

    @app.post("/api/demand/resolve", response_model=DemandResolveResponse)
    def resolve_demand(req: DemandResolveRequest) -> DemandResolveResponse:
        profile = resolve(req, areas.entry_nodes(req.area_id), demand)
        return DemandResolveResponse(demand_profile=profile, data_status=simulated_data_status())

    @app.get("/api/demand/{profile_id}", response_model=DemandProfile)
    def get_demand(profile_id: str) -> DemandProfile:
        return demand.get(profile_id)

    log.info("backend ready (state=%s, sample=%s, grid area=%s)", s.state_dir,
             "present" if s.sample_path.exists() else "absent", GRID_AREA_ID)
    return app


app = create_app()
