"""Acceptance criterion 2:
Closing a pass produces a valid alternate route and a new plan; solve time
displayed (returned by the endpoint and shown in the UI).
Also covers the API surface: network, forecast, plan, events, metrics.
"""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_network_shape(client):
    r = client.get("/api/network")
    assert r.status_code == 200
    d = r.json()
    kinds = {n["kind"] for n in d["nodes"]}
    assert {"depot", "staging", "fob", "pass", "gateway"} <= kinds
    assert len([n for n in d["nodes"] if n["kind"] == "fob"]) == 10
    assert len(d["edges"]) > 0
    assert len(d["stock"]) == 13 * 3  # 10 FOBs + 3 depots, 3 classes
    # map needs both corridor geometries even while one is closed
    assert d["gateway_routes"]["west"]["distance_km"] > 0
    assert d["gateway_routes"]["south"]["distance_km"] > 0
    assert d["corridor_open"] == {"west": True, "south": True}


def test_plan_zero_stockouts(client):
    r = client.get("/api/plan")
    assert r.status_code == 200
    p = r.json()
    assert p["status"] in ("OPTIMAL", "FEASIBLE")
    assert p["kpi"]["stockout_units"] <= 0.001
    assert p["solve_time_ms"] > 0
    assert len(p["deliveries"]) > 0
    assert p["gateway_routes"]["west"] is not None


def test_forecast_endpoint(client):
    r = client.get("/api/forecast?days=7")
    assert r.status_code == 200
    d = r.json()
    assert len(d["dates"]) == 7
    assert len(d["fobs"]) == 10
    for fob in d["fobs"].values():
        for c in ("I", "III", "V"):
            assert len(fob["forecast"][c]["daily"]) == 7


def test_pass_closure_flow(client):
    # baseline plan first
    base = client.get("/api/plan").json()

    r = client.post("/api/events/pass-closure",
                    json={"pass_id": "ZOJILA", "action": "closed", "days": 5})
    assert r.status_code == 200
    ev = r.json()

    # solve time must be reported (this is what the UI displays in ms)
    assert ev["solve_time_ms"] > 0
    assert ev["router_time_ms"] > 0
    # west corridor severed, south corridor is the valid alternate
    assert ev["west_corridor_open"] is False
    assert ev["corridor_broken"] is True
    assert ev["gateway_routes"]["west"] is None
    assert ev["gateway_routes"]["south"] is not None
    assert "ZOJILA" not in ev["gateway_routes"]["south"]["nodes"]

    # network payload keeps the closed corridor's geometry so the map can
    # draw it red (and reports the corridor as shut)
    net = client.get("/api/network").json()
    assert net["corridor_open"]["west"] is False
    assert net["gateway_routes"]["west"]["distance_km"] > 0

    # new plan is feasible, still zero stockouts, and differs from the old one
    new_plan = ev["new_plan"]
    assert new_plan["status"] in ("OPTIMAL", "FEASIBLE")
    assert new_plan["kpi"]["stockout_units"] <= 0.001
    diff = ev["diff"]
    assert diff["counts"]["added"] > 0 or diff["counts"]["changed"] > 0, \
        "closure must change the plan"
    assert ev["old_plan_id"] != ev["new_plan_id"]

    # the new plan costs more (south corridor procurement premium) - sanity
    assert new_plan["objective_inr"] > base["objective_inr"]

    # event is logged
    events = client.get("/api/events").json()
    assert any(e["pass_id"] == "ZOJILA" and e["action"] == "closed"
               for e in events)

    # reopening restores the west corridor
    r2 = client.post("/api/events/pass-closure",
                     json={"pass_id": "ZOJILA", "action": "reopened"})
    assert r2.status_code == 200
    ev2 = r2.json()
    assert ev2["west_corridor_open"] is True
    assert ev2["gateway_routes"]["west"] is not None


def test_unknown_pass_404(client):
    r = client.post("/api/events/pass-closure",
                    json={"pass_id": "NOT_A_PASS", "action": "closed"})
    assert r.status_code == 404


def test_metrics_simulated_label(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["label"] == "SIMULATED"
    assert m["scenario"]["days"] == 30
    assert m["scenario"]["closure"]["pass"] == "ZOJILA"
    # metrics must be computed, not hardcoded: naive and drona numbers differ
    assert m["naive"]["hold_events"] > 0
    assert m["drona"]["hold_events"] == 0
    assert m["drona"]["stockout_units"] < m["naive"]["stockout_units"]
    # delta percentages consistent with the raw numbers
    assert m["delta"]["stockout_reduction_pct"] >= 50
