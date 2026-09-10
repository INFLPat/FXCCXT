"""
run_walk_forward_demo.py

Runs SmaCrossoverStrategy through walk-forward testing on synthetic FX data:
slides a window through history, re-selects the best fast/slow SMA periods
from a grid on each in-sample slice, and reports ONLY out-of-sample
performance - the honest test of whether this strategy has anything real,
per window and combined across all of them.

Run with: python run_walk_forward_demo.py
"""

from backtest.engine import CostModel
from backtest.walk_forward import run_walk_forward
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles


def main():
    candles = generate_synthetic_candles(n_candles=3000)

    cost_model = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)

    # a small grid of fast/slow SMA period combinations to re-select from on
    # every in-sample window - swap in whatever range you want to explore
    param_grid = [
        {"fast_period": 5, "slow_period": 20},
        {"fast_period": 10, "slow_period": 30},
        {"fast_period": 15, "slow_period": 40},
        {"fast_period": 20, "slow_period": 60},
    ]

    result = run_walk_forward(
        candles,
        strategy_factory=SmaCrossoverStrategy,
        param_grid=param_grid,
        in_sample_size=500,
        out_of_sample_size=150,
        cost_model=cost_model,
        starting_balance=10_000.0,
        units_per_trade=1_000.0,
    )

    print(f"{'window':>6} | {'params':>20} | {'in-sample':>10} | {'out-of-sample':>14} | {'oos trades':>10}")
    print("-" * 72)
    for w in result.windows:
        params_str = f"{w.chosen_params['fast_period']}/{w.chosen_params['slow_period']}"
        print(
            f"{w.window_index:>6} | {params_str:>20} | {w.in_sample_score:>9.2f}% | "
            f"{w.out_of_sample_metrics.total_return_pct:>13.2f}% | {w.out_of_sample_metrics.total_trades:>10}"
        )

    print()
    print("--- Combined out-of-sample performance (compounded across all windows) ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(result.combined_metrics.summary())

    # quick eyeball check for overfitting: average in-sample score vs average
    # out-of-sample return across windows. A big, consistent gap - in-sample
    # looking good while out-of-sample doesn't - is the signature to watch for.
    avg_in_sample = sum(w.in_sample_score for w in result.windows) / len(result.windows)
    avg_out_of_sample = sum(w.out_of_sample_metrics.total_return_pct for w in result.windows) / len(result.windows)
    print()
    print(f"Average in-sample score:      {avg_in_sample:+.2f}%")
    print(f"Average out-of-sample return: {avg_out_of_sample:+.2f}%")


if __name__ == "__main__":
    main()
