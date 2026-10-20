"""Demand forecasting: one LightGBM model per supply class.

Features: temperature, snowfall, troop count, day-of-week. Trained on the
seeded 12-month daily history, predicting daily consumption per FOB.
"""
import datetime as dt
import os
import pickle

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
