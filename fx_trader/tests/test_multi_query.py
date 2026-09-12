"""
tests/test_multi_query.py

Verifies FxStore.get_candles_multi() - the multi-instrument query helper
added to support the future cross-pair/cartesian comparison work.

Run with: python -m tests.test_multi_query
"""

import tempfile

from data.store import Candle, FxStore


def make_candle(instrument: str, ts: str, close: float) -> Candle:
    return Candle(
        instrument=instrument, granularity="H1", timestamp=ts,
        bid_open=close, bid_high=close, bid_low=close, bid_close=close,
        ask_open=close, ask_high=close, ask_low=close, ask_close=close,
        volume=100,
    )


def test_get_candles_multi_groups_correctly_and_matches_single_instrument_calls():
    tmp_path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{tmp_path}")

    gbpusd = [make_candle("GBP_USD", f"2024-01-01T{h:02d}:00:00Z", 1.27 + h * 0.001) for h in range(5)]
    eurusd = [make_candle("EUR_USD", f"2024-01-01T{h:02d}:00:00Z", 1.10 + h * 0.001) for h in range(3)]
    store.upsert_candles(gbpusd)
    store.upsert_candles(eurusd)

    result = store.get_candles_multi(["GBP_USD", "EUR_USD"], "H1")

    assert set(result.keys()) == {"GBP_USD", "EUR_USD"}
    assert len(result["GBP_USD"]) == 5
    assert len(result["EUR_USD"]) == 3
    assert result["GBP_USD"][0].timestamp == "2024-01-01T00:00:00Z"

    single_gbpusd = store.get_candles("GBP_USD", "H1")
    assert [c.timestamp for c in result["GBP_USD"]] == [c.timestamp for c in single_gbpusd]
    assert [c.bid_close for c in result["GBP_USD"]] == [c.bid_close for c in single_gbpusd]

    print(f"GBP_USD: {len(result['GBP_USD'])} candles, EUR_USD: {len(result['EUR_USD'])} candles")
    print("get_candles_multi matches get_candles() exactly - assertion passed.")


def test_get_candles_multi_handles_an_instrument_with_no_data():
    tmp_path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{tmp_path}")
    store.upsert_candles([make_candle("GBP_USD", "2024-01-01T00:00:00Z", 1.27)])

    result = store.get_candles_multi(["GBP_USD", "NO_SUCH_INSTRUMENT"], "H1")

    assert len(result["GBP_USD"]) == 1
    assert result["NO_SUCH_INSTRUMENT"] == [], "instrument with no rows should be an empty list, not missing"
    print("Empty-instrument handling assertion passed.")


def test_get_candles_multi_empty_instrument_list_returns_empty_dict():
    tmp_path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{tmp_path}")
    assert store.get_candles_multi([], "H1") == {}
    print("Empty-instrument-list edge case assertion passed.")


if __name__ == "__main__":
    test_get_candles_multi_groups_correctly_and_matches_single_instrument_calls()
    test_get_candles_multi_handles_an_instrument_with_no_data()
    test_get_candles_multi_empty_instrument_list_returns_empty_dict()
    print("\nAll multi-query tests passed.")
