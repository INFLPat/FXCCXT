"""
tests/test_bootstrap_extended.py

Extends the existing shuffle order-invariance proof (test_bootstrap.py) to
the new TRACKED_METRICS added this session. Two groups, both asserted
explicitly rather than assumed:

ORDER-INVARIANT under shuffle (depend only on the multiset of trade P&Ls,
not their sequence): trade_expectancy(_pct), payoff_ratio, skewness,
kurtosis, kelly_fraction, cost_drag_pct, avg_trade_duration_hours - same
category as the pre-existing total_return_pct/win_rate_pct/ending_balance.

ORDER-DEPENDENT under shuffle (care about the PATH, not just the set):
ulcer_index, max_drawdown_duration, avg_drawdown_duration,
max_consecutive_wins, max_consecutive_losses - same category as the
pre-existing max_drawdown_pct.

Run: python -m tests.test_bootstrap_extended
"""

from backtest.bootstrap import run_bootstrap
from backtest.engine import Trade

ORDER_INVARIANT_METRICS = (
    "trade_expectancy", "trade_expectancy_pct", "payoff_ratio",
    "skewness", "kurtosis", "kelly_fraction", "cost_drag_pct", "avg_trade_duration_hours",
)
ORDER_DEPENDENT_METRICS = (
    "ulcer_index", "max_drawdown_duration", "avg_drawdown_duration",
    "max_consecutive_wins", "max_consecutive_losses",
)


def _closed_trade(pnl: float, idx: int) -> Trade:
    """A closed LONG trade with a specific realized P&L (zero commission,
    1 unit), and a real ISO timestamp pair 30 minutes apart - so
    avg_trade_duration_hours is well-defined (0.5h) rather than None."""
    entry = f"2024-01-01T{idx:02d}:00:00.000000000Z"
    exit_ = f"2024-01-01T{idx:02d}:30:00.000000000Z"
    return Trade(side="LONG", units=1.0, entry_time=entry, entry_price=100.0,
                 exit_time=exit_, exit_price=100.0 + pnl, commission_paid=0.0)


def test_new_metrics_are_tracked_and_finite():
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]
    result = run_bootstrap(trades, starting_balance=1000.0, method="resample", n_iterations=300, seed=3)

    for name in ORDER_INVARIANT_METRICS + ORDER_DEPENDENT_METRICS:
        assert name in result.observed, f"'{name}' missing from observed metrics"
        assert result.observed[name] is not None, f"'{name}' should be defined for this trade set"
        assert len(result._clean(name)) > 0, f"'{name}' produced no valid distribution values"
    print(f"All {len(ORDER_INVARIANT_METRICS) + len(ORDER_DEPENDENT_METRICS)} new tracked metrics present and non-empty.")
    print("Tracked-and-finite assertion passed.")


def test_order_invariant_metrics_are_constant_under_shuffle():
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]
    result = run_bootstrap(trades, starting_balance=1000.0, method="shuffle", n_iterations=500, seed=1)

    for name in ORDER_INVARIANT_METRICS:
        values = result._clean(name)
        spread = max(values) - min(values)
        assert spread < 1e-9, f"'{name}' varied under shuffling by {spread} - should be impossible (multiset-only metric)"
    print(f"Confirmed order-invariant under shuffle: {ORDER_INVARIANT_METRICS}")
    print("Order-invariant assertions passed.")


def test_order_dependent_metrics_actually_vary_under_shuffle():
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]
    result = run_bootstrap(trades, starting_balance=1000.0, method="shuffle", n_iterations=500, seed=1)

    for name in ORDER_DEPENDENT_METRICS:
        values = result._clean(name)
        spread = max(values) - min(values)
        assert spread > 1e-9, f"'{name}' was constant under shuffling - expected it to be path-dependent"
    print(f"Confirmed order-dependent (varies) under shuffle: {ORDER_DEPENDENT_METRICS}")
    print("Order-dependent assertions passed.")


def test_avg_trade_duration_is_exactly_half_an_hour():
    """Every scripted trade spans exactly 30 minutes by construction,
    regardless of order or resampling - an exact, not just order-
    invariant, check."""
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]
    result = run_bootstrap(trades, starting_balance=1000.0, method="resample", n_iterations=200, seed=5)
    assert abs(result.observed["avg_trade_duration_hours"] - 0.5) < 1e-9
    for v in result._clean("avg_trade_duration_hours"):
        assert abs(v - 0.5) < 1e-9
    print("avg_trade_duration_hours == 0.5h exactly, observed and every resampled iteration.")
    print("Exact-duration assertion passed.")


if __name__ == "__main__":
    test_new_metrics_are_tracked_and_finite()
    test_order_invariant_metrics_are_constant_under_shuffle()
    test_order_dependent_metrics_actually_vary_under_shuffle()
    test_avg_trade_duration_is_exactly_half_an_hour()
    print("\nAll extended-bootstrap tests passed.")
