import os

DB_PATH = os.environ.get("DRONA_DB", os.path.join(os.path.dirname(__file__), "..", "data", "drona.db"))

# Simulation clock
SIM_TODAY = "2026-10-01"
HISTORY_START = "2025-10-01"          # 12 months of daily history
HISTORY_END = "2026-09-30"
EVAL_DAYS = 30                        # "actual future" horizon used for honest metrics
FORECAST_DAYS = 7                     # operational forecast horizon
PLAN_HORIZON_DAYS = 7                 # MILP horizon

# Router
GRADIENT_K = 6.0                      # cost-km added per 1% of gradient
ROAD_FACTOR = 1.25                    # road distance vs great-circle

# Vehicles / economics (synthetic INR figures)
TRUCK_CAPACITY = 10.0                 # units per truck (t for I/V, kl for III)
RATE_PER_UNIT_KM = 42.0               # INR per unit-km
TRUCK_FIXED_COST = 4000.0             # INR per truck dispatch
FLEET = {"LEH": 24, "KARU": 8, "UPSHI": 8}   # trucks available per depot per day
STOCKOUT_PENALTY = 500_000.0          # INR per unit of unmet demand
BUFFER_PENALTY = 60_000.0             # INR per unit-day below safety buffer
# Gateway inflow into LEH (units/day) and procurement cost (INR/unit)
INFLOW_WEST_CAP = {"I": 6.0, "III": 26.0, "V": 2.5}    # Zoji La corridor (ample)
INFLOW_SOUTH_CAP = {"I": 2.5, "III": 11.0, "V": 1.2}   # Manali corridor (constrained)
PROC_COST_WEST = 18_000.0
PROC_COST_SOUTH = 24_000.0            # +33% longer haul
INFLOW_DAY_TIEBREAK = 40.0            # INR/day preference for earlier procurement
BUFFER_DAYS = 7                       # safety buffer = BUFFER_DAYS * mean daily demand
DELIVERY_LEAD_DAYS = 1                # dispatch day t -> arrives day t+1

CLASSES = ["I", "III", "V"]
DEPOTS = ["LEH", "KARU", "UPSHI"]
SOLVE_TIME_LIMIT_MS = 10000
SOLVE_GAP_REL = 0.01
