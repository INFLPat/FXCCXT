"""
run_crypto_backtest_demo.py

Crypto path demo: synthetic BTC/USDT-shaped data (bid==ask) -> storage ->
SMA crossover -> backtest engine using percentage-based costs instead of
FX-style pip costs -> metrics.

Run: python run_crypto_backtest_demo.py
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

    store = FxStore()
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")

    loaded = store.get_candles(instrument, granularity)
    print(f"Loaded {len(loaded)} candles back from storage.")

    cost_model = CostModel(commission_pct=0.001, slippage_pct=0.0005)

    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(
        strategy=strategy, cost_model=cost_model,
        starting_balance=10_000.0, units_per_trade=0.05,
    )
    result = engine.run(loaded)

    metrics = compute_metrics(result, periods_per_year=365 * 24)
    print("\n--- Crypto Backtest Results ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(metrics.summary())


if __name__ == "__main__":
    main()
