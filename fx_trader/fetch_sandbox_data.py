"""
fetch_sandbox_data.py

Pulls real historical data (OANDA FX + Binance USD-crypto) for an
ARBITRARY, WIDE date range into ONE continuous local SQLite database -
generalized this session from a fixed-H2-2025-only puller into a proper
multi-year history builder (ROADMAP.md Section 1 / out-of-time
validation). Any non-overlapping window can then be sliced out of the one
resulting DB via FxStore.get_candles(start=..., end=...), which already
supports arbitrary date-range filtering - no per-window script needed.

Produces: data/sandbox_history.db (one continuous file - NOT one file per
window). The original 2025 H2 sandbox, data/sandbox_2025h2.db, is left
alone and still usable on its own; this script's output is the superset
going forward for anything needing more than one window.

START/END below default to 2022-01-01 through today - a starting point,
not a verified availability guarantee. OANDA and Binance's REST APIs both
paginate arbitrary historical ranges already (see brokers/oanda.py,
brokers/ccxt_broker.py - MAX_PAGES=500 pages of up to 5000/1000 candles
each is far beyond even a decade of H1 data), so widening the range here
needed NO broker/engine code changes - this is a config change to an
already-working puller, not new engineering. If a pair's history doesn't
go back that far, the fetch simply returns fewer candles for it (never
crashes) - the coverage check at the end shows exactly what was obtained
per instrument; narrow START if a particular pair's real availability
turns out to be shorter than requested.

Setup (run from inside fx_trader/):
    pip install -r requirements.txt
    export OANDA_API_TOKEN="..."      # free practice account
    export OANDA_ACCOUNT_ID="..."
    python fetch_sandbox_data.py

CcxtBroker defaults to sandbox=True (testnet), which has no meaningful
trading history - sandbox=False is passed explicitly here since this
script only ever calls fetch_candles (read-only).
"""

import time
from datetime import datetime, timezone

from brokers.ccxt_broker import CcxtBroker
from brokers.oanda import OandaBroker
from data.store import FxStore

# Widen/narrow these two lines to change the pulled range - everything
# else in this script is range-agnostic.
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)

# UK-based business - GBP/USD-leaning FX majors + crosses, EUR represented.
FX_PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
FX_GRANULARITY = "H1"

CRYPTO_USD = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "LTC/USDT"]   # via Binance
CRYPTO_GRANULARITY = "1h"

DB_PATH = "data/sandbox_history.db"


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
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")

    print(f"Requested range: {START.date()} to {END.date()}")
    fetch_fx(store)
    fetch_crypto(store, "binance", CRYPTO_USD, "USD")

    print("\n=== Coverage check (what was ACTUALLY obtained, not just requested) ===")
    all_instruments = [(p, FX_GRANULARITY) for p in FX_PAIRS] + \
                       [(s, CRYPTO_GRANULARITY) for s in CRYPTO_USD]
    for instrument, granularity in all_instruments:
        coverage = store.coverage(instrument, granularity)
        print(f"  {instrument} ({granularity}): {coverage or 'NO DATA WRITTEN'}")

    print(f"\nDone. GBP-crypto still needs ingest_kraken_gbp_csv.py run separately "
          f"(now also generalized to auto-discover any number of quarterly CSV folders).")


if __name__ == "__main__":
    main()
