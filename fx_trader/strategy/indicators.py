"""
strategy/indicators.py

Two things live here, deliberately kept separate and cross-checked against
each other in tests/test_indicators.py rather than one calling the other:

1. BATCH/REFERENCE functions - ema(), rsi(), macd(). Given a full closes
   list, return the full indicator series (aligned 1:1, None during
   warmup). Independently coded, not built from RunningSmoothedAverage
   below - if they were, a bug in RunningSmoothedAverage would silently
   pass its own cross-check. These exist to verify the streaming
   strategies (rsi_strategy.py, macd_strategy.py) match a from-scratch
   recomputation, and for any future reuse (charting, diagnostics) that
   wants a full series rather than one step at a time.

2. RunningSmoothedAverage - the O(1)-per-candle STREAMING primitive the
   strategies actually use in on_candle(). SMA-seeded, then updated one
   value at a time. This is what an EMA and Wilder's RSI smoothing both
   ARE, underneath - EMA is alpha=2/(period+1), Wilder smoothing (RSI's
   average gain/loss) is alpha=1/period. Recomputing a full EMA from
   scratch on every on_candle() call would be O(n) per call, O(n^2) over a
   backtest - wasteful and non-idiomatic for something whose whole point
   is incremental updates.
"""

from dataclasses import dataclass, field


def ema(values: list[float], period: int) -> list[float | None]:
    """SMA-seeded EMA: the first `period-1` points are None, point
    `period-1` (0-indexed) is the SMA of the first `period` values, every
    point after applies the standard recursion with alpha = 2/(period+1)."""
    assert period > 0, "period must be positive"
    if len(values) < period:
        return [None] * len(values)
    alpha = 2 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    prev = seed
    for v in values[period:]:
        prev = alpha * v + (1 - alpha) * prev
        result.append(prev)
    return result


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder-smoothed RSI (the original formulation - NOT a plain-SMA-of-
    gains variant). Needs `period` price CHANGES to seed the average gain/
    loss, i.e. `period + 1` closes - the first `period` entries are None."""
    assert period > 0, "period must be positive"
    n = len(closes)
    if n < period + 1:
        return [None] * n

    changes = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    result: list[float | None] = [None] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result.append(_rsi_from_averages(avg_gain, avg_loss))

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(_rsi_from_averages(avg_gain, avg_loss))
    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(macd_line, signal_line, histogram), each aligned 1:1 with `closes`.
    macd_line = EMA(fast) - EMA(slow). signal_line = EMA(macd_line, signal),
    SMA-seeded over the first `signal` DEFINED macd_line points (not the
    raw index) - the signal line can only start once the MACD line has."""
    assert fast < slow, "fast period must be smaller than slow period"
    assert signal > 0, "signal period must be positive"

    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    first_defined_idx = next((i for i, v in enumerate(macd_line) if v is not None), None)
    defined = [v for v in macd_line if v is not None]
    if first_defined_idx is None or len(defined) < signal:
        signal_line: list[float | None] = [None] * len(closes)
    else:
        signal_line = [None] * first_defined_idx + ema(defined, signal)

    histogram: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(middle_band, upper_band, lower_band), each aligned 1:1 with
    `closes`. middle = SMA(period); band width = num_std * POPULATION
    standard deviation over the same trailing window (divide by `period`,
    not `period - 1`) - the conventional Bollinger Band definition, not
    the sample-variance convention `metrics.py`'s Sharpe uses elsewhere in
    this project. The current close IS included in its own window (the
    standard definition), which is why a single sharp outlier widens the
    band around itself rather than always poking outside it - real, not a
    bug; see `bollinger_strategy.py`'s docstring for what that implies for
    signal timing."""
    assert period > 1, "period must be greater than 1"
    assert num_std > 0, "num_std must be positive"
    n = len(closes)
    middle: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        middle[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return middle, upper, lower


@dataclass
class RunningSmoothedAverage:
    """SMA-seeded exponential-style smoothing, one value at a time.
    update(x) feeds one new sample and returns the updated average (or
    None while still seeding). `value` reads the current average without
    feeding a new sample. Default alpha is EMA's 2/(period+1); pass
    alpha=1/period for Wilder's RSI smoothing instead - same primitive."""

    period: int
    alpha: float | None = None
    _seed_buffer: list = field(default_factory=list, repr=False)
    _value: float | None = field(default=None, repr=False)

    def __post_init__(self):
        assert self.period > 0, "period must be positive"
        if self.alpha is None:
            self.alpha = 2 / (self.period + 1)
        assert 0 < self.alpha <= 1, "alpha must be in (0, 1]"

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, x: float) -> float | None:
        if self._value is None:
            self._seed_buffer.append(x)
            if len(self._seed_buffer) < self.period:
                return None
            self._value = sum(self._seed_buffer) / self.period
            self._seed_buffer = []
            return self._value
        self._value = self.alpha * x + (1 - self.alpha) * self._value
        return self._value

    def reset(self) -> None:
        self._seed_buffer = []
        self._value = None
