# version: 261003
"""
tests/test_time_policy.py

Method: time_policy functions checked against hand-written expectations;
the FX session rule is checked against the four weekend patterns observed in
the real sandbox (chat 14 audit: EDT, EST, spring change week, autumn change
week); the store-bound test first proves the bug MECHANISM with raw SQL, then
that FxStore no longer has it. Temp files only (hermetic).

Run: python3 -m tests.test_time_policy
"""

import sqlite3
import tempfile
from datetime import date, datetime, timezone

from data.store import Candle, FxStore
from time_policy import (
    epoch_to_stored_ts, format_ts, fx_weekly_session, local_view,
    normalize_bound, parse_ts, stored_ts_to_epoch,
)

UTC = timezone.utc


def _candle(ts: str, close: float = 1.0, instrument: str = "TEST") -> Candle:
    return Candle(
        instrument=instrument, granularity="H1", timestamp=ts,
        bid_open=close, bid_high=close, bid_low=close, bid_close=close,
        ask_open=close, ask_high=close, ask_low=close, ask_close=close, volume=1,
    )


def _raises(exc_type, call):
    try:
        call()
    except exc_type:
        return True
    return False


def test_format_parse_roundtrip_and_variants():
    dt = datetime(2026, 3, 31, 23, 0, 0, tzinfo=UTC)
    stored = "2026-03-31T23:00:00.000000000Z"
    assert format_ts(dt) == stored
    for variant in (stored, "2026-03-31T23:00:00+00:00", "2026-03-31T23:00:00Z", "2026-03-31T23:00:00.000000Z",
                    "2026-04-01T00:00:00+01:00"):
        assert parse_ts(variant) == dt, variant
        assert normalize_bound(variant) == stored, variant
    assert normalize_bound(dt) == stored and normalize_bound(stored) == stored, "must be idempotent"
    print("format/parse/normalize variants passed.")


def test_naive_and_bad_input_rejected():
    assert _raises(ValueError, lambda: normalize_bound(datetime(2026, 1, 1)))
    assert _raises(ValueError, lambda: normalize_bound("2026-01-01T00:00:00"))
    assert _raises(ValueError, lambda: format_ts(datetime(2026, 1, 1, 0, 0, 0, 5, tzinfo=UTC)))
    assert _raises(TypeError, lambda: normalize_bound(12345))
    assert _raises(ValueError, lambda: stored_ts_to_epoch("2024-01-01T00:00:00Z"))
    print("rejection assertions passed.")


def test_epoch_roundtrip():
    stored = "2024-02-29T13:00:00.000000000Z"
    epoch = stored_ts_to_epoch(stored)
    assert epoch == int(datetime(2024, 2, 29, 13, tzinfo=UTC).timestamp())
    assert epoch_to_stored_ts(epoch) == stored
    print("epoch round-trip passed.")


def test_fx_session_matches_observed_sandbox_patterns():
    """(last bar open, first bar open) in UTC for the four patterns seen in the
    real data: US summer, US winter, spring change week, autumn change week."""
    cases = [
        (date(2024, 7, 12), (20, date(2024, 7, 12)), (21, date(2024, 7, 14))),   # EDT
        (date(2024, 1, 12), (21, date(2024, 1, 12)), (22, date(2024, 1, 14))),   # EST
        (date(2024, 3, 8), (21, date(2024, 3, 8)), (21, date(2024, 3, 10))),     # EST close, EDT reopen
        (date(2024, 11, 1), (20, date(2024, 11, 1)), (22, date(2024, 11, 3))),   # EDT close, EST reopen
    ]
    for friday, (last_hour, last_day), (open_hour, open_day) in cases:
        last_bar, first_bar = fx_weekly_session(friday)
        assert last_bar == datetime(last_day.year, last_day.month, last_day.day, last_hour, tzinfo=UTC), (friday, last_bar)
        assert first_bar == datetime(open_day.year, open_day.month, open_day.day, open_hour, tzinfo=UTC), (friday, first_bar)
    assert _raises(ValueError, lambda: fx_weekly_session(date(2024, 7, 13)))
    print("FX session rule matches the four observed weekend patterns.")


def test_local_view_london_bst_and_gmt():
    assert local_view("2026-07-01T23:30:00.000000000Z", "Europe/London") == "2026-07-02T00:30+01:00"
    assert local_view("2026-12-31T23:30:00.000000000Z", "Europe/London") == "2026-12-31T23:30+00:00"
    assert local_view(0, "UTC") == "1970-01-01T00:00+00:00"
    assert _raises(ValueError, lambda: local_view("2026-01-01T00:00:00Z", "Not/AZone"))
    print("local_view BST/GMT assertions passed.")


def test_store_inclusive_end_bound_with_isoformat():
    """The chat-14 bug: raw SQL with an isoformat() end bound drops the
    candle stamped exactly at the bound; FxStore must not."""
    path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{path}")
    stamps = [f"2024-01-01T{h:02d}:00:00.000000000Z" for h in range(5)]
    store.upsert_candles([_candle(ts, 1.0 + i) for i, ts in enumerate(stamps)])
    end_dt = datetime(2024, 1, 1, 4, tzinfo=UTC)

    raw = sqlite3.connect(path)
    raw_count = raw.execute("SELECT COUNT(*) FROM candles WHERE timestamp <= ?", (end_dt.isoformat(),)).fetchone()[0]
    raw.close()
    assert raw_count == 4, f"mechanism check: raw isoformat bound should drop the boundary candle, got {raw_count}"

    assert len(store.get_candles("TEST", "H1", end=end_dt.isoformat())) == 5, "end bound must be inclusive"
    assert len(store.get_candles("TEST", "H1", end=end_dt)) == 5
    assert len(store.get_candles("TEST", "H1", start=datetime(2024, 1, 1, 2, tzinfo=UTC))) == 3
    assert len(store.get_candles("TEST", "H1", start="2024-01-01T02:00:00+00:00", end="2024-01-01T03:00:00Z")) == 2
    multi = store.get_candles_multi(["TEST"], "H1", end=end_dt.isoformat())
    assert len(multi["TEST"]) == 5, "get_candles_multi must normalise bounds too"
    assert _raises(ValueError, lambda: store.get_candles("TEST", "H1", end=datetime(2024, 1, 1, 4)))
    print("FxStore inclusive-end-bound assertions passed (raw SQL drops it: 4 vs 5).")


if __name__ == "__main__":
    test_format_parse_roundtrip_and_variants()
    test_naive_and_bad_input_rejected()
    test_epoch_roundtrip()
    test_fx_session_matches_observed_sandbox_patterns()
    test_local_view_london_bst_and_gmt()
    test_store_inclusive_end_bound_with_isoformat()
    print("\nAll time_policy tests passed.")
