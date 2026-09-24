"""
tests/test_base_metrics.py

Hand-verified tests for backtest/base_metrics.py's candidate scoring-weight
formulas. Run: python -m tests.test_base_metrics
"""

from backtest.base_metrics import cost_adjusted, effect_size_over_sample_size
from backtest.engine import Trade


def _closed_trade(pnl: float, idx: int) -> Trade:
    return Trade(side="LONG", units=1.0, entry_time=f"e{idx}", entry_price=100.0,
                 exit_time=f"x{idx}", exit_price=100.0 + pnl, commission_paid=0.0)


def test_cost_adjusted_hand_computed():
    # magnitude=10.0, cost_drag=30% -> discount factor 0.7 -> 7.0
    assert abs(cost_adjusted(10.0, 30.0) - 7.0) < 1e-9
    # cost_drag=0 -> unchanged
    assert abs(cost_adjusted(10.0, 0.0) - 10.0) < 1e-9
    # cost_drag > 100 -> floored at 0 discount, not negative
    assert abs(cost_adjusted(10.0, 150.0) - 0.0) < 1e-9
    print("cost_adjusted hand-computed assertions passed.")


def test_cost_adjusted_none_propagates():
    assert cost_adjusted(None, 30.0) is None
    assert cost_adjusted(10.0, None) is None
    print("cost_adjusted None-propagation assertion passed.")


def test_effect_size_hand_computed():
    # pnls = [10, 20, 30] -> mean=20, sample variance=100, std=10,
    # standard_error = 10/sqrt(3), effect_size = 20 / (10/sqrt(3)) = 2*sqrt(3)
    import math
    trades = [_closed_trade(p, i) for i, p in enumerate([10.0, 20.0, 30.0])]
    result = effect_size_over_sample_size(trades)
    expected = 2 * math.sqrt(3)
    assert abs(result - expected) < 1e-9, f"expected {expected}, got {result}"
    print(f"effect_size={result:.4f} (expected {expected:.4f})")
    print("effect_size hand-computed assertion passed.")


def test_effect_size_edge_cases():
    assert effect_size_over_sample_size([_closed_trade(10.0, 0)]) is None, "needs at least 2 trades"
    identical = [_closed_trade(10.0, i) for i in range(5)]
    assert effect_size_over_sample_size(identical) is None, "zero variance -> undefined, must be None not a crash"
    print("effect_size edge-case assertions passed.")


def test_effect_size_more_trades_same_mean_increases_confidence():
    """Sample-size-awareness check: doubling the trade count at the SAME
    mean and spread should increase the effect size (more evidence for
    the same apparent edge) - the whole point of this metric existing
    alongside a pure magnitude number like trade_expectancy."""
    small = [_closed_trade(p, i) for i, p in enumerate([10.0, -5.0, 15.0, -8.0])]
    large = [_closed_trade(p, i) for i, p in enumerate([10.0, -5.0, 15.0, -8.0] * 5)]
    small_es = effect_size_over_sample_size(small)
    large_es = effect_size_over_sample_size(large)
    assert large_es > small_es, f"expected more trades at the same distribution to raise effect size, got {small_es} -> {large_es}"
    print(f"small (n=4) effect_size={small_es:.3f}, large (n=20, same distribution) effect_size={large_es:.3f}")
    print("Sample-size-awareness assertion passed.")


if __name__ == "__main__":
    test_cost_adjusted_hand_computed()
    test_cost_adjusted_none_propagates()
    test_effect_size_hand_computed()
    test_effect_size_edge_cases()
    test_effect_size_more_trades_same_mean_increases_confidence()
    print("\nAll base_metrics tests passed.")
