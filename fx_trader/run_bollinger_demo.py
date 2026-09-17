"""
run_bollinger_demo.py

End-to-end demo: synthetic FX data -> BollingerBandsStrategy (mean-
reversion on a band reversal) -> backtest engine -> metrics. Mirrors
run_rsi_demo.py's structure - same pipeline, same everything downstream.

Run: python run_bollinger_demo.py
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.bollinger_strategy import BollingerBandsStrategy
from tests.synthetic_data import generate_synthetic_candles


def main():
    instrument, granularity = "EUR_USD", "H1"

    print("Generating synthetic candle data...")
    candles = generate_synthetic_candles(instrument=instrument, granularity=granularity, n_candles=3000)

    store = FxStore()
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")

    loaded = store.get_candles(instrument, granularity)
    print(f"Loaded {len(loaded)} candles back from storage.")

    cost_model = CostModel(commission_per_unit=0.00002, commission_flat=0.0, slippage_pips=1.0, pip_size=0.0001)

    strategy = BollingerBandsStrategy(period=20, num_std=2.0)
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(loaded)

    metrics = compute_metrics(result, periods_per_year=252 * 24, candles=loaded)
    print("\n--- Bollinger Bands Strategy Backtest Results ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(metrics.summary())


if __name__ == "__main__":
    main()
