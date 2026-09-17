"""
strategy/macd_strategy.py

Classic MACD line/signal-line crossover: trend-following like
SmaCrossoverStrategy, but using exponential (not simple) moving averages
of price, plus a signal line that's itself an EMA of the MACD line. Goes
long when the MACD line crosses above its signal line, short (or flat) on
the reverse cross - same go_short toggle as SmaCrossoverStrategy.

RSI CONFIRMATION FILTER (require_rsi_confirmation=True, the default): a
MACD bullish cross is skipped if RSI is already overbought, and a bearish
cross is skipped if RSI is already oversold - avoids chasing a move that's
already extended by the other oscillator's own measure. This is the
textbook "RSI + MACD confirmation" combination, both oscillators in one
strategy. Set require_rsi_confirmation=False for pure, unfiltered MACD
crossover - useful for testing through the validation hierarchy whether
the RSI filter actually earns its keep on real data, rather than assuming
it does; that flag is exactly the kind of thing a param_grid should vary.

Both MACD's EMAs and the RSI filter's Wilder averages are maintained
incrementally (O(1) per candle) via indicators.RunningSmoothedAverage -
NOT recomputed from the full history on every call. Cross-checked against
indicators.macd()/rsi()'s independent batch implementations in
tests/test_indicators.py.
"""

from strategy.base import Signal, Strategy, StrategyDecision
from strategy.indicators import RunningSmoothedAverage


class MacdStrategy(Strategy):
    def __init__(
        self, fast: int = 12, slow: int = 26, signal: int = 9, go_short: bool = True,
        require_rsi_confirmation: bool = True, rsi_period: int = 14,
        rsi_oversold: float = 30.0, rsi_overbought: float = 70.0,
    ):
        assert fast < slow, "fast period must be smaller than slow period"
        assert signal > 0, "signal period must be positive"
        assert 0 < rsi_oversold < rsi_overbought < 100, "require 0 < rsi_oversold < rsi_overbought < 100"
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.go_short = go_short
        self.require_rsi_confirmation = require_rsi_confirmation
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

        self._fast_ema = RunningSmoothedAverage(fast)
        self._slow_ema = RunningSmoothedAverage(slow)
        self._signal_ema = RunningSmoothedAverage(signal)
        self._prev_macd_above_signal: bool | None = None
        self.last_macd: float | None = None      # public - for inspection/tests
        self.last_signal: float | None = None

        self._avg_gain = RunningSmoothedAverage(rsi_period, alpha=1 / rsi_period)
        self._avg_loss = RunningSmoothedAverage(rsi_period, alpha=1 / rsi_period)
        self._prev_close: float | None = None
        self.last_rsi: float | None = None

    def reset(self) -> None:
        self._fast_ema.reset()
        self._slow_ema.reset()
        self._signal_ema.reset()
        self._prev_macd_above_signal = None
        self.last_macd = None
        self.last_signal = None
        self._avg_gain.reset()
        self._avg_loss.reset()
        self._prev_close = None
        self.last_rsi = None

    def on_candle(self, candle, history) -> StrategyDecision:
        close = candle.mid_close
        self._update_rsi(close)

        fast_val = self._fast_ema.update(close)
        slow_val = self._slow_ema.update(close)
        if fast_val is None or slow_val is None:
            return StrategyDecision(Signal.HOLD, "warming up MACD EMAs")

        macd_value = fast_val - slow_val
        self.last_macd = macd_value  # set as soon as macd_value itself is defined, even if signal isn't yet
        signal_value = self._signal_ema.update(macd_value)
        if signal_value is None:
            return StrategyDecision(Signal.HOLD, "warming up MACD signal line")

        self.last_signal = signal_value
        return self._decide(macd_value, signal_value)

    def _update_rsi(self, close: float) -> None:
        if self._prev_close is None:
            self._prev_close = close
            return
        change = close - self._prev_close
        self._prev_close = close
        gain = self._avg_gain.update(max(change, 0.0))
        loss = self._avg_loss.update(max(-change, 0.0))
        if gain is None or loss is None:
            return
        self.last_rsi = 100.0 if loss == 0 else 100 - (100 / (1 + gain / loss))

    def _decide(self, macd_value: float, signal_value: float) -> StrategyDecision:
        macd_above_signal = macd_value > signal_value
        prev = self._prev_macd_above_signal
        self._prev_macd_above_signal = macd_above_signal

        if prev is None:
            return StrategyDecision(Signal.HOLD, "no prior MACD/signal relationship yet")
        if macd_above_signal and not prev:
            return self._bullish_cross_decision()
        if (not macd_above_signal) and prev:
            return self._bearish_cross_decision()
        return StrategyDecision(Signal.HOLD, "no MACD crossover")

    def _bullish_cross_decision(self) -> StrategyDecision:
        if self.require_rsi_confirmation and self.last_rsi is not None and self.last_rsi >= self.rsi_overbought:
            return StrategyDecision(Signal.HOLD, f"MACD bullish cross but RSI={self.last_rsi:.1f} already overbought - skipped")
        return StrategyDecision(Signal.BUY, "MACD crossed above signal")

    def _bearish_cross_decision(self) -> StrategyDecision:
        if self.require_rsi_confirmation and self.last_rsi is not None and self.last_rsi <= self.rsi_oversold:
            return StrategyDecision(Signal.HOLD, f"MACD bearish cross but RSI={self.last_rsi:.1f} already oversold - skipped")
        out_signal = Signal.SELL if self.go_short else Signal.CLOSE
        return StrategyDecision(out_signal, "MACD crossed below signal")
