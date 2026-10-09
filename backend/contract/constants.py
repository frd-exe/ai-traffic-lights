"""Single source of truth for numeric constants. Import from here; never hard-code.

All values are SI (seconds, metres, metres/second) unless the name says otherwise.
"""

from typing import Final

CONTRACT_VERSION: Final = "0.1.0"

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

# --- Google data ---
GOOGLE_CACHE_TTL_S: Final = 1800.0  # 30 min

# --- Deterministic id recipe ---
ID_COORD_DECIMALS: Final = 5
ID_BEARING_STEP_DEG: Final = 5
ID_HASH_HEX_LEN: Final = 8

# --- Demand mapping (heuristic, tunable) ---
DEMAND_SCALE_MIN: Final = 0.4  # at congestion_ratio <= DEMAND_RATIO_LO
DEMAND_SCALE_MAX: Final = 1.6  # at congestion_ratio >= DEMAND_RATIO_HI
DEMAND_RATIO_LO: Final = 1.0
DEMAND_RATIO_HI: Final = 2.0
# Base per-entry flow (veh/h) for each baseline level; google scales multiply the "medium" flow.
LEVEL_FLOW_VEH_PER_H: Final = {"low": 150.0, "medium": 300.0, "high": 500.0, "rush": 700.0}
BASE_LEVEL: Final = "medium"

# --- Area limits ---
MAX_BBOX_SIDE_M: Final = 3000.0
