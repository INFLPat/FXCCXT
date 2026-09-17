"""
tests/test_rolling.py

Cross-checks rolling.py against metrics.py rather than hand-typing new
decimals: with window == len(equity_curve), the rolling Sharpe/Sortino at
the FINAL point must exactly equal the whole-curve Sharpe/Sortino computed
by compute_metrics() on the same data - same underlying formula, same
input window. That equality is the test; it fails if either implementation
drifts from the other, not because I hand-picked matching magic numbers.

Run: python -m tests.test_rolling
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from backtest.rolling import compute_rolling_metrics
from tests.test_metrics_extended import PRICES, SixTradeScript, make_candle


def _run():
    candles = [make_candle(i, p) for i, p in enumerate(PRICES)]
    engine = BacktestEngine(strategy=SixTradeScript(), cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1.0)
    return engine.run(candles)


def test_rolling_at_full_window_matches_whole_curve_metrics():
    result = _run()
    n = len(result.equity_curve)
    full_metrics = compute_metrics(result, periods_per_year=11)
    rolling = compute_rolling_metrics(result.equity_curve, window=n, periods_per_year=11)

    assert rolling.rolling_sharpe[-1] is not None
    assert abs(rolling.rolling_sharpe[-1] - full_metrics.sharpe_ratio) < 1e-9, (
        f"expected {full_metrics.sharpe_ratio}, got {rolling.rolling_sharpe[-1]}"
    )
    assert abs(rolling.rolling_sortino[-1] - full_metrics.sortino_ratio) < 1e-9, (
        f"expected {full_metrics.sortino_ratio}, got {rolling.rolling_sortino[-1]}"
    )
    assert abs(rolling.rolling_max_drawdown_pct[-1] - full_metrics.max_drawdown_pct) < 1e-9

    # Every point before the single full window must be None - there's
    # nowhere for a second window to fit in a 12-point series with window=12.
    assert all(v is None for v in rolling.rolling_sharpe[:-1])
    print(f"rolling[-1] sharpe={rolling.rolling_sharpe[-1]:.4f} == whole-curve sharpe={full_metrics.sharpe_ratio:.4f}")
    print("Rolling-vs-whole-curve equivalence assertion passed.")


def test_rolling_window_alignment_and_none_padding():
    result = _run()
    window = 6
    rolling = compute_rolling_metrics(result.equity_curve, window=window, periods_per_year=11)

    assert len(rolling.rolling_sharpe) == len(result.equity_curve), "series must stay index-aligned with equity_curve"
    assert all(v is None for v in rolling.rolling_sharpe[: window - 1]), "no value before the first full window"
    assert rolling.rolling_sharpe[window - 1] is not None, "first full window must produce a value"
    assert rolling.rolling_sharpe[-1] is not None

    stats = rolling.summary_stats()
    defined = [v for v in rolling.rolling_sharpe if v is not None]
    assert abs(stats["rolling_sharpe"]["max"] - max(defined)) < 1e-9
    assert abs(stats["rolling_sharpe"]["mean"] - sum(defined) / len(defined)) < 1e-9
    assert rolling.latest()["rolling_sharpe"] == rolling.rolling_sharpe[-1]

    print(f"window={window}: {sum(1 for v in rolling.rolling_sharpe if v is None)} leading Nones, "
          f"{len(defined)} defined values, summary_stats/latest() consistent")
    print("Window alignment / None-padding / summary_stats assertions passed.")


def test_window_too_small_raises():
    result = _run()
    try:
        compute_rolling_metrics(result.equity_curve, window=2, periods_per_year=11)
        raise AssertionError("expected an AssertionError for window < 3")
    except AssertionError as e:
        assert "at least 3" in str(e)
    print("Window-too-small assertion passed.")


if __name__ == "__main__":
    test_rolling_at_full_window_matches_whole_curve_metrics()
    test_rolling_window_alignment_and_none_padding()
    test_window_too_small_raises()
    print("\nAll rolling-metrics tests passed.")
