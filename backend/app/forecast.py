"""Demand forecasting: one LightGBM model per supply class.

Features: temperature, snowfall, troop count, day-of-week. Trained on the
seeded 12-month daily history, predicting daily consumption per FOB.
"""
import datetime as dt
import os
import pickle
from collections import defaultdict, deque

import lightgbm as lgb
import numpy as np

from . import config

MODEL_DIR = os.environ.get("DRONA_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "models"))

PARAMS = dict(
    objective="regression",
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=31,
    min_child_samples=20,
    random_state=26251,
    deterministic=True,
    force_col_wise=True,
    n_jobs=1,
    verbosity=-1,
)
FEATURES = ["temp_c", "snowfall_cm", "troops", "dow"]


def _rows_to_xy(rows):
    X = np.array([[r["temp_c"], r["snowfall_cm"], r["troops"],
                   dt.date.fromisoformat(r["date"]).weekday()] for r in rows], dtype=float)
    y = np.array([r["qty"] for r in rows], dtype=float)
    return X, y


def train_models(conn, model_dir=None):
    """Train one model per supply class on the full history. Returns {class: model}."""
    model_dir = model_dir or MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)
    models = {}
    for class_id in config.CLASSES:
        rows = conn.execute(
            "SELECT date, qty, temp_c, snowfall_cm, troops FROM history WHERE class_id=?",
            (class_id,)).fetchall()
        X, y = _rows_to_xy(rows)
        m = lgb.LGBMRegressor(**PARAMS)
        m.fit(X, y)
        models[class_id] = m
        with open(os.path.join(model_dir, f"model_{class_id}.pkl"), "wb") as f:
            pickle.dump(m, f)
    return models


def load_models(model_dir=None):
    model_dir = model_dir or MODEL_DIR
    models = {}
    for class_id in config.CLASSES:
        path = os.path.join(model_dir, f"model_{class_id}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                models[class_id] = pickle.load(f)
    return models


def ensure_models(conn, model_dir=None):
    models = load_models(model_dir)
    if len(models) != len(config.CLASSES):
        models = train_models(conn, model_dir)
    return models


def predict(conn, models, start_date, days, model_dir=None):
    """7-day (or `days`) forecast per FOB per class, using the synthetic
    weather forecast table. Returns {(fob, class): [qty per day]}."""
    end = (dt.date.fromisoformat(start_date) + dt.timedelta(days=days - 1)).isoformat()
    out = {}
    wx = {}
    for r in conn.execute(
            "SELECT date, fob_id, temp_c, snowfall_cm, troops FROM weather_forecast "
            "WHERE date >= ? AND date <= ? ORDER BY date", (start_date, end)):
        wx.setdefault(r["fob_id"], []).append(r)
    for fob, rows in wx.items():
        X = np.array([[r["temp_c"], r["snowfall_cm"], r["troops"],
                       dt.date.fromisoformat(r["date"]).weekday()] for r in rows], dtype=float)
        for class_id, m in models.items():
            out[(fob, class_id)] = [float(max(0.0, round(float(v), 4))) for v in m.predict(X)]
    return out


def forecast_payload(conn, models, start_date, days, model_dir=None):
    """Forecast shaped for the API: per-FOB per-class with day labels."""
    raw = predict(conn, models, start_date, days, model_dir)
    dates = [(dt.date.fromisoformat(start_date) + dt.timedelta(days=i)).isoformat()
             for i in range(days)]
    fobs = sorted({f for f, _ in raw})
    payload = {"start_date": start_date, "dates": dates, "fobs": {}}
    for fob in fobs:
        node = conn.execute("SELECT name, troops FROM nodes WHERE id=?", (fob,)).fetchone()
        per_class = {}
        for c in config.CLASSES:
            vals = raw.get((fob, c), [])
            per_class[c] = {"daily": vals, "total": round(sum(vals), 3)}
        payload["fobs"][fob] = {"name": node["name"], "troops": node["troops"],
                                "forecast": per_class}
    return payload


def walk_forward_backtest(conn, n_splits=4, test_days=28):
    """Walk-forward evaluation: for each split, train on all data strictly
    before the test window and predict the next `test_days` days.

    MAE is pooled across FOB-days and splits per class. The baseline is a
    trailing 7-day mean per (FOB, class) — the "look at last week" policy a
    staff officer would use without a model. Positive improvement_pct means
    LightGBM beats that naive baseline.
    """
    dates = [r["date"] for r in
             conn.execute("SELECT DISTINCT date FROM history ORDER BY date")]
    n = len(dates)
    if n <= n_splits * test_days:
        raise ValueError(f"history too short: {n} days for "
                         f"{n_splits}x{test_days}-day walk-forward")

    abs_err = {c: [] for c in config.CLASSES}
    base_err = {c: [] for c in config.CLASSES}
    for split in range(n_splits):
        # oldest split first: train on everything before the window
        start_idx = n - (n_splits - split) * test_days
        train_end = dates[start_idx - 1]
        test_window = set(dates[start_idx:start_idx + test_days])

        train_rows = conn.execute(
            "SELECT date, qty, temp_c, snowfall_cm, troops, class_id FROM history "
            "WHERE date <= ?", (train_end,)).fetchall()
        by_class = defaultdict(list)
        for r in train_rows:
            by_class[r["class_id"]].append(r)

        # trailing 7-day mean per (fob, class) up to (not incl.) each test day
        tail = defaultdict(deque)
        tail_rows = conn.execute(
            "SELECT date, fob_id, class_id, qty FROM history "
            "WHERE date <= ? ORDER BY date", (train_end,)).fetchall()
        for r in tail_rows:
            tail[(r["fob_id"], r["class_id"])].append(r["qty"])
            if len(tail[(r["fob_id"], r["class_id"])]) > 7:
                tail[(r["fob_id"], r["class_id"])].popleft()

        test_rows = conn.execute(
            "SELECT date, fob_id, class_id, qty, temp_c, snowfall_cm, troops "
            "FROM history WHERE date > ? AND date <= ? ORDER BY date",
            (train_end, dates[start_idx + test_days - 1])).fetchall()

        for class_id in config.CLASSES:
            rows = by_class[class_id]
            X, y = _rows_to_xy(rows)
            m = lgb.LGBMRegressor(**PARAMS)
            m.fit(X, y)
            cr = [r for r in test_rows if r["class_id"] == class_id
                  and r["date"] in test_window]
            if not cr:
                continue
            X_test = np.array([[r["temp_c"], r["snowfall_cm"], r["troops"],
                                dt.date.fromisoformat(r["date"]).weekday()]
                               for r in cr], dtype=float)
            preds = m.predict(X_test)
            for r, p in zip(cr, preds):
                abs_err[class_id].append(abs(float(p) - r["qty"]))
                # baseline rolls forward: score against the trailing 7-day
                # mean, then fold the actual into the window so day k+1 of
                # the test window sees day k's observation (a staff officer
                # updating yesterday's estimate, not a frozen one).
                t = tail[(r["fob_id"], class_id)]
                mean7 = sum(t) / len(t) if t else r["qty"]
                base_err[class_id].append(abs(mean7 - r["qty"]))
                t.append(r["qty"])
                if len(t) > 7:
                    t.popleft()

    per_class = {}
    for class_id in config.CLASSES:
        if not abs_err[class_id]:
            continue
        mae = float(np.mean(abs_err[class_id]))
        baseline = float(np.mean(base_err[class_id]))
        per_class[class_id] = {
            "mae": round(mae, 4),
            "baseline_mae": round(baseline, 4),
            "improvement_pct": round((baseline - mae) / baseline * 100, 1)
                               if baseline > 0 else 0.0,
        }
    return {
        "label": "SIMULATED",
        "method": "walk-forward: train on data strictly before each test window; "
                  "pooled MAE over FOB-days; baseline = rolling trailing 7-day mean "
                  "(updated with each observed day)",
        "splits": n_splits,
        "test_days_per_split": test_days,
        "per_class": per_class,
    }
