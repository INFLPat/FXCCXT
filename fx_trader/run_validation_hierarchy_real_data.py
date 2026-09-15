"""
run_validation_hierarchy_real_data.py

Runs SmaCrossoverStrategy through VALIDATION_HIERARCHY.md's Tier 0-4 gate
against all 16 real sandbox instruments (data/sandbox_2025h2.db). Persists
only Tier 4 survivors via RunStore (data/validated_runs.db).

Position sizing: units_per_trade is set so each trade's opening notional is
approximately TARGET_NOTIONAL (10% of starting balance) regardless of the
instrument's price scale - BTC/USDT and XRP/USDT need very different unit
counts to represent "the same size bet". This wasn't specified anywhere in
the existing single-instrument demos; it's the sizing choice this script
makes explicit.

Run: python run_validation_hierarchy_real_data.py
"""

from backtest.engine import CostModel
from backtest.validation_orchestrator import run_validation_hierarchy
from data.run_store import RunStore
from data.store import FxStore
from strategy.sma_crossover import SmaCrossoverStrategy

SANDBOX_DB = "sqlite:///data/sandbox_2025h2.db"
RUNS_DB = "sqlite:///data/validated_runs.db"

TARGET_NOTIONAL = 1_000.0   # 10% of the 10,000 starting balance, per instrument
STARTING_BALANCE = 10_000.0
PARAM_GRID = {"fast_period": [5, 8, 10, 12, 15, 20], "slow_period": [20, 25, 30, 40, 50, 60]}
WINDOW_SIZES = (1000, 300)  # (in_sample_size, out_of_sample_size), same for every instrument

FX_COST_MODEL = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)
FX_COST_MODEL_JPY = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.01)
CRYPTO_COST_MODEL = CostModel(commission_pct=0.001, slippage_pct=0.0005)

FX_PERIODS_PER_YEAR = 252 * 24
CRYPTO_PERIODS_PER_YEAR = 365 * 24

INSTRUMENTS = [
    # (instrument, granularity, cost_model, periods_per_year)
    ("GBP_USD", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("EUR_GBP", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("GBP_JPY", "H1", FX_COST_MODEL_JPY, FX_PERIODS_PER_YEAR),
    ("GBP_CHF", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("EUR_USD", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("USD_JPY", "H1", FX_COST_MODEL_JPY, FX_PERIODS_PER_YEAR),
    ("USD_CHF", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("USD_CAD", "H1", FX_COST_MODEL, FX_PERIODS_PER_YEAR),
    ("BTC/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("ETH/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("XRP/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("LTC/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("BTC/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("ETH/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("XRP/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
    ("LTC/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PERIODS_PER_YEAR),
]


def main():
    assert len(INSTRUMENTS) == 16, "expected exactly the 16 sandbox instruments"
    store = FxStore(database_url=SANDBOX_DB)
    run_store = RunStore(database_url=RUNS_DB)

    print(f"{'instrument':<10} {'candles':>8} {'grid':>5} {'T0+1':>5} {'T2':>4} {'T3':>4} {'T4':>4}  notes")
    print("-" * 100)

    total_persisted = 0
    for instrument, granularity, cost_model, periods_per_year in INSTRUMENTS:
        candles = store.get_candles(instrument, granularity)
        if len(candles) < sum(WINDOW_SIZES):
            print(f"{instrument:<10} SKIPPED - only {len(candles)} candles, need >= {sum(WINDOW_SIZES)}")
            continue

        units_per_trade = TARGET_NOTIONAL / candles[0].mid_close
        outcome = run_validation_hierarchy(
            instrument=instrument, candles=candles, strategy_factory=SmaCrossoverStrategy,
            param_grid=PARAM_GRID, cost_model=cost_model, starting_balance=STARTING_BALANCE,
            units_per_trade=units_per_trade, periods_per_year=periods_per_year,
            window_sizes=WINDOW_SIZES, run_store=run_store, granularity=granularity,
        )
        total_persisted += outcome.tier4_survivors

        print(
            f"{instrument:<10} {len(candles):>8} {outcome.grid_size:>5} "
            f"{outcome.tier1_survivors:>5} {outcome.tier2_survivors:>4} "
            f"{outcome.tier3_finalists:>4} {outcome.tier4_survivors:>4}"
        )
        for note in outcome.notes:
            print(f"    {note}")

    print("-" * 100)
    print(f"Total Tier 4 survivors persisted across all instruments: {total_persisted}")
    print(f"Persisted to: {RUNS_DB}")


if __name__ == "__main__":
    main()
