"""
ingest_kraken_gbp_csv.py

Loads Kraken bulk-historical OHLCVT CSVs (BTC/ETH/XRP/LTC vs GBP) into the
sandbox database. GENERALIZED this session (ROADMAP.md Section 1 /
out-of-time validation): instead of a hardcoded dict naming exactly which
quarters to load, this now AUTO-DISCOVERS every quarterly subfolder under
CSV_DIR and ingests whatever it finds - drop in as many quarters as you
can obtain from Kraken's bulk-download page (25Q1, 25Q2, 24Q4, 24Q3, ...,
however far back their bulk exports go) and one run picks up all of them.
No date-range filtering is applied here (unlike the original H2-2025-only
version) - every row from every discovered file is ingested; slice out
whatever window you need later via FxStore.get_candles(start=, end=).

Kraken's LIVE OHLC API only serves a rolling recent window, not arbitrary
historical ranges - this bulk-CSV path is the confirmed, still-necessary
workaround (see brokers/ccxt_broker.py's docstring for the same
limitation from the live-API side). How far back Kraken's bulk exports
actually go is NOT something this script or this sandbox can check (no
network access here) - confirm on Kraken's own bulk-download page before
assuming a given historical quarter is available.

FILE ORGANISATION - one subfolder per quarter, Kraken's own filenames
(which repeat identically across quarters) kept as-is inside each:

    kraken_csv/25Q1/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    kraken_csv/25Q2/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    kraken_csv/25Q3/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    kraken_csv/25Q4/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv
    ... (add as many quarter folders as you have - no code change needed)

CSV format (no header): unix_timestamp,open,high,low,close,volume,trades

Like Binance, bid=ask=traded price (no separate historical bid/ask from
Kraken). Volume is stored as the true float, NOT truncated to int like
ccxt_broker.py does - Kraken's GBP-pair volumes are frequently under 1.

Run: python3 ingest_kraken_gbp_csv.py
"""

from datetime import datetime, timezone
from pathlib import Path

from data.store import Candle, FxStore

CSV_DIR = Path("kraken_csv")
DB_PATH = "data/sandbox_history.db"
GRANULARITY = "1h"

# Kraken's own per-quarter filename -> instrument. Same 4 files expected
# inside EVERY discovered quarter subfolder.
FILENAME_TO_INSTRUMENT = {
    "XBTGBP_60.csv": "BTC/GBP",
    "ETHGBP_60.csv": "ETH/GBP",
    "XRPGBP_60.csv": "XRP/GBP",
    "LTCGBP_60.csv": "LTC/GBP",
}

MAX_QUARTER_FOLDERS = 200  # explicit ceiling - 50 years of quarters is far beyond any realistic use


def discover_quarter_folders() -> list[Path]:
    """Every immediate subfolder of CSV_DIR, sorted for deterministic,
    readable run order - no assumption about naming beyond "a folder"."""
    if not CSV_DIR.exists():
        return []
    folders = sorted(p for p in CSV_DIR.iterdir() if p.is_dir())
    assert len(folders) <= MAX_QUARTER_FOLDERS, (
        f"{len(folders)} quarter folders found under {CSV_DIR} - exceeds the explicit "
        f"ceiling of {MAX_QUARTER_FOLDERS}, check CSV_DIR isn't pointed somewhere unexpected"
    )
    return folders


def load_kraken_csv(path: Path, instrument: str) -> list[Candle]:
    """Every row in the file, unfiltered by date - window-slicing happens
    later via FxStore.get_candles(start=, end=), not here."""
    candles = []
    with open(path, newline="") as f:
        for line in f:
            row = line.strip().split(",")
            if len(row) < 6:
                continue
            ts = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
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


def ingest_one_quarter(store: FxStore, quarter_folder: Path) -> dict[str, int]:
    """Ingests every recognized file inside one quarter folder. Returns
    {instrument: rows_written} for whatever was actually found there -
    a quarter folder missing some/all of the 4 files is logged and
    partially processed, never a hard failure for the whole run."""
    assert quarter_folder.is_dir(), f"{quarter_folder} must be a directory"
    written: dict[str, int] = {}
    for filename, instrument in FILENAME_TO_INSTRUMENT.items():
        path = quarter_folder / filename
        if not path.exists():
            print(f"    {quarter_folder.name}/{filename}: not found - skipped")
            continue
        try:
            candles = load_kraken_csv(path, instrument)
        except (OSError, ValueError, IndexError) as exc:
            print(f"    {quarter_folder.name}/{filename}: FAILED to parse - {exc}")
            continue
        if not candles:
            print(f"    {quarter_folder.name}/{filename}: parsed but 0 rows")
            continue
        n = store.upsert_candles(candles)
        written[instrument] = written.get(instrument, 0) + n
    return written


def main():
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")
    quarter_folders = discover_quarter_folders()

    if not quarter_folders:
        print(f"No quarter subfolders found under {CSV_DIR}/ - nothing to ingest.")
        print("Expected layout: kraken_csv/<label>/{XBTGBP,ETHGBP,XRPGBP,LTCGBP}_60.csv")
        return

    print(f"Found {len(quarter_folders)} quarter folder(s): {[p.name for p in quarter_folders]}")
    totals: dict[str, int] = {}
    for folder in quarter_folders:
        print(f"\n=== {folder.name} ===")
        written = ingest_one_quarter(store, folder)
        for instrument, n in written.items():
            totals[instrument] = totals.get(instrument, 0) + n
            print(f"    {instrument}: {n} rows written from this quarter")

    print("\n=== Totals across all discovered quarters ===")
    for instrument in FILENAME_TO_INSTRUMENT.values():
        print(f"  {instrument}: {totals.get(instrument, 0)} rows written")

    print("\n=== Coverage check (full stored range, all quarters combined) ===")
    for instrument in FILENAME_TO_INSTRUMENT.values():
        coverage = store.coverage(instrument, GRANULARITY)
        print(f"  {instrument} ({GRANULARITY}): {coverage or 'NO DATA WRITTEN'}")


if __name__ == "__main__":
    main()
