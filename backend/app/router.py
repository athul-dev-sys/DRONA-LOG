"""Elevation-penalized A* routing on the Ladakh road network."""
import time

import networkx as nx

from . import config
from .graph import astar_heuristic, build_graph


def route(g, src, dst):
    """A* over cost-km (distance + gradient penalty).

    Returns dict with path (node ids), geometry, distance_km, cost_km,
    max_elev_m and per-hop legs, or None if unreachable.
    """
    if src == dst:
        n = g.nodes[src]
        return _mk_route(g, [src], [0.0], [0.0], n["elev_m"])
    try:
        path = nx.astar_path(g, src, dst, heuristic=astar_heuristic(g, dst), weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    dists, costs = [], []
    for u, v in zip(path, path[1:]):
        e = g.edges[u, v]
        dists.append(e["distance_km"])
        costs.append(e["weight"])
    max_elev = max(g.nodes[n]["elev_m"] for n in path)
    return _mk_route(g, path, dists, costs, max_elev)


def _mk_route(g, path, dists, costs, max_elev):
    return {
        "from": path[0],
        "to": path[-1],
        "nodes": path,
        "geometry": [[g.nodes[n]["lat"], g.nodes[n]["lon"]] for n in path],
        "distance_km": round(sum(dists), 2),
        "cost_km": round(sum(costs), 2),
        "gradient_penalty_km": round(sum(costs) - sum(dists), 2),
        "max_elev_m": max_elev,
        "legs": [
            {"u": u, "v": v, "distance_km": g.edges[u, v]["distance_km"],
             "gradient_pct": g.edges[u, v]["gradient_pct"],
             "corridor": g.edges[u, v]["corridor"]}
            for u, v in zip(path, path[1:])
        ],
    }


def route_matrix(g, sources, sinks, timed=False):
    """Routes between every source/sink pair. Unreachable pairs map to None."""
    t0 = time.perf_counter()
    out = {}
    for s in sources:
        for t in sinks:
            if s == t:
                continue
            out[(s, t)] = route(g, s, t)
    ms = (time.perf_counter() - t0) * 1000
    return (out, ms) if timed else out


def gateway_routes(g):
    """Routes from the two gateways into the base depot - used to show the
    active and alternate supply corridors on the map."""
    return {
        "west": route(g, "SRINAGAR", "LEH"),
        "south": route(g, "MANALI", "LEH"),
    }
