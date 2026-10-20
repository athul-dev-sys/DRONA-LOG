"""Builds the NetworkX routing graph from the DB, honouring pass closures."""
import networkx as nx

from . import config


def build_graph(conn, closed_passes=None):
    """Undirected road graph. Closed pass nodes are removed entirely
    (all corridors through them become impassable)."""
    closed = set(closed_passes or [])
    g = nx.Graph()
    for r in conn.execute("SELECT * FROM nodes"):
        if r["kind"] == "pass" and r["id"] in closed:
            continue
        g.add_node(r["id"], name=r["name"], kind=r["kind"], lat=r["lat"], lon=r["lon"],
                   elev_m=r["elev_m"], corridor=r["corridor"])
    for r in conn.execute("SELECT * FROM edges"):
        if r["u"] not in g or r["v"] not in g:
            continue
        g.add_edge(
            r["u"], r["v"],
            distance_km=r["distance_km"],
            elev_gain_m=r["elev_gain_m"],
            gradient_pct=r["gradient_pct"],
            corridor=r["corridor"],
            weight=edge_weight(r["distance_km"], r["gradient_pct"]),
        )
    return g


def edge_weight(distance_km, gradient_pct):
    """Cost-km = distance + K * gradient%. Climbs into high passes are penalised."""
    return distance_km + config.GRADIENT_K * gradient_pct


def astar_heuristic(g, goal):
    node = g.nodes[goal]
    import math

    def h(u, v=None):
        other = g.nodes[u]
        r = 6371.0
        p1, p2 = math.radians(other["lat"]), math.radians(node["lat"])
        dp = p2 - p1
        dl = math.radians(node["lon"] - other["lon"])
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))  # admissible: true cost >= great-circle
    return h
