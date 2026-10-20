"""SQLite persistence layer. Everything (network, history, plans, ledger) lives in one DB file."""
import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,              -- gateway | depot | staging | fob | pass | transit
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  elev_m REAL NOT NULL,
  troops INTEGER DEFAULT 0,
  corridor TEXT,                   -- named supply corridor this node belongs to
  pass_open INTEGER DEFAULT 1      -- 1/0 for pass nodes, NULL otherwise
);
CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  u TEXT NOT NULL,
  v TEXT NOT NULL,
  distance_km REAL NOT NULL,
  elev_gain_m REAL NOT NULL,
  gradient_pct REAL NOT NULL,
  corridor TEXT
);
CREATE TABLE IF NOT EXISTS supply_classes (
  class_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  unit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock (
  node_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  on_hand REAL NOT NULL,
  buffer REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (node_id, class_id)
);
CREATE TABLE IF NOT EXISTS history (
  date TEXT NOT NULL,
  fob_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  qty REAL NOT NULL,
  temp_c REAL NOT NULL,
  snowfall_cm REAL NOT NULL,
  troops INTEGER NOT NULL,
  PRIMARY KEY (date, fob_id, class_id)
);
CREATE TABLE IF NOT EXISTS weather_forecast (
  date TEXT NOT NULL,
  fob_id TEXT NOT NULL,
  temp_c REAL NOT NULL,
  snowfall_cm REAL NOT NULL,
  troops INTEGER NOT NULL,
  PRIMARY KEY (date, fob_id)
);
CREATE TABLE IF NOT EXISTS actual_future (
  date TEXT NOT NULL,
  fob_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  qty REAL NOT NULL,
  PRIMARY KEY (date, fob_id, class_id)
);
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  scenario TEXT NOT NULL,
  status TEXT NOT NULL,
  solve_time_ms REAL NOT NULL,
  objective_inr REAL NOT NULL,
  plan_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pass_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  pass_id TEXT NOT NULL,
  action TEXT NOT NULL,             -- closed | reopened
  days INTEGER,
  old_plan_id INTEGER,
  new_plan_id INTEGER,
  router_time_ms REAL,
  solve_time_ms REAL,
  detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  computed_at TEXT NOT NULL,
  metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
  id TEXT PRIMARY KEY,              -- client-assigned uuid
  device_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  fob_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  qty REAL NOT NULL,
  entry_type TEXT NOT NULL,
  client_ts TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'accepted'
);
CREATE TABLE IF NOT EXISTS chain_heads (
  device_id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  rejected INTEGER NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect(db_path=None):
    from . import config
    path = db_path or config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value):
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()


@contextmanager
def get_conn(db_path=None):
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def is_seeded(conn):
    row = conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
    return row["n"] > 0
