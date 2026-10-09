"""Single source of truth for numeric constants. Import from here; never hard-code.

All values are SI (seconds, metres, metres/second) unless the name says otherwise.
"""

from typing import Final

CONTRACT_VERSION: Final = "0.4.0"

# --- Signal timing (engine rules) ---
YELLOW_S: Final = 3.0
ALL_RED_S: Final = 2.0
MIN_GREEN_S: Final = 7.0
MAX_RED_S: Final = 60.0  # no approach waits on red longer than this
FIXED_PHASE_S: Final = 30.0  # green per phase for the fixed-timer baseline / AI fallback

# --- Simulation / cadence ---
SIM_DT_S: Final = 0.1
CONTROLLER_PERIOD_S: Final = 1.0  # controllers are asked every 1 sim-second
WS_HZ: Final = 5  # tick rate of WS /ws/sim
GEMINI_MIN_INTERVAL_S: Final = 6.0  # wall-clock gap between LLM supervisor calls

# --- Metric definitions ---
STOPPED_SPEED_MPS: Final = 0.5  # speed below this counts as "stopped"
QUEUE_DISTANCE_M: Final = 50.0  # queue counted within this distance upstream of the stop line
METRICS_FINISHED_WINDOW_S: Final = 120.0  # finished trips kept in the metric population
THROUGHPUT_WINDOW_S: Final = 60.0

# --- AI limit / fallback ---
AI_CONSECUTIVE_FAILURE_LIMIT: Final = 3
AI_PROBE_INTERVAL_S: Final = 60.0  # wall-clock
LIVE_AI_INTERVAL_S: Final = 15.0  # live sessions: wall-clock seconds between Gemini calls (0.4.0)
AI_SESSION_MAX_CALLS: Final = 40  # live sessions: max Gemini calls per session, then adaptive fallback (0.4.0)

# --- Google data ---
GOOGLE_CACHE_TTL_S: Final = 1800.0  # 30 min

# --- Deterministic id recipe ---
ID_COORD_DECIMALS: Final = 5
ID_BEARING_STEP_DEG: Final = 5
ID_HASH_HEX_LEN: Final = 8

# --- Demand ---
# Per-entry flow (veh/h) for each level. Entry flow = LEVEL_FLOW[level] * multiplier * entry_override.
# v0.3.0 retune (was 150/300/500/700): calibrated on the demo grid so that low/medium are clearly
# under capacity, high is near it and rush is visibly over it without gridlock (docs/results.md).
LEVEL_FLOW_VEH_PER_H: Final = {"low": 120.0, "medium": 200.0, "high": 280.0, "rush": 380.0}
BASE_LEVEL: Final = "medium"  # DemandEntry.scale is relative to this level's flow
DEMAND_MULTIPLIER_MIN: Final = 0.2
DEMAND_MULTIPLIER_MAX: Final = 3.0
ENTRY_OVERRIDE_MAX: Final = 5.0  # per-entry scale override, 0 < x <= this (0 not allowed: use a small value)
# Reserved (unused since v0.2.0, no external traffic data): congestion ratio -> scale heuristic.
DEMAND_SCALE_MIN: Final = 0.4
DEMAND_SCALE_MAX: Final = 1.6
DEMAND_RATIO_LO: Final = 1.0
DEMAND_RATIO_HI: Final = 2.0

# --- Areas ---
MAX_BBOX_SIDE_M: Final = 3000.0
GRID_AREA_ID: Final = "area_grid_mock"  # the synthetic demo city (backend/data/grid_network.json)
CONSOLIDATE_RADIUS_M: Final = 20.0  # graph nodes closer than this are merged into one junction
SIGNAL_SNAP_M: Final = 30.0  # an OSM traffic_signals node within this distance marks the junction as signalised
AREA_RATE_LIMIT_PER_MIN: Final = 10  # new area computations per client per minute (cache hits are free)

# --- Geocoding ---
NOMINATIM_MIN_INTERVAL_S: Final = 1.0  # Nominatim usage policy: max 1 request/s
