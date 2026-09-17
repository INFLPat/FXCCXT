"""
tests/test_macd_strategy.py

1. MacdStrategy.last_macd/last_signal cross-checked against
   indicators.macd()'s batch output at EVERY step over a longer synthetic
   series.
2. A hand-traced crafted price series (fast=2, slow=3, signal=2 - small
   enough to trace) produces a crossover at an exact, checkable candle.
3. The RSI-confirmation filter is tested DIRECTLY against the decision
   methods with a forced last_rsi value, rather than trying to coincide a
   MACD crossover with an RSI extreme in one crafted series - cleaner and
   exactly targets the documented behavior.
4. require_rsi_confirmation=False bypasses the filter regardless of RSI.

Run: python -m tests.test_macd_strategy
"""

from datetime import datetime, timedelta, timezone

from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.base import Signal
from strategy.indicators import macd
from strategy.macd_strategy import MacdStrategy
from tests.synthetic_data import generate_synthetic_candles


def make_candle(index: int, price: float) -> Candle:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price, volume=1,
    )


def test_last_macd_and_signal_match_batch_at_every_step():
    candles = generate_synthetic_candles(n_candles=300, seed=31)
    closes = [c.mid_close for c in candles]
    expected_macd, expected_signal, _hist = macd(closes, fast=12, slow=26, signal=9)

    strategy = MacdStrategy(fast=12, slow=26, signal=9, require_rsi_confirmation=False)
    strategy.reset()
    observed_macd, observed_signal = [], []
    for candle in candles:
        strategy.on_candle(candle, [])
        observed_macd.append(strategy.last_macd)
        observed_signal.append(strategy.last_signal)

    macd_mismatches = [
        i for i in range(len(closes))
        if (expected_macd[i] is None) != (observed_macd[i] is None)
        or (expected_macd[i] is not None and abs(expected_macd[i] - observed_macd[i]) > 1e-9)
    ]
    signal_mismatches = [
        i for i in range(len(closes))
        if (expected_signal[i] is None) != (observed_signal[i] is None)
        or (expected_signal[i] is not None and abs(expected_signal[i] - observed_signal[i]) > 1e-9)
    ]
    assert not macd_mismatches, f"last_macd diverged from batch macd() at indices {macd_mismatches[:5]}"
    assert not signal_mismatches, f"last_signal diverged from batch macd() at indices {signal_mismatches[:5]}"
    print(f"MacdStrategy matched batch macd() at all {len(closes)} steps (both lines).")
    print("Streaming-strategy-vs-batch assertion passed.")


def test_crossover_fires_at_expected_candle():
    """fast=2, slow=3, signal=2 on closes=[10,11,9,12,8,13,14]: macd_line
    becomes defined at index 2, signal_line at index 3. Sign of
    (macd - signal): index3 +, index4 - (down-cross -> SELL),
    index5 + (up-cross -> BUY), index6 + (no cross)."""
    prices = [10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 14.0]
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    strategy = MacdStrategy(fast=2, slow=3, signal=2, require_rsi_confirmation=False)
    strategy.reset()
    signals = [strategy.on_candle(c, []).signal for c in candles]

    assert signals[4] == Signal.SELL, f"expected SELL at index 4, got {signals}"
    assert signals[5] == Signal.BUY, f"expected BUY at index 5, got {signals}"
    for i in (0, 1, 2, 3, 6):
        assert signals[i] == Signal.HOLD, f"expected HOLD at index {i}, got {signals[i]}"
    print(f"signals={[s.value for s in signals]} (SELL@4, BUY@5, as hand-traced)")
    print("MACD crossover timing assertion passed.")


def test_rsi_confirmation_filter_suppresses_bullish_when_overbought():
    strategy = MacdStrategy(require_rsi_confirmation=True, rsi_overbought=70.0, rsi_oversold=30.0)

    strategy.last_rsi = 75.0
    decision = strategy._bullish_cross_decision()
    assert decision.signal == Signal.HOLD, "bullish cross should be suppressed when RSI is already overbought"

    strategy.last_rsi = 50.0
    decision = strategy._bullish_cross_decision()
    assert decision.signal == Signal.BUY, "bullish cross should fire when RSI is neutral"
    print("RSI-confirmation filter (bullish/overbought) assertions passed.")


def test_rsi_confirmation_filter_suppresses_bearish_when_oversold():
    strategy = MacdStrategy(require_rsi_confirmation=True, rsi_overbought=70.0, rsi_oversold=30.0, go_short=True)

    strategy.last_rsi = 20.0
    decision = strategy._bearish_cross_decision()
    assert decision.signal == Signal.HOLD, "bearish cross should be suppressed when RSI is already oversold"

    strategy.last_rsi = 50.0
    decision = strategy._bearish_cross_decision()
    assert decision.signal == Signal.SELL, "bearish cross should fire (as SELL, go_short=True) when RSI is neutral"
    print("RSI-confirmation filter (bearish/oversold) assertions passed.")


def test_require_rsi_confirmation_false_bypasses_filter():
    strategy = MacdStrategy(require_rsi_confirmation=False)
    strategy.last_rsi = 99.0  # deeply overbought - would suppress if filter were on
    decision = strategy._bullish_cross_decision()
    assert decision.signal == Signal.BUY, "filter disabled - should fire regardless of RSI"
    print("require_rsi_confirmation=False bypass assertion passed.")


def test_integration_with_backtest_engine():
    candles = generate_synthetic_candles(n_candles=500, seed=6)
    engine = BacktestEngine(strategy=MacdStrategy(), cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    assert len(result.equity_curve) == len(candles)
    print(f"BacktestEngine integration: {len(result.trades)} trades, ending balance {result.ending_balance:,.2f}")
    print("MacdStrategy/BacktestEngine integration assertion passed.")


if __name__ == "__main__":
    test_last_macd_and_signal_match_batch_at_every_step()
    test_crossover_fires_at_expected_candle()
    test_rsi_confirmation_filter_suppresses_bullish_when_overbought()
    test_rsi_confirmation_filter_suppresses_bearish_when_oversold()
    test_require_rsi_confirmation_false_bypasses_filter()
    test_integration_with_backtest_engine()
    print("\nAll MacdStrategy tests passed.")
