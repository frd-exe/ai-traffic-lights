/* Generated from docs/schemas/contract.bundle.json by `npm run gen:types`. Do not edit. */

/**
 * AI traffic lights contract v0.2.0 (generated, do not edit)
 */
export interface Contract {
  AiResetResponse?: AiResetResponse;
  Approach?: Approach;
  ApproachObservation?: ApproachObservation;
  AreaRequest?: AreaRequest;
  AreaResponse?: AreaResponse;
  BBox?: BBox;
  ControllerStatus?: ControllerStatus;
  DataStatus?: DataStatus;
  DemandEntry?: DemandEntry;
  DemandProfile?: DemandProfile;
  DemandResolveRequest?: DemandResolveRequest;
  DemandResolveResponse?: DemandResolveResponse;
  Edge?: Edge;
  ErrorDetail?: ErrorDetail;
  ErrorResponse?: ErrorResponse;
  Explanation?: Explanation;
  GeocodeResponse?: GeocodeResponse;
  GeocodeResult?: GeocodeResult;
  HealthResponse?: HealthResponse;
  Intersection?: Intersection;
  IntersectionObservation?: IntersectionObservation;
  Metrics?: Metrics;
  MetricsResponse?: MetricsResponse;
  Node?: Node;
  Observation?: Observation;
  Phase?: Phase;
  Plan?: Plan;
  RoadNetwork?: RoadNetwork;
  SignalState?: SignalState;
  SimDemandRequest?: SimDemandRequest;
  SimDemandResponse?: SimDemandResponse;
  SimStartRequest?: SimStartRequest;
  SimStartResponse?: SimStartResponse;
  SimStopRequest?: SimStopRequest;
  SimStopResponse?: SimStopResponse;
  SimTick?: SimTick;
  Vehicle?: Vehicle;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "AiResetResponse".
 */
export interface AiResetResponse {
  calls_today: number;
  daily_cap: number;
  message: string;
  reset: boolean;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Approach".
 */
export interface Approach {
  /**
   * direction FROM the intersection TOWARD the upstream end of in_edge
   */
  bearing: number;
  /**
   * a_ + sha1(intersection_id:bearing_rounded_5deg)[:8]
   */
  id: string;
  in_edge: string;
  lanes: number;
  /**
   * edges a vehicle on this approach may take (no U-turns)
   */
  out_edges: string[];
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "ApproachObservation".
 */
export interface ApproachObservation {
  /**
   * veh/s, trailing 60 sim-s
   */
  arrival_rate: number;
  /**
   * stopped vehicles within 50 m of stop line
   */
  queue: number;
  /**
   * longest current continuous stopped time on this approach
   */
  wait_s: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "AreaRequest".
 */
export interface AreaRequest {
  bbox: BBox;
  /**
   * ignore bbox, return the synthetic grid demo city (v0.2.0)
   */
  demo_city?: boolean;
  ignore_osm_signals?: boolean;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "BBox".
 */
export interface BBox {
  east: number;
  north: number;
  south: number;
  west: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "AreaResponse".
 */
export interface AreaResponse {
  area_id: string;
  /**
   * ranked: best signal candidates first
   */
  intersections: Intersection[];
  network: RoadNetwork;
  /**
   * default selection for the UI
   */
  recommended_ids?: string[];
  /**
   * synthetic_grid = demo city; osm = parsed OpenStreetMap data (v0.2.0)
   */
  source: "synthetic_grid" | "osm";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Intersection".
 */
export interface Intersection {
  approaches: Approach[];
  has_signal_in_osm: boolean;
  /**
   * i_ + sha1(lat5,lon5)[:8]
   */
  id: string;
  lat: number;
  lon: number;
  node_id: string;
  /**
   * estimated delay saved by signalising (s); null until computed
   */
  sim_gain_s?: number | null;
  structural_score: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "RoadNetwork".
 */
export interface RoadNetwork {
  bbox?: BBox | null;
  edges: Edge[];
  /**
   * boundary nodes where vehicles spawn
   */
  entry_nodes: string[];
  /**
   * boundary nodes where vehicles leave
   */
  exit_nodes: string[];
  nodes: Node[];
}
/**
 * Directed road segment from_node -> to_node.
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Edge".
 */
export interface Edge {
  from_node: string;
  /**
   * @minItems 2
   */
  geometry: [[number, number], [number, number], ...[number, number][]];
  id: string;
  lanes: number;
  length_m: number;
  road_class:
    | "motorway"
    | "trunk"
    | "primary"
    | "secondary"
    | "tertiary"
    | "unclassified"
    | "residential"
    | "living_street"
    | "service";
  /**
   * m/s
   */
  speed_limit: number;
  to_node: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Node".
 */
export interface Node {
  id: string;
  lat: number;
  lon: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "ControllerStatus".
 */
export interface ControllerStatus {
  calls_last_min: number;
  calls_today: number;
  daily_cap: number;
  effective_controller: "gemini+max_pressure" | "fixed" | "webster" | "max_pressure";
  message: string;
  /**
   * sim time when this state began
   */
  since_t: number;
  state: "ai_active" | "ai_limit_reached" | "ai_unavailable" | "ai_replay" | "traditional";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "DataStatus".
 */
export interface DataStatus {
  calls_today: number;
  daily_cap: number;
  /**
   * when the underlying traffic data was produced; null for simulated demand
   */
  last_update: string | null;
  message: string;
  state: "google_live" | "google_cached" | "google_snapshot" | "baseline_only";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "DemandEntry".
 */
export interface DemandEntry {
  /**
   * reserved for external traffic data; always null since v0.2.0
   */
  congestion_ratio?: number | null;
  entry_node_id: string;
  /**
   * entry flow = scale * LEVEL_FLOW_VEH_PER_H['medium'] (veh/h)
   */
  scale: number;
}
/**
 * Resolved ONCE and frozen; sessions reference it by id.
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "DemandProfile".
 */
export interface DemandProfile {
  area_id?: string | null;
  created_at: string;
  departure_time?: string | null;
  entries: DemandEntry[];
  /**
   * per-entry scale factor on top of level x multiplier (v0.2.0)
   */
  entry_overrides?: {
    [k: string]: number;
  };
  id: string;
  /**
   * set for baseline_only
   */
  level?: ("low" | "medium" | "high" | "rush") | null;
  /**
   * global demand multiplier (v0.2.0)
   */
  multiplier?: number;
  /**
   * profile this one was derived from by /api/sim/demand (v0.2.0)
   */
  parent_id?: string | null;
  source: "google_live" | "google_cached" | "google_snapshot" | "baseline_only";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "DemandResolveRequest".
 */
export interface DemandResolveRequest {
  area_id: string;
  departure_time?: string | null;
  /**
   * {entry_node_id: scale factor} (v0.2.0)
   */
  entry_overrides?: {
    [k: string]: number;
  };
  /**
   * baseline level; default medium
   */
  level?: ("low" | "medium" | "high" | "rush") | null;
  /**
   * global demand multiplier (v0.2.0)
   */
  multiplier?: number;
  /**
   * only 'baseline' is accepted since v0.2.0; google_* are reserved and rejected
   */
  source?: "google_live" | "google_snapshot" | "baseline";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "DemandResolveResponse".
 */
export interface DemandResolveResponse {
  data_status: DataStatus;
  demand_profile: DemandProfile;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "ErrorDetail".
 */
export interface ErrorDetail {
  /**
   * machine-readable, snake_case, e.g. unknown_session
   */
  code: string;
  details?: {
    [k: string]: unknown;
  } | null;
  message: string;
}
/**
 * Body of every non-2xx REST response and of WS error frames.
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "ErrorResponse".
 */
export interface ErrorResponse {
  error: ErrorDetail;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Explanation".
 */
export interface Explanation {
  /**
   * null = session-wide (e.g. AI fallback notice)
   */
  intersection_id: string | null;
  t: number;
  text: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "GeocodeResponse".
 */
export interface GeocodeResponse {
  results: GeocodeResult[];
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "GeocodeResult".
 */
export interface GeocodeResult {
  bbox?: BBox | null;
  display_name: string;
  lat: number;
  lon: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "HealthResponse".
 */
export interface HealthResponse {
  contract_version?: string;
  mock?: boolean;
  status: "ok";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "IntersectionObservation".
 */
export interface IntersectionObservation {
  approaches: {
    [k: string]: ApproachObservation;
  };
  current_phase: string;
  is_transition?: boolean;
  phases: Phase[];
  time_in_phase_s: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Phase".
 */
export interface Phase {
  /**
   * approaches that get green in this phase
   */
  approach_ids: string[];
  /**
   * <intersection_id>:p<k>, unique within an area
   */
  id: string;
}
/**
 * See CONTRACT.md 'Metric definitions'; identical in engine, experiments and UI.
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Metrics".
 */
export interface Metrics {
  avg_queue: number;
  avg_wait_s: number;
  blocked_spawns: number;
  deadlocks: number;
  effective_controller: "gemini+max_pressure" | "fixed" | "webster" | "max_pressure";
  mode: "fixed" | "webster" | "max_pressure" | "ai";
  /**
   * vehicles in the metric population
   */
  population?: number | null;
  /**
   * sim time the metrics refer to
   */
  t?: number | null;
  throughput_per_min: number;
  trip_delay_s: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "MetricsResponse".
 */
export interface MetricsResponse {
  metrics: Metrics;
  session_id: string;
  t: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Observation".
 */
export interface Observation {
  intersections: {
    [k: string]: IntersectionObservation;
  };
  t: number;
}
/**
 * LLM supervisor output: hold `phase` for up to `hold_s` (engine rules still apply).
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Plan".
 */
export interface Plan {
  hold_s: number;
  intersection_id: string;
  /**
   * phase id
   */
  phase: string;
  reason: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SignalState".
 */
export interface SignalState {
  color_per_approach: {
    [k: string]: "green" | "yellow" | "red";
  };
  intersection_id: string;
  /**
   * true during yellow / all-red
   */
  is_transition: boolean;
  /**
   * phase being served; during a transition, the phase being switched TO
   */
  phase_id: string;
  /**
   * seconds since this phase (or transition) began
   */
  time_in_phase_s: number;
}
/**
 * Change demand of a running session (v0.2.0). Omitted fields keep the current value.
 * For split compare, send the same at_t to both sessions.
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimDemandRequest".
 */
export interface SimDemandRequest {
  /**
   * sim time to apply the change; null = next step
   */
  at_t?: number | null;
  /**
   * replaces the current overrides when set
   */
  entry_overrides?: {
    [k: string]: number;
  } | null;
  level?: ("low" | "medium" | "high" | "rush") | null;
  multiplier?: number | null;
  session_id: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimDemandResponse".
 */
export interface SimDemandResponse {
  applies_at_t: number;
  demand_profile: DemandProfile1;
  session_id: string;
}
/**
 * Resolved ONCE and frozen; sessions reference it by id.
 */
export interface DemandProfile1 {
  area_id?: string | null;
  created_at: string;
  departure_time?: string | null;
  entries: DemandEntry[];
  /**
   * per-entry scale factor on top of level x multiplier (v0.2.0)
   */
  entry_overrides?: {
    [k: string]: number;
  };
  id: string;
  /**
   * set for baseline_only
   */
  level?: ("low" | "medium" | "high" | "rush") | null;
  /**
   * global demand multiplier (v0.2.0)
   */
  multiplier?: number;
  /**
   * profile this one was derived from by /api/sim/demand (v0.2.0)
   */
  parent_id?: string | null;
  source: "google_live" | "google_cached" | "google_snapshot" | "baseline_only";
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimStartRequest".
 */
export interface SimStartRequest {
  area_id: string;
  demand_profile_id: string;
  mode: "fixed" | "webster" | "max_pressure" | "ai";
  seed: number;
  /**
   * signalised in ALL modes; others unsignalised
   */
  selected_intersections: string[];
  /**
   * sim-seconds per wall-second
   */
  speed?: number;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimStartResponse".
 */
export interface SimStartResponse {
  session_id: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimStopRequest".
 */
export interface SimStopRequest {
  session_id: string;
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimStopResponse".
 */
export interface SimStopResponse {
  final_metrics?: Metrics | null;
  session_id: string;
  stopped: boolean;
}
/**
 * WS /ws/sim frame, 5 Hz, latest-only (stale ticks dropped).
 *
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "SimTick".
 */
export interface SimTick {
  controller_status: ControllerStatus;
  data_status: DataStatus;
  /**
   * new since the previous delivered tick
   */
  explanations: Explanation[];
  metrics: Metrics;
  session_id: string;
  signals: SignalState[];
  /**
   * sim seconds
   */
  t: number;
  type?: "tick";
  vehicles: Vehicle[];
}
/**
 * This interface was referenced by `Contract`'s JSON-Schema
 * via the `definition` "Vehicle".
 */
export interface Vehicle {
  edge_id: string;
  /**
   * degrees, 0 = north, clockwise
   */
  heading: number;
  id: string;
  lat: number;
  lon: number;
  /**
   * distance from the start of edge_id
   */
  offset_m: number;
  /**
   * m/s
   */
  speed: number;
}
