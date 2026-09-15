"""
tests/test_store.py

Verifies FxStore round-trips data correctly and upsert updates, not duplicates.

Run against local SQLite (default): python -m tests.test_store
Run against real cloud Postgres: DATABASE_URL="postgresql://..." python -m tests.test_store
"""

import os
import tempfile

from data.store import Candle, FxStore


def make_candle(ts: str, close: float) -> Candle:
    return Candle(
        instrument="EUR_USD", granularity="H1", timestamp=ts,
        bid_open=close, bid_high=close, bid_low=close, bid_close=close,
        ask_open=close + 0.0002, ask_high=close + 0.0002,
        ask_low=close + 0.0002, ask_close=close + 0.0002,
        volume=100,
    )


def test_round_trip_and_upsert():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        print(f"Testing against DATABASE_URL ({database_url.split('://')[0]} backend)")
        store = FxStore(database_url=database_url)
    else:
        tmp_path = tempfile.mktemp(suffix=".db")
        print(f"No DATABASE_URL set - testing against local SQLite file: {tmp_path}")
        store = FxStore(database_url=f"sqlite:///{tmp_path}")

    candles = [make_candle(f"2024-01-01T{h:02d}:00:00Z", 1.1000 + h * 0.0001) for h in range(5)]
    n = store.upsert_candles(candles)
    assert n == 5, f"expected 5 rows written, got {n}"

    loaded = store.get_candles("EUR_USD", "H1")
    assert len(loaded) == 5, f"expected 5 candles back, got {len(loaded)}"
    assert loaded[0].timestamp == "2024-01-01T00:00:00Z"
    assert abs(loaded[0].bid_close - 1.1000) < 1e-9

    updated_candle = make_candle("2024-01-01T00:00:00Z", 1.2345)
    store.upsert_candles([updated_candle])
    loaded_again = store.get_candles("EUR_USD", "H1")
    assert len(loaded_again) == 5, f"upsert duplicated a row instead of updating: got {len(loaded_again)} rows"
    assert abs(loaded_again[0].bid_close - 1.2345) < 1e-9, "upsert did not update the existing row's price"

    filtered = store.get_candles("EUR_USD", "H1", start="2024-01-01T02:00:00Z")
    assert len(filtered) == 3, f"expected 3 candles from 02:00 onward, got {len(filtered)}"

    coverage = store.coverage("EUR_USD", "H1")
    assert coverage == ("2024-01-01T00:00:00Z", "2024-01-01T04:00:00Z"), f"unexpected coverage: {coverage}"

    print("All store assertions passed.")


def test_sslmode_warning_for_postgres_url_without_it():
    """Doesn't need a real Postgres connection - checks the warning fires on
    the URL string before any connection is attempted."""
    import warnings

    from data.store import FxStore

    fake_url = "postgresql://user:pass@nonexistent-host-for-test:5432/postgres"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            FxStore(database_url=fake_url)
        except Exception as exc:
            print(f"connection failed as expected (host doesn't exist): {exc}")

    sslmode_warnings = [w for w in caught if "sslmode" in str(w.message)]
    assert sslmode_warnings, "expected a warning about missing sslmode on a postgres:// URL"
    print("sslmode warning assertion passed.")


if __name__ == "__main__":
    test_round_trip_and_upsert()
    test_sslmode_warning_for_postgres_url_without_it()
