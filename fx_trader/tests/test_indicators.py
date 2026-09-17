"""
tests/test_indicators.py

1. ema() and rsi() checked with hand-computed EXACT values on tiny series
   (small enough to verify by hand, including exact fractions for RSI).
2. RunningSmoothedAverage (the streaming primitive) fed sequentially and
   checked against ema()/rsi()'s batch output AT EVERY STEP, on a longer
   synthetic series - proves the incremental state machine matches a
   from-scratch recomputation, not just at one cherry-picked point.
3. macd() checked against an INDEPENDENTLY written reference
   implementation in this file (differently structured: explicit
   dict-keyed state loop, not list comprehensions) rather than hand-typed
   decimals - same philosophy as test_metrics_extended.py's
   distribution-shape checks.

Run: python -m tests.test_indicators
"""

from strategy.indicators import RunningSmoothedAverage, bollinger_bands, ema, macd, rsi
from tests.synthetic_data import generate_synthetic_candles


def test_ema_exact_small_series():
    # period=3, alpha=0.5. Seed = avg(10,12,14) = 12.0.
    values = [10.0, 12.0, 14.0, 12.0, 16.0]
    result = ema(values, period=3)
    assert result[0] is None and result[1] is None
    assert abs(result[2] - 12.0) < 1e-9
    assert abs(result[3] - 12.0) < 1e-9, "0.5*12 + 0.5*12 = 12.0"
    assert abs(result[4] - 14.0) < 1e-9, "0.5*16 + 0.5*12 = 14.0"
    print(f"ema={result} (hand-verified)")
    print("EMA exact-value assertions passed.")


def test_rsi_exact_small_series_with_fraction():
    # period=3. changes=[2,-1,2,-4,5] -> gains=[2,0,2,0,5], losses=[0,1,0,4,0]
    # seed avg_gain=4/3, avg_loss=1/3 -> RSI=80.0 exactly (rs=4.0)
    # next: avg_gain=8/9, avg_loss=14/9 -> rs=4/7 -> RSI=400/11
    closes = [10.0, 12.0, 11.0, 13.0, 9.0, 14.0]
    result = rsi(closes, period=3)
    assert result[0] is None and result[1] is None and result[2] is None
    assert abs(result[3] - 80.0) < 1e-9, f"expected 80.0, got {result[3]}"
    assert abs(result[4] - 400 / 11) < 1e-9, f"expected {400/11}, got {result[4]}"
    print(f"rsi={result} (hand-verified, second value = 400/11 = {400/11:.4f})")
    print("RSI exact-value assertions passed.")


def test_rsi_pins_at_100_when_no_losses():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]  # strictly rising
    result = rsi(closes, period=3)
    assert result[3] == 100.0 and result[4] == 100.0, "zero avg_loss must pin RSI at exactly 100, not divide by zero"
    print("RSI zero-loss edge case assertion passed.")


def test_running_smoothed_average_matches_ema_batch_at_every_step():
    candles = generate_synthetic_candles(n_candles=200)
    closes = [c.mid_close for c in candles]
    period = 12
    expected = ema(closes, period)

    running = RunningSmoothedAverage(period)
    streamed = [running.update(c) for c in closes]

    mismatches = [
        i for i in range(len(closes))
        if (expected[i] is None) != (streamed[i] is None)
        or (expected[i] is not None and abs(expected[i] - streamed[i]) > 1e-9)
    ]
    assert not mismatches, f"streaming diverged from batch ema() at indices {mismatches[:5]}"
    print(f"RunningSmoothedAverage matched batch ema() at all {len(closes)} steps.")
    print("Streaming-vs-batch EMA assertion passed.")


def test_running_smoothed_average_wilder_mode_matches_rsi_batch_at_every_step():
    candles = generate_synthetic_candles(n_candles=200, seed=99)
    closes = [c.mid_close for c in candles]
    period = 14
    expected = rsi(closes, period)

    avg_gain = RunningSmoothedAverage(period, alpha=1 / period)
    avg_loss = RunningSmoothedAverage(period, alpha=1 / period)
    streamed = [None]  # index 0 has no prior close, matches rsi()'s alignment
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        g = avg_gain.update(max(change, 0.0))
        l = avg_loss.update(max(-change, 0.0))
        if g is None or l is None:
            streamed.append(None)
        else:
            streamed.append(100.0 if l == 0 else 100 - (100 / (1 + g / l)))

    mismatches = [
        i for i in range(len(closes))
        if (expected[i] is None) != (streamed[i] is None)
        or (expected[i] is not None and abs(expected[i] - streamed[i]) > 1e-9)
    ]
    assert not mismatches, f"streaming Wilder RSI diverged from batch rsi() at indices {mismatches[:5]}"
    print(f"RunningSmoothedAverage (Wilder mode) matched batch rsi() at all {len(closes)} steps.")
    print("Streaming-vs-batch RSI assertion passed.")


def _independent_ema(values, period):
    """Deliberately restructured (dict-keyed running state, not a list
    comprehension loop) vs. indicators.ema() - see module docstring."""
    n = len(values)
    if n < period:
        return [None] * n
    alpha = 2.0 / (period + 1)
    out = {}
    window_sum = sum(values[0:period])
    out[period - 1] = window_sum / period
    state = out[period - 1]
    for i in range(period, n):
        state = values[i] * alpha + state * (1 - alpha)
        out[i] = state
    return [out.get(i) for i in range(n)]


def _independent_macd(closes, fast, slow, signal):
    fast_e = _independent_ema(closes, fast)
    slow_e = _independent_ema(closes, slow)
    macd_line = {}
    for i in range(len(closes)):
        if fast_e[i] is not None and slow_e[i] is not None:
            macd_line[i] = fast_e[i] - slow_e[i]
    if not macd_line:
        return [None] * len(closes), [None] * len(closes)
    ordered_indices = sorted(macd_line.keys())
    ordered_values = [macd_line[i] for i in ordered_indices]
    signal_e = _independent_ema(ordered_values, signal)
    signal_line = {ordered_indices[j]: signal_e[j] for j in range(len(ordered_indices)) if signal_e[j] is not None}
    macd_out = [macd_line.get(i) for i in range(len(closes))]
    signal_out = [signal_line.get(i) for i in range(len(closes))]
    return macd_out, signal_out


def test_macd_against_independent_reference():
    candles = generate_synthetic_candles(n_candles=150, seed=13)
    closes = [c.mid_close for c in candles]
    macd_line, signal_line, histogram = macd(closes, fast=5, slow=13, signal=4)
    expected_macd, expected_signal = _independent_macd(closes, fast=5, slow=13, signal=4)

    for i in range(len(closes)):
        assert (macd_line[i] is None) == (expected_macd[i] is None), f"macd None-alignment mismatch at {i}"
        if macd_line[i] is not None:
            assert abs(macd_line[i] - expected_macd[i]) < 1e-9, f"macd mismatch at {i}: {macd_line[i]} vs {expected_macd[i]}"
        assert (signal_line[i] is None) == (expected_signal[i] is None), f"signal None-alignment mismatch at {i}"
        if signal_line[i] is not None:
            assert abs(signal_line[i] - expected_signal[i]) < 1e-9, f"signal mismatch at {i}: {signal_line[i]} vs {expected_signal[i]}"
        if macd_line[i] is not None and signal_line[i] is not None:
            assert abs(histogram[i] - (macd_line[i] - signal_line[i])) < 1e-12

    print(f"macd()/signal() matched independent reference at all {len(closes)} points.")
    print("MACD independent-reference assertions passed.")


def test_bollinger_bands_against_independent_reference():
    """Independent reference here means Python's own `statistics` module
    (statistics.mean/statistics.pstdev) - genuinely different code (a
    library implementation, not this project's sum-of-squares loop) - not
    just a differently-shaped version of the same loop."""
    import statistics

    candles = generate_synthetic_candles(n_candles=200, seed=41)
    closes = [c.mid_close for c in candles]
    period, num_std = 20, 2.0
    middle, upper, lower = bollinger_bands(closes, period=period, num_std=num_std)

    for i in range(len(closes)):
        if i < period - 1:
            assert middle[i] is None and upper[i] is None and lower[i] is None, f"expected None before warmup at {i}"
            continue
        window = closes[i - period + 1: i + 1]
        expected_mean = statistics.mean(window)
        expected_std = statistics.pstdev(window)
        assert abs(middle[i] - expected_mean) < 1e-9, f"middle mismatch at {i}"
        assert abs(upper[i] - (expected_mean + num_std * expected_std)) < 1e-9, f"upper mismatch at {i}"
        assert abs(lower[i] - (expected_mean - num_std * expected_std)) < 1e-9, f"lower mismatch at {i}"

    print(f"bollinger_bands() matched statistics.mean/pstdev at all {len(closes) - period + 1} defined points.")
    print("Bollinger Bands independent-reference assertions passed.")


if __name__ == "__main__":
    test_ema_exact_small_series()
    test_rsi_exact_small_series_with_fraction()
    test_rsi_pins_at_100_when_no_losses()
    test_running_smoothed_average_matches_ema_batch_at_every_step()
    test_running_smoothed_average_wilder_mode_matches_rsi_batch_at_every_step()
    test_macd_against_independent_reference()
    test_bollinger_bands_against_independent_reference()
    print("\nAll indicator tests passed.")
