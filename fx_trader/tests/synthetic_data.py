"""
tests/synthetic_data.py

Generates fake but plausible FX/crypto candle data (random walk + mild
trend) so the pipeline can be exercised without a real API. Useful for
mechanics/CI-style tests - NOT for evaluating whether a strategy is good
(that needs real historical data, e.g. the sandbox dataset).
"""

import random
from datetime import datetime, timedelta, timezone

from data.store import Candle


def generate_synthetic_candles(
    instrument: str = "EUR_USD",
    granularity: str = "H1",
    n_candles: int = 2000,
    start_price: float = 1.1000,
    seed: int = 42,
    typical_spread_pips: float = 1.2,
    pip_size: float = 0.0001,
) -> list[Candle]:
    rng = random.Random(seed)
    candles = []
    price = start_price
    t = datetime(2023, 1, 2, tzinfo=timezone.utc)
    step = timedelta(hours=1) if granularity == "H1" else timedelta(days=1)

    for _ in range(n_candles):
        drift = rng.gauss(0, 0.00003)
        o = price
        c = price + rng.gauss(drift, 0.0006)
        hi = max(o, c) + abs(rng.gauss(0, 0.0003))
        lo = min(o, c) - abs(rng.gauss(0, 0.0003))
        price = c

        spread = typical_spread_pips * pip_size * rng.uniform(0.7, 1.5)
        half = spread / 2

        candles.append(
            Candle(
                instrument=instrument,
                granularity=granularity,
                timestamp=t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                bid_open=o - half, bid_high=hi - half, bid_low=lo - half, bid_close=c - half,
                ask_open=o + half, ask_high=hi + half, ask_low=lo + half, ask_close=c + half,
                volume=rng.randint(50, 500),
            )
        )
        t += step

    return candles


def generate_synthetic_crypto_candles(
    instrument: str = "BTC/USDT",
    granularity: str = "1h",
    n_candles: int = 2000,
    start_price: float = 60_000.0,
    seed: int = 7,
) -> list[Candle]:
    """Like generate_synthetic_candles, but crypto-shaped: higher volatility,
    bid == ask (matching CcxtBroker's real historical data shape)."""
    rng = random.Random(seed)
    candles = []
    price = start_price
    t = datetime(2023, 1, 2, tzinfo=timezone.utc)
    step = timedelta(hours=1) if granularity == "1h" else timedelta(days=1)

    for _ in range(n_candles):
        drift = rng.gauss(0, 3.0)
        o = price
        c = price + rng.gauss(drift, 120.0)
        hi = max(o, c) + abs(rng.gauss(0, 60.0))
        lo = min(o, c) - abs(rng.gauss(0, 60.0))
        price = max(c, 1.0)

        candles.append(
            Candle(
                instrument=instrument,
                granularity=granularity,
                timestamp=t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                bid_open=o, bid_high=hi, bid_low=lo, bid_close=price,
                ask_open=o, ask_high=hi, ask_low=lo, ask_close=price,
                volume=rng.randint(1, 200),
            )
        )
        t += step

    return candles
