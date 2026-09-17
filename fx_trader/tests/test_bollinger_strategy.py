"""
tests/test_bollinger_strategy.py

1. BollingerBandsStrategy.last_middle/last_upper/last_lower cross-checked
   against indicators.bollinger_bands()'s batch output at EVERY step over
   a longer synthetic series.
2. A crash-and-recovery price series (flat baseline, one sharp drop, back
   to flat) with the expected BUY/SELL candle indices derived
   PROGRAMMATICALLY from an independent zone classification (using
   Python's own `statistics` module, not this project's code) computed in
   this test - not hand-typed magic numbers, since Bollinger Band width
   depends on the window's own contents in a way that makes hand-picking
   "obviously correct" indices fragile (a sharp move partly widens the
   band around itself - see bollinger_strategy.py's docstring). The
   independent classification is the ground truth; the assertion is that
   the strategy matches it exactly.
3. go_short=False degrades the upper-band reversal to CLOSE.
4. BacktestEngine integration.

Run: python -m tests.test_bollinger_strategy
"""

import statistics
from datetime import datetime, timedelta, timezone

from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.base import Signal
from strategy.bollinger_strategy import BollingerBandsStrategy
from strategy.indicators import bollinger_bands
from tests.synthetic_data import generate_synthetic_candles


def make_candle(index: int, price: float) -> Candle:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price, volume=1,
    )


def test_last_bands_match_batch_at_every_step():
    candles = generate_synthetic_candles(n_candles=300, seed=51)
    closes = [c.mid_close for c in candles]
    period = 20
    expected_mid, expected_up, expected_lo = bollinger_bands(closes, period=period)

    strategy = BollingerBandsStrategy(period=period)
    strategy.reset()
    observed_mid, observed_up, observed_lo = [], [], []
    for candle in candles:
        strategy.on_candle(candle, [])
        observed_mid.append(strategy.last_middle)
        observed_up.append(strategy.last_upper)
        observed_lo.append(strategy.last_lower)

    def _mismatches(expected, observed):
        return [
            i for i in range(len(expected))
            if (expected[i] is None) != (observed[i] is None)
            or (expected[i] is not None and abs(expected[i] - observed[i]) > 1e-9)
        ]

    assert not _mismatches(expected_mid, observed_mid), "last_middle diverged from batch bollinger_bands()"
    assert not _mismatches(expected_up, observed_up), "last_upper diverged from batch bollinger_bands()"
    assert not _mismatches(expected_lo, observed_lo), "last_lower diverged from batch bollinger_bands()"
    print(f"BollingerBandsStrategy matched batch bollinger_bands() at all {len(closes)} steps.")
    print("Streaming-strategy-vs-batch assertion passed.")


def _independent_zone_sequence(closes, period, num_std):
    """Independently classifies each candle's zone (below_lower/inside/
    above_upper) using statistics.mean/pstdev directly - not this
    project's bollinger_bands(). Returns a list of zone strings, None
    during warmup."""
    zones = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        mean = statistics.mean(window)
        std = statistics.pstdev(window)
        upper, lower = mean + num_std * std, mean - num_std * std
        close = closes[i]
        zones[i] = "above_upper" if close > upper else "below_lower" if close < lower else "inside"
    return zones


def _expected_signals_from_zones(zones):
    """Reimplements the reversal-detection rule independently (prev zone
    was an extreme, current zone is not that same extreme -> BUY/SELL) -
    a second, separate pass over the ground-truth zone sequence, not the
    strategy's own _decide()."""
    signals = []
    prev = None
    for zone in zones:
        if zone is None or prev is None:
            signals.append(Signal.HOLD)
        elif prev == "below_lower" and zone != "below_lower":
            signals.append(Signal.BUY)
        elif prev == "above_upper" and zone != "above_upper":
            signals.append(Signal.SELL)
        else:
            signals.append(Signal.HOLD)
        if zone is not None:
            prev = zone
    return signals


def test_crash_and_recovery_matches_independent_zone_classification():
    # Flat baseline at 100, a sharp 5-candle drop to 60, back to flat.
    prices = [100.0] * 15 + [60.0] * 5 + [100.0] * 15
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    period, num_std = 10, 2.0

    zones = _independent_zone_sequence(prices, period, num_std)
    expected_signals = _expected_signals_from_zones(zones)
    assert Signal.BUY in expected_signals or Signal.SELL in expected_signals, (
        "test setup should produce at least one band-reversal signal - adjust the crafted series if this ever fails"
    )

    strategy = BollingerBandsStrategy(period=period, num_std=num_std)
    strategy.reset()
    actual_signals = [strategy.on_candle(c, []).signal for c in candles]

    assert actual_signals == expected_signals, (
        f"strategy signals diverged from independent zone classification:\n"
        f"expected={[s.value for s in expected_signals]}\nactual=  {[s.value for s in actual_signals]}"
    )
    fired_at = [i for i, s in enumerate(actual_signals) if s != Signal.HOLD]
    print(f"Signals fired at indices {fired_at}: {[actual_signals[i].value for i in fired_at]}")
    print("Crash-and-recovery independent-classification assertion passed.")


def test_go_short_false_closes_instead_of_shorting():
    prices = [100.0] * 15 + [140.0] * 5 + [100.0] * 15  # sharp spike instead of a crash
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    period, num_std = 10, 2.0

    zones = _independent_zone_sequence(prices, period, num_std)
    if Signal.SELL not in _expected_signals_from_zones(zones):
        print("SKIPPED: this crafted spike didn't produce an above_upper reversal - not a strategy bug, adjust the series.")
        return

    strategy = BollingerBandsStrategy(period=period, num_std=num_std, go_short=False)
    strategy.reset()
    signals = [strategy.on_candle(c, []).signal for c in candles]
    assert Signal.CLOSE in signals, f"expected a CLOSE (not SELL) on the upper-band reversal, got {[s.value for s in signals]}"
    assert Signal.SELL not in signals
    print("go_short=False -> CLOSE (not SELL) assertion passed.")


def test_integration_with_backtest_engine():
    candles = generate_synthetic_candles(n_candles=500, seed=9)
    engine = BacktestEngine(strategy=BollingerBandsStrategy(), cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    assert len(result.equity_curve) == len(candles)
    print(f"BacktestEngine integration: {len(result.trades)} trades, ending balance {result.ending_balance:,.2f}")
    print("BollingerBandsStrategy/BacktestEngine integration assertion passed.")


if __name__ == "__main__":
    test_last_bands_match_batch_at_every_step()
    test_crash_and_recovery_matches_independent_zone_classification()
    test_go_short_false_closes_instead_of_shorting()
    test_integration_with_backtest_engine()
    print("\nAll BollingerBandsStrategy tests passed.")
