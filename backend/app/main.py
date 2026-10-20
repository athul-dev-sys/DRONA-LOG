"""DRONA-LOG FastAPI backend.

Endpoints:
  GET  /api/health                 - liveness + db state
  GET  /api/network                - nodes, edges, closures for the map
  GET  /api/forecast?days=7        - LightGBM forecast per FOB
  GET  /api/plan                   - current 7-day MILP plan (solves + caches)
  GET  /api/plan/latest            - last stored plan
  POST /api/plan/recompute         - force a fresh solve
  POST /api/events/pass-closure    - close/reopen a pass: reroute + re-solve + diff
  GET  /api/events                 - pass event log
  GET  /api/metrics                - SIMULATED naive-vs-DRONA comparison (cached)
  POST /api/metrics/recompute      - re-run the 30-day comparison
  POST /api/sync                   - edge ledger reconciliation (hash-chain verify)
  GET  /api/ledger                 - server-side accepted ledger
"""
import datetime as dt
import json
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .allocator import solve_plan
from .db import connect, init_schema, is_seeded, kv_get, kv_set
from .forecast import ensure_models, forecast_payload
from .graph import build_graph
from .router import gateway_routes, route
from .scenario import build_inputs
from .seed import seed as seed_db
from .simulate import WEST_PASSES, compute_metrics

app = FastAPI(title="DRONA-LOG", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

_state = {"models": None, "metrics": None, "metrics_thread": None}
GENESIS = "0" * 64


def get_db():
    conn = connect()
    if not is_seeded(conn):
        conn.close()
        seed_db()
        conn = connect()
    return conn


def get_models(conn):
    if _state["models"] is None:
        _state["models"] = ensure_models(conn)
    return _state["models"]


@app.on_event("startup")
def startup():
    conn = get_db()
    get_models(conn)
    conn.close()

    def bg_metrics():
        t0 = time.perf_counter()
        conn = get_db()
        models = get_models(conn)
        m = compute_metrics(conn, models)
        conn.execute(
            "INSERT INTO metrics (computed_at, metrics_json) VALUES (?,?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(), json.dumps(m)))
        conn.commit()
        conn.close()
        _state["metrics"] = m
        print(f"[metrics] computed in {time.perf_counter()-t0:.1f}s")

    if _state["metrics_thread"] is None or not _state["metrics_thread"].is_alive():
        _state["metrics_thread"] = threading.Thread(target=bg_metrics, daemon=True)
        _state["metrics_thread"].start()


@app.get("/api/health")
def health():
    conn = get_db()
    n_nodes = conn.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
    conn.close()
    return {"status": "ok", "nodes": n_nodes, "models_loaded": _state["models"] is not None,
            "metrics_ready": _state["metrics"] is not None}


# ---------------- network / map ----------------

@app.get("/api/network")
def network():
    conn = get_db()
    closed = kv_get(conn, "closed_passes", {})
    nodes = [dict(r) for r in conn.execute("SELECT * FROM nodes ORDER BY kind, id")]
    edges = [dict(r) for r in conn.execute("SELECT * FROM edges")]
    classes = [dict(r) for r in conn.execute("SELECT * FROM supply_classes")]
    stocks = [dict(r) for r in conn.execute("SELECT * FROM stock")]
    # corridor geometry always uses the unrestricted graph so a closed west
    # route can still be drawn (red); openness is reported separately
    open_graph = build_graph(conn, closed_passes=[])
    gws = gateway_routes(open_graph)
    conn.close()
    south_passes = {"ROHTANG", "BARALACHA", "TANGLANGLA"}
    return {"nodes": nodes, "edges": edges, "classes": classes, "stock": stocks,
            "closed_passes": closed, "sim_today": config.SIM_TODAY,
            "gateway_routes": gws,
            "corridor_open": {
                "west": not (set(closed) & WEST_PASSES),
                "south": not (set(closed) & south_passes),
            }}


def _plan_payload(conn, plan, inputs, plan_id=None):
    """Attach route geometries and stock context to a solved plan."""
    fobs = sorted({f for f, _ in inputs["demand"]})
    routes_used = {}
    for dl in plan.get("deliveries", []):
        key = (dl["from"], dl["to"])
        if key not in routes_used:
            r = inputs["routes"].get(key)
            routes_used[key] = r
    g = inputs["graph"]
    return {
        **plan,
        "id": plan_id,
        "scenario": inputs["scenario"],
        "closed_passes": inputs["closed_passes"],
        "start_date": inputs["start_date"],
        "router_time_ms": inputs["router_time_ms"],
        "routes": {f"{d}|{f}": v for (d, f), v in routes_used.items()},
        "gateway_routes": gateway_routes(g),
        "fob_buffers": {f: {c: inputs["fob_buffer"].get((f, c), 0)
                            for c in config.CLASSES} for f in fobs},
    }


def _solve_current(conn, scenario="baseline"):
    models = get_models(conn)
    closed = kv_get(conn, "closed_passes", {})
    inputs = build_inputs(conn, models, config.SIM_TODAY, config.PLAN_HORIZON_DAYS,
                          closed_passes=closed.keys(), scenario=scenario)
    plan = solve_plan(
        inputs["demand"], inputs["fob_stock0"], inputs["fob_buffer"],
        inputs["depot_stock0"], inputs["route_cost_km"],
        inputs["cap_west"], inputs["cap_south"],
        depot_buffer=inputs["depot_buffer"])
    return plan, inputs


def _store_plan(conn, plan, payload, scenario):
    cur = conn.execute(
        "INSERT INTO plans (created_at, scenario, status, solve_time_ms, objective_inr, plan_json) "
        "VALUES (?,?,?,?,?,?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(), scenario, plan["status"],
         plan["solve_time_ms"], plan["objective_inr"], json.dumps(payload)))
    conn.commit()
    return cur.lastrowid


def _latest_plan_row(conn):
    return conn.execute("SELECT * FROM plans ORDER BY id DESC LIMIT 1").fetchone()


@app.get("/api/plan")
def get_plan(recompute: bool = False):
    conn = get_db()
    row = None if recompute else _latest_plan_row(conn)
    if row is None:
        plan, inputs = _solve_current(conn)
        payload = _plan_payload(conn, plan, inputs)
        pid = _store_plan(conn, plan, payload, inputs["scenario"])
        payload["id"] = pid
        row = _latest_plan_row(conn)
    out = json.loads(row["plan_json"])
    out["id"] = row["id"]
    out["created_at"] = row["created_at"]
    conn.close()
    return out


@app.post("/api/plan/recompute")
def recompute_plan():
    conn = get_db()
    plan, inputs = _solve_current(conn)
    payload = _plan_payload(conn, plan, inputs)
    pid = _store_plan(conn, plan, payload, inputs["scenario"])
    payload["id"] = pid
    conn.close()
    return payload


# ---------------- forecast ----------------

@app.get("/api/forecast")
def forecast(days: int = config.FORECAST_DAYS):
    if not 1 <= days <= 14:
        raise HTTPException(400, "days must be 1..14")
    conn = get_db()
    models = get_models(conn)
    start = (dt.date.fromisoformat(config.SIM_TODAY) + dt.timedelta(days=1)).isoformat()
    out = forecast_payload(conn, models, start, days)
    conn.close()
    return out


# ---------------- pass closure events ----------------

class PassClosure(BaseModel):
    pass_id: str = Field(..., description="e.g. ZOJILA")
    action: str = Field("closed", description="closed | reopened")
    days: int = Field(7, description="projected closure duration (informational)")


def _diff_plans(old, new):
    def key(d):
        return (d["day"], d["from"], d["to"])
    old_d, new_d = {key(d): d for d in old.get("deliveries", [])}, \
                   {key(d): d for d in new.get("deliveries", [])}
    removed, added, changed = [], [], []
    for k, d in old_d.items():
        if k not in new_d:
            removed.append(d)
        elif new_d[k]["classes"] != d["classes"] or new_d[k]["trucks"] != d["trucks"]:
            changed.append({"before": d, "after": new_d[k]})
    for k, d in new_d.items():
        if k not in old_d:
            added.append(d)
    return {"removed": removed, "added": added, "changed": changed,
            "counts": {"removed": len(removed), "added": len(added),
                       "changed": len(changed), "same": len(new_d) - len(added) - len(changed)}}


@app.post("/api/events/pass-closure")
def pass_closure(ev: PassClosure):
    conn = get_db()
    node = conn.execute("SELECT * FROM nodes WHERE id=? AND kind='pass'",
                        (ev.pass_id,)).fetchone()
    if node is None:
        conn.close()
        raise HTTPException(404, f"unknown pass {ev.pass_id}")
    closed = kv_get(conn, "closed_passes", {})
    if ev.action == "closed":
        closed[ev.pass_id] = {"since": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "days": ev.days}
    elif ev.action == "reopened":
        closed.pop(ev.pass_id, None)
    else:
        conn.close()
        raise HTTPException(400, "action must be closed|reopened")
    kv_set(conn, "closed_passes", closed)

    models = get_models(conn)
    t_route0 = time.perf_counter()
    inputs = build_inputs(conn, models, config.SIM_TODAY, config.PLAN_HORIZON_DAYS,
                          closed_passes=closed.keys(), scenario=(
                              f"closure-{ev.pass_id}" if ev.action == "closed" else "baseline"))
    gateway = gateway_routes(inputs["graph"])
    router_ms = (time.perf_counter() - t_route0) * 1000

    plan = solve_plan(
        inputs["demand"], inputs["fob_stock0"], inputs["fob_buffer"],
        inputs["depot_stock0"], inputs["route_cost_km"],
        inputs["cap_west"], inputs["cap_south"],
        depot_buffer=inputs["depot_buffer"])
    payload = _plan_payload(conn, plan, inputs)

    old_row = _latest_plan_row(conn)
    old_plan = json.loads(old_row["plan_json"]) if old_row else None
    diff = _diff_plans(old_plan or {"deliveries": []}, payload)

    # affected FOB routes that actually changed
    affected_routes = {}
    if old_plan:
        for k, new_r in payload["routes"].items():
            old_r = old_plan.get("routes", {}).get(k)
            if old_r and old_r["nodes"] != new_r["nodes"]:
                affected_routes[k] = {"before": old_r, "after": new_r}
            elif old_r is None:
                affected_routes[k] = {"after": new_r}

    old_id = old_row["id"] if old_row else None
    new_id = _store_plan(conn, plan, payload, inputs["scenario"])
    payload["id"] = new_id

    detail = {
        "pass": dict(node), "action": ev.action, "days": ev.days,
        "west_corridor_open": not inputs["west_closed"],
        "south_corridor_open": not inputs["south_closed"],
        "gateway_routes": gateway,
        "corridor_broken": inputs["west_closed"] or inputs["south_closed"],
        "diff": diff, "affected_routes": affected_routes,
        "old_plan_id": old_id, "new_plan_id": new_id,
        "old_plan": old_plan, "new_plan": payload,
    }
    cur = conn.execute(
        "INSERT INTO pass_events (ts, pass_id, action, days, old_plan_id, new_plan_id, "
        "router_time_ms, solve_time_ms, detail_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(), ev.pass_id, ev.action, ev.days,
         old_id, new_id, router_ms, plan["solve_time_ms"], json.dumps(detail)))
    conn.commit()
    event_id = cur.lastrowid
    conn.close()

    print(f"[event] {ev.pass_id} {ev.action}: reroute {router_ms:.0f} ms, "
          f"re-solve {plan['solve_time_ms']:.0f} ms, "
          f"diff -{diff['counts']['removed']} +{diff['counts']['added']} "
          f"~{diff['counts']['changed']}")
    return {"event_id": event_id, "router_time_ms": round(router_ms, 1),
            "solve_time_ms": plan["solve_time_ms"], **detail}


@app.get("/api/events")
def events():
    conn = get_db()
    rows = []
    for r in conn.execute("SELECT * FROM pass_events ORDER BY id DESC LIMIT 50"):
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json"))
        rows.append(d)
    conn.close()
    return rows


# ---------------- metrics (SIMULATED) ----------------

@app.get("/api/metrics")
def metrics():
    if _state["metrics"] is not None:
        return _state["metrics"]
    conn = get_db()
    row = conn.execute("SELECT * FROM metrics ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if row is None:
        return {"status": "computing", "label": "SIMULATED"}
    return json.loads(row["metrics_json"])


@app.post("/api/metrics/recompute")
def metrics_recompute():
    conn = get_db()
    models = get_models(conn)
    m = compute_metrics(conn, models)
    conn.execute("INSERT INTO metrics (computed_at, metrics_json) VALUES (?,?)",
                 (dt.datetime.now(dt.timezone.utc).isoformat(), json.dumps(m)))
    conn.commit()
    conn.close()
    _state["metrics"] = m
    return m


# ---------------- edge sync ----------------

class LedgerEntry(BaseModel):
    id: str
    seq: int
    prev_hash: str
    hash: str
    payload_json: str
    fob_id: str
    class_id: str
    qty: float
    entry_type: str          # requisition | receipt
    client_ts: str


class SyncRequest(BaseModel):
    device_id: str
    entries: list[LedgerEntry]


def _entry_hash(seq, prev_hash, payload_json, qty=None, fob_id=None,
                class_id=None, entry_type=None):
    """Hash covers the chain fields AND the semantic transaction fields, so
    editing qty/fob/class by hand (outside the client) always breaks it."""
    import hashlib
    body = f"{seq}|{prev_hash}|{payload_json}"
    if qty is not None:
        body += f"|{fob_id}|{class_id}|{entry_type}|{qty:.4f}"
    return hashlib.sha256(body.encode()).hexdigest()


@app.post("/api/sync")
def sync(req: SyncRequest):
    conn = get_db()
    accepted, rejected = [], []
    reason = None

    # per-device chain head (each edge device has its own append-only chain)
    head = conn.execute("SELECT seq, hash FROM chain_heads WHERE device_id=?",
                        (req.device_id,)).fetchone()
    if head:
        last_seq, last_hash = head["seq"], head["hash"]
    else:
        row = conn.execute(
            "SELECT seq, hash FROM ledger WHERE device_id=? AND status='accepted' "
            "ORDER BY seq DESC LIMIT 1", (req.device_id,)).fetchone()
        last_seq = row["seq"] if row else 0
        last_hash = row["hash"] if row else GENESIS

    entries = sorted(req.entries, key=lambda e: e.seq)
    chain_ok = True
    for e in entries:
        recomputed = _entry_hash(e.seq, e.prev_hash, e.payload_json,
                                 qty=e.qty, fob_id=e.fob_id, class_id=e.class_id,
                                 entry_type=e.entry_type)
        dup = conn.execute("SELECT device_id, seq, hash FROM ledger WHERE id=?",
                           (e.id,)).fetchone()
        if dup is not None:
            # idempotent replay is fine, but a replayed entry must still match
            # both its own hash and the copy the server already accepted
            if dup["device_id"] != req.device_id:
                problems = ["entry id already belongs to another device"]
            elif recomputed != e.hash or dup["hash"] != e.hash or dup["seq"] != e.seq:
                problems = ["HASH MISMATCH: entry hash does not match its content "
                            "(tampered or corrupted)"]
            else:
                accepted.append({"id": e.id, "seq": e.seq,
                                 "note": "duplicate, already synced"})
                continue
            chain_ok = False
            rejected.append({"id": e.id, "seq": e.seq, "errors": problems})
            reason = problems[-1] if reason is None else reason
            continue
        problems = []
        if not chain_ok:
            problems.append("chain already broken earlier in batch")
        if e.seq != last_seq + 1:
            problems.append(f"sequence gap: got {e.seq}, expected {last_seq + 1}")
        if e.prev_hash != last_hash:
            problems.append("prev_hash does not match preceding entry")
        if recomputed != e.hash:
            problems.append("HASH MISMATCH: entry hash does not match its content "
                            "(tampered or corrupted)")
        if e.qty < 0:
            problems.append("negative qty")
        if problems:
            chain_ok = False
            rejected.append({"id": e.id, "seq": e.seq, "errors": problems})
            reason = problems[-1] if reason is None else reason
            continue
        conn.execute(
            "INSERT INTO ledger (id, device_id, seq, prev_hash, hash, payload_json, fob_id, "
            "class_id, qty, entry_type, client_ts, received_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'accepted')",
            (e.id, req.device_id, e.seq, e.prev_hash, e.hash, e.payload_json,
             e.fob_id, e.class_id, e.qty, e.entry_type, e.client_ts,
             dt.datetime.now(dt.timezone.utc).isoformat()))
        # receipts confirm stock that reached the FOB offline
        if e.entry_type == "receipt":
            conn.execute("UPDATE stock SET on_hand = on_hand + ? WHERE node_id=? AND class_id=?",
                         (e.qty, e.fob_id, e.class_id))
        accepted.append({"id": e.id, "seq": e.seq, "applied": True})
        last_seq, last_hash = e.seq, e.hash
        conn.execute(
            "INSERT INTO chain_heads (device_id, seq, hash) VALUES (?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET seq=excluded.seq, hash=excluded.hash",
            (req.device_id, last_seq, last_hash))

    conn.execute(
        "INSERT INTO sync_log (ts, accepted, rejected, detail_json) VALUES (?,?,?,?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(), len(accepted), len(rejected),
         json.dumps({"device_id": req.device_id, "accepted": accepted, "rejected": rejected})))
    conn.commit()
    conn.close()
    return {"device_id": req.device_id, "accepted": len(accepted),
            "rejected": len(rejected), "reason": reason,
            "chain_head_seq": last_seq, "entries": {"accepted": accepted, "rejected": rejected}}


@app.get("/api/ledger")
def ledger():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM ledger ORDER BY seq")]
    conn.close()
    return rows


@app.get("/api/fobs")
def fobs():
    conn = get_db()
    out = []
    for r in conn.execute(
            "SELECT n.id, n.name, n.lat, n.lon, n.elev_m, n.troops, "
            "s.class_id, s.on_hand, s.buffer FROM nodes n JOIN stock s ON s.node_id=n.id "
            "WHERE n.kind='fob' ORDER BY n.id, s.class_id"):
        out.append(dict(r))
    conn.close()
    return out


# ---------------- static frontend (built by Docker / npm run build) ----------------
import os
_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
