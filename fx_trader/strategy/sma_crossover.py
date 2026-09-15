"""
strategy/sma_crossover.py

A simple moving-average crossover strategy: go long when the fast SMA
crosses above the slow SMA, go short (or flat) when it crosses below.

This is deliberately simple. It is NOT presented as a profitable strategy -
FX majors are highly efficient and simple SMA crossovers on raw price rarely
have a durable edge after realistic costs. Its purpose here is to exercise
the full pipeline (data -> strategy -> backtest -> costs -> metrics) with
something easy to reason about, so you can verify the plumbing works before
you invest time researching an actual edge.
"""

from collections import deque

from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class SmaCrossoverStrategy(Strategy):
    def __init__(self, fast_period: int = 10, slow_period: int = 30, go_short: bool = True):
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.go_short = go_short  # if False, crosses below just flatten instead of shorting
        self._closes: deque = deque(maxlen=slow_period)
        self._prev_fast_above_slow: bool | None = None

    def reset(self) -> None:
        self._closes = deque(maxlen=self.slow_period)
        self._prev_fast_above_slow = None

    def on_candle(self, candle: Candle, history: list[Candle]) -> StrategyDecision:
        self._closes.append(candle.mid_close)

        if len(self._closes) < self.slow_period:
            return StrategyDecision(Signal.HOLD, "warming up")

        closes = list(self._closes)
        fast_sma = sum(closes[-self.fast_period:]) / self.fast_period
        slow_sma = sum(closes) / self.slow_period
        fast_above_slow = fast_sma > slow_sma

        decision = StrategyDecision(Signal.HOLD, "no crossover")
        if self._prev_fast_above_slow is not None:
            if fast_above_slow and not self._prev_fast_above_slow:
                decision = StrategyDecision(Signal.BUY, f"fast SMA crossed above slow ({fast_sma:.5f} > {slow_sma:.5f})")
            elif not fast_above_slow and self._prev_fast_above_slow:
                signal = Signal.SELL if self.go_short else Signal.CLOSE
                decision = StrategyDecision(signal, f"fast SMA crossed below slow ({fast_sma:.5f} < {slow_sma:.5f})")

        self._prev_fast_above_slow = fast_above_slow
        return decision
