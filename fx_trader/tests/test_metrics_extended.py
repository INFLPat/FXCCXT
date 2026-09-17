"""
tests/test_metrics_extended.py

Hand-verified tests for every metric added to backtest/metrics.py this
session. Uses a deterministic, zero-cost 6-trade scripted strategy (bid ==
ask, no commission/slippage) so trade P&L, drawdown shape, and streaks are
all exactly computable by hand - same discipline as tests/test_engine.py.

Trade P&L sequence (by construction): +10, +15, -10, -10, -5, +20
  -> 3 wins / 3 losses, streak pattern W,W,L,L,L,W
  -> gross_profit=45, gross_loss=25, profit_factor=1.8
  -> avg_win=15, avg_loss=-8.3333, payoff_ratio=1.8, kelly=2/9
  -> equity curve: 10000,10010,10010,10025,10025,10015,10015,10005,10005,
     10000,10000,10020 (12 points, starting_balance=10000)
  -> single continuous drawdown from index 5 through the end (7 periods),
     still underwater when the run ends
  -> exposure: 6 of 12 candles have a position open (50%)

Trickier distribution-shape metrics (Sortino/Omega/skew/kurtosis/Ulcer) are
checked against an INDEPENDENTLY written reference formula in this file
(not copy-pasted from metrics.py) rather than hand-typed decimals, since
those involve irrational intermediate values - independence is what makes
it a real check rather than a tautology.

Run: python -m tests.test_metrics_extended
"""

import math
from datetime import datetime, timedelta, timezone

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from backtest.service_tiers import ServiceTier
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class SixTradeScript(Strategy):
    """BUY,CLOSE,BUY,CLOSE,... alternating every candle for exactly 12
    candles (6 round-trip trades), then HOLD forever. Deterministic by
    candle index, not by price - safe against the specific prices used."""

    def __init__(self):
        self._i = -1

    def reset(self):
        self._i = -1

    def on_candle(self, candle, history):
        self._i += 1
        if self._i > 11:
            return StrategyDecision(Signal.HOLD)
        return StrategyDecision(Signal.BUY if self._i % 2 == 0 else Signal.CLOSE)


def make_candle(index: int, price: float) -> Candle:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price,
        volume=1,
    )


PRICES = [100, 110, 110, 125, 125, 115, 115, 105, 105, 100, 100, 120]


def _run() -> tuple:
    candles = [make_candle(i, p) for i, p in enumerate(PRICES)]
    engine = BacktestEngine(
        strategy=SixTradeScript(), cost_model=CostModel(),
        starting_balance=10_000.0, units_per_trade=1.0,
    )
    result = engine.run(candles)
    # periods_per_year=11 is deliberate: with 12 equity points, that makes
    # years = (12-1)/11 = 1.0 exactly, so Calmar's CAGR annualization
    # reduces to the plain (non-annualized) return - letting us check it
    # against recovery_factor, which is NOT annualized, as a cross-check.
    metrics = compute_metrics(result, periods_per_year=11)
    return result, metrics


def test_trade_economics_exact():
    result, m = _run()
    assert len(result.closed_trades) == 6
    assert abs(m.win_rate_pct - 50.0) < 1e-9
    assert abs(m.profit_factor - 1.8) < 1e-9
    assert abs(m.avg_win - 15.0) < 1e-9
    assert abs(m.avg_loss - (-25 / 3)) < 1e-9
    assert abs(m.trade_expectancy - 10 / 3) < 1e-9, f"expected 3.3333, got {m.trade_expectancy}"
    assert abs(m.trade_expectancy_pct - (10 / 3 / 10_000 * 100)) < 1e-9
    assert abs(m.payoff_ratio - 1.8) < 1e-9
    print(f"expectancy={m.trade_expectancy:.4f} payoff_ratio={m.payoff_ratio:.4f} (both hand-verified)")
    print("Trade economics assertions passed.")


def test_streaks_exact():
    _, m = _run()
    assert m.max_consecutive_wins == 2, f"expected 2 (W,W at trades 1-2), got {m.max_consecutive_wins}"
    assert m.max_consecutive_losses == 3, f"expected 3 (L,L,L at trades 3-5), got {m.max_consecutive_losses}"
    print(f"streaks: {m.max_consecutive_wins} wins / {m.max_consecutive_losses} losses (hand-verified)")
    print("Streak assertions passed.")


def test_exposure_and_duration_exact():
    result, m = _run()
    assert result.periods_in_market == 6, f"expected 6 of 12 candles in-market, got {result.periods_in_market}"
    assert abs(m.exposure_pct - 50.0) < 1e-9
    assert abs(m.avg_trade_duration_hours - 1.0) < 1e-9, "every scripted trade spans exactly 1 hour"
    assert abs(m.cost_drag_pct - 0.0) < 1e-9, "zero-cost model - cost drag must be exactly 0%"
    print(f"exposure={m.exposure_pct}% avg_duration={m.avg_trade_duration_hours}h cost_drag={m.cost_drag_pct}%")
    print("Exposure/duration/cost-drag assertions passed.")


def test_drawdown_duration_exact():
    """Equity: 10000,10010,10010,10025,10025,10015,10015,10005,10005,
    10000,10000,10020 - peaks at 10025 (index 3-4), never recovers past
    it, so indices 5-11 (7 points) are one continuous drawdown streak
    still open when the run ends."""
    _, m = _run()
    assert m.max_drawdown_duration == 7, f"expected 7, got {m.max_drawdown_duration}"
    assert abs(m.avg_drawdown_duration - 7.0) < 1e-9, "only one drawdown streak, so avg == max"
    assert m.still_in_drawdown_at_end is True
    expected_max_dd_pct = (10025 - 10000) / 10025 * 100
    assert abs(m.max_drawdown_pct - expected_max_dd_pct) < 1e-9
    print(f"max_drawdown_duration={m.max_drawdown_duration} avg={m.avg_drawdown_duration} still_underwater={m.still_in_drawdown_at_end}")
    print("Drawdown duration assertions passed.")


def test_var_cvar_tail_ratio_exact():
    """pnls sorted: [-10,-10,-5,10,15,20]. 5th percentile lands exactly on
    the tied -10 value regardless of interpolation fraction (both
    neighbors are -10), so var_95/cvar_95 are exact, not approximate."""
    _, m = _run()
    assert abs(m.var_95 - 10.0) < 1e-9, f"expected 10.0, got {m.var_95}"
    assert abs(m.cvar_95 - 10.0) < 1e-9, f"expected 10.0 (mean of the two -10 trades), got {m.cvar_95}"
    expected_tail_ratio = abs(18.75) / abs(-10.0)  # p95=15+(20-15)*0.75=18.75, p5=-10.0
    assert abs(m.tail_ratio - expected_tail_ratio) < 1e-9, f"expected {expected_tail_ratio}, got {m.tail_ratio}"
    print(f"var_95={m.var_95} cvar_95={m.cvar_95} tail_ratio={m.tail_ratio:.4f}")
    print("VaR/CVaR/tail-ratio assertions passed.")


def test_kelly_fraction_exact():
    """f* = W - (1-W)/R = 0.5 - 0.5/1.8 = 2/9 exactly."""
    _, m = _run()
    assert abs(m.kelly_fraction - 2 / 9) < 1e-9, f"expected {2/9}, got {m.kelly_fraction}"
    print(f"kelly_fraction={m.kelly_fraction:.6f} (expected {2/9:.6f})")
    print("Kelly fraction assertion passed.")


def test_calmar_equals_recovery_when_years_equals_one():
    """The deliberate periods_per_year=11 choice makes Calmar's CAGR
    annualization reduce to the plain (non-annualized) return - so Calmar
    and recovery_factor must come out IDENTICAL here. This is an
    invariant check, not a magic-number check: it fails if either
    formula's algebra is wrong relative to the other."""
    _, m = _run()
    assert m.calmar_ratio is not None and m.recovery_factor is not None
    assert abs(m.calmar_ratio - m.recovery_factor) < 1e-9, (
        f"calmar ({m.calmar_ratio}) should equal recovery_factor ({m.recovery_factor}) when years=1"
    )
    expected_recovery = 0.2 / ((10025 - 10000) / 10025 * 100)
    assert abs(m.recovery_factor - expected_recovery) < 1e-9
    print(f"calmar={m.calmar_ratio:.6f} recovery_factor={m.recovery_factor:.6f} (match, as required)")
    print("Calmar/recovery invariant assertion passed.")


def _independent_period_returns(equity_curve):
    """Deliberately re-derived here rather than importing metrics.py's
    _period_returns - see module docstring."""
    values = [e for _, e in equity_curve]
    return [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]


def test_sortino_omega_against_independent_reference():
    result, m = _run()
    returns = _independent_period_returns(result.equity_curve)
    n = len(returns)
    mean = sum(returns) / n

    downside_sq = sum(min(r, 0.0) ** 2 for r in returns)
    downside_dev = math.sqrt(downside_sq / n)
    expected_sortino = (mean / downside_dev) * math.sqrt(11) if downside_dev != 0 else None

    gains = sum(r for r in returns if r > 0)
    losses = sum(-r for r in returns if r < 0)
    expected_omega = gains / losses if losses != 0 else None

    assert abs(m.downside_deviation - downside_dev) < 1e-12
    assert expected_sortino is not None and m.sortino_ratio is not None
    assert abs(m.sortino_ratio - expected_sortino) < 1e-9, f"expected {expected_sortino}, got {m.sortino_ratio}"
    assert expected_omega is not None and m.omega_ratio is not None
    assert abs(m.omega_ratio - expected_omega) < 1e-9, f"expected {expected_omega}, got {m.omega_ratio}"
    print(f"sortino={m.sortino_ratio:.4f} (expected {expected_sortino:.4f}), omega={m.omega_ratio:.4f} (expected {expected_omega:.4f})")
    print("Sortino/Omega independent-reference assertions passed.")


def test_ulcer_index_against_independent_reference():
    result, m = _run()
    # Independently re-derive the drawdown series with a plain running-max
    # loop, not by importing metrics.py's _drawdown_pct_series.
    peak = result.equity_curve[0][1]
    dd_series = []
    for _, equity in result.equity_curve:
        peak = max(peak, equity)
        dd_series.append((peak - equity) / peak * 100)
    expected_ulcer = math.sqrt(sum(d ** 2 for d in dd_series) / len(dd_series))

    assert abs(m.ulcer_index - expected_ulcer) < 1e-9, f"expected {expected_ulcer}, got {m.ulcer_index}"
    print(f"ulcer_index={m.ulcer_index:.4f} (expected {expected_ulcer:.4f})")
    print("Ulcer index independent-reference assertion passed.")


def test_skew_kurtosis_against_independent_reference():
    _, m = _run()
    pnls = [10.0, 15.0, -10.0, -10.0, -5.0, 20.0]
    n = len(pnls)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / n
    std = math.sqrt(var)
    expected_skew = (sum((p - mean) ** 3 for p in pnls) / n) / (std ** 3)
    expected_kurtosis = (sum((p - mean) ** 4 for p in pnls) / n) / (std ** 4) - 3.0

    assert abs(m.skewness - expected_skew) < 1e-9, f"expected {expected_skew}, got {m.skewness}"
    assert abs(m.kurtosis - expected_kurtosis) < 1e-9, f"expected {expected_kurtosis}, got {m.kurtosis}"
    print(f"skew={m.skewness:.5f} (expected {expected_skew:.5f}), kurtosis={m.kurtosis:.5f} (expected {expected_kurtosis:.5f})")
    print("Skew/kurtosis independent-reference assertions passed.")


def test_buy_hold_and_alpha():
    candles = [make_candle(i, p) for i, p in enumerate(PRICES)]
    engine = BacktestEngine(strategy=SixTradeScript(), cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1.0)
    result = engine.run(candles)
    m = compute_metrics(result, periods_per_year=11, candles=candles)

    expected_buy_hold = (120 - 100) / 100 * 100
    assert abs(m.buy_hold_return_pct - expected_buy_hold) < 1e-9
    assert abs(m.alpha_vs_buy_hold_pct - (m.total_return_pct - expected_buy_hold)) < 1e-9

    m_no_candles = compute_metrics(result, periods_per_year=11)
    assert m_no_candles.buy_hold_return_pct is None
    assert m_no_candles.alpha_vs_buy_hold_pct is None
    print(f"buy_hold={m.buy_hold_return_pct}% alpha={m.alpha_vs_buy_hold_pct}% | omitted candles -> both None as expected")
    print("Buy & hold / alpha assertions passed.")


def test_service_tier_filtering_is_cumulative_and_fails_closed():
    _, m = _run()
    bronze = m.for_service_tier(ServiceTier.BRONZE)
    silver = m.for_service_tier(ServiceTier.SILVER)
    gold = m.for_service_tier(ServiceTier.GOLD)

    assert set(bronze.keys()).issubset(silver.keys()), "silver must see everything bronze sees"
    assert set(silver.keys()).issubset(gold.keys()), "gold must see everything silver sees"
    assert "total_return_pct" in bronze
    assert "sortino_ratio" not in bronze and "sortino_ratio" in silver
    assert "kelly_fraction" not in silver and "kelly_fraction" in gold
    assert len(gold) == len(m.as_dict()), "gold should see every field that exists"

    print(f"bronze={len(bronze)} silver={len(silver)} gold={len(gold)} fields (cumulative, as required)")
    print("Service-tier filtering assertions passed.")


if __name__ == "__main__":
    test_trade_economics_exact()
    test_streaks_exact()
    test_exposure_and_duration_exact()
    test_drawdown_duration_exact()
    test_var_cvar_tail_ratio_exact()
    test_kelly_fraction_exact()
    test_calmar_equals_recovery_when_years_equals_one()
    test_sortino_omega_against_independent_reference()
    test_ulcer_index_against_independent_reference()
    test_skew_kurtosis_against_independent_reference()
    test_buy_hold_and_alpha()
    test_service_tier_filtering_is_cumulative_and_fails_closed()
    print("\nAll extended-metrics tests passed.")
