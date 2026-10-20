"""Honest-metrics simulation.

Runs a 30-day day-by-day supply simulation on the SAME seeded scenario
(same starting stocks, same synthetic "actual" consumption, same scripted
Zoji La closure during days 10-16) for two policies:

  NAIVE  - per-FOB reorder from the nearest depot, fixed 3-day lead time,
           no forecasting (trailing 7-day mean), no rerouting: a closed pass
           holds the convoy, gateway procurement waits for the west corridor.
  DRONA  - LightGBM forecast + MILP replan (3-day rolling horizon, executed
           in batch), 1-day lead, corridor-aware: inflow switches to the
           Manali corridor when Zoji La closes, trucks reroute around
           closed passes, immediate replan on closure events.

Every number shown in the UI comes from this simulation - nothing is hardcoded.
"""
import datetime as dt

from . import config
from .allocator import solve_plan
from .graph import build_graph
from .router import route_matrix
from .forecast import predict

SIM_DAYS = config.EVAL_DAYS
CLOSURE = ("ZOJILA", 10, 16)          # pass, closed from day a to day b (inclusive)
NAIVE_LEAD_DAYS = 3
DRONA_LEAD_DAYS = config.DELIVERY_LEAD_DAYS
REPLAN_HORIZON = 3
WEST_PASSES = {"ZOJILA", "NAMIKALA", "FOTULA"}


def _closed_on(day):
    p, a, b = CLOSURE
    return p if a <= day <= b else None


class SimState:
    def __init__(self, conn):
        self.fob_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM nodes WHERE kind='fob' ORDER BY id")]
        self.actuals = {}
        for r in conn.execute("SELECT date, fob_id, class_id, qty FROM actual_future "
                              "WHERE date <= (SELECT MIN(date) FROM actual_future) + ? ORDER BY date",
                              (SIM_DAYS - 1,)):
            self.actuals.setdefault((r["fob_id"], r["class_id"]), []).append(r["qty"])
        self.fob_stock, self.fob_buf, self.depot_stock, self.depot_buf = {}, {}, {}, {}
        for r in conn.execute("SELECT * FROM stock"):
            k = (r["node_id"], r["class_id"])
            tgt = ((self.depot_stock, self.depot_buf) if r["node_id"] in config.DEPOTS
                   else (self.fob_stock, self.fob_buf))
            tgt[0][k] = r["on_hand"]
            tgt[1][k] = r["buffer"]
        self.total_units = sum(q for vals in self.actuals.values() for q in vals)


class Ledger:
    def __init__(self):
        self.stockouts_units = 0.0
        self.below_buffer_unit_days = 0.0
        self.transport_cost = 0.0     # trucking (fixed + per-unit-km)
        self.proc_cost = 0.0          # gateway procurement
        self.truck_km = 0.0
        self.trucks = 0
        self.units_delivered = 0.0
        self.hold_events = 0          # convoys held / requisitions unmet
        self.daily_stockouts = []
        self.daily_stock = {}

    def consume(self, st, day):
        short_day = 0.0
        for f in st.fob_ids:
            for c in config.CLASSES:
                k = (f, c)
                q = st.actuals[k][day]
                take = min(st.fob_stock[k], q)
                short = q - take
                if short > 1e-9:
                    short_day += short
                    self.stockouts_units += short
                st.fob_stock[k] -= take
                if st.fob_stock[k] < st.fob_buf[k]:
                    self.below_buffer_unit_days += st.fob_buf[k] - st.fob_stock[k]
        self.daily_stockouts.append(round(short_day, 3))
        self.daily_stock[day] = {f: sum(st.fob_stock[(f, c)] for c in config.CLASSES)
                                 for f in st.fob_ids}

    def trucking(self, trucks, dist_km, units):
        self.trucks += trucks
        self.truck_km += trucks * dist_km
        self.transport_cost += trucks * config.TRUCK_FIXED_COST + \
            config.RATE_PER_UNIT_KM * dist_km * units
        self.units_delivered += units


def run_naive(conn):
    st = SimState(conn)
    g = build_graph(conn)
    matrix = route_matrix(g, config.DEPOTS, st.fob_ids + config.DEPOTS[1:])
    L = Ledger()

    # trailing-mean "forecast" (naive has no model)
    mean_dem = {(r["fob_id"], r["class_id"]): r["m"] for r in conn.execute(
        "SELECT fob_id, class_id, AVG(qty) m FROM history "
        "WHERE date >= (SELECT MAX(date) FROM history) GROUP BY fob_id, class_id")}
    theatre_mean = {c: mean_dem_theatre(mean_dem, c) for c in config.CLASSES}

    nearest, pass_on_route = {}, {}
    for f in st.fob_ids:
        best, bd = None, 1e18
        for d in config.DEPOTS:
            rt = matrix.get((d, f))
            if rt and rt["distance_km"] < bd:
                best, bd = d, rt["distance_km"]
        nearest[f] = (best, bd)
        rt = matrix.get((best, f))
        pass_on_route[f] = set(n for n in (rt["nodes"] if rt else [])
                               if g.nodes[n].get("kind") == "pass")
    pass_on_route["KARU"] = {"KHARDUNGLA"} if "KHARDUNGLA" in g else set()
    pass_on_route["UPSHI"] = set()

    on_order = {n: [] for n in st.fob_ids + config.DEPOTS}   # node -> [(arrival, {class: qty})]

    def cover(node, c):
        return sum(q for o in on_order[node] for cc, q in o[1].items() if cc == c)

    def convoy(src, dst, dist, loads):
        """One consolidated multi-class convoy src->dst. loads = {class: qty}."""
        total = sum(loads.values())
        trucks = max(1, int(-(-total // config.TRUCK_CAPACITY)))
        for c, q in loads.items():
            st.depot_stock[(src, c)] -= q
        L.trucking(trucks, dist, total)
        on_order[dst].append((day + NAIVE_LEAD_DAYS, loads))

    def reorder_loads(src, dst_node, levels, position):
        """Plain reorder-point ordering: when position < trigger, order up to level.
        No lead-time demand compensation, no forecasting - the legacy behaviour."""
        loads = {}
        for c, level in levels.items():
            if position[c] < level / 2:          # trigger: below safety buffer
                want = level - position[c]       # order back up to 2x buffer
                loads[c] = min(want, st.depot_stock[(src, c)])
        loads = {c: q for c, q in loads.items() if q > 1e-6}
        if not loads:
            L.hold_events += 1
            return None
        return loads

    for day in range(SIM_DAYS):
        closed = _closed_on(day)
        west_shut = closed in WEST_PASSES

        L.consume(st, day)

        # arrivals
        for n, orders in on_order.items():
            arrived = [o for o in orders if o[0] == day]
            on_order[n] = [o for o in orders if o[0] > day]
            for _, loads in arrived:
                tgt = st.depot_stock if n in config.DEPOTS else st.fob_stock
                for c, q in loads.items():
                    tgt[(n, c)] += q

        # gateway -> LEH: plain reorder point at reserve, west corridor only
        if not west_shut:
            for c in config.CLASSES:
                if st.depot_stock[("LEH", c)] + cover("LEH", c) < st.depot_buf[("LEH", c)]:
                    want = 2 * st.depot_buf[("LEH", c)] - st.depot_stock[("LEH", c)] - cover("LEH", c)
                    got = min(want, config.INFLOW_WEST_CAP[c] * NAIVE_LEAD_DAYS)
                    st.depot_stock[("LEH", c)] += got
                    L.proc_cost += got * config.PROC_COST_WEST
        else:
            L.hold_events += 1  # gateway convoy held at Zoji La

        # LEH -> staging: plain reorder point at staging reserve
        for s in config.DEPOTS[1:]:
            dist = matrix.get(("LEH", s), {}).get("distance_km", 1e9)
            if closed and closed in pass_on_route.get(s, set()):
                continue
            levels = {c: 2 * st.depot_buf[(s, c)] for c in config.CLASSES}
            position = {c: st.depot_stock[(s, c)] + cover(s, c) for c in config.CLASSES}
            loads = reorder_loads("LEH", s, levels, position)
            if loads:
                convoy("LEH", s, dist, loads)

        # depot -> FOB: nearest depot, plain reorder point at the safety buffer
        for f in st.fob_ids:
            d, dist = nearest[f]
            if closed and closed in pass_on_route[f]:
                L.hold_events += 1
                continue
            levels = {c: 2 * st.fob_buf[(f, c)] for c in config.CLASSES}
            position = {c: st.fob_stock[(f, c)] + cover(f, c) for c in config.CLASSES}
            loads = reorder_loads(d, f, levels, position)
            if loads:
                convoy(d, f, dist, loads)
    return L, st


def mean_dem_theatre(mean_dem, c):
    return sum(v for (f, cc), v in mean_dem.items() if cc == c)


def run_drona(conn, models):
    st = SimState(conn)
    L = Ledger()
    inflight = []   # [(arrival_day, dst, {class: qty})]
    dates = [r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM actual_future ORDER BY date LIMIT ?", (SIM_DAYS,))]

    def replan(day):
        closed = _closed_on(day)
        closed_set = {closed} if closed else set()
        g = build_graph(conn, closed_set)
        matrix = route_matrix(g, config.DEPOTS, st.fob_ids)
        route_cost = {k: v["cost_km"] for k, v in matrix.items() if v}
        dist_of = {k: v["distance_km"] for k, v in matrix.items() if v}
        start = (dt.date.fromisoformat(dates[day]) + dt.timedelta(days=1)).isoformat()
        dem = predict(conn, models, start, REPLAN_HORIZON)
        for f in st.fob_ids:
            for c in config.CLASSES:
                vals = dem.get((f, c), [])
                dem[(f, c)] = vals + [0.0] * (REPLAN_HORIZON - len(vals))
        west_closed = bool(closed_set & WEST_PASSES)
        cap_w = {c: ([0.0] * REPLAN_HORIZON if west_closed
                     else [config.INFLOW_WEST_CAP[c]] * REPLAN_HORIZON)
                 for c in config.CLASSES}
        cap_s = {c: [config.INFLOW_SOUTH_CAP[c]] * REPLAN_HORIZON for c in config.CLASSES}
        plan = solve_plan(dem, st.fob_stock, st.fob_buf, st.depot_stock, route_cost,
                          cap_w, cap_s, depot_buffer=st.depot_buf)
        # execute the full replan window in batch
        for dl in plan["deliveries"]:
            src, dst = dl["from"], dl["to"]
            moved = {}
            for c, q in dl["classes"].items():
                q = min(q, st.depot_stock[(src, c)])
                if q <= 1e-9:
                    continue
                st.depot_stock[(src, c)] -= q
                moved[c] = q
            if moved:
                d_km = dist_of.get((src, dst), 0)
                L.trucking(dl["trucks"], d_km, sum(moved.values()))
                inflight.append((day + 1 + dl["day"], dst, moved))
        for t in range(REPLAN_HORIZON):
            for c, q in plan["inflow"]["west"][t].items():
                if q > 0:
                    st.depot_stock[("LEH", c)] += q
                    L.proc_cost += q * config.PROC_COST_WEST
                    L.units_delivered += q
            for c, q in plan["inflow"]["south"][t].items():
                if q > 0:
                    st.depot_stock[("LEH", c)] += q
                    L.proc_cost += q * config.PROC_COST_SOUTH
                    L.units_delivered += q
        return plan

    prev_closed = None
    for day in range(SIM_DAYS):
        closed = _closed_on(day)
        L.consume(st, day)
        arrived = [o for o in inflight if o[0] == day]
        for _, dst, loads in arrived:
            tgt = st.depot_stock if dst in config.DEPOTS else st.fob_stock
            for c, q in loads.items():
                tgt[(dst, c)] += q
        inflight = [o for o in inflight if o[0] > day]
        # replan on cadence, immediately on closure state change, or when a
        # FOB runs below half its safety buffer (ops-room trigger)
        urgent = any(st.fob_stock[(f, c)] < 0.5 * st.fob_buf[(f, c)]
                     for f in st.fob_ids for c in config.CLASSES)
        if day < SIM_DAYS - 1 and (day % REPLAN_HORIZON == 0 or closed != prev_closed or urgent):
            replan(day)
        prev_closed = closed
    return L, st


def compute_metrics(conn, models):
    naive, st1 = run_naive(conn)
    drona, st2 = run_drona(conn, models)
    total_units = st1.total_units

    def pct(na, dr):
        if na <= 0:
            return 0.0
        return round(100.0 * (na - dr) / na, 1)

    n_cost = naive.transport_cost + naive.proc_cost
    d_cost = drona.transport_cost + drona.proc_cost
    n_cpu = n_cost / total_units if total_units else 0   # cost per unit of demand
    d_cpu = d_cost / total_units if total_units else 0
    n_fpu = naive.truck_km / total_units if total_units else 0
    d_fpu = drona.truck_km / total_units if total_units else 0

    # mean days-of-cover held at FOBs (inventory posture, both policies)
    fob_mean_dem = {f: sum(st1.actuals[(f, c)][d] for c in config.CLASSES for d in range(SIM_DAYS)) / SIM_DAYS
                    for f in st1.fob_ids}
    def mean_cover(L):
        vals = [stock / fob_mean_dem[f]
                for day, stocks in L.daily_stock.items()
                for f, stock in stocks.items() if fob_mean_dem[f] > 0]
        return round(sum(vals) / len(vals), 1) if vals else None
    n_cover, d_cover = mean_cover(naive), mean_cover(drona)

    return {
        "label": "SIMULATED",
        "scenario": {
            "days": SIM_DAYS,
            "closure": {"pass": CLOSURE[0], "from_day": CLOSURE[1], "to_day": CLOSURE[2]},
            "total_demand_units": round(total_units, 1),
            "note": ("Naive baseline: nearest-depot resupply, fixed 3-day lead, "
                     "trailing-mean demand, no rerouting. DRONA: LightGBM forecast + "
                     "MILP replan, 1-day lead, corridor-aware. Same seed, same demand. "
                     "Cost/fuel are compared per unit of demand served to the theatre."),
        },
        "naive": _summarise(naive, total_units),
        "drona": _summarise(drona, total_units),
        "delta": {
            "stockout_units": round(naive.stockouts_units - drona.stockouts_units, 2),
            "stockout_reduction_pct": pct(naive.stockouts_units, drona.stockouts_units),
            "hold_event_reduction_pct": pct(naive.hold_events, drona.hold_events),
            "cost_per_unit_naive_inr": round(n_cpu, 0),
            "cost_per_unit_drona_inr": round(d_cpu, 0),
            "cost_change_pct": pct(n_cpu, d_cpu),
            "fuel_per_unit_naive_km": round(n_fpu, 2),
            "fuel_per_unit_drona_km": round(d_fpu, 2),
            "fuel_change_pct": pct(n_fpu, d_fpu),
            "mean_days_cover_naive": n_cover,
            "mean_days_cover_drona": d_cover,
            "inventory_reduction_pct": pct(n_cover, d_cover),
            "buffer_violation_reduction_pct": pct(naive.below_buffer_unit_days,
                                                  drona.below_buffer_unit_days),
        },
        "series": {
            "days": list(range(SIM_DAYS)),
            "stockouts_naive": naive.daily_stockouts,
            "stockouts_drona": drona.daily_stockouts,
            "theater_stock_naive": [sum(v.values()) for v in naive.daily_stock.values()],
            "theater_stock_drona": [sum(v.values()) for v in drona.daily_stock.values()],
        },
    }


def _summarise(L, total_units):
    cost = L.transport_cost + L.proc_cost
    return {
        "stockout_units": round(L.stockouts_units, 2),
        "stockout_pct": round(100 * L.stockouts_units / total_units, 3) if total_units else 0.0,
        "below_buffer_unit_days": round(L.below_buffer_unit_days, 1),
        "transport_cost_inr": round(L.transport_cost, 0),
        "proc_cost_inr": round(L.proc_cost, 0),
        "total_cost_inr": round(cost, 0),
        "cost_per_unit_inr": round(cost / total_units, 0) if total_units else None,
        "truck_km": round(L.truck_km, 0),
        "fuel_per_unit_km": round(L.truck_km / total_units, 2) if total_units else None,
        "trucks": L.trucks,
        "hold_events": L.hold_events,
        "daily_stockouts": L.daily_stockouts,
    }
