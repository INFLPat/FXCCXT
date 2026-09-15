"""
ingest_kraken_gbp_csv.py

Loads Kraken bulk-historical OHLCVT CSVs (BTC/ETH/XRP/LTC vs GBP) into the
sandbox database, filtered to the 2025 H2 window. Kraken's live OHLC API
cannot serve historical data this old - this is the confirmed workaround.

FILE ORGANISATION: Kraken's quarterly incremental downloads reuse the same
filename per pair across quarters (e.g. every quarter's ETHGBP_60.csv is
named identically) - extract each quarter into its OWN subfolder:

    kraken_csv/25Q3/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    kraken_csv/25Q4/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv

CSV format (no header): unix_timestamp,open,high,low,close,volume,trades

Like Binance, bid=ask=traded price (no separate historical bid/ask from
Kraken). Volume is stored as the true float, NOT truncated to int like
ccxt_broker.py does - Kraken's GBP-pair volumes are frequently under 1.

Run: python3 ingest_kraken_gbp_csv.py
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from data.store import Candle, FxStore

CSV_DIR = Path("kraken_csv")

KRAKEN_FILES = {
    "BTC/GBP": ["25Q3/XBTGBP_60.csv", "25Q4/XBTGBP_60.csv"],
    "ETH/GBP": ["25Q3/ETHGBP_60.csv", "25Q4/ETHGBP_60.csv"],
    "XRP/GBP": ["25Q3/XRPGBP_60.csv", "25Q4/XRPGBP_60.csv"],
    "LTC/GBP": ["25Q3/LTCGBP_60.csv", "25Q4/LTCGBP_60.csv"],
}

GRANULARITY = "1h"
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
                    volume=vol,
                )
            )
    return candles


def _load_instrument_files(instrument: str, filenames: list[str]) -> tuple[list[Candle], bool]:
    """Loads and merges every quarterly file for one instrument. Returns
    (candles, any_file_found) - a missing/unparseable file is logged and
    skipped, never a silent drop."""
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
