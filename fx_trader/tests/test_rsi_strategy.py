"""
tests/test_rsi_strategy.py

1. RsiStrategy.last_rsi cross-checked against indicators.rsi()'s batch
   output at EVERY step over a longer synthetic series - proves the
   strategy's incremental computation matches a from-scratch recomputation.
2. Two hand-traced crafted price series (period=3, small enough to trace
   by hand) - one producing an overbought-reversal SELL, one producing an
   oversold-reversal BUY - checked against the exact expected candle index.
3. go_short=False degrades the overbought-reversal signal to CLOSE.

Run: python -m tests.test_rsi_strategy
"""

from datetime import datetime, timedelta, timezone

from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.indicators import rsi
from strategy.rsi_strategy import RsiStrategy
from strategy.base import Signal
from tests.synthetic_data import generate_synthetic_candles


def make_candle(index: int, price: float) -> Candle:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price, volume=1,
    )


def test_last_rsi_matches_batch_at_every_step():
    candles = generate_synthetic_candles(n_candles=300, seed=21)
    closes = [c.mid_close for c in candles]
    expected = rsi(closes, period=14)

    strategy = RsiStrategy(period=14)
    strategy.reset()
    observed = []
    for candle in candles:
        strategy.on_candle(candle, [])
        observed.append(strategy.last_rsi)

    mismatches = [
        i for i in range(len(closes))
        if (expected[i] is None) != (observed[i] is None)
        or (expected[i] is not None and abs(expected[i] - observed[i]) > 1e-9)
    ]
    assert not mismatches, f"RsiStrategy.last_rsi diverged from batch rsi() at indices {mismatches[:5]}"
    print(f"RsiStrategy.last_rsi matched batch rsi() at all {len(closes)} steps.")
    print("Streaming-strategy-vs-batch assertion passed.")


def test_overbought_reversal_produces_sell_at_expected_candle():
    """closes=[10,12,11,13,9,14], period=3 -> rsi=[.,.,.,80.0,36.36,68.5].
    prev_rsi(80)>=70 and 70>36.36 at candle index 4 -> SELL there, HOLD
    everywhere else (hand-traced in the module this test lives beside)."""
    prices = [10.0, 12.0, 11.0, 13.0, 9.0, 14.0]
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    strategy = RsiStrategy(period=3, go_short=True)
    strategy.reset()
    signals = [strategy.on_candle(c, []).signal for c in candles]

    assert signals[4] == Signal.SELL, f"expected SELL at index 4, got {signals}"
    for i in (0, 1, 2, 3, 5):
        assert signals[i] == Signal.HOLD, f"expected HOLD at index {i}, got {signals[i]}"
    print(f"signals={[s.value for s in signals]} (SELL at index 4, as hand-traced)")
    print("Overbought-reversal SELL assertion passed.")


def test_overbought_reversal_closes_instead_of_shorting_when_go_short_false():
    prices = [10.0, 12.0, 11.0, 13.0, 9.0, 14.0]
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    strategy = RsiStrategy(period=3, go_short=False)
    strategy.reset()
    signals = [strategy.on_candle(c, []).signal for c in candles]
    assert signals[4] == Signal.CLOSE, f"expected CLOSE at index 4, got {signals}"
    print("go_short=False -> CLOSE (not SELL) assertion passed.")


def test_oversold_reversal_produces_buy_at_expected_candle():
    """closes=[20,18,16,14,12,20], period=3 -> rsi=[.,.,.,0.0,0.0,66.667].
    Stays fully oversold (0.0 -> 0.0, no cross) until index 5 where it
    jumps back above 30 -> BUY there, HOLD everywhere else."""
    prices = [20.0, 18.0, 16.0, 14.0, 12.0, 20.0]
    candles = [make_candle(i, p) for i, p in enumerate(prices)]
    strategy = RsiStrategy(period=3)
    strategy.reset()
    signals = [strategy.on_candle(c, []).signal for c in candles]

    assert signals[5] == Signal.BUY, f"expected BUY at index 5, got {signals}"
    for i in range(5):
        assert signals[i] == Signal.HOLD, f"expected HOLD at index {i}, got {signals[i]}"
    print(f"signals={[s.value for s in signals]} (BUY at index 5, as hand-traced)")
    print("Oversold-reversal BUY assertion passed.")


def test_integration_with_backtest_engine():
    candles = generate_synthetic_candles(n_candles=500, seed=5)
    engine = BacktestEngine(strategy=RsiStrategy(period=14), cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    assert len(result.equity_curve) == len(candles)
    assert result.starting_balance == 10_000.0
    print(f"BacktestEngine integration: {len(result.trades)} trades, ending balance {result.ending_balance:,.2f}")
    print("RsiStrategy/BacktestEngine integration assertion passed.")


if __name__ == "__main__":
    test_last_rsi_matches_batch_at_every_step()
    test_overbought_reversal_produces_sell_at_expected_candle()
    test_overbought_reversal_closes_instead_of_shorting_when_go_short_false()
    test_oversold_reversal_produces_buy_at_expected_candle()
    test_integration_with_backtest_engine()
    print("\nAll RsiStrategy tests passed.")
