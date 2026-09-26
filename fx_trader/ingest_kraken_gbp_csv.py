"""
ingest_kraken_gbp_csv.py

Loads Kraken bulk-historical OHLCVT CSVs (BTC/ETH/XRP/LTC vs GBP) into the
sandbox database, filtered to the shared sandbox window (sandbox_config.py).
Kraken's live OHLC API cannot serve historical data this old - this is the
confirmed workaround.

TWO KINDS OF SOURCE FOLDER, auto-discovered under kraken_csv/ - nothing to
list by hand:
- `kraken_csv/historical/` - Kraken's "Complete OHLCVT" bulk export, which
  covers everything from Kraken's own listing date up to whenever it was
  downloaded. Only rows inside the shared window are ever loaded (see
  sandbox_config.GLOBAL_START), so this folder is safe to hold more history
  than the sandbox wants - the row-level filter in load_kraken_csv() does
  the trimming. Needed because the quarterly exports only go back to
  23Q1 - this folder is what makes 2022 data available at all.
- `kraken_csv/YYQ#/` (e.g. `kraken_csv/23Q1/`) - Kraken's quarterly
  incremental exports, one folder per quarter, covering the period the
  bulk export doesn't reach. Loaded AFTER the historical folder, so a
  quarterly file's rows win over the historical file's for any date both
  happen to cover (upsert_candles is last-write-wins) - the quarterly
  export is the more current source for its own quarter.

FILE ORGANISATION WITHIN A FOLDER: Kraken's quarterly incremental
downloads reuse the same filename per pair across quarters (e.g. every
quarter's ETHGBP_60.csv is named identically) - each quarter must be
extracted into its OWN subfolder, which is exactly what the folder-per-
quarter layout below requires.

    kraken_csv/historical/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv   # pre-quarterly bulk export
    kraken_csv/23Q1/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    kraken_csv/23Q2/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    ...
    kraken_csv/26Q1/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv

CSV format (no header): unix_timestamp,open,high,low,close,volume,trades

Like Binance, bid=ask=traded price (no separate historical bid/ask from
Kraken). Volume is stored as the true float, NOT truncated to int like
ccxt_broker.py does - Kraken's GBP-pair volumes are frequently under 1.

WINDOW ALIGNMENT: START/END/DB_PATH come from sandbox_config.py, the same
shared source fetch_sandbox_data.py uses - END here is auto-detected from
whichever quarterly folders actually exist under kraken_csv/, so the two
scripts can never drift out of alignment as long as both are re-run after
adding a new quarter's folder (this replaces the earlier hardcoded-quarter
approach, which silently stalled GBP-crypto at 25Q4 while FX/USD-crypto
kept advancing - see sandbox_config.py's docstring).

Run: python3 ingest_kraken_gbp_csv.py
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from data.store import Candle, FxStore
from sandbox_config import (
    GLOBAL_START,
    HISTORICAL_FOLDER_NAME,
    KRAKEN_CSV_DIR,
    discover_sandbox_end,
    quarter_folder_names,
    sandbox_db_path,
)

CSV_DIR = KRAKEN_CSV_DIR
GRANULARITY = "1h"
START = GLOBAL_START
END = discover_sandbox_end(CSV_DIR)
DB_PATH = sandbox_db_path(START, END)

KRAKEN_FILENAMES = {
    "BTC/GBP": "XBTGBP_60.csv",
    "ETH/GBP": "ETHGBP_60.csv",
    "XRP/GBP": "XRPGBP_60.csv",
    "LTC/GBP": "LTCGBP_60.csv",
}


def _source_folders(csv_dir: Path) -> list[str]:
    """'historical' (if present) followed by every discovered quarterly
    folder, chronologically - the load order that makes a quarterly file
    win over the historical file for any overlapping date."""
    assert csv_dir is not None, "_source_folders requires a csv_dir"
    folders = []
    if (csv_dir / HISTORICAL_FOLDER_NAME).is_dir():
        folders.append(HISTORICAL_FOLDER_NAME)
    folders.extend(quarter_folder_names(csv_dir))
    assert folders, (
        f"no source folders found under {csv_dir} - expected '{HISTORICAL_FOLDER_NAME}/' "
        "and/or at least one YYQ# quarterly folder"
    )
    return folders


def _instrument_files(csv_dir: Path, filename: str) -> list[str]:
    return [f"{folder}/{filename}" for folder in _source_folders(csv_dir)]


KRAKEN_FILES = {
    instrument: _instrument_files(CSV_DIR, filename)
    for instrument, filename in KRAKEN_FILENAMES.items()
}


def load_kraken_csv(path: Path, instrument: str) -> list[Candle]:
    candles = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            ts = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            if not (START <= ts <= END):
                continue
            o, h, l, c, vol = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
            candles.append(
                Candle(
                    instrument=instrument, granularity=GRANULARITY,
                    timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                    bid_open=o, bid_high=h, bid_low=l, bid_close=c,
                    ask_open=o, ask_high=h, ask_low=l, ask_close=c,
                    volume=vol,
                )
            )
    return candles


def _load_instrument_files(instrument: str, filenames: list[str]) -> tuple[list[Candle], bool]:
    """Loads and merges every source file for one instrument, in the order
    given (historical first, then quarters ascending). Returns (candles,
    any_file_found) - a missing/unparseable file is logged and skipped,
    never a silent drop."""
    assert filenames, f"{instrument}: KRAKEN_FILES entry must list at least one file"
    all_candles: list[Candle] = []
    any_file_found = False
    for filename in filenames:
        path = CSV_DIR / filename
        if not path.exists():
            print(f"  {instrument}: FILE NOT FOUND at {path}")
            continue
        any_file_found = True
        try:
            all_candles.extend(load_kraken_csv(path, instrument))
        except (OSError, ValueError, IndexError) as exc:
            print(f"  {instrument}: FAILED to parse {filename} - {exc}")
    return all_candles, any_file_found


def main():
    print(f"Sandbox window: {START.date()} to {END.date()} -> {DB_PATH}")
    print(f"Source folders discovered: {_source_folders(CSV_DIR)}")
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")

    for instrument, filenames in KRAKEN_FILES.items():
        all_candles, any_file_found = _load_instrument_files(instrument, filenames)
        if not any_file_found:
            continue
        if not all_candles:
            print(f"  {instrument}: parsed {len(filenames)} file(s) but found 0 rows in range")
            continue
        n = store.upsert_candles(all_candles)
        print(f"  {instrument}: {len(all_candles)} candles in range across {len(filenames)} file(s), {n} rows written")

    print("\n=== Coverage check ===")
    for instrument in KRAKEN_FILES:
        coverage = store.coverage(instrument, GRANULARITY)
        print(f"  {instrument} ({GRANULARITY}): {coverage or 'NO DATA WRITTEN'}")


if __name__ == "__main__":
    main()
