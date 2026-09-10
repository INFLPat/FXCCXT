"""
run_sensitivity_demo.py

Runs SmaCrossoverStrategy across a full grid of fast/slow SMA periods on
synthetic FX data, and reports how performance varies across that grid -
not just which single combination scored best. Prints the top results with
a neighbor-gap diagnostic for each: does its immediate neighborhood in
parameter space perform similarly (a plateau - more trustworthy), or is it
an isolated spike surrounded by much worse combinations (a strong sign of
fitting to this dataset's noise rather than finding a real effect)?

Run with: python run_sensitivity_demo.py
"""

from backtest.engine import CostModel
from backtest.sensitivity import run_sensitivity_analysis
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles


def main():
    candles = generate_synthetic_candles(n_candles=3000)
    cost_model = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)

    param_grid = {
        "fast_period": [5, 8, 10, 12, 15, 20],
        "slow_period": [20, 25, 30, 40, 50, 60],
    }

    result = run_sensitivity_analysis(
        candles, SmaCrossoverStrategy, param_grid,
        cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0,
    )

    print(result.summary(top_n=5))

    print()
    print("--- Marginal effect of fast_period (averaged over all slow_period values) ---")
    for value, mean_score, count in result.marginal_effect("fast_period"):
        mean_str = f"{mean_score:+.3f}%" if mean_score is not None else "n/a"
        print(f"  fast_period={value:>3}: {mean_str}  (n={count})")

    print()
    print("--- Marginal effect of slow_period (averaged over all fast_period values) ---")
    for value, mean_score, count in result.marginal_effect("slow_period"):
        mean_str = f"{mean_score:+.3f}%" if mean_score is not None else "n/a"
        print(f"  slow_period={value:>3}: {mean_str}  (n={count})")


if __name__ == "__main__":
    main()
