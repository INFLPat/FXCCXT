"""
run_macd_demo.py

End-to-end demo: synthetic FX data -> MacdStrategy (MACD crossover, RSI-
confirmation filter on by default) -> backtest engine with realistic costs
-> metrics. Also runs the SAME data with require_rsi_confirmation=False,
so the printed comparison directly shows whether the RSI filter is doing
anything on this data - exactly the kind of question the validation
hierarchy's param_grid should settle on real data, not assumption.

Run: python run_macd_demo.py
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.macd_strategy import MacdStrategy
from tests.synthetic_data import generate_synthetic_candles


def _run(candles, cost_model, require_rsi_confirmation):
    strategy = MacdStrategy(fast=12, slow=26, signal=9, require_rsi_confirmation=require_rsi_confirmation)
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    return result, compute_metrics(result, periods_per_year=252 * 24, candles=candles)


def main():
    instrument, granularity = "EUR_USD", "H1"

    print("Generating synthetic candle data...")
    candles = generate_synthetic_candles(instrument=instrument, granularity=granularity, n_candles=3000)

    store = FxStore()
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")
    loaded = store.get_candles(instrument, granularity)

    cost_model = CostModel(commission_per_unit=0.00002, commission_flat=0.0, slippage_pips=1.0, pip_size=0.0001)

    result_filtered, metrics_filtered = _run(loaded, cost_model, require_rsi_confirmation=True)
    print("\n--- MACD + RSI confirmation filter ---")
    print(f"Starting balance: {result_filtered.starting_balance:,.2f}")
    print(f"Ending balance:   {result_filtered.ending_balance:,.2f}")
    print()
    print(metrics_filtered.summary())

    result_pure, metrics_pure = _run(loaded, cost_model, require_rsi_confirmation=False)
    print("\n--- Pure MACD crossover (RSI filter off) ---")
    print(f"Starting balance: {result_pure.starting_balance:,.2f}")
    print(f"Ending balance:   {result_pure.ending_balance:,.2f}")
    print()
    print(metrics_pure.summary())

    print("\n--- Filter impact on this data ---")
    print(f"Trades:   filtered={metrics_filtered.total_trades}  pure={metrics_pure.total_trades}")
    print(f"Return:   filtered={metrics_filtered.total_return_pct:+.2f}%  pure={metrics_pure.total_return_pct:+.2f}%")
    print(f"Sharpe:   filtered={metrics_filtered.sharpe_ratio}  pure={metrics_pure.sharpe_ratio}")


if __name__ == "__main__":
    main()
