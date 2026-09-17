"""
strategy/rsi_macd_confluence.py

RsiMacdConfluenceStrategy: RSI and MACD each fire their OWN independent
signal every candle - this class does not read one's internal state to
gate the other's decision (that's what MacdStrategy's
require_rsi_confirmation does; this is a genuinely different design).
Instead it composes real, complete RsiStrategy and MacdStrategy instances,
calls each one's on_candle() every candle exactly as if it were running
standalone, and only AFTER both have independently decided does this class
look at whether they AGREE.

"Agree" means: both fired the same-direction signal (BUY, or SELL) within
`confirmation_window` candles of each other - not necessarily the exact
same candle, since two independently-parameterized oscillators rarely
cross on the identical tick. confirmation_window=0 is the strict special
case (both must fire on the literal same candle); the default (5) is more
practically tradeable. Whichever indicator fires SECOND is what actually
emits the combined signal - there is no "primary" indicator on either side
of that relationship, unlike MacdStrategy's design.

Internally, both sub-strategies always run go_short=True and (for MACD)
require_rsi_confirmation=False regardless of this class's own settings -
each must produce an unfiltered, unambiguous BUY/SELL/HOLD independent of
what the other or the combined strategy will eventually decide. THIS
class's own `go_short` controls only what the FINAL combined bearish
signal is (SELL vs. CLOSE), applied after agreement is already established.

Cross-checked in tests/test_rsi_macd_confluence.py against standalone
RsiStrategy/MacdStrategy(require_rsi_confirmation=False) run on the same
candle stream, to prove the two sub-signals really are computed
independently and not silently coupled.
"""

from strategy.base import Signal, Strategy, StrategyDecision
from strategy.macd_strategy import MacdStrategy
from strategy.rsi_strategy import RsiStrategy


class RsiMacdConfluenceStrategy(Strategy):
    def __init__(
        self, rsi_period: int = 14, rsi_oversold: float = 30.0, rsi_overbought: float = 70.0,
        macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
        confirmation_window: int = 5, go_short: bool = True,
    ):
        assert confirmation_window >= 0, "confirmation_window must be non-negative (0 = same-candle only)"
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.confirmation_window = confirmation_window
        self.go_short = go_short

        self._rsi = RsiStrategy(period=rsi_period, oversold=rsi_oversold, overbought=rsi_overbought, go_short=True)
        self._macd = MacdStrategy(
            fast=macd_fast, slow=macd_slow, signal=macd_signal, go_short=True, require_rsi_confirmation=False,
        )

        self._candle_index = -1
        self._last_rsi_bull_idx: int | None = None
        self._last_macd_bull_idx: int | None = None
        self._last_rsi_bear_idx: int | None = None
        self._last_macd_bear_idx: int | None = None

    def reset(self) -> None:
        self._rsi.reset()
        self._macd.reset()
        self._candle_index = -1
        self._last_rsi_bull_idx = None
        self._last_macd_bull_idx = None
        self._last_rsi_bear_idx = None
        self._last_macd_bear_idx = None

    def on_candle(self, candle, history) -> StrategyDecision:
        self._candle_index += 1
        idx = self._candle_index

        rsi_decision = self._rsi.on_candle(candle, history)
        macd_decision = self._macd.on_candle(candle, history)

        self._update_trackers(idx, rsi_decision.signal, macd_decision.signal)
        return self._combine(idx, rsi_decision.signal, macd_decision.signal)

    def _update_trackers(self, idx: int, rsi_signal: Signal, macd_signal: Signal) -> None:
        if rsi_signal == Signal.BUY:
            self._last_rsi_bull_idx = idx
        if macd_signal == Signal.BUY:
            self._last_macd_bull_idx = idx
        if rsi_signal == Signal.SELL:
            self._last_rsi_bear_idx = idx
        if macd_signal == Signal.SELL:
            self._last_macd_bear_idx = idx

    def _combine(self, idx: int, rsi_signal: Signal, macd_signal: Signal) -> StrategyDecision:
        """Only evaluated meaningfully on a candle where at least one side
        just fired (`fired_bull`/`fired_bear`) - on a quiet candle where
        neither moved, this always returns HOLD regardless of stale
        window state, so confluence fires once, on whichever indicator
        completes the pair, not on every candle both happen to still be
        'recently true'."""
        fired_bull = rsi_signal == Signal.BUY or macd_signal == Signal.BUY
        fired_bear = rsi_signal == Signal.SELL or macd_signal == Signal.SELL

        if fired_bull and self._within_window(self._last_rsi_bull_idx, idx) and self._within_window(self._last_macd_bull_idx, idx):
            gap = abs(self._last_rsi_bull_idx - self._last_macd_bull_idx)
            return StrategyDecision(Signal.BUY, f"RSI+MACD confluence: both bullish, {gap} candle(s) apart")

        if fired_bear and self._within_window(self._last_rsi_bear_idx, idx) and self._within_window(self._last_macd_bear_idx, idx):
            gap = abs(self._last_rsi_bear_idx - self._last_macd_bear_idx)
            out_signal = Signal.SELL if self.go_short else Signal.CLOSE
            return StrategyDecision(out_signal, f"RSI+MACD confluence: both bearish, {gap} candle(s) apart")

        return StrategyDecision(Signal.HOLD, "no confluence yet")

    def _within_window(self, last_idx: int | None, current_idx: int) -> bool:
        return last_idx is not None and (current_idx - last_idx) <= self.confirmation_window
