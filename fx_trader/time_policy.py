# version: 261003
"""
time_policy.py  (fx_trader/time_policy.py)

Single home for the project's time conventions (rationale and history:
CONTEXT_HANDOFF.md Section 4f, "Time policy"). Standard library only.

1. STORED FORMAT. Market-data timestamps are stored as UTC TEXT in ONE fixed
   format, STORED_TS_FORMAT ('2026-03-31T23:00:00.000000000Z'). SQL orders
   and filters on that text, so the format is a CONTRACT. Why it matters
   (found chat 14): Python's isoformat() gives '...+00:00'; at the 20th
   character '+' (ASCII 43) sorts BEFORE '.' (ASCII 46), so an inclusive
   `timestamp <= '...23:00:00+00:00'` silently EXCLUDES the candle stamped
   exactly 23:00:00. normalize_bound() makes every bound match the stored
   format before it reaches SQL.
2. AWARE DATETIMES ONLY. Naive datetimes and naive strings are rejected,
   never guessed.
3. BAR LABEL = bar OPEN time (OANDA, Binance and - relative to Binance,
   by lag test - Kraken; absolute Kraken convention still to be confirmed).
4. DISPLAY ONLY. Conversion to a user's IANA zone happens in local_view();
   storage and analysis periods stay UTC.
5. FX WEEK. The FX weekly session follows New York 17:00 (not London):
   fx_weekly_session().
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STORED_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.000000000Z"
STORED_TS_SUFFIX = ".000000000Z"
STORED_TS_LEN = 30
NEW_YORK = "America/New_York"
FX_ROLLOVER_HOUR_NY = 17
_EXCESS_FRACTION = re.compile(r"(\.\d{6})\d+")


def format_ts(dt: datetime) -> str:
    """Stored-format UTC text for an aware, whole-second datetime."""
    assert isinstance(dt, datetime), "format_ts requires a datetime"
    if dt.tzinfo is None:
        raise ValueError("naive datetime rejected: attach a timezone (UTC) explicitly")
    if dt.microsecond != 0:
        raise ValueError("sub-second precision is not representable in the stored format")
    text = dt.astimezone(timezone.utc).strftime(STORED_TS_FORMAT)
    assert len(text) == STORED_TS_LEN, f"unexpected stored-format length: {text!r}"
    assert text.endswith(STORED_TS_SUFFIX), f"unexpected stored-format suffix: {text!r}"
    return text


def parse_ts(text: str) -> datetime:
    """Aware UTC datetime from stored format, '+00:00' / 'Z' / short forms,
    or any ISO text with an explicit offset. Naive text is rejected."""
    assert isinstance(text, str) and text.strip(), "parse_ts requires non-empty text"
    cleaned = text.strip()
    if cleaned[-1] in "Zz":
        cleaned = cleaned[:-1] + "+00:00"
    cleaned = _EXCESS_FRACTION.sub(r"\1", cleaned)
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp text rejected (no Z or offset): {text!r}")
    result = dt.astimezone(timezone.utc)
    assert result.tzinfo is not None, "parse_ts must return an aware datetime"
    return result


def normalize_bound(value) -> str:
    """Any aware datetime or ISO text -> stored-format text, ready to compare
    with the candles table's timestamp column. Idempotent."""
    assert value is not None, "normalize_bound requires a value"
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_ts(value)
    else:
        raise TypeError(f"bound must be an aware datetime or ISO text, got {type(value).__name__}")
    text = format_ts(dt)
    assert text == format_ts(parse_ts(text)), "normalize_bound must be idempotent"
    return text


def stored_ts_to_epoch(ts: str) -> int:
    """Fast, STRICT parse of stored-format text to integer epoch seconds.
    Doubles as a format validator for every row read from the candles table."""
    assert isinstance(ts, str), "stored timestamp must be text"
    if len(ts) != STORED_TS_LEN or not ts.endswith(STORED_TS_SUFFIX) or ts[10] != "T":
        raise ValueError(f"not in stored format: {ts!r}")
    dt = datetime(int(ts[0:4]), int(ts[5:7]), int(ts[8:10]), int(ts[11:13]),
                  int(ts[14:16]), int(ts[17:19]), tzinfo=timezone.utc)
    return int(dt.timestamp())


def epoch_to_stored_ts(epoch: int) -> str:
    assert isinstance(epoch, int), "epoch must be an int (whole seconds)"
    assert epoch >= 0, "epoch must be non-negative"
    return format_ts(datetime.fromtimestamp(epoch, timezone.utc))


def local_view(value, tz_name: str) -> str:
    """DISPLAY ONLY: an instant rendered in an IANA zone, minute precision
    (e.g. 'Europe/London' -> '2026-07-02T00:30+01:00'). Never used for storage."""
    assert isinstance(tz_name, str) and tz_name, "tz_name must be a non-empty IANA zone name"
    if isinstance(value, bool) or not isinstance(value, (int, str, datetime)):
        raise TypeError(f"cannot display {type(value).__name__}")
    if isinstance(value, int):
        dt = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        dt = parse_ts(value)
    else:
        dt = value
    if dt.tzinfo is None:
        raise ValueError("naive datetime rejected")
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {tz_name!r}") from exc
    return dt.astimezone(zone).isoformat(timespec="minutes")


def fx_weekly_session(friday: date) -> tuple[datetime, datetime]:
    """(last_bar_open_utc, first_bar_open_utc) around the weekend that
    follows `friday`: market close = Friday 17:00 New York, reopen = Sunday
    17:00 New York. Follows US daylight saving, NOT UK: in the weeks where
    the clocks change, close and open sit in different UTC offsets."""
    if friday.weekday() != 4:
        raise ValueError(f"{friday} is not a Friday")
    zone = ZoneInfo(NEW_YORK)
    sunday = friday + timedelta(days=2)
    close_utc = datetime(friday.year, friday.month, friday.day, FX_ROLLOVER_HOUR_NY, tzinfo=zone).astimezone(timezone.utc)
    open_utc = datetime(sunday.year, sunday.month, sunday.day, FX_ROLLOVER_HOUR_NY, tzinfo=zone).astimezone(timezone.utc)
    last_bar_open = close_utc - timedelta(hours=1)
    assert open_utc > last_bar_open, "reopen must follow the last bar"
    assert (open_utc - close_utc) <= timedelta(hours=49), "weekend closure is at most 49h"
    return last_bar_open, open_utc
