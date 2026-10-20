"""MILP allocation engine (OR-Tools / SCIP).

Minimises  transport cost + stockout penalties  s.t.
  * vehicle capacity (integer trucks) and per-depot daily fleet,
  * depot stock balance (incl. gateway inflow into the base depot),
  * FOB stock balance with 1-day delivery lead,
  * safety buffers (soft, heavily penalised) and zero-stockout (soft, huge penalty).
Solve time is measured and logged in ms.
"""
import time

from ortools.linear_solver import pywraplp

from . import config


def solve_plan(
    demand,            # {(fob, class): [qty per day, len T]} forecast demand
    fob_stock0,        # {(fob, class): units}
    fob_buffer,        # {(fob, class): units}
    depot_stock0,      # {(depot, class): units}
    route_cost_km,     # {(depot, sink): cost-km} (None = unreachable)
    cap_west,          # {class: [cap per day]} (0 on days the west corridor is closed)
    cap_south,         # {class: [cap per day]}
    fleet=None,
    classes=None,
    depot_buffer=None, # {(depot, class): units} war-reserve level kept at depots
):
    classes = classes or config.CLASSES
    fleet = fleet or config.FLEET
    depots = config.DEPOTS
    fobs = sorted({f for f, _ in demand})
    staging = [d for d in depots if d != "LEH"]
    sinks = fobs + staging
    T = len(next(iter(demand.values())))

    t_start = time.perf_counter()
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        raise RuntimeError("SCIP solver unavailable")
    solver.SetNumThreads(1)
    solver.SetTimeLimit(config.SOLVE_TIME_LIMIT_MS)
    solver.SetSolverSpecificParametersAsString(
        f"limits/gap {config.SOLVE_GAP_REL}\nlimits/time {config.SOLVE_TIME_LIMIT_MS // 1000}"
    )

    x = {}      # (d, j, c, t) units dispatched d->j on day t (arrives t+1)
    n = {}      # (d, j, t) trucks dispatched
    for d in depots:
        for j in sinks:
            if d == j or route_cost_km.get((d, j)) is None:
                continue
            for t in range(T):
                n[d, j, t] = solver.IntVar(0, fleet[d], f"n_{d}_{j}_{t}")
                for c in classes:
                    x[d, j, c, t] = solver.NumVar(
                        0, solver.infinity(), f"x_{d}_{j}_{c}_{t}")

    sf = {}     # FOB stock start-of-day
    short = {}
    bdef = {}
    for p in fobs:
        for c in classes:
            for t in range(T + 1):
                sf[p, c, t] = solver.NumVar(0, solver.infinity(), f"s_{p}_{c}_{t}")
            for t in range(T):
                short[p, c, t] = solver.NumVar(0, solver.infinity(), f"sh_{p}_{c}_{t}")
                bdef[p, c, t] = solver.NumVar(0, solver.infinity(), f"bd_{p}_{c}_{t}")
            solver.Add(sf[p, c, 0] == fob_stock0.get((p, c), 0.0))
            for t in range(T):
                arrivals = 0.0
                if t >= 1:
                    arrivals = sum(x.get((d, p, c, t - 1), 0)
                                   for d in depots if (d, p, c, t - 1) in x)
                solver.Add(
                    sf[p, c, t + 1] == sf[p, c, t] + arrivals
                    - demand[(p, c)][t] + short[p, c, t])
                solver.Add(sf[p, c, t] + bdef[p, c, t] >= fob_buffer.get((p, c), 0.0))

    sd = {}     # depot stock start-of-day
    bdef_d = {}  # depot reserve shortfall at end of horizon
    in_w, in_s = {}, {}
    depot_buffer = depot_buffer or {}
    for d in depots:
        for c in classes:
            for t in range(T + 1):
                sd[d, c, t] = solver.NumVar(0, solver.infinity(), f"sd_{d}_{c}_{t}")
            solver.Add(sd[d, c, 0] == depot_stock0.get((d, c), 0.0))
            if depot_buffer.get((d, c), 0) > 0:
                # War reserve is enforced once, at end of horizon: keeps the
                # model discriminating (per-day depot constraints create a
                # symmetric plateau that stalls branch-and-bound).
                bdef_d[d, c] = solver.NumVar(0, solver.infinity(), f"bdd_{d}_{c}")
                solver.Add(sd[d, c, T] + bdef_d[d, c] >= depot_buffer[(d, c)])
    for c in classes:
        for t in range(T):
            in_w[c, t] = solver.NumVar(0, cap_west[c][t], f"iw_{c}_{t}")
            in_s[c, t] = solver.NumVar(0, cap_south[c][t], f"is_{c}_{t}")
            for d in depots:
                outflow = sum(x.get((d, j, c, t), 0) for j in sinks if (d, j, c, t) in x)
                inflow = (in_w[c, t] + in_s[c, t]) if d == "LEH" else 0.0
                solver.Add(sd[d, c, t + 1] == sd[d, c, t] + inflow - outflow)

    for d in depots:
        for t in range(T):
            for j in sinks:
                if (d, j, t) not in n:
                    continue
                load = sum(x[d, j, c, t] for c in classes if (d, j, c, t) in x)
                solver.Add(load <= config.TRUCK_CAPACITY * n[d, j, t])
            used = sum(n[d, j, t] for j in sinks if (d, j, t) in n)
            solver.Add(used <= fleet[d])

    obj = solver.Objective()
    for (d, j, c, t), var in x.items():
        obj.SetCoefficient(var, config.RATE_PER_UNIT_KM * route_cost_km[(d, j)])
    for (d, j, t), var in n.items():
        obj.SetCoefficient(var, config.TRUCK_FIXED_COST)
    for var in short.values():
        obj.SetCoefficient(var, config.STOCKOUT_PENALTY)
    for var in bdef.values():
        obj.SetCoefficient(var, config.BUFFER_PENALTY)
    for var in bdef_d.values():
        obj.SetCoefficient(var, config.BUFFER_PENALTY)
    for c in classes:
        for t in range(T):
            # tiny per-day escalation breaks the "which day to procure" symmetry
            obj.SetCoefficient(in_w[c, t],
                               config.PROC_COST_WEST + config.INFLOW_DAY_TIEBREAK * t)
            obj.SetCoefficient(in_s[c, t],
                               config.PROC_COST_SOUTH + config.INFLOW_DAY_TIEBREAK * t)
    obj.SetMinimization()

    status = solver.Solve()
    wall_ms = solver.WallTime()
    total_ms = (time.perf_counter() - t_start) * 1000.0

    status_name = {solver.OPTIMAL: "OPTIMAL", solver.FEASIBLE: "FEASIBLE",
                   solver.INFEASIBLE: "INFEASIBLE", solver.ABNORMAL: "ABNORMAL"}.get(status, str(status))
    feasible = status in (solver.OPTIMAL, solver.FEASIBLE)

    plan = {
        "status": status_name,
        "solve_time_ms": round(total_ms, 1),
        "solver_wall_time_ms": round(wall_ms, 1),
        "objective_inr": round(solver.Objective().Value(), 2) if feasible else 0.0,
        "horizon_days": T,
    }

    if not feasible:
        plan["deliveries"] = []
        plan["inflow"] = {"west": [], "south": []}
        plan["stock_projection"] = {}
        plan["depot_projection"] = {}
        plan["kpi"] = {"stockout_units": None, "buffer_deficit_units": None,
                       "trucks": 0, "transport_inr": 0}
        print(f"[allocator] status={status_name} in {total_ms:.0f} ms")
        return plan

    deliveries = []
    for (d, j, t), nvar in sorted(n.items()):
        trucks = int(round(nvar.SolutionValue()))
        if trucks <= 0:
            continue
        per_class = {}
        for c in classes:
            v = x.get((d, j, c, t))
            if v is not None and v.SolutionValue() > 1e-6:
                per_class[c] = round(v.SolutionValue(), 3)
        cost = sum(route_cost_km[(d, j)] * config.RATE_PER_UNIT_KM * q for q in per_class.values())
        cost += config.TRUCK_FIXED_COST * trucks
        deliveries.append({
            "day": t, "from": d, "to": j, "trucks": trucks,
            "classes": per_class, "cost_inr": round(cost, 0),
            "cost_km": route_cost_km[(d, j)],
        })
    inflow = {"west": [], "south": []}
    for t in range(T):
        inflow["west"].append({c: round(in_w[c, t].SolutionValue(), 2) for c in classes})
        inflow["south"].append({c: round(in_s[c, t].SolutionValue(), 2) for c in classes})
    stock_projection = {p: {c: [round(sf[p, c, t].SolutionValue(), 3) for t in range(T + 1)]
                            for c in classes} for p in fobs}
    depot_projection = {d: {c: [round(sd[d, c, t].SolutionValue(), 3) for t in range(T + 1)]
                            for c in classes} for d in depots}
    stockout_units = max(0.0, sum(v.SolutionValue() for v in short.values()))
    buffer_deficit_units = max(0.0, sum(v.SolutionValue() for v in bdef.values())
                               + sum(v.SolutionValue() for v in bdef_d.values()))
    trucks_total = sum(int(round(nvar.SolutionValue())) for nvar in n.values())
    transport_inr = sum(dl["cost_inr"] for dl in deliveries)

    plan.update({
        "deliveries": deliveries,
        "inflow": inflow,
        "stock_projection": stock_projection,
        "depot_projection": depot_projection,
        "kpi": {
            "stockout_units": round(stockout_units, 3),
            "buffer_deficit_units": round(buffer_deficit_units, 3),
            "trucks": trucks_total,
            "transport_inr": round(transport_inr, 0),
            "units_by_class": {
                c: round(sum(q for dl in deliveries for cc, q in dl["classes"].items() if cc == c), 3)
                for c in classes},
        },
    })
    print(f"[allocator] status={status_name} obj={plan['objective_inr']:.0f} INR "
          f"trucks={trucks_total} stockouts={stockout_units:.2f} "
          f"buffer_deficit={buffer_deficit_units:.2f} solve={total_ms:.0f} ms")
    return plan
