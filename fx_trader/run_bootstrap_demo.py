"""
run_bootstrap_demo.py

Runs SmaCrossoverStrategy on synthetic FX data, then bootstrap-analyzes the
resulting trades two ways:
  - resample (with replacement): a confidence interval around total return
  - shuffle (permutation): isolates how much of the reported drawdown is
    about trade order/luck versus the trades themselves

Run with: python run_bootstrap_demo.py
"""

from backtest.bootstrap import run_bootstrap_from_result
from backtest.engine import BacktestEngine, CostModel
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles


def main():
    candles = generate_synthetic_candles(n_candles=3000)
    cost_model = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)
    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)

    print(f"Backtest: {len(result.trades)} trades, ending balance {result.ending_balance:,.2f}")
    print()

    resample_result = run_bootstrap_from_result(result, method="resample", n_iterations=5000, seed=42)
    print(resample_result.summary())
    print()

    shuffle_result = run_bootstrap_from_result(result, method="shuffle", n_iterations=5000, seed=42)
    print(shuffle_result.summary())


if __name__ == "__main__":
    main()
