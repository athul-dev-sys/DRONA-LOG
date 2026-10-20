"""Synthetic data generator for DRONA-LOG.

Creates the Ladakh road network (base depot Leh, 2 staging depots, 10 FOBs,
12 high-altitude passes, two gateway corridors), plus 12 months of daily
consumption history driven by temperature, snowfall and troop count.

All data is deterministic (fixed seeds) and fully synthetic - no external APIs.
Run:  python -m app.seed [--db path] [--force]
"""
import argparse
import datetime as dt
import math
import os
import random
import sqlite3
import zlib

from . import config
from .db import connect, init_schema, kv_set

SEED = 26251  # SIH26251

# Python's str hash() is salted per process; crc32 keeps seeded data identical
# across reruns and containers.
def _fob_seed(fob):
    return zlib.crc32(fob.encode()) & 0xFFFF

# Scripted operational event inside the evaluation window: a forward-troop
# surge (exercise) at three northern FOBs, days 12-18 inclusive.
SURGE_FOBS = ("DBO", "SASOMA", "TURTUK")
SURGE_FROM, SURGE_TO = 12, 18
SURGE_TROOP_FACTOR = 1.5

# id, name, kind, lat, lon, elev_m, corridor, troops
NODES = [
    # ---- gateways (supply enters the theatre here) ----
    ("SRINAGAR",   "Srinagar (Gateway)",    "gateway", 34.08, 74.80, 1585, "WEST"),
    ("MANALI",     "Manali (Gateway)",      "gateway", 32.24, 77.19, 2050, "SOUTH"),
    # ---- west corridor: Srinagar - Zoji La - Kargil - Leh (NH1) ----
    ("SONAMARG",   "Sonamarg",              "transit", 34.34, 75.31, 2730, "WEST"),
    ("ZOJILA",     "Zoji La",               "pass",    34.28, 75.47, 3528, "WEST"),
    ("DRASS",      "Drass",                 "transit", 34.43, 75.75, 3068, "WEST"),
    ("KARGIL",     "Kargil",                "transit", 34.56, 76.13, 2676, "WEST"),
    ("MULBEKH",    "Mulbekh",               "transit", 34.45, 76.60, 3300, "WEST"),
    ("NAMIKALA",   "Namika La",             "pass",    34.45, 76.75, 3700, "WEST"),
    ("LAMAYURU",   "Lamayuru",              "transit", 34.29, 76.72, 3510, "WEST"),
    ("FOTULA",     "Fotu La",               "pass",    34.24, 76.85, 4108, "WEST"),
    ("KHALSI",     "Khalsi",                "transit", 34.31, 76.98, 2980, "WEST"),
    ("NIMMOO",     "Nimmoo",                "transit", 34.21, 77.24, 3250, "WEST"),
    # ---- south corridor: Manali - Baralacha - Tanglang La - Leh (NH3) ----
    ("ROHTANG",    "Rohtang La",            "pass",    32.37, 77.25, 3978, "SOUTH"),
    ("KEYLONG",    "Keylong",               "transit", 32.57, 77.03, 3080, "SOUTH"),
    ("BARALACHA",  "Baralacha La",          "pass",    32.75, 77.44, 4890, "SOUTH"),
    ("SARCHU",     "Sarchu",                "transit", 32.87, 77.59, 4290, "SOUTH"),
    ("DEBRING",    "Debring (Morey Plain)", "transit", 33.23, 77.75, 4660, "SOUTH"),
    ("TANGLANGLA", "Tanglang La",           "pass",    33.51, 77.79, 5328, "SOUTH"),
    ("RUMTSE",     "Rumtse",                "transit", 33.60, 77.86, 4100, "SOUTH"),
    # ---- theatre: base + staging depots ----
    ("LEH",        "Leh (Base Depot)",      "depot",   34.16, 77.58, 3524, None),
    ("KARU",       "Karu (Staging)",        "staging", 34.02, 77.74, 3400, "PANGONG"),
    ("UPSHI",      "Upshi (Staging)",       "staging", 33.81, 77.87, 3400, "CHANGTHANG"),
    # ---- Nubra / Shyok / Siachen approach ----
    ("KHARDUNGLA", "Khardung La",           "pass",    34.28, 77.60, 5359, "NUBRA"),
    ("KHALSAR",    "Khalsar",               "transit", 34.65, 77.59, 3050, "NUBRA"),
    ("WARILA",     "Wari La",               "pass",    34.35, 77.86, 5312, "NUBRA"),
    ("AGHAM",      "Agham",                 "transit", 34.54, 77.68, 3000, "NUBRA"),
    ("SHYOK",      "Shyok",                 "transit", 34.48, 77.80, 3100, "NUBRA"),
    ("CHALUNKA",   "Chalunka",              "transit", 34.75, 77.63, 3400, "NUBRA"),
    ("MURUD",      "Murud",                 "transit", 35.05, 77.45, 4400, "NUBRA"),
    ("SASERLA",    "Saser La",              "pass",    35.02, 77.80, 5411, "NUBRA"),
    ("DISKIT",     "FOB Diskit (Nubra)",    "fob",     34.52, 77.53, 3100, "NUBRA", 620),
    ("TURTUK",     "FOB Turtuk",            "fob",     34.98, 77.26, 2800, "NUBRA", 380),
    ("SASOMA",     "FOB Sasoma",            "fob",     34.90, 77.73, 3520, "NUBRA", 300),
    ("DBO",        "FOB Daulat Beg Oldi",   "fob",     35.39, 77.12, 5065, "NUBRA", 250),
    # ---- Pangong ----
    ("CHANGLA",    "Chang La",              "pass",    34.03, 77.93, 5360, "PANGONG"),
    ("TANGTSE",    "Tangtse",               "transit", 34.03, 78.28, 3960, "PANGONG"),
    ("DURBUK",     "FOB Durbuk",            "fob",     34.22, 78.16, 4025, "PANGONG", 420),
    ("LUKUNG",     "Lukung",                "transit", 33.85, 78.55, 4250, "PANGONG"),
    ("SPANGMIK",   "FOB Spangmik",          "fob",     33.75, 78.92, 4250, "PANGONG", 310),
    ("CHUSHUL",    "FOB Chushul",           "fob",     33.40, 78.83, 4340, "CHANGTHANG", 520),
    # ---- Changthang (SE Ladakh) ----
    ("CHUMATHANG", "Chumathang",            "transit", 33.39, 78.30, 4000, "CHANGTHANG"),
    ("MAHE",       "Mahe",                  "transit", 33.25, 78.48, 4250, "CHANGTHANG"),
    ("KAKSANGLA",  "Kaksang La",            "pass",    33.18, 78.72, 5438, "CHANGTHANG"),
    ("NYOMA",      "FOB Nyoma",             "fob",     33.10, 78.52, 4280, "CHANGTHANG", 560),
    ("TSAGA",      "Tsaga",                 "transit", 32.95, 78.60, 4400, "CHANGTHANG"),
    ("LOMA",       "Loma",                  "transit", 32.86, 78.72, 4380, "CHANGTHANG"),
    ("HANLE",      "FOB Hanle",             "fob",     32.78, 79.00, 4480, "CHANGTHANG", 430),
    ("UMLINGLA",   "Umling La",             "pass",    32.55, 79.20, 5798, "CHANGTHANG"),
    ("DEMCHOK",    "FOB Demchok",           "fob",     32.35, 79.42, 4370, "CHANGTHANG", 290),
]

EDGES = [
    # west corridor
    ("SRINAGAR", "SONAMARG"), ("SONAMARG", "ZOJILA"), ("ZOJILA", "DRASS"),
    ("DRASS", "KARGIL"), ("KARGIL", "MULBEKH"), ("MULBEKH", "NAMIKALA"),
    ("NAMIKALA", "LAMAYURU"), ("LAMAYURU", "FOTULA"), ("FOTULA", "KHALSI"),
    ("KHALSI", "NIMMOO"), ("NIMMOO", "LEH"),
    # south corridor
    ("MANALI", "ROHTANG"), ("ROHTANG", "KEYLONG"), ("KEYLONG", "BARALACHA"),
    ("BARALACHA", "SARCHU"), ("SARCHU", "DEBRING"), ("DEBRING", "TANGLANGLA"),
    ("TANGLANGLA", "RUMTSE"), ("RUMTSE", "UPSHI"),
    # theatre core
    ("LEH", "KARU"), ("LEH", "KHARDUNGLA"), ("KHARDUNGLA", "KHALSAR"),
    ("KARU", "WARILA"), ("WARILA", "AGHAM"), ("KHALSAR", "AGHAM"),
    ("KHALSAR", "DISKIT"), ("DISKIT", "TURTUK"), ("DISKIT", "SASOMA"),
    ("SASOMA", "SASERLA"), ("SASERLA", "MURUD"), ("MURUD", "DBO"),
    ("AGHAM", "SHYOK"), ("SHYOK", "CHALUNKA"), ("CHALUNKA", "MURUD"),
    # Pangong
    ("KARU", "CHANGLA"), ("CHANGLA", "TANGTSE"), ("TANGTSE", "DURBUK"),
    ("DURBUK", "LUKUNG"), ("LUKUNG", "SPANGMIK"), ("DURBUK", "AGHAM"),
    # Changthang
    ("LEH", "UPSHI"), ("UPSHI", "CHUMATHANG"), ("CHUMATHANG", "MAHE"),
    ("MAHE", "NYOMA"), ("NYOMA", "TSAGA"), ("TSAGA", "LOMA"),
    ("LOMA", "HANLE"), ("LOMA", "DEMCHOK"), ("MAHE", "KAKSANGLA"),
    ("KAKSANGLA", "CHUSHUL"), ("SPANGMIK", "CHUSHUL"),
    ("HANLE", "UMLINGLA"), ("UMLINGLA", "DEMCHOK"),
]

CLASSES = [
    ("I",   "Rations & Fresh Supplies", "tonne"),
    ("III", "POL (Fuel & Lubricants)",  "kl"),
    ("V",   "Ammunition",               "tonne"),
]


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def gen_weather(day, elev_m, rng):
    """Deterministic seasonal weather model: temperature falls with elevation and
    with mid-winter season; snow occurs when it is cold."""
    doy = day.timetuple().tm_yday
    seasonal = math.cos(2 * math.pi * (doy - 15) / 365.0)   # +1 mid-Jan, -1 mid-Jul
    base = 30.0 - 0.0068 * elev_m
    amp = 8.0 + elev_m / 1000.0 * 1.4
    temp = base + amp * seasonal + rng.gauss(0, 2.5)
    if temp < 2.0:
        snow = min(60.0, rng.gammavariate(0.8, 8.0 * (1 + (2.0 - temp) * 0.15)))
    else:
        snow = 0.0
    return temp, snow


def gen_troops_series(fob_id, base_troops, days, rng):
    """Troop count with two ~10-day exercise surges per year and small monthly drift."""
    out = []
    surge_starts = {rng.randrange(20, len(days) - 20), rng.randrange(20, len(days) - 20)}
    for i, day in enumerate(days):
        t = base_troops * (1.0 + 0.06 * math.sin(2 * math.pi * i / 90.0))
        if any(s <= i < s + 10 for s in surge_starts):
            t *= 1.4
        out.append(max(50, int(round(t))))
    return out


def gen_consumption(class_id, temp, snow, troops, rng, exercise):
    """Daily demand per supply class, driven by weather and troop activity."""
    if class_id == "I":
        cold_factor = 1.12 if temp < -10 else (1.06 if temp < 0 else 1.0)
        return 0.00115 * troops * cold_factor * max(0.7, rng.gauss(1.0, 0.08))
    if class_id == "III":
        heating = 1.0 + max(0.0, (5.0 - temp)) * 0.05
        snow_ops = 1.0 + min(0.25, snow * 0.008)
        return 0.0035 * troops * heating * snow_ops * max(0.7, rng.gauss(1.0, 0.12))
    if class_id == "V":
        mult = 6.0 if exercise else 1.0
        return 0.00045 * troops * mult * max(0.5, rng.gauss(1.0, 0.15))
    raise ValueError(class_id)


def seed(db_path=None, force=False):
    path = db_path or config.DB_PATH
    if os.path.exists(path) and not force:
        conn = connect(path)
        from .db import is_seeded
        if is_seeded(conn):
            conn.close()
            print(f"[seed] {path} already seeded - skipping (use --force to rebuild)")
            return
        conn.close()
    if os.path.exists(path) and force:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass

    rng = random.Random(SEED)
    start = dt.date.fromisoformat(config.HISTORY_START)
    sim_today = dt.date.fromisoformat(config.SIM_TODAY)
    hist_days = [start + dt.timedelta(days=i) for i in range((sim_today - start).days)]
    # 365 days ending the day before SIM_TODAY
    assert len(hist_days) == 365, f"history length {len(hist_days)}"

    node_map = {}
    for row in NODES:
        nid, name, kind, lat, lon, elev, corridor = row[:7]
        troops = row[7] if len(row) > 7 else 0
        node_map[nid] = dict(id=nid, name=name, kind=kind, lat=lat, lon=lon,
                             elev_m=elev, corridor=corridor, troops=troops)
    fobs = sorted(n for n, v in node_map.items() if v["kind"] == "fob")

    conn = connect(path)
    init_schema(conn)

    conn.executemany(
        "INSERT INTO nodes (id, name, kind, lat, lon, elev_m, troops, corridor, pass_open) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(v["id"], v["name"], v["kind"], v["lat"], v["lon"], v["elev_m"],
          v["troops"], v["corridor"], 1 if v["kind"] == "pass" else None)
         for v in node_map.values()],
    )

    edge_rows = []
    for u, v in EDGES:
        a, b = node_map[u], node_map[v]
        d = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * config.ROAD_FACTOR
        d = max(3.0, round(d, 2))
        gain = abs(a["elev_m"] - b["elev_m"])
        grad = gain / (d * 10.0)  # percent
        edge_rows.append((u, v, d, gain, round(grad, 3), a["corridor"] or b["corridor"]))
    conn.executemany(
        "INSERT INTO edges (u, v, distance_km, elev_gain_m, gradient_pct, corridor) "
        "VALUES (?,?,?,?,?,?)", edge_rows)

    conn.executemany("INSERT INTO supply_classes VALUES (?,?,?)", CLASSES)

    # ---- daily history: weather -> troops -> consumption ----
    hist_rows = []
    fob_troops = {}
    fob_weather = {}
    for fob in fobs:
        meta = node_map[fob]
        wrng = random.Random(SEED ^ _fob_seed(fob))
        troops_series = gen_troops_series(fob, meta["troops"], hist_days, wrng)
        fob_troops[fob] = troops_series
        fob_weather[fob] = [gen_weather(d, meta["elev_m"], wrng) for d in hist_days]
    for i, day in enumerate(hist_days):
        for fob in fobs:
            temp, snow = fob_weather[fob][i]
            troops = fob_troops[fob][i]
            exercise = rng.random() < 0.02
            for class_id, _, _ in CLASSES:
                qty = gen_consumption(class_id, temp, snow, troops, rng, exercise)
                hist_rows.append((day.isoformat(), fob, class_id, round(qty, 4),
                                  round(temp, 2), round(snow, 2), troops))
    conn.executemany(
        "INSERT INTO history (date, fob_id, class_id, qty, temp_c, snowfall_cm, troops) "
        "VALUES (?,?,?,?,?,?,?)", hist_rows)

    # ---- weather forecast + troop plan for the next 37 days ----
    fc_days = [sim_today + dt.timedelta(days=i) for i in range(config.EVAL_DAYS + 7)]
    fc_rows = []
    troops_by_date = {}
    for fob in fobs:
        wrng = random.Random(SEED ^ _fob_seed(fob) ^ 0xFC)
        troops_now = fob_troops[fob][-1]
        for i, day in enumerate(fc_days):
            temp, snow = gen_weather(day, node_map[fob]["elev_m"], wrng)
            troops = troops_now
            if fob in SURGE_FOBS and SURGE_FROM <= i <= SURGE_TO:
                troops = int(troops_now * SURGE_TROOP_FACTOR)
            troops_by_date[(fob, day.isoformat())] = troops
            fc_rows.append((day.isoformat(), fob, round(temp, 2), round(snow, 2), troops))
    conn.executemany(
        "INSERT INTO weather_forecast (date, fob_id, temp_c, snowfall_cm, troops) "
        "VALUES (?,?,?,?,?)", fc_rows)

    # ---- "actual" future consumption used only for honest metric simulation ----
    # Uses the same weather as the forecast table (the forecast is weather-driven),
    # but fresh consumption noise the forecast model cannot see.
    actual_rows = []
    for fob in fobs:
        arng = random.Random(SEED ^ _fob_seed(fob) ^ 0xAC7)
        for day in fc_days[:config.EVAL_DAYS]:
            row = conn.execute(
                "SELECT temp_c, snowfall_cm FROM weather_forecast WHERE date=? AND fob_id=?",
                (day.isoformat(), fob)).fetchone()
            wtemp, wsnow = row["temp_c"], row["snowfall_cm"]
            troops = troops_by_date[(fob, day.isoformat())]
            exercise = arng.random() < 0.02
            for class_id, _, _ in CLASSES:
                qty = gen_consumption(class_id, wtemp, wsnow, troops, arng, exercise)
                actual_rows.append((day.isoformat(), fob, class_id, round(qty, 4)))
    conn.executemany(
        "INSERT INTO actual_future (date, fob_id, class_id, qty) VALUES (?,?,?,?)",
        actual_rows)

    # ---- stock: buffers from trailing 60-day mean demand ----
    means = {}
    for fob in fobs:
        for class_id, _, _ in CLASSES:
            row = conn.execute(
                "SELECT AVG(qty) AS m FROM history WHERE fob_id=? AND class_id=? "
                "AND date >= ?", (fob, class_id, (sim_today - dt.timedelta(days=60)).isoformat())
            ).fetchone()
            means[(fob, class_id)] = row["m"]
    stock_rows = []
    for fob in fobs:
        for class_id, _, _ in CLASSES:
            buf = round(means[(fob, class_id)] * config.BUFFER_DAYS, 3)
            stock_rows.append((fob, class_id, round(buf * 1.35, 3), buf))
    # depot + staging stock (units): lean, import-dependent posture
    theater_mean = {c: sum(means[(f, c)] for f in fobs) for c, _, _ in CLASSES}
    depot_stock = {
        "LEH": {c: round(theater_mean[c] * 3.0, 2) for c in theater_mean},
        "KARU": {c: round(theater_mean[c] * 1.5, 2) for c in theater_mean},
        "UPSHI": {c: round(theater_mean[c] * 1.5, 2) for c in theater_mean},
    }
    for depot, classes in depot_stock.items():
        for c, q in classes.items():
            reserve = round(theater_mean[c] * (4.0 if depot == "LEH" else 1.0), 2)
            stock_rows.append((depot, c, q, reserve))
    conn.executemany(
        "INSERT INTO stock (node_id, class_id, on_hand, buffer) VALUES (?,?,?,?)",
        stock_rows)

    kv_set(conn, "sim_today", config.SIM_TODAY)
    kv_set(conn, "closed_passes", {})
    conn.commit()
    conn.close()

    print(f"[seed] wrote {len(node_map)} nodes ({len(fobs)} FOBs), {len(edge_rows)} edges, "
          f"{len(hist_rows)} history rows, {len(actual_rows)} actual-future rows -> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    seed(args.db, args.force)
