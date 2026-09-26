"""
sandbox_config.py

Single source of truth for the sandbox dataset's time window and output
filename, imported by every script that reads or writes it:
fetch_sandbox_data.py (OANDA FX + Binance USD-crypto), ingest_kraken_gbp_
csv.py (Kraken GBP-crypto), and every downstream consumer (run_validation_
hierarchy_real_data.py, run_full_sweep.py, run_out_of_time_validation.py,
visualize_period_comparison.py). The point: every instrument in the
sandbox must cover the IDENTICAL window, or cross-instrument comparisons
(correlation, sensitivity, out-of-time replay) are silently comparing
different periods without anyone noticing - exactly the bug this file
replaces (FX/USD-crypto were drifting to "today" while GBP-crypto stalled
at the last quarterly Kraken export, a ~6 month silent gap).

GLOBAL_START is a fixed decision (2022-01-01) - Kraken data before this
isn't kept, regardless of how far back the 'historical' bulk export goes.

GLOBAL_END is AUTO-DETECTED from whichever Kraken quarterly folders exist
under KRAKEN_CSV_DIR at run time, never hardcoded - Kraken's quarterly
bulk CSV exports are the hard ceiling on how recent the sandbox can be,
since GBP-crypto has no other historical source (see ingest_kraken_gbp_
csv.py's own docstring). OANDA/Binance could fetch further forward on
their own, but that would misalign the sandbox across instruments -
deliberately not done. Add a new quarter's folder under kraken_csv/ and
every script downstream picks up the new end date automatically, with no
constant to edit by hand.

DB_FILENAME encodes the window directly (e.g. sandbox_22Q1to26Q1.db) so a
stale/mismatched sandbox file is visible from its own name, not just from
reading its contents.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

GLOBAL_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
KRAKEN_CSV_DIR = Path("kraken_csv")
HISTORICAL_FOLDER_NAME = "historical"
QUARTER_FOLDER_RE = re.compile(r"^(\d{2})Q([1-4])$")
MAX_QUARTER_FOLDERS = 60  # explicit ceiling - 15 years of quarters, far beyond realistic need


def _quarter_end(yy: int, q: int) -> datetime:
    """Last second of calendar quarter `q` of 20YY, UTC."""
    assert 1 <= q <= 4, f"quarter must be 1-4, got {q}"
    assert 0 <= yy <= 99, f"yy must be a 2-digit year, got {yy}"
    year = 2000 + yy
    end_month = q * 3
    if end_month == 12:
        return datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    next_month_start = datetime(year, end_month + 1, 1, tzinfo=timezone.utc)
    return next_month_start - timedelta(seconds=1)


def _find_quarter_folders(csv_dir: Path) -> list[tuple[int, int]]:
    """Every (yy, q) pair with a matching YYQ# subfolder under csv_dir."""
    assert csv_dir is not None, "_find_quarter_folders requires a csv_dir"
    found: list[tuple[int, int]] = []
    if csv_dir.exists():
        for entry in sorted(csv_dir.iterdir()):
            if not entry.is_dir():
                continue
            m = QUARTER_FOLDER_RE.match(entry.name)
            if m:
                found.append((int(m.group(1)), int(m.group(2))))
    assert len(found) <= MAX_QUARTER_FOLDERS, (
        f"{len(found)} quarter folders under {csv_dir} exceeds the explicit "
        f"ceiling of {MAX_QUARTER_FOLDERS}"
    )
    return found


def quarter_folder_names(csv_dir: Path = KRAKEN_CSV_DIR) -> list[str]:
    """Every YYQ# folder name under csv_dir, chronologically sorted -
    shared with ingest_kraken_gbp_csv.py so it builds its per-instrument
    file lists in load order without hardcoding which quarters exist."""
    return [f"{yy:02d}Q{q}" for yy, q in sorted(_find_quarter_folders(csv_dir))]


def discover_sandbox_end(csv_dir: Path = KRAKEN_CSV_DIR) -> datetime:
    """Latest quarter-end among csv_dir's YYQ# subfolders. Raises rather
    than silently defaulting - a fresh checkout with no Kraken folders yet
    should fail loudly, not produce an empty-range sandbox."""
    quarters = _find_quarter_folders(csv_dir)
    if not quarters:
        raise FileNotFoundError(
            f"No quarterly folders (e.g. {csv_dir}/23Q1/) found under {csv_dir} - "
            "cannot determine the sandbox's end date. Add at least one before running."
        )
    return _quarter_end(*max(quarters))


def quarter_label(dt: datetime) -> str:
    assert dt is not None, "quarter_label requires a datetime"
    return f"{dt.year % 100:02d}Q{(dt.month - 1) // 3 + 1}"


def sandbox_db_filename(start: datetime, end: datetime) -> str:
    assert start < end, "sandbox_db_filename requires start < end"
    return f"sandbox_{quarter_label(start)}to{quarter_label(end)}.db"


def sandbox_db_path(start: datetime, end: datetime) -> str:
    return f"data/{sandbox_db_filename(start, end)}"
