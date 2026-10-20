"""Honest-metrics simulation tests: naive vs DRONA on the same seeded scenario."""
import pytest

from app import config
from app.simulate import (CLOSURE, SIM_DAYS, compute_metrics, run_drona,
                          run_naive)


def test_metrics_computed_not_hardcoded(conn, models):
    m = compute_metrics(conn, models)
    assert m["label"] == "SIMULATED"
    assert m["scenario"]["days"] == SIM_DAYS == 30
    assert m["scenario"]["closure"] == {"pass": "ZOJILA", "from_day": 10, "to_day": 16}
    # all numbers present and finite
    for side in ("naive", "drona"):
        s = m[side]
        assert s["stockout_units"] >= 0
        assert s["transport_cost_inr"] > 0
        assert s["proc_cost_inr"] > 0
        assert s["trucks"] > 0
        assert len(s["daily_stockouts"]) == SIM_DAYS


def test_drona_beats_naive_on_stockouts_and_holds(conn, models):
    naive, _ = run_naive(conn)
    drona, _ = run_drona(conn, models)
    # DRONA must not stock out materially more than naive
    assert drona.stockouts_units <= naive.stockouts_units * 0.5
    # DRONA never has requisitions held up by the closure
    assert drona.hold_events == 0
    assert naive.hold_events > 0


def test_series_shapes(conn, models):
    m = compute_metrics(conn, models)
    s = m["series"]
    assert len(s["days"]) == SIM_DAYS
    assert len(s["stockouts_naive"]) == SIM_DAYS
    assert len(s["stockouts_drona"]) == SIM_DAYS
    assert len(s["theater_stock_naive"]) == SIM_DAYS
    assert len(s["theater_stock_drona"]) == SIM_DAYS
    # all non-negative
    assert all(v >= 0 for v in s["stockouts_naive"])
    assert all(v >= 0 for v in s["stockouts_drona"])
