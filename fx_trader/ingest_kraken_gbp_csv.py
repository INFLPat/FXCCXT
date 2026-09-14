"""
ingest_kraken_gbp_csv.py

Loads Kraken bulk-historical OHLCVT CSVs (BTC/ETH/XRP/LTC vs GBP) into the
same sandbox database the OANDA/Binance fetch populates - filtered to the
same window everything else in the sandbox uses.

WINDOW: 2025-07-01 to 2025-12-31 (25H2 = Q3+Q4 2025), not the originally
planned 2026 H1. Changed because Kraken's Q2 2026 quarterly incremental
update isn't published yet as of this window being chosen - 25H2 uses only
already-published quarters, and deliberately leaves 26Q1 (also already
available) unused for now, as a ready-to-go extension for a future,
longer/rolling sandbox window rather than using it up immediately.

FILE ORGANISATION: Kraken's quarterly incremental downloads all use the same
filename per pair regardless of which quarter they cover (e.g. every
quarter's Ethereum-vs-GBP file is called ETHGBP_60.csv) - extracting two
different quarters into the same folder means the second overwrites the
first. Fix: extract each quarter's download into ITS OWN subfolder named
after the quarter, and reference full subfolder-qualified paths below -
no renaming needed, no risk of mixing quarters up.

    kraken_csv/
    ├── 25Q3/
    │   ├── XBTGBP_60.csv
    │   ├── ETHGBP_60.csv
    │   ├── XRPGBP_60.csv
    │   └── LTCGBP_60.csv
    └── 25Q4/
        ├── XBTGBP_60.csv
        ├── ETHGBP_60.csv
        ├── XRPGBP_60.csv
        └── LTCGBP_60.csv

Kraken's bulk CSV format (confirmed from a real sample, not assumed):
    unix_timestamp,open,high,low,close,volume,trades
No header row. Each quarterly file is expected to be self-contained for its
own quarter (that's the point of a quarterly "incremental update"), so this
script does NOT also require Kraken's separate base "Complete Data" archive
for this window - just the two quarter-specific files per instrument.

Like Binance/ccxt, bid=ask=the traded price (no separate historical bid/ask
from Kraken, same as everywhere else crypto is handled in this project).
Volume is stored as the TRUE float value, not truncated to an int - unlike
ccxt_broker.py's existing int(vol) cast (a separate, known, low-priority
precision loss for the Binance-sourced rows - harmless there since those
volumes are in the hundreds, but would be destructive here: Kraken's GBP-pair
volumes are frequently well under 1).

SETUP:
1. Extract each quarter's Kraken download into kraken_csv/25Q3/ and
   kraken_csv/25Q4/ respectively (see layout above).
2. If any filename below doesn't match what you actually have, the script
   says exactly which file/quarter is missing rather than failing silently -
   edit KRAKEN_FILES to match.
3. Run: python3 ingest_kraken_gbp_csv.py
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from data.store import Candle, FxStore

CSV_DIR = Path("kraken_csv")

# instrument name -> list of quarter-subfolder-qualified filenames to read
# and merge for it. Order doesn't matter; upsert_candles de-dupes by
# timestamp downstream if quarters ever overlap.
KRAKEN_FILES = {
    "BTC/GBP": ["25Q3/XBTGBP_60.csv", "25Q4/XBTGBP_60.csv"],
    "ETH/GBP": ["25Q3/ETHGBP_60.csv", "25Q4/ETHGBP_60.csv"],
    "XRP/GBP": ["25Q3/XRPGBP_60.csv", "25Q4/XRPGBP_60.csv"],
    "LTC/GBP": ["25Q3/LTCGBP_60.csv", "25Q4/LTCGBP_60.csv"],
}

GRANULARITY = "1h"  # matches the Binance-side crypto granularity label
START = datetime(2025, 7, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
DB_PATH = "data/sandbox_2025h2.db"


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
                    volume=vol,  # true float, not truncated - see module docstring
                )
            )
    return candles


def main():
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")

    for instrument, filenames in KRAKEN_FILES.items():
        all_candles = []
        any_file_found = False
        for filename in filenames:
            path = CSV_DIR / filename
            if not path.exists():
                print(f"  {instrument}: FILE NOT FOUND at {path} - check the quarter subfolder "
                      f"and filename, or update KRAKEN_FILES if it differs from '{filename}'")
                continue
            any_file_found = True
            try:
                all_candles.extend(load_kraken_csv(path, instrument))
            except Exception as exc:  # noqa: BLE001
                print(f"  {instrument}: FAILED to parse {filename} - {exc}")

        if not any_file_found:
            continue
        if not all_candles:
            print(f"  {instrument}: parsed {len(filenames)} file(s) but found 0 rows in the "
                  f"2025-07-01 to 2025-12-31 window - double check the files are the right quarters")
            continue
        n = store.upsert_candles(all_candles)
        print(f"  {instrument}: {len(all_candles)} candles in range across {len(filenames)} file(s), {n} rows written")

    print("\n=== Coverage check (all 4 GBP-crypto pairs) ===")
    for instrument in KRAKEN_FILES:
        coverage = store.coverage(instrument, GRANULARITY)
        print(f"  {instrument} ({GRANULARITY}): {coverage or 'NO DATA WRITTEN'}")

    print(f"\nDone. Upload {DB_PATH} back to Claude to continue.")


if __name__ == "__main__":
    main()
