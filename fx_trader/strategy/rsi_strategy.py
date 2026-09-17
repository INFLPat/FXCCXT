"""
strategy/rsi_strategy.py

Classic Wilder RSI mean-reversion strategy: goes long on a REVERSAL out of
oversold (RSI crosses back ABOVE the oversold line) and closes/shorts on a
reversal out of overbought (RSI crosses back BELOW the overbought line) -
not "RSI is currently below 30, buy". Buying the instant RSI dips under 30
means buying into a still-falling move; waiting for the cross back up is
the standard, less naive version of this signal, and mirrors how
SmaCrossoverStrategy only fires on a crossover, not on "fast is currently
above slow".

RSI is maintained incrementally (O(1) per candle) via
indicators.RunningSmoothedAverage in its Wilder-alpha (1/period) mode -
NOT recomputed from the full history on every call. Cross-checked against
indicators.rsi()'s independent batch implementation in
tests/test_indicators.py.
"""

from strategy.base import Signal, Strategy, StrategyDecision
from strategy.indicators import RunningSmoothedAverage


class RsiStrategy(Strategy):
    def __init__(
        self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0, go_short: bool = True,
    ):
        assert 0 < oversold < overbought < 100, "require 0 < oversold < overbought < 100"
        assert period > 0, "period must be positive"
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.go_short = go_short

        self._avg_gain = RunningSmoothedAverage(period, alpha=1 / period)
        self._avg_loss = RunningSmoothedAverage(period, alpha=1 / period)
        self._prev_close: float | None = None
        self._prev_rsi: float | None = None
        self.last_rsi: float | None = None  # public - last computed RSI, for inspection/tests

    def reset(self) -> None:
        self._avg_gain.reset()
        self._avg_loss.reset()
        self._prev_close = None
        self._prev_rsi = None
        self.last_rsi = None

    def on_candle(self, candle, history) -> StrategyDecision:
        close = candle.mid_close
        if self._prev_close is None:
            self._prev_close = close
            return StrategyDecision(Signal.HOLD, "warming up (first candle)")

        change = close - self._prev_close
        self._prev_close = close
        gain = self._avg_gain.update(max(change, 0.0))
        loss = self._avg_loss.update(max(-change, 0.0))
        if gain is None or loss is None:
            return StrategyDecision(Signal.HOLD, "warming up RSI averages")

        rsi_value = 100.0 if loss == 0 else 100 - (100 / (1 + gain / loss))
        self.last_rsi = rsi_value
        decision = self._decide(rsi_value)
        self._prev_rsi = rsi_value
        return decision

    def _decide(self, rsi_value: float) -> StrategyDecision:
        if self._prev_rsi is None:
            return StrategyDecision(Signal.HOLD, f"RSI={rsi_value:.1f}, no prior value yet")
        if self._prev_rsi <= self.oversold < rsi_value:
            return StrategyDecision(Signal.BUY, f"RSI crossed back above oversold ({self._prev_rsi:.1f} -> {rsi_value:.1f})")
        if self._prev_rsi >= self.overbought > rsi_value:
            signal = Signal.SELL if self.go_short else Signal.CLOSE
            return StrategyDecision(signal, f"RSI crossed back below overbought ({self._prev_rsi:.1f} -> {rsi_value:.1f})")
        return StrategyDecision(Signal.HOLD, f"RSI={rsi_value:.1f}, no crossover")
