"""
tests/test_rsi_macd_confluence.py

1. _combine()/_within_window() tested DIRECTLY with hand-set tracker
   state - no price data needed, exact and fast. Covers: same-candle
   agreement, agreement within a window, agreement too far apart (no
   confluence), the confirmation_window=0 strict special case, and both
   bullish/bearish directions with go_short True/False.
2. INDEPENDENCE PROOF: runs the confluence strategy over a synthetic
   candle series and separately runs standalone RsiStrategy and
   MacdStrategy(require_rsi_confirmation=False) over the SAME series -
   confirms the confluence strategy's internal sub-decisions match the
   standalone strategies bit-for-bit at every step. This is the actual
   proof that "both fire independently" is true, not just documented.
3. BacktestEngine integration smoke test.
4. reset() clears both sub-strategies and all tracking state.

Run: python -m tests.test_rsi_macd_confluence
"""

from backtest.engine import BacktestEngine, CostModel
from strategy.base import Signal
from strategy.macd_strategy import MacdStrategy
from strategy.rsi_macd_confluence import RsiMacdConfluenceStrategy
from strategy.rsi_strategy import RsiStrategy
from tests.synthetic_data import generate_synthetic_candles


def _make(window=5, go_short=True):
    return RsiMacdConfluenceStrategy(confirmation_window=window, go_short=go_short)


def test_combine_fires_on_exact_same_candle():
    s = _make(window=0)
    s._last_rsi_bull_idx = 10
    s._last_macd_bull_idx = 10
    decision = s._combine(idx=10, rsi_signal=Signal.BUY, macd_signal=Signal.BUY)
    assert decision.signal == Signal.BUY
    print("Same-candle (window=0) confluence assertion passed.")


def test_combine_window_zero_rejects_one_candle_apart():
    s = _make(window=0)
    s._last_rsi_bull_idx = 9   # fired one candle ago
    s._last_macd_bull_idx = 10  # fires now
    decision = s._combine(idx=10, rsi_signal=Signal.HOLD, macd_signal=Signal.BUY)
    assert decision.signal == Signal.HOLD, "window=0 must reject anything not on the literal same candle"
    print("window=0 rejects 1-candle gap assertion passed.")


def test_combine_within_window_confirms():
    s = _make(window=5)
    s._last_rsi_bull_idx = 8    # fired 4 candles ago
    s._last_macd_bull_idx = 12  # fires now
    decision = s._combine(idx=12, rsi_signal=Signal.HOLD, macd_signal=Signal.BUY)
    assert decision.signal == Signal.BUY, f"4 candles apart should confirm within a window of 5, got {decision.signal}"
    assert "4 candle" in decision.reason
    print(f"Within-window confluence assertion passed: {decision.reason}")


def test_combine_outside_window_does_not_confirm():
    s = _make(window=5)
    s._last_rsi_bull_idx = 5     # fired 7 candles ago
    s._last_macd_bull_idx = 12   # fires now
    decision = s._combine(idx=12, rsi_signal=Signal.HOLD, macd_signal=Signal.BUY)
    assert decision.signal == Signal.HOLD, "7 candles apart should NOT confirm within a window of 5"
    print("Outside-window no-confluence assertion passed.")


def test_combine_one_sided_never_confirms_alone():
    s = _make(window=5)
    s._last_macd_bull_idx = 12
    # RSI has never fired bullish (_last_rsi_bull_idx stays None)
    decision = s._combine(idx=12, rsi_signal=Signal.HOLD, macd_signal=Signal.BUY)
    assert decision.signal == Signal.HOLD, "MACD alone, with no RSI agreement ever, must not confirm"
    print("One-sided-only no-confluence assertion passed.")


def test_combine_bearish_respects_go_short():
    s_short = _make(window=5, go_short=True)
    s_short._last_rsi_bear_idx = 10
    s_short._last_macd_bear_idx = 11
    decision = s_short._combine(idx=11, rsi_signal=Signal.HOLD, macd_signal=Signal.SELL)
    assert decision.signal == Signal.SELL

    s_flat = _make(window=5, go_short=False)
    s_flat._last_rsi_bear_idx = 10
    s_flat._last_macd_bear_idx = 11
    decision = s_flat._combine(idx=11, rsi_signal=Signal.HOLD, macd_signal=Signal.SELL)
    assert decision.signal == Signal.CLOSE, "go_short=False should CLOSE, not SELL, on bearish confluence"
    print("Bearish go_short True/False assertions passed.")


def test_quiet_candle_never_fires_even_if_window_state_is_stale_true():
    """Neither side fired THIS candle - must be HOLD regardless of how
    recently both fired in the past (avoids re-firing every candle while
    both happen to still be 'within window')."""
    s = _make(window=5)
    s._last_rsi_bull_idx = 10
    s._last_macd_bull_idx = 11
    decision = s._combine(idx=12, rsi_signal=Signal.HOLD, macd_signal=Signal.HOLD)
    assert decision.signal == Signal.HOLD
    print("Quiet-candle no-refire assertion passed.")


def test_independence_matches_standalone_strategies_bit_for_bit():
    candles = generate_synthetic_candles(n_candles=400, seed=17)

    confluence = RsiMacdConfluenceStrategy(confirmation_window=5)
    confluence.reset()
    standalone_rsi = RsiStrategy(period=14, go_short=True)
    standalone_rsi.reset()
    standalone_macd = MacdStrategy(fast=12, slow=26, signal=9, go_short=True, require_rsi_confirmation=False)
    standalone_macd.reset()

    for candle in candles:
        confluence.on_candle(candle, [])
        standalone_rsi.on_candle(candle, [])
        standalone_macd.on_candle(candle, [])

        assert confluence._rsi.last_rsi == standalone_rsi.last_rsi, "confluence's internal RSI diverged from standalone RsiStrategy"
        assert confluence._macd.last_macd == standalone_macd.last_macd, "confluence's internal MACD diverged from standalone MacdStrategy"
        assert confluence._macd.last_signal == standalone_macd.last_signal

    print(f"Confluence's internal RSI/MACD matched standalone strategies bit-for-bit across all {len(candles)} candles.")
    print("Independence-proof assertion passed.")


def test_integration_with_backtest_engine():
    candles = generate_synthetic_candles(n_candles=800, seed=8)
    strategy = RsiMacdConfluenceStrategy(confirmation_window=5)
    engine = BacktestEngine(strategy=strategy, cost_model=CostModel(), starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    assert len(result.equity_curve) == len(candles)
    print(f"BacktestEngine integration: {len(result.trades)} trades, ending balance {result.ending_balance:,.2f}")
    print("RsiMacdConfluenceStrategy/BacktestEngine integration assertion passed.")


def test_reset_clears_both_substrategies_and_tracking_state():
    candles = generate_synthetic_candles(n_candles=100, seed=2)
    strategy = RsiMacdConfluenceStrategy()
    strategy.reset()
    for candle in candles:
        strategy.on_candle(candle, [])
    assert strategy._rsi.last_rsi is not None, "test setup should have produced a defined RSI by now"

    strategy.reset()
    assert strategy._rsi.last_rsi is None
    assert strategy._macd.last_macd is None
    assert strategy._candle_index == -1
    assert strategy._last_rsi_bull_idx is None
    assert strategy._last_macd_bull_idx is None
    assert strategy._last_rsi_bear_idx is None
    assert strategy._last_macd_bear_idx is None
    print("reset() full-state-clear assertion passed.")


if __name__ == "__main__":
    test_combine_fires_on_exact_same_candle()
    test_combine_window_zero_rejects_one_candle_apart()
    test_combine_within_window_confirms()
    test_combine_outside_window_does_not_confirm()
    test_combine_one_sided_never_confirms_alone()
    test_combine_bearish_respects_go_short()
    test_quiet_candle_never_fires_even_if_window_state_is_stale_true()
    test_independence_matches_standalone_strategies_bit_for_bit()
    test_integration_with_backtest_engine()
    test_reset_clears_both_substrategies_and_tracking_state()
    print("\nAll RsiMacdConfluenceStrategy tests passed.")
