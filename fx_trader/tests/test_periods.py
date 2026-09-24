"""
tests/test_periods.py

Hand-verified tests for backtest/periods.py - the shared period-chunking
abstraction behind both out-of-time validation and period-comparison
visualization. Run: python -m tests.test_periods
"""

import tempfile
from datetime import datetime, timezone

from backtest.periods import calendar_year_periods, fixed_month_periods
from data.store import Candle, FxStore


def _make_store_with_coverage(instrument: str, granularity: str, start: datetime, end: datetime, step_hours: int = 24) -> FxStore:
    """Builds a throwaway FxStore with candles spanning exactly
    [start, end], spaced step_hours apart - just enough for
    FxStore.coverage() to report the right range; prices are irrelevant
    for these tests."""
    tmp_path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{tmp_path}")
    candles = []
    t = start
    while t <= end:
        candles.append(Candle(
            instrument=instrument, granularity=granularity, timestamp=t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            bid_open=100.0, bid_high=100.0, bid_low=100.0, bid_close=100.0,
            ask_open=100.0, ask_high=100.0, ask_low=100.0, ask_close=100.0, volume=1,
        ))
        t = t.replace(hour=0) if step_hours >= 24 else t
        from datetime import timedelta
        t = t + timedelta(hours=step_hours)
    store.upsert_candles(candles)
    return store


def test_calendar_year_periods_spans_full_years_and_partial_edges():
    start = datetime(2022, 7, 15, tzinfo=timezone.utc)   # partial first year
    end = datetime(2025, 3, 10, tzinfo=timezone.utc)      # partial last year
    store = _make_store_with_coverage("TEST", "D1", start, end)

    periods = calendar_year_periods(store, "TEST", "D1")
    labels = [p.label for p in periods]
    assert labels == ["2022", "2023", "2024", "2025"], f"expected 4 calendar years, got {labels}"

    assert periods[0].start == start, "partial first year must start at actual coverage start, not Jan 1"
    assert periods[0].end.year == 2022 and periods[0].end.month == 12
    assert periods[-1].start.year == 2025 and periods[-1].start.month == 1
    assert periods[-1].end <= end, "partial last year must not extend past actual coverage end"
    print(f"periods: {[(p.label, p.start.date(), p.end.date()) for p in periods]}")
    print("Calendar-year full-span-with-partial-edges assertion passed.")


def test_calendar_year_periods_single_year_no_data_edge_case():
    tmp_path = tempfile.mktemp(suffix=".db")
    store = FxStore(database_url=f"sqlite:///{tmp_path}")  # schema created, but no candles inserted
    periods = calendar_year_periods(store, "NO_SUCH_INSTRUMENT", "D1")
    assert periods == [], "an instrument with zero coverage must return an empty period list, not crash"
    print("No-coverage edge case assertion passed.")


def test_fixed_month_periods_matches_calendar_year_when_window_is_12():
    """window_months=12, no exclusion - should produce the same boundaries
    as calendar_year_periods for a range that happens to align to Jan 1,
    proving the two generators are consistent with each other where their
    behavior should overlap."""
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 12, 31, tzinfo=timezone.utc)
    store = _make_store_with_coverage("TEST", "D1", start, end)

    yearly = calendar_year_periods(store, "TEST", "D1")
    fixed = fixed_month_periods(store, "TEST", "D1", window_months=12)

    assert len(yearly) == len(fixed) == 3
    for y, f in zip(yearly, fixed):
        assert y.start == f.start and y.end == f.end, f"mismatch: {y} vs {f}"
    print("fixed_month_periods(12) matches calendar_year_periods on an aligned range.")
    print("Cross-generator consistency assertion passed.")


def test_fixed_month_periods_excludes_training_window():
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 12, 31, tzinfo=timezone.utc)
    store = _make_store_with_coverage("TEST", "D1", start, end)

    exclude_start = datetime(2025, 7, 1, tzinfo=timezone.utc)
    exclude_end = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    periods = fixed_month_periods(store, "TEST", "D1", window_months=6, exclude_start=exclude_start, exclude_end=exclude_end)

    for p in periods:
        overlaps = p.start < exclude_end and p.end > exclude_start
        assert not overlaps, f"period {p.label} [{p.start}..{p.end}] overlaps the excluded training window"
    assert len(periods) >= 6, f"expected at least 6 six-month blocks across 2022-2025 minus H2 2025, got {len(periods)}"
    print(f"{len(periods)} periods generated, none overlapping the excluded window.")
    print("Training-window exclusion assertion passed.")


if __name__ == "__main__":
    test_calendar_year_periods_spans_full_years_and_partial_edges()
    test_calendar_year_periods_single_year_no_data_edge_case()
    test_fixed_month_periods_matches_calendar_year_when_window_is_12()
    test_fixed_month_periods_excludes_training_window()
    print("\nAll periods tests passed.")
