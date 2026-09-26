"""
fetch_sandbox_data.py

Pulls real historical data for 12 of the 16 sandbox instruments (8 FX via
OANDA, 4 USD-crypto via Binance) into a local SQLite sandbox file. The
remaining 4 (GBP-crypto via Kraken) are handled by ingest_kraken_gbp_csv.py
- Kraken's live OHLC API only serves a rolling recent window, not arbitrary
historical ranges, so GBP-crypto comes from Kraken's bulk CSV exports
instead (quarterly incremental files plus one 'historical' folder for
everything before the quarterly exports start - see that script).

WINDOW: START/END/DB_PATH all come from sandbox_config.py, the single
shared source of truth also used by ingest_kraken_gbp_csv.py and every
downstream script (run_full_sweep.py, run_validation_hierarchy_real_data.py,
run_out_of_time_validation.py, visualize_period_comparison.py). START is a
fixed decision (2022-01-01); END is auto-detected from whichever Kraken
quarterly folders exist under kraken_csv/ at run time - Kraken's bulk
exports are the hard ceiling on sandbox recency, since GBP-crypto has no
other historical source. Run this AFTER kraken_csv/ has at least the
quarter folder(s) you want the sandbox to extend through - adding a later
quarter and re-running both scripts extends the whole sandbox forward with
no constant to edit by hand.

Setup (run from inside fx_trader/):
    pip install -r requirements.txt
    export OANDA_API_TOKEN="..."      # free practice account
    export OANDA_ACCOUNT_ID="..."
    python fetch_sandbox_data.py

Produces: <DB_PATH> (e.g. data/sandbox_22Q1to26Q1.db - see sandbox_config.py)

CcxtBroker defaults to sandbox=True (testnet), which has no meaningful
trading history - sandbox=False is passed explicitly here since this script
only ever calls fetch_candles (read-only).
"""

import time

from brokers.ccxt_broker import CcxtBroker
from brokers.oanda import OandaBroker
from data.store import FxStore
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path

START = GLOBAL_START
END = discover_sandbox_end()
DB_PATH = sandbox_db_path(START, END)

# UK-based business - GBP/USD-leaning FX majors + crosses, EUR represented.
FX_PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
FX_GRANULARITY = "H1"

# Same 4 assets on both quote currencies (BTC/ETH/XRP/LTC) so any USD-vs-GBP
# behavioural difference is a real quote-currency effect, not a different-
# asset artifact. Crypto vs GBP is NOT fetched here - see ingest_kraken_gbp_csv.py.
CRYPTO_USD = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "LTC/USDT"]   # via Binance
CRYPTO_GRANULARITY = "1h"


def fetch_fx(store: FxStore):
    print("\n=== FX (OANDA) ===")
    try:
        oanda = OandaBroker()
    except ValueError as e:
        print(f"Skipping FX entirely - {e}")
        return

    for pair in FX_PAIRS:
        try:
            candles = oanda.fetch_candles(pair, FX_GRANULARITY, START, END)
            n = store.upsert_candles(candles)
            print(f"  {pair}: {len(candles)} candles fetched, {n} rows written")
        except Exception as exc:  # noqa: BLE001 - one bad pair shouldn't kill the run
            print(f"  {pair}: FAILED - {exc}")
        time.sleep(0.5)


def fetch_crypto(store: FxStore, exchange_id: str, symbols: list[str], quote_label: str):
    print(f"\n=== Crypto vs {quote_label} ({exchange_id}) ===")
    broker = CcxtBroker(exchange_id=exchange_id, sandbox=False)
    try:
        markets = broker.exchange.load_markets()
    except Exception as exc:
        print(f"  Could not load {exchange_id} markets - {exc}")
        return

    for symbol in symbols:
        if symbol not in markets:
            print(f"  {symbol}: NOT LISTED on {exchange_id} - skipped.")
            continue
        try:
            candles = broker.fetch_candles(symbol, CRYPTO_GRANULARITY, START, END)
            n = store.upsert_candles(candles)
            print(f"  {symbol}: {len(candles)} candles fetched, {n} rows written")
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: FAILED - {exc}")


def main():
    print(f"Sandbox window: {START.date()} to {END.date()} -> {DB_PATH}")
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")

    fetch_fx(store)
    fetch_crypto(store, "binance", CRYPTO_USD, "USD")

    print("\n=== Coverage check ===")
    all_instruments = [(p, FX_GRANULARITY) for p in FX_PAIRS] + \
                       [(s, CRYPTO_GRANULARITY) for s in CRYPTO_USD]
    for instrument, granularity in all_instruments:
        coverage = store.coverage(instrument, granularity)
        print(f"  {instrument} ({granularity}): {coverage or 'NO DATA WRITTEN'}")

    print(f"\nDone. GBP-crypto still needs ingest_kraken_gbp_csv.py run separately "
          f"(same window: {START.date()} to {END.date()}).")


if __name__ == "__main__":
    main()
