"""
strategy/bollinger_strategy.py

Bollinger Band mean-reversion: goes long on a REVERSAL back above the
lower band after price was below it (oversold extreme fading), and closes/
shorts on a reversal back below the upper band after price was above it -
not "price is currently outside the band, trade now". Same philosophy as
RsiStrategy: buying the instant price pokes below the lower band means
buying into a still-falling move; waiting for the close back inside the
band is the less naive version.

A REAL PROPERTY OF BOLLINGER BANDS WORTH KNOWING BEFORE PICKING `period`:
the current close is included in its own window (the standard definition -
see indicators.bollinger_bands()), so a single sharp move partly widens
the band around itself, rather than always poking cleanly outside it. With
a short period this can make "outside the band" a rarer event than
intuition suggests, especially for a one-candle spike - the band and the
spike inflate together. Longer periods (this project defaults to 20, the
standard) dilute that effect since one outlier is a smaller share of the
window. Worth being aware of rather than assuming the band always reacts
the way a fixed threshold (like RSI's 30/70) would.

Middle/upper/lower bands are recomputed from a bounded window each candle
(O(period) per candle via a deque, same approach SmaCrossoverStrategy
already uses) - NOT an O(1) recursive update like the EMA-based
RsiStrategy/MacdStrategy, because a simple moving average + population std
dev over a fixed window is a genuinely different (non-exponentially-
decaying) computation; recomputing over a bounded window of the default
size (20) is already cheap. Cross-checked against
indicators.bollinger_bands()'s independent batch implementation (itself
checked against Python's own `statistics` module) in
tests/test_bollinger_strategy.py.
"""

from collections import deque

from strategy.base import Signal, Strategy, StrategyDecision


class BollingerBandsStrategy(Strategy):
    def __init__(self, period: int = 20, num_std: float = 2.0, go_short: bool = True):
        assert period > 1, "period must be greater than 1"
        assert num_std > 0, "num_std must be positive"
        self.period = period
        self.num_std = num_std
        self.go_short = go_short

        self._closes: deque = deque(maxlen=period)
        self._prev_zone: str | None = None
        self.last_middle: float | None = None  # public - for inspection/tests
        self.last_upper: float | None = None
        self.last_lower: float | None = None

    def reset(self) -> None:
        self._closes = deque(maxlen=self.period)
        self._prev_zone = None
        self.last_middle = None
        self.last_upper = None
        self.last_lower = None

    def on_candle(self, candle, history) -> StrategyDecision:
        close = candle.mid_close
        self._closes.append(close)
        if len(self._closes) < self.period:
            return StrategyDecision(Signal.HOLD, "warming up")

        mean = sum(self._closes) / self.period
        variance = sum((x - mean) ** 2 for x in self._closes) / self.period
        std = variance ** 0.5
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        self.last_middle, self.last_upper, self.last_lower = mean, upper, lower

        zone = self._zone(close, upper, lower)
        decision = self._decide(zone, close, upper, lower)
        self._prev_zone = zone
        return decision

    @staticmethod
    def _zone(close: float, upper: float, lower: float) -> str:
        if close > upper:
            return "above_upper"
        if close < lower:
            return "below_lower"
        return "inside"

    def _decide(self, zone: str, close: float, upper: float, lower: float) -> StrategyDecision:
        if self._prev_zone is None:
            return StrategyDecision(Signal.HOLD, f"price={close:.5f}, bands=[{lower:.5f}, {upper:.5f}], no prior zone yet")
        if self._prev_zone == "below_lower" and zone != "below_lower":
            return StrategyDecision(Signal.BUY, f"price reverted back above lower band ({close:.5f} > {lower:.5f})")
        if self._prev_zone == "above_upper" and zone != "above_upper":
            signal = Signal.SELL if self.go_short else Signal.CLOSE
            return StrategyDecision(signal, f"price reverted back below upper band ({close:.5f} < {upper:.5f})")
        return StrategyDecision(Signal.HOLD, "no band reversal")
