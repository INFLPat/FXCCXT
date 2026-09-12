"""
tests/test_run_store.py

Verifies RunStore persists a real BacktestResult (from the actual engine,
not a hand-built fake) correctly - the run row, every trade row, and that
list_runs()/get_trades() round-trip what record_run() wrote, including the
validation_stage/metrics fields the tiered validation hierarchy depends on.

Run with: python -m tests.test_run_store
"""

import tempfile

from backtest.engine import BacktestEngine, CostModel
from backtest.engine import Trade as EngineTrade
from data.run_store import RunStore
from strategy.sma_crossover import SmaCrossoverStrategy
from tests.synthetic_data import generate_synthetic_candles


def test_record_and_retrieve_a_real_run():
    candles = generate_synthetic_candles(n_candles=500)
    cost_model = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)
    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)

    assert len(result.trades) > 0, "test needs a strategy run that actually traded"

    tmp_path = tempfile.mktemp(suffix=".db")
    store = RunStore(database_url=f"sqlite:///{tmp_path}")

    run_id = store.record_run(
        result, strategy_name="sma_crossover",
        params={"fast_period": 10, "slow_period": 30},
        instrument="EUR_USD", granularity="H1",
        cost_model={"commission_per_unit": 0.00002, "slippage_pips": 1.0, "pip_size": 0.0001},
        validation_stage="tier4_bootstrap",
        metrics={"total_return_pct": -0.51, "sharpe_ratio": -2.22},
        period_start=candles[0].timestamp, period_end=candles[-1].timestamp,
        notes="test run",
    )

    fetched_run = store.get_run(run_id)
    assert fetched_run is not None
    assert fetched_run.strategy_name == "sma_crossover"
    assert fetched_run.params == {"fast_period": 10, "slow_period": 30}
    assert fetched_run.validation_stage == "tier4_bootstrap"
    assert fetched_run.metrics == {"total_return_pct": -0.51, "sharpe_ratio": -2.22}
    assert abs(fetched_run.starting_balance - result.starting_balance) < 1e-9
    assert abs(fetched_run.ending_balance - result.ending_balance) < 1e-9

    fetched_trades = store.get_trades(run_id)
    assert len(fetched_trades) == len(result.trades), (
        f"expected {len(result.trades)} trades round-tripped, got {len(fetched_trades)}"
    )
    original: EngineTrade = result.trades[0]
    fetched = fetched_trades[0]
    assert fetched.side == original.side
    assert abs(fetched.entry_price - original.entry_price) < 1e-9
    assert abs(fetched.realized_pnl() - original.realized_pnl()) < 1e-9

    listed = store.list_runs(strategy_name="sma_crossover")
    assert len(listed) == 1
    assert listed[0].run_id == run_id

    print(f"Run recorded: {run_id}")
    print(f"Trades round-tripped: {len(fetched_trades)}/{len(result.trades)} matched exactly")
    print("RunStore round-trip assertions passed.")


def test_multiple_runs_independently_queryable_by_validation_stage():
    candles = generate_synthetic_candles(n_candles=500)
    tmp_path = tempfile.mktemp(suffix=".db")
    store = RunStore(database_url=f"sqlite:///{tmp_path}")

    stages = [("tier1_light", (5, 20)), ("tier4_bootstrap", (10, 30))]
    for stage, (fast, slow) in stages:
        strategy = SmaCrossoverStrategy(fast_period=fast, slow_period=slow)
        engine = BacktestEngine(strategy=strategy, starting_balance=10_000.0, units_per_trade=1_000.0)
        result = engine.run(candles)
        store.record_run(
            result, strategy_name="sma_crossover", params={"fast_period": fast, "slow_period": slow},
            instrument="EUR_USD", granularity="H1", cost_model={}, validation_stage=stage,
        )

    all_runs = store.list_runs(strategy_name="sma_crossover")
    assert len(all_runs) == 2, f"expected 2 independent runs, got {len(all_runs)}"

    fully_validated = store.list_runs(strategy_name="sma_crossover", validation_stage="tier4_bootstrap")
    assert len(fully_validated) == 1, "validation_stage filter should isolate only the fully-validated run"
    assert fully_validated[0].params == {"fast_period": 10, "slow_period": 30}

    print(f"{len(all_runs)} total runs, {len(fully_validated)} filtered to tier4_bootstrap only.")
    print("validation_stage filtering assertion passed.")


if __name__ == "__main__":
    test_record_and_retrieve_a_real_run()
    test_multiple_runs_independently_queryable_by_validation_stage()
    print("\nAll run_store tests passed.")
