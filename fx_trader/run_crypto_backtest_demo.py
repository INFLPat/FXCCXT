"""
run_crypto_backtest_demo.py

End-to-end demo of the crypto path: synthetic BTC/USDT-shaped data (bid==ask,
matching what CcxtBroker's real historical data looks like) -> SQLite/Postgres
storage -> SMA crossover strategy -> backtest engine using percentage-based
costs (commission_pct/slippage_pct) instead of FX-style pip costs -> metrics.

Run with: python run_crypto_backtest_demo.py

Once you're ready for real data, swap generate_synthetic_crypto_candles(...)
for CcxtBroker(exchange_id="binance", sandbox=True).fetch_candles(...) -
same Candle objects, same downstream code, unchanged.
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_crypto_candles


def main():
    instrument, granularity = "BTC/USDT", "1h"

    print("Generating synthetic crypto candle data...")
    candles = generate_synthetic_crypto_candles(instrument=instrument, granularity=granularity, n_candles=3000)

    store = FxStore()  # same DATABASE_URL / local SQLite fallback as the FX demo
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")

    loaded = store.get_candles(instrument, granularity)
    print(f"Loaded {len(loaded)} candles back from storage.")

    # Typical spot exchange taker fee is ~0.1%; slippage assumption stands in
    # for the missing historical spread (see brokers/ccxt_broker.py docstring
    # for why crypto doesn't have historical bid/ask the way FX does here).
    cost_model = CostModel(
        commission_pct=0.001,   # 0.1% per side
        slippage_pct=0.0005,    # 0.05% adverse move on execution
    )

    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(
        strategy=strategy,
        cost_model=cost_model,
        starting_balance=10_000.0,
        units_per_trade=0.05,  # e.g. 0.05 BTC per trade
    )
    result = engine.run(loaded)

    metrics = compute_metrics(result, periods_per_year=365 * 24)  # 1h candles, crypto trades 24/7
    print("\n--- Crypto Backtest Results ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(metrics.summary())


if __name__ == "__main__":
    main()
