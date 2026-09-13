"""
ingest_kraken_gbp_csv.py

Loads the 4 Kraken bulk-historical OHLCVT CSVs (BTC/ETH/XRP/LTC vs GBP) into
the same data/sandbox_2026h1.db the OANDA/Binance fetch already populated -
filtered down to the same 2026-01-01 to 2026-06-30 window everything else
uses, so the whole sandbox stays one consistent, comparable dataset.

Kraken's bulk CSV format (confirmed from a real sample, not assumed):
    unix_timestamp,open,high,low,close,volume,trades
No header row. Same format across all pairs/intervals per Kraken's own
documentation - only the "_60.csv" (60-minute = hourly) file is needed here,
matching the H1/1h granularity used everywhere else in this sandbox.

Like Binance/ccxt, Kraken's historical data is a single trade-price series,
not separate bid/ask - so bid=ask=the traded price here too, consistent
with how brokers/ccxt_broker.py already handles this for USD-quoted crypto.

Volume is stored as the TRUE float value from the CSV, not truncated to an
int - unlike ccxt_broker.py's existing int(vol) cast (a separate, known,
low-priority precision loss for the Binance-sourced rows, harmless there
since those volumes are in the hundreds, but would be destructive here:
Kraken's actual GBP-pair volumes are frequently well under 1 - see the
sample in the docstring below, several rows are 0.06, 0.2, 0.405 BTC -
truncating those to int would zero them out entirely.

SETUP:
1. Place the 4 CSVs in a folder named kraken_csv/ next to this script
   (i.e. inside fx_trader/, alongside data/sandbox_2026h1.db).
2. If any of the filenames below don't match what you actually have -
   the script tells you exactly which one, rather than failing silently -
   just edit the KRAKEN_FILES dict to match.
3. Run: python3 ingest_kraken_gbp_csv.py
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from data.store import Candle, FxStore

CSV_DIR = Path("kraken_csv")

# instrument name (matching the BTC/USDT-style convention already used for
# the USD-quoted crypto pairs) -> list of filenames to read for it. A list,
# not a single filename, because Kraken's "Complete Data" archive is a
# point-in-time snapshot (that's exactly why Kraken separately offers
# quarterly "Incremental Updates" for anyone who's already downloaded the
# base file) - if the base snapshot predates 2026, none of our target window
# will be in it at all, and the real data will only exist in one or more
# incremental update files layered on top. Add filenames here as needed;
# every file listed for an instrument gets read and merged before filtering
# to the 2026-01-01 - 2026-06-30 window, so order doesn't matter and
# duplicate/overlapping rows between files are harmless (upsert_candles
# de-dupes by timestamp downstream anyway).
KRAKEN_FILES = {
    "BTC/GBP": ["XBTGBP_60.csv"],
    "ETH/GBP": ["ETHGBP_60.csv"],
    "XRP/GBP": ["XRPGBP_60.csv"],
    "LTC/GBP": ["LTCGBP_60.csv"],
}

GRANULARITY = "1h"  # matches the Binance-side crypto granularity label
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
DB_PATH = "data/sandbox_2026h1.db"


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
                print(f"  {instrument}: FILE NOT FOUND at {path} - check the actual filename "
                      f"in kraken_csv/ and update KRAKEN_FILES if it differs from '{filename}'")
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
                  f"2026-01-01 to 2026-06-30 window - the file(s) likely don't extend into 2026 yet "
                  f"(check with: tail -3 kraken_csv/{filenames[0]}). If so, add the matching quarterly "
                  f"incremental-update file(s) to KRAKEN_FILES for this instrument.")
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
