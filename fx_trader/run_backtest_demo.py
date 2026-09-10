"""
run_backtest_demo.py

End-to-end demo: generates synthetic data, stores it via FxStore (SQLite
locally by default, or a cloud Postgres database if DATABASE_URL is set),
runs the SMA crossover strategy through the backtest engine with realistic
costs, and prints performance metrics.

Run with:            python run_backtest_demo.py
Against a cloud DB:  DATABASE_URL="postgresql+psycopg2://..." python run_backtest_demo.py

Once you have a real OANDA account, swap `generate_synthetic_candles(...)`
for `OandaBroker().fetch_candles(...)` — everything downstream (storage,
strategy, engine, metrics) is unchanged, since OandaBroker returns the same
Candle objects.
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles


def main():
    instrument, granularity = "EUR_USD", "H1"

    # 1. Generate + store synthetic data (replace with OandaBroker for real data)
    print("Generating synthetic candle data...")
    candles = generate_synthetic_candles(instrument=instrument, granularity=granularity, n_candles=3000)

    # Uses DATABASE_URL if set (e.g. your Neon/Supabase Postgres connection
    # string), otherwise falls back to a local SQLite file automatically.
    store = FxStore()
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")

    # 2. Read back from storage (proves the SQL round-trip works)
    loaded = store.get_candles(instrument, granularity)
    print(f"Loaded {len(loaded)} candles back from storage.")

    # 3. Configure realistic costs
    #    - typical retail FX commission: ~$0.00002 per unit (i.e. $20 per 1M traded)
    #    - 1 pip flat slippage as a conservative assumption
    cost_model = CostModel(
        commission_per_unit=0.00002,
        commission_flat=0.0,
        slippage_pips=1.0,
        pip_size=0.0001,
    )

    # 4. Run backtest
    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(
        strategy=strategy,
        cost_model=cost_model,
        starting_balance=10_000.0,
        units_per_trade=1_000.0,
    )
    result = engine.run(loaded)

    # 5. Report
    metrics = compute_metrics(result, periods_per_year=252 * 24)  # H1 candles -> hourly periods/year approx
    print("\n--- Backtest Results ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(metrics.summary())


if __name__ == "__main__":
    main()
