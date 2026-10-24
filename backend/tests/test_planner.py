"""Acceptance criterion 1:
Seed + forecast + MILP produce a feasible plan with ZERO stockouts on the
baseline scenario.
Acceptance criterion 2 (routing part):
Router honours pass closures and produces valid alternates.
"""
import networkx as nx
import pytest

from app import config
from app.allocator import solve_plan
from app.graph import build_graph
from app.router import gateway_routes, route, route_matrix
from app.scenario import build_inputs


def test_baseline_plan_feasible_zero_stockouts(conn, models):
    inputs = build_inputs(conn, models, config.SIM_TODAY, config.PLAN_HORIZON_DAYS,
                          closed_passes=[], scenario="baseline")
    plan = solve_plan(
        inputs["demand"], inputs["fob_stock0"], inputs["fob_buffer"],
        inputs["depot_stock0"], inputs["route_cost_km"],
        inputs["cap_west"], inputs["cap_south"],
        depot_buffer=inputs["depot_buffer"])
    assert plan["status"] in ("OPTIMAL", "FEASIBLE")
    assert plan["solve_time_ms"] > 0
    assert plan["kpi"]["stockout_units"] == pytest.approx(0.0, abs=1e-3), \
        f"baseline plan has stockouts: {plan['kpi']['stockout_units']}"
    assert len(plan["deliveries"]) > 0, "plan should actually move supplies"
    # every delivery follows a route that exists and respects capacity
    for dl in plan["deliveries"]:
        load = sum(dl["classes"].values())
        # SCIP feasibility tolerance can exceed the hard bound by ~1e-3 on
        # continuous load variables; anything within 0.1% of a truck is fine.
        assert load <= config.TRUCK_CAPACITY * dl["trucks"] + 1e-3
        assert dl["trucks"] <= config.FLEET[dl["from"]]
    # FOB stock never goes negative (short vars only cover genuine unmet demand
    # at zero stock; here we require the zero-stockout objective be met)
    for fob, per_class in plan["stock_projection"].items():
        for c, series in per_class.items():
            assert all(v >= -1e-6 for v in series)


def test_forecast_shape(conn, models):
    from app.forecast import predict
    dem = predict(conn, models, config.SIM_TODAY, 7)
    fob_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE kind='fob'")]
    for f in fob_ids:
        for c in config.CLASSES:
            vals = dem[(f, c)]
            assert len(vals) == 7
            assert all(v >= 0 for v in vals)
            assert any(v > 0 for v in vals)


def test_router_gradient_penalty(conn):
    g = build_graph(conn)
    r = route(g, "LEH", "DEMCHOK")
    assert r is not None
    assert r["distance_km"] > 0
    assert r["cost_km"] >= r["distance_km"], "gradient penalty must add cost"
    assert r["gradient_penalty_km"] > 0


def test_zojila_closure_severs_west_corridor(conn):
    g = build_graph(conn)
    assert gateway_routes(g)["west"] is not None
    g_closed = build_graph(conn, closed_passes=["ZOJILA"])
    gw = gateway_routes(g_closed)
    assert gw["west"] is None, "Srinagar->Leh must be unreachable with Zojila closed"
    assert gw["south"] is not None, "Manali->Leh alternate must exist"
    assert "ZOJILA" not in gw["south"]["nodes"]


def test_khardungla_closure_alternate_route(conn):
    g = build_graph(conn)
    normal = route(g, "LEH", "DISKIT")
    assert "KHARDUNGLA" in normal["nodes"]
    g2 = build_graph(conn, closed_passes=["KHARDUNGLA"])
    alt = route(g2, "LEH", "DISKIT")
    assert alt is not None, "alternate via Wari La must exist"
    assert "KHARDUNGLA" not in alt["nodes"]
    assert "WARILA" in alt["nodes"]
    assert alt["distance_km"] > normal["distance_km"]  # detour is longer


def test_all_fobs_reachable_baseline_and_closure(conn):
    fobs = [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE kind='fob'")]
    for closed in ([], ["ZOJILA"], ["KHARDUNGLA"], ["TANGLANGLA"]):
        g = build_graph(conn, closed_passes=closed)
        m = route_matrix(g, config.DEPOTS, fobs)
        for pair, r in m.items():
            assert r is not None, f"{pair} unreachable with {closed} closed"
            for n in r["nodes"]:
                assert n not in closed
