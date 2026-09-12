"""
fetch_sandbox_data.py

Pulls a fixed 6-month window of REAL historical market data (not synthetic)
for a mixed FX + crypto instrument set, and stores it in a local SQLite file
to use as a fixed "sandbox" dataset - something to tweak and re-run the
strategy against repeatedly without re-fetching or drifting between runs.

This also doubles as the first real test of OandaBroker and CcxtBroker
against live servers - both were previously written-to-spec only, never
run against anything real (see CONTEXT_HANDOFF.md VERIFICATION STATUS).
Report back exactly what happens - success or failure - so that section
can be updated honestly either way.

SETUP (run from inside fx_trader/, i.e. alongside run_backtest_demo.py):
    pip install -r requirements.txt

    # OANDA (FX) - free practice account, no funding required:
    #   https://www.oanda.com/demo-account/ -> My Account -> Manage API Access
    export OANDA_API_TOKEN="..."
    export OANDA_ACCOUNT_ID="..."

    # Crypto (Binance, Kraken): no credentials needed - historical OHLCV is
    # a public, unauthenticated endpoint on both. Only order placement would
    # need keys, and this script never places orders.

    python fetch_sandbox_data.py

Produces: data/sandbox_2026h1.db (SQLite). Upload this file back once it's
finished, to continue the analysis.

GOTCHA handled explicitly: CcxtBroker defaults to sandbox=True (testnet),
the right default for anything that places real orders - but a testnet has
no meaningful trading history, so it would silently return little or no
data for a historical pull. This script passes sandbox=False on both
exchanges since we only ever call fetch_candles (read-only) here.
"""

import time
from datetime import datetime, timezone

from brokers.ccxt_broker import CcxtBroker
from brokers.oanda import OandaBroker
from data.store import FxStore

# --- window: fixed 6-month sandbox, fully historical, no partial-candle risk ---
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

# --- instruments -------------------------------------------------------------
# FX: GBP/USD/EUR-leaning majors + crosses (UK-based business - see memory note)
FX_PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
FX_GRANULARITY = "H1"

# Crypto: same 4 assets on both quote currencies (BTC/ETH/XRP/LTC), so any
# USD-vs-GBP behavioural difference the strategy shows is a real quote-
# currency effect, not a different-asset artifact. LTC replaces the
# originally-proposed SOL for the GBP leg specifically because SOL/GBP's
# availability on Kraken wasn't confirmed at the time this was written;
# the availability check below will skip and warn on anything not listed
# rather than guessing, so swapping the 4th asset back is a one-line change.
CRYPTO_USD = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "LTC/USDT"]   # via Binance
CRYPTO_GBP = ["BTC/GBP", "ETH/GBP", "XRP/GBP", "LTC/GBP"]        # via Kraken
CRYPTO_GRANULARITY = "1h"

DB_PATH = "data/sandbox_2026h1.db"


def fetch_fx(store: FxStore):
    print("\n=== FX (OANDA) ===")
    try:
        oanda = OandaBroker()  # reads OANDA_API_TOKEN / OANDA_ACCOUNT_ID from env
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
        time.sleep(0.5)  # polite gap between requests - OANDA has no built-in throttling here


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
            print(f"  {symbol}: NOT LISTED on {exchange_id} - skipped. "
                  f"Substitute a different asset for this slot if this matters.")
            continue
        try:
            candles = broker.fetch_candles(symbol, CRYPTO_GRANULARITY, START, END)
            n = store.upsert_candles(candles)
            print(f"  {symbol}: {len(candles)} candles fetched, {n} rows written")
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: FAILED - {exc}")


def main():
    store = FxStore(database_url=f"sqlite:///{DB_PATH}")

    fetch_fx(store)
    fetch_crypto(store, "binance", CRYPTO_USD, "USD")
    fetch_crypto(store, "kraken", CRYPTO_GBP, "GBP")

    print("\n=== Coverage check ===")
    all_instruments = [(p, FX_GRANULARITY) for p in FX_PAIRS] + \
                       [(s, CRYPTO_GRANULARITY) for s in CRYPTO_USD + CRYPTO_GBP]
    for instrument, granularity in all_instruments:
        coverage = store.coverage(instrument, granularity)
        print(f"  {instrument} ({granularity}): {coverage or 'NO DATA WRITTEN'}")

    print(f"\nDone. Upload {DB_PATH} back to Claude to continue.")


if __name__ == "__main__":
    main()
