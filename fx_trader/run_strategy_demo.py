"""
run_strategy_demo.py

End-to-end single-strategy demo: synthetic FX data -> FxStore -> strategy
-> backtest engine with realistic costs -> metrics. Replaces
run_backtest_demo.py / run_rsi_demo.py / run_bollinger_demo.py - those
three were byte-for-byte the same shape, only the strategy class and its
params differed. run_macd_demo.py and run_confluence_demo.py stay
separate scripts: both run a multi-variant COMPARISON (MACD's RSI-filter
on/off, confluence's window sweep + baselines), not a single backtest -
folding them into this template would drop real functionality, not just
duplicate boilerplate. run_crypto_backtest_demo.py also stays separate
(percentage-based cost path, not pip-based).

Change STRATEGY_NAME below to switch strategies; everything else is
strategy-agnostic. Swap generate_synthetic_candles(...) for
OandaBroker().fetch_candles(...) for real data - everything downstream is
unchanged.

Run: python run_strategy_demo.py
Against a cloud DB: DATABASE_URL="postgresql+psycopg2://..." python run_strategy_demo.py
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.bollinger_strategy import BollingerBandsStrategy
from strategy.rsi_strategy import RsiStrategy
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles

# Change this to switch strategies - everything below is generic.
STRATEGY_NAME = "SmaCrossoverStrategy"

STRATEGIES = {
    "SmaCrossoverStrategy": (SmaCrossoverStrategy, {"fast_period": 10, "slow_period": 30}),
    "RsiStrategy": (RsiStrategy, {"period": 14, "oversold": 30.0, "overbought": 70.0}),
    "BollingerBandsStrategy": (BollingerBandsStrategy, {"period": 20, "num_std": 2.0}),
}


def main():
    assert STRATEGY_NAME in STRATEGIES, f"STRATEGY_NAME must be one of {list(STRATEGIES)}"
    instrument, granularity = "EUR_USD", "H1"
    strategy_factory, params = STRATEGIES[STRATEGY_NAME]

    print(f"Generating synthetic candle data for {STRATEGY_NAME}...")
    candles = generate_synthetic_candles(instrument=instrument, granularity=granularity, n_candles=3000)

    store = FxStore()
    n_written = store.upsert_candles(candles)
    print(f"Wrote {n_written} candles to {store.database_url.split('://')[0]} storage.")

    loaded = store.get_candles(instrument, granularity)
    print(f"Loaded {len(loaded)} candles back from storage.")

    cost_model = CostModel(commission_per_unit=0.00002, commission_flat=0.0, slippage_pips=1.0, pip_size=0.0001)

    strategy = strategy_factory(**params)
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(loaded)

    # candles= passed for every strategy here (buy_hold_return_pct/alpha
    # populated) - the original run_backtest_demo.py (SMA) omitted this
    # and printed those two fields as "n/a"; standardized on always
    # passing it during the demo-script consolidation. Purely additive.
    metrics = compute_metrics(result, periods_per_year=252 * 24, candles=loaded)
    print(f"\n--- {STRATEGY_NAME} Backtest Results ---")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(metrics.summary())


if __name__ == "__main__":
    main()
