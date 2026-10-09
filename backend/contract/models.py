"""Machine-readable contract (pydantic v2). docs/CONTRACT.md is the prose version.

Conventions: SI units, WGS84 lat/lon in degrees, string ids, timestamps ISO-8601 UTC.
Geometry is a list of [lon, lat] pairs (GeoJSON order).
Contract changes are ADDITIVE ONLY (new optional fields / new models); bump CONTRACT_VERSION.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .constants import CONTRACT_VERSION, DEMAND_MULTIPLIER_MAX, DEMAND_MULTIPLIER_MIN, ENTRY_OVERRIDE_MAX


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- enums
RoadClass = Literal[
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "service",
]
SimMode = Literal["fixed", "webster", "max_pressure", "ai"]
EffectiveController = Literal["gemini+max_pressure", "fixed", "webster", "max_pressure"]
ControllerState = Literal["ai_active", "ai_limit_reached", "ai_unavailable", "ai_replay", "traditional"]
DataState = Literal["google_live", "google_cached", "google_snapshot", "baseline_only"]
DemandSource = DataState
DemandRequestSource = Literal["google_live", "google_snapshot", "baseline"]
DemandLevel = Literal["low", "medium", "high", "rush"]
SignalColor = Literal["green", "yellow", "red"]
AreaSource = Literal["synthetic_grid", "osm"]

Lat = Annotated[float, Field(ge=-90, le=90)]
Lon = Annotated[float, Field(ge=-180, le=180)]
LonLat = Annotated[list[float], Field(min_length=2, max_length=2, description="[lon, lat]")]
Bearing = Annotated[float, Field(ge=0, lt=360, description="degrees, 0 = north, clockwise")]
NonNeg = Annotated[float, Field(ge=0)]


# ---------------------------------------------------------------- errors
class ErrorDetail(Model):
    code: str = Field(description="machine-readable, snake_case, e.g. unknown_session")
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(Model):
    """Body of every non-2xx REST response and of WS error frames."""
    error: ErrorDetail


# ---------------------------------------------------------------- geography
class BBox(Model):
    west: Lon
    south: Lat
    east: Lon
    north: Lat

    @model_validator(mode="after")
    def _ordered(self) -> "BBox":
        if not (self.west < self.east and self.south < self.north):
            raise ValueError("bbox must satisfy west < east and south < north")
        return self


class Node(Model):
    id: str
    lat: Lat
    lon: Lon


class Edge(Model):
    """Directed road segment from_node -> to_node."""
    id: str
    from_node: str
    to_node: str
    geometry: list[LonLat] = Field(min_length=2)
    length_m: Annotated[float, Field(gt=0)]
    speed_limit: Annotated[float, Field(gt=0, description="m/s")]
    lanes: Annotated[int, Field(ge=1)]
    road_class: RoadClass


class RoadNetwork(Model):
    nodes: list[Node]
    edges: list[Edge]
    entry_nodes: list[str] = Field(description="boundary nodes where vehicles spawn")
    exit_nodes: list[str] = Field(description="boundary nodes where vehicles leave")
    bbox: BBox | None = None


class Approach(Model):
    id: str = Field(description="a_ + sha1(intersection_id:bearing_rounded_5deg)[:8]")
    bearing: Bearing = Field(description="direction FROM the intersection TOWARD the upstream end of in_edge")
    lanes: Annotated[int, Field(ge=1)]
    in_edge: str
    out_edges: list[str] = Field(description="edges a vehicle on this approach may take (no U-turns)")


class Intersection(Model):
    id: str = Field(description="i_ + sha1(lat5,lon5)[:8]")
    node_id: str
    lat: Lat
    lon: Lon
    approaches: list[Approach]
    has_signal_in_osm: bool
    structural_score: Annotated[float, Field(ge=0, le=1)]
    sim_gain_s: float | None = Field(default=None, description="estimated delay saved by signalising (s); null until computed")


# ---------------------------------------------------------------- signals / vehicles
class Phase(Model):
    id: str = Field(description="<intersection_id>:p<k>, unique within an area")
    approach_ids: list[str] = Field(description="approaches that get green in this phase")


class SignalState(Model):
    intersection_id: str
    phase_id: str = Field(description="phase being served; during a transition, the phase being switched TO")
    color_per_approach: dict[str, SignalColor]
    time_in_phase_s: NonNeg = Field(description="seconds since this phase (or transition) began")
    is_transition: bool = Field(description="true during yellow / all-red")


class Vehicle(Model):
    id: str
    edge_id: str
    offset_m: NonNeg = Field(description="distance from the start of edge_id")
    lat: Lat
    lon: Lon
    heading: Bearing
    speed: NonNeg = Field(description="m/s")


class Metrics(Model):
    """See CONTRACT.md 'Metric definitions'; identical in engine, experiments and UI."""
    mode: SimMode
    effective_controller: EffectiveController
    trip_delay_s: NonNeg
    avg_wait_s: NonNeg
    avg_queue: NonNeg
    throughput_per_min: NonNeg
    blocked_spawns: Annotated[int, Field(ge=0)]
    deadlocks: Annotated[int, Field(ge=0)]
    t: NonNeg | None = Field(default=None, description="sim time the metrics refer to")
    population: Annotated[int, Field(ge=0)] | None = Field(default=None, description="vehicles in the metric population")


class ControllerStatus(Model):
    state: ControllerState
    effective_controller: EffectiveController
    message: str
    since_t: NonNeg = Field(description="sim time when this state began")
    calls_last_min: Annotated[int, Field(ge=0)]
    calls_today: Annotated[int, Field(ge=0)]
    daily_cap: Annotated[int, Field(ge=0)]


class DemandEntry(Model):
    entry_node_id: str
    congestion_ratio: Annotated[float, Field(gt=0)] | None = Field(
        default=None, description="reserved for external traffic data; always null since v0.2.0")
    scale: Annotated[float, Field(gt=0)] = Field(
        description="entry flow = scale * LEVEL_FLOW_VEH_PER_H['medium'] (veh/h)")


Multiplier = Annotated[float, Field(ge=DEMAND_MULTIPLIER_MIN, le=DEMAND_MULTIPLIER_MAX)]
EntryOverrides = dict[str, Annotated[float, Field(gt=0, le=ENTRY_OVERRIDE_MAX)]]


class DemandProfile(Model):
    """Resolved ONCE and frozen; sessions reference it by id."""
    id: str
    area_id: str | None = None
    source: DemandSource
    level: DemandLevel | None = Field(default=None, description="set for baseline_only")
    created_at: datetime
    departure_time: datetime | None = None
    entries: list[DemandEntry]
    multiplier: Multiplier = Field(default=1.0, description="global demand multiplier (v0.2.0)")
    entry_overrides: EntryOverrides = Field(
        default_factory=dict, description="per-entry scale factor on top of level x multiplier (v0.2.0)")
    parent_id: str | None = Field(default=None, description="profile this one was derived from by /api/sim/demand (v0.2.0)")


class DataStatus(Model):
    state: DataState
    message: str
    last_update: datetime | None = Field(description="when the underlying traffic data was produced; null for simulated demand")
    calls_today: Annotated[int, Field(ge=0)]
    daily_cap: Annotated[int, Field(ge=0)]


class Explanation(Model):
    t: NonNeg
    intersection_id: str | None = Field(description="null = session-wide (e.g. AI fallback notice)")
    text: str


# ---------------------------------------------------------------- controller I/O
class ApproachObservation(Model):
    queue: Annotated[int, Field(ge=0)] = Field(description="stopped vehicles within 50 m of stop line")
    wait_s: NonNeg = Field(description="longest current continuous stopped time on this approach")
    arrival_rate: NonNeg = Field(description="veh/s, trailing 60 sim-s")


class IntersectionObservation(Model):
    phases: list[Phase]
    current_phase: str
    time_in_phase_s: NonNeg
    is_transition: bool = False
    approaches: dict[str, ApproachObservation]


class Observation(Model):
    t: NonNeg
    intersections: dict[str, IntersectionObservation]


class Plan(Model):
    """LLM supervisor output: hold `phase` for up to `hold_s` (engine rules still apply)."""
    intersection_id: str
    phase: str = Field(description="phase id")
    hold_s: Annotated[float, Field(ge=0, le=120)]
    reason: str


# ---------------------------------------------------------------- REST
class HealthResponse(Model):
    status: Literal["ok"]
    contract_version: str = CONTRACT_VERSION
    mock: bool = False


class AreaRequest(Model):
    bbox: BBox
    ignore_osm_signals: bool = False
    demo_city: bool = Field(default=False, description="ignore bbox, return the synthetic grid demo city (v0.2.0)")


class AreaResponse(Model):
    area_id: str
    source: AreaSource = Field(description="synthetic_grid = demo city; osm = parsed OpenStreetMap data (v0.2.0)")
    network: RoadNetwork
    intersections: list[Intersection] = Field(description="ranked: best signal candidates first")
    recommended_ids: list[str] = Field(default_factory=list, description="default selection for the UI")


class GeocodeResult(Model):
    display_name: str
    lat: Lat
    lon: Lon
    bbox: BBox | None = None


class GeocodeResponse(Model):
    results: list[GeocodeResult]


class DemandResolveRequest(Model):
    area_id: str
    source: DemandRequestSource = Field(
        default="baseline", description="only 'baseline' is accepted since v0.2.0; google_* are reserved and rejected")
    level: DemandLevel | None = Field(default=None, description="baseline level; default medium")
    departure_time: datetime | None = None
    multiplier: Multiplier = Field(default=1.0, description="global demand multiplier (v0.2.0)")
    entry_overrides: EntryOverrides = Field(default_factory=dict, description="{entry_node_id: scale factor} (v0.2.0)")


class DemandResolveResponse(Model):
    demand_profile: DemandProfile
    data_status: DataStatus


class SimStartRequest(Model):
    area_id: str
    mode: SimMode
    demand_profile_id: str
    seed: int
    selected_intersections: list[str] = Field(description="signalised in ALL modes; others unsignalised")
    speed: Annotated[float, Field(gt=0, le=20)] = Field(default=1.0, description="sim-seconds per wall-second")


class SimStartResponse(Model):
    session_id: str


class SimStopRequest(Model):
    session_id: str


class SimStopResponse(Model):
    session_id: str
    stopped: bool
    final_metrics: Metrics | None = None


class SimDemandRequest(Model):
    """Change demand of a running session (v0.2.0). Omitted fields keep the current value.
    For split compare, send the same at_t to both sessions."""
    session_id: str
    level: DemandLevel | None = None
    multiplier: Multiplier | None = None
    entry_overrides: EntryOverrides | None = Field(default=None, description="replaces the current overrides when set")
    at_t: NonNeg | None = Field(default=None, description="sim time to apply the change; null = next step")


class SimDemandResponse(Model):
    session_id: str
    demand_profile: DemandProfile = Field(description="new frozen profile (parent_id = previous profile)")
    applies_at_t: NonNeg


class MetricsResponse(Model):
    session_id: str
    t: NonNeg
    metrics: Metrics


class AiResetResponse(Model):
    reset: bool
    message: str
    calls_today: Annotated[int, Field(ge=0)]
    daily_cap: Annotated[int, Field(ge=0)]


# ---------------------------------------------------------------- WS
class SimTick(Model):
    """WS /ws/sim frame, 5 Hz, latest-only (stale ticks dropped)."""
    type: Literal["tick"] = "tick"
    session_id: str
    t: NonNeg = Field(description="sim seconds")
    vehicles: list[Vehicle]
    signals: list[SignalState]
    metrics: Metrics
    controller_status: ControllerStatus
    data_status: DataStatus
    explanations: list[Explanation] = Field(description="new since the previous delivered tick")


# Every model exported to docs/schemas (order = export order).
ALL_MODELS: list[type[BaseModel]] = [
    ErrorDetail, ErrorResponse, BBox, Node, Edge, RoadNetwork, Approach, Intersection,
    Phase, SignalState, Vehicle, Metrics, ControllerStatus, DemandEntry, DemandProfile,
    DataStatus, Explanation, ApproachObservation, IntersectionObservation, Observation, Plan,
    HealthResponse, AreaRequest, AreaResponse, GeocodeResult, GeocodeResponse,
    DemandResolveRequest, DemandResolveResponse, SimStartRequest, SimStartResponse,
    SimStopRequest, SimStopResponse, MetricsResponse, AiResetResponse, SimTick,
    SimDemandRequest, SimDemandResponse,
]
