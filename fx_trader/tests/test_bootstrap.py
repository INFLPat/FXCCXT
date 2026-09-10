"""
tests/test_bootstrap.py

Layered testing, same approach as the other backtest/ modules:
1. Hand-verified percentile/CI/probability math against a manually
   constructed BootstrapResult with known values.
2. The core mathematical invariant of shuffle mode (total return and win
   rate must be EXACTLY order-invariant) - proven, not assumed.
3. A ground-truth check: for a small enough trade set, exhaustively enumerate
   every possible ordering with itertools.permutations and compare against
   the Monte Carlo estimate from run_bootstrap - they should closely agree.
4. Integration + edge cases (too few trades, invalid method).

Run with: python -m tests.test_bootstrap
"""

import itertools

from backtest.bootstrap import BootstrapResult, _extract, run_bootstrap
from backtest.engine import Trade


def _closed_trade(pnl: float, idx: int) -> Trade:
    """A closed LONG trade with a specific realized P&L, zero commission,
    1 unit - realized_pnl() = exit_price - entry_price, so just pick prices
    that differ by exactly `pnl`."""
    return Trade(
        side="LONG", units=1.0, entry_time=f"e{idx}", entry_price=100.0,
        exit_time=f"x{idx}", exit_price=100.0 + pnl, commission_paid=0.0,
    )


def test_percentile_and_confidence_interval_math():
    result = BootstrapResult(
        method="resample", n_iterations=10, n_trades=5, starting_balance=1000.0,
        observed={"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "win_rate_pct": 0.0,
                  "profit_factor": None, "ending_balance": 1000.0},
        distributions={"total_return_pct": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                       "max_drawdown_pct": [], "win_rate_pct": [], "profit_factor": [], "ending_balance": []},
    )

    assert abs(result.percentile("total_return_pct", 0) - 1) < 1e-9
    assert abs(result.percentile("total_return_pct", 100) - 10) < 1e-9
    assert abs(result.percentile("total_return_pct", 50) - 5.5) < 1e-9, "median of 1..10 should be 5.5"

    lo, hi = result.confidence_interval("total_return_pct", level=0.80)
    assert abs(lo - 1.9) < 1e-9, f"expected 10th percentile 1.9, got {lo}"
    assert abs(hi - 9.1) < 1e-9, f"expected 90th percentile 9.1, got {hi}"

    assert abs(result.mean("total_return_pct") - 5.5) < 1e-9

    print(f"percentile(50)={result.percentile('total_return_pct', 50)} (expected 5.5)")
    print(f"80% CI = ({lo}, {hi}) (expected (1.9, 9.1))")
    print("Percentile/CI math assertions passed.")


def test_probability_below_and_observed_percentile_rank():
    result = BootstrapResult(
        method="resample", n_iterations=7, n_trades=5, starting_balance=1000.0,
        observed={"total_return_pct": 2.0, "max_drawdown_pct": 0.0, "win_rate_pct": 0.0,
                  "profit_factor": None, "ending_balance": 1000.0},
        distributions={"total_return_pct": [-5, -3, -1, 1, 3, 5, 7],
                       "max_drawdown_pct": [], "win_rate_pct": [], "profit_factor": [], "ending_balance": []},
    )

    prob = result.probability_below("total_return_pct", 0.0)
    assert abs(prob - (3 / 7 * 100)) < 1e-9, f"expected 3/7={3 / 7 * 100:.3f}%, got {prob}"

    # observed=2.0 -> values strictly below 2.0 are [-5,-3,-1,1] = 4 of 7
    rank = result.observed_percentile_rank("total_return_pct")
    assert abs(rank - (4 / 7 * 100)) < 1e-9, f"expected 4/7={4 / 7 * 100:.3f}%, got {rank}"

    print(f"probability_below(0) = {prob:.3f}% (expected {3 / 7 * 100:.3f}%)")
    print(f"observed_percentile_rank = {rank:.3f}% (expected {4 / 7 * 100:.3f}%)")
    print("probability_below / observed_percentile_rank assertions passed.")


def test_shuffle_mode_return_and_win_rate_are_order_invariant():
    """The core mathematical guarantee of shuffle mode: reordering the same
    trades cannot change their sum (total return) or their win/loss count
    (win rate) - only path-dependent metrics like drawdown can move."""
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]

    result = run_bootstrap(trades, starting_balance=1000.0, method="shuffle", n_iterations=500, seed=1)

    returns = result.distributions["total_return_pct"]
    win_rates = result.distributions["win_rate_pct"]
    balances = result.distributions["ending_balance"]

    assert max(returns) - min(returns) < 1e-9, "total_return_pct varied under shuffling - should be impossible"
    assert max(win_rates) - min(win_rates) < 1e-9, "win_rate_pct varied under shuffling - should be impossible"
    assert max(balances) - min(balances) < 1e-9, "ending_balance varied under shuffling - should be impossible"

    # but drawdown SHOULD vary - the whole reason this mode exists
    drawdowns = result.distributions["max_drawdown_pct"]
    assert max(drawdowns) - min(drawdowns) > 1e-6, "max_drawdown_pct should vary across different orderings"

    print(f"total_return_pct constant at {returns[0]:.4f}% across all {len(returns)} shuffles (as required)")
    print(f"max_drawdown_pct ranged from {min(drawdowns):.3f}% to {max(drawdowns):.3f}% (as expected)")
    print("Shuffle-mode order-invariance assertions passed.")


def test_shuffle_distribution_matches_exhaustive_enumeration():
    """Ground truth check: with only 5 trades (120 possible orderings), we
    can enumerate every single permutation directly and compute its exact
    drawdown - then verify the Monte Carlo estimate from run_bootstrap
    closely matches reality, not just that it runs without crashing."""
    pnls = [40, -25, 15, -35, 20]
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate(pnls)]
    starting_balance = 1000.0

    # exhaustive ground truth
    true_drawdowns = []
    for perm in itertools.permutations(trades):
        values = _extract(list(perm), starting_balance)
        true_drawdowns.append(values["max_drawdown_pct"])
    true_min, true_max = min(true_drawdowns), max(true_drawdowns)
    true_mean = sum(true_drawdowns) / len(true_drawdowns)

    # Monte Carlo estimate (large n relative to only 120 possible orderings,
    # so it should have sampled the full range and converged close to the true mean)
    mc_result = run_bootstrap(trades, starting_balance, method="shuffle", n_iterations=3000, seed=7)
    mc_drawdowns = mc_result.distributions["max_drawdown_pct"]

    assert abs(min(mc_drawdowns) - true_min) < 1e-6, f"MC min {min(mc_drawdowns)} should reach the true min {true_min} with 3000 draws from only 120 orderings"
    assert abs(max(mc_drawdowns) - true_max) < 1e-6, f"MC max {max(mc_drawdowns)} should reach the true max {true_max} with 3000 draws from only 120 orderings"
    mc_mean = sum(mc_drawdowns) / len(mc_drawdowns)
    assert abs(mc_mean - true_mean) < 0.05, f"MC mean {mc_mean:.4f} should be very close to true mean {true_mean:.4f} with this many draws"

    print(f"True (enumerated over {len(true_drawdowns)} orderings): min={true_min:.3f}% max={true_max:.3f}% mean={true_mean:.4f}%")
    print(f"Monte Carlo (3000 draws): min={min(mc_drawdowns):.3f}% max={max(mc_drawdowns):.3f}% mean={mc_mean:.4f}%")
    print("Monte Carlo vs. exhaustive enumeration assertions passed.")


def test_resample_mode_return_actually_varies():
    """Unlike shuffle, resample-with-replacement can omit or repeat trades,
    so total_return_pct should genuinely vary between iterations."""
    trades = [_closed_trade(pnl, i) for i, pnl in enumerate([50, -20, 30, -40, 10, -5, 60, -15])]
    result = run_bootstrap(trades, starting_balance=1000.0, method="resample", n_iterations=500, seed=1)

    returns = result.distributions["total_return_pct"]
    assert max(returns) - min(returns) > 1e-6, "total_return_pct should vary under resample-with-replacement"
    print(f"resample total_return_pct ranged {min(returns):.2f}% to {max(returns):.2f}% (varies, as expected)")
    print("Resample-mode variation assertion passed.")


def test_too_few_trades_raises():
    trades = [_closed_trade(10.0, 0)]  # only 1 closed trade
    try:
        run_bootstrap(trades, starting_balance=1000.0, method="resample", n_iterations=100)
        raise AssertionError("expected a ValueError for fewer than 2 closed trades")
    except ValueError as e:
        assert "at least 2" in str(e)
    print("Too-few-trades ValueError assertion passed.")


def test_invalid_method_raises():
    trades = [_closed_trade(10.0, 0), _closed_trade(-5.0, 1)]
    try:
        run_bootstrap(trades, starting_balance=1000.0, method="nonsense", n_iterations=100)
        raise AssertionError("expected a ValueError for an invalid method")
    except ValueError as e:
        assert "resample" in str(e) and "shuffle" in str(e)
    print("Invalid-method ValueError assertion passed.")


if __name__ == "__main__":
    test_percentile_and_confidence_interval_math()
    test_probability_below_and_observed_percentile_rank()
    test_shuffle_mode_return_and_win_rate_are_order_invariant()
    test_shuffle_distribution_matches_exhaustive_enumeration()
    test_resample_mode_return_actually_varies()
    test_too_few_trades_raises()
    test_invalid_method_raises()
    print("\nAll bootstrap tests passed.")
