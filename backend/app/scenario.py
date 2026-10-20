"""Assembles MILP inputs from the DB for a given scenario (baseline or pass-closure)."""
from . import config
from .db import kv_get
from .graph import build_graph
from .router import route_matrix
from .forecast import predict


def load_state(conn):
    """Current FOB/depot stock and buffers from the DB."""
    fob_stock, fob_buf = {}, {}
    depot_stock, depot_buf = {}, {}
    for r in conn.execute("SELECT * FROM stock"):
        key = (r["node_id"], r["class_id"])
        if r["node_id"] in config.DEPOTS:
            depot_stock[key] = r["on_hand"]
            depot_buf[key] = r["buffer"]
        else:
            fob_stock[key] = r["on_hand"]
            fob_buf[key] = r["buffer"]
    return fob_stock, fob_buf, depot_stock, depot_buf


def fobs(conn):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE kind='fob' ORDER BY id")]


def build_inputs(conn, models, start_date, horizon, closed_passes=None, scenario="baseline"):
    """Everything solve_plan() needs, plus routing details for the response."""
    closed = set(closed_passes or [])
    g = build_graph(conn, closed)
    fob_ids = fobs(conn)
    dem = predict(conn, models, start_date, horizon)
    # every FOB must have a forecast entry for every class (pad with zeros if short)
    for f in fob_ids:
        for c in config.CLASSES:
            vals = dem.get((f, c))
            if vals is None or len(vals) < horizon:
                dem[(f, c)] = (vals or [])[:horizon] + [0.0] * (horizon - len(vals or []))
    fob_stock, fob_buf, depot_stock, depot_buf = load_state(conn)
    matrix, router_ms = route_matrix(g, config.DEPOTS, fob_ids + config.DEPOTS[1:], timed=True)
    route_cost = {k: v["cost_km"] for k, v in matrix.items() if v}
    west_closed = bool(closed & {"ZOJILA", "NAMIKALA", "FOTULA"})
    south_closed = bool(closed & {"ROHTANG", "BARALACHA", "TANGLANGLA"})
    cap_w = {c: ([0.0] * horizon if west_closed
                 else [config.INFLOW_WEST_CAP[c]] * horizon) for c in config.CLASSES}
    cap_s = {c: ([0.0] * horizon if south_closed
                 else [config.INFLOW_SOUTH_CAP[c]] * horizon) for c in config.CLASSES}
    return {
        "graph": g,
        "demand": dem,
        "fob_stock0": fob_stock,
        "fob_buffer": fob_buf,
        "depot_stock0": depot_stock,
        "depot_buffer": depot_buf,
        "route_cost_km": route_cost,
        "routes": matrix,
        "cap_west": cap_w,
        "cap_south": cap_s,
        "closed_passes": sorted(closed),
        "west_closed": west_closed,
        "south_closed": south_closed,
        "router_time_ms": round(router_ms, 1),
        "start_date": start_date,
        "horizon": horizon,
        "scenario": scenario,
    }
