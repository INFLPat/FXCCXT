"""
backtest/periods.py

Generic period-definition utilities for comparing a strategy's behavior
across time - CONFIDENCE_SIZING_DESIGN.md-adjacent, but not specific to
scoring: this is the shared abstraction both out-of-time validation
(run_out_of_time_validation.py) and period-comparison visualization
(visualize_period_comparison.py) build on, so the two don't duplicate
period-chunking logic - see SUBTASK_VISUALIZATION.md for the full design
and why this was factored out rather than left inline in one script.

Per your instruction: concentrate on CALENDAR-YEAR comparison for now
(`calendar_year_periods`), but keep the shape flexible for what's
explicitly wanted later:
- calendar/financial quarters
- custom user-dictated periods
- regime-labeled periods (trending vs. ranging, volatility regime, etc.)

Every period generator returns the SAME `Period` shape, so any downstream
consumer (out-of-time validation, visualization, a future scoring-engine
regime check) works identically regardless of which generator produced
the periods - the generator is the only thing that needs to change to add
a new comparison axis, nothing downstream does.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from data.store import FxStore

MAX_PERIODS = 200  # explicit ceiling - far beyond any realistic use (200 years of yearly periods, or 800 quarters)


@dataclass
class Period:
    label: str
    start: datetime
    end: datetime


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _month_add(dt: datetime, months: int) -> datetime:
    """Adds `months` calendar months to dt - no external dependency,
    shared by every period generator below."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month)


def _end_of_period(next_period_start: datetime) -> datetime:
    """One second before the next period's start - the SHARED boundary
    convention every generator below uses for an internally-computed
    period end (as opposed to a period end that's clipped to the actual
    end of available data, which needs no adjustment). Using this
    consistently in every generator is what makes calendar_year_periods
    and fixed_month_periods(window_months=12) produce IDENTICAL
    boundaries on an aligned range - see test_periods.py's cross-
    generator consistency check. Without it, one generator ending a
    period at "23:59:59" and another at the literal next midnight would
    silently double-count any candle landing exactly on that boundary,
    since FxStore.get_candles filters inclusively on both start and end."""
    return next_period_start - timedelta(seconds=1)


def calendar_year_periods(store: FxStore, instrument: str, granularity: str) -> list[Period]:
    """One Period per calendar year the instrument has ANY data for - a
    partial first/last year (however much of it exists) is still
    included, labeled the same as a full year, since a caller comparing
    "2025 vs 2024" should see both even if 2025 is only half-loaded so
    far - trimming a partial year silently would hide exactly the kind of
    coverage gap worth knowing about."""
    coverage = store.coverage(instrument, granularity)
    if coverage is None:
        return []
    earliest, latest = _parse_iso(coverage[0]), _parse_iso(coverage[1])

    periods: list[Period] = []
    year = earliest.year
    while year <= latest.year and len(periods) < MAX_PERIODS:
        year_start = datetime(year, 1, 1, tzinfo=timezone.utc)
        next_year_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        period_start = max(year_start, earliest)
        period_end = min(_end_of_period(next_year_start), latest)
        periods.append(Period(label=str(year), start=period_start, end=period_end))
        year += 1
    return periods


def fixed_month_periods(
    store: FxStore, instrument: str, granularity: str, window_months: int,
    exclude_start: datetime | None = None, exclude_end: datetime | None = None,
) -> list[Period]:
    """Splits an instrument's coverage into window_months-sized blocks,
    optionally excluding a [exclude_start, exclude_end) range (e.g. the
    original training window - see run_out_of_time_validation.py, which
    now calls this instead of keeping its own private chunking logic)."""
    coverage = store.coverage(instrument, granularity)
    if coverage is None:
        return []
    earliest, latest = _parse_iso(coverage[0]), _parse_iso(coverage[1])

    if exclude_start is None or exclude_end is None:
        return _chunk_side(earliest, latest, window_months, "")

    periods: list[Period] = []
    if earliest < exclude_start:
        periods.extend(_chunk_side(earliest, min(exclude_start, latest), window_months, "pre_"))
    if latest > exclude_end:
        periods.extend(_chunk_side(max(exclude_end, earliest), latest, window_months, "post_"))
    return periods


def _chunk_side(start: datetime, end: datetime, window_months: int, label_prefix: str, min_days: int = 30) -> list[Period]:
    assert start < end, "_chunk_side requires start < end"
    periods: list[Period] = []
    cursor = start
    idx = 0
    while cursor < end and idx < MAX_PERIODS:
        next_cursor = _month_add(cursor, window_months)
        chunk_end = min(_end_of_period(next_cursor), end)
        if (chunk_end - cursor).days >= min_days:
            periods.append(Period(label=f"{label_prefix}{idx}", start=cursor, end=chunk_end))
        cursor = min(next_cursor, end)
        idx += 1
    return periods


# --- Not implemented yet, structured so they slot in without changing
# anything downstream - flagged explicitly rather than left unmentioned:
#
# def calendar_quarter_periods(store, instrument, granularity) -> list[Period]: ...
# def financial_year_periods(store, instrument, granularity, fy_start_month: int) -> list[Period]: ...
# def custom_periods(labels_and_ranges: list[tuple[str, datetime, datetime]]) -> list[Period]: ...
#     (trivial - just wraps user-supplied (label, start, end) tuples into Period objects)
# def regime_labeled_periods(store, instrument, granularity, regime_classifier) -> list[Period]: ...
#     (needs a regime classifier - e.g. trending-vs-ranging via ADX or similar -
#     genuinely new detection logic, not just a chunking rule; explicitly deferred,
#     see CONFIDENCE_SIZING_DESIGN.md Section 3/13's own regime-detection deferral)
