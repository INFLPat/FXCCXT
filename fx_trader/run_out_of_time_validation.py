"""
run_out_of_time_validation.py

Out-of-time validation for the 17 individually-persisted Tier 4 survivor
runs from the 2025 H2 sweep (ROADMAP.md Section 1). Reads whatever
multi-year history exists in data/sandbox_history.db and tests every
survivor against every WINDOW_SIZE_MONTHS-sized block outside the
original training window, via backtest/periods.py's shared
fixed_month_periods() - REFACTORED this session to use that shared
module instead of its own private chunking logic (was duplicated with
what visualize_period_comparison.py also needs - see
SUBTASK_VISUALIZATION.md for why this was factored out).

Two-part methodology (ROADMAP.md Section 1):
1. FROZEN-CANDIDATE REPLAY (this script): every survivor's exact
   (strategy, params, instrument) combination, unchanged - no
   re-selection, no re-tuning - re-run against EVERY out-of-training
   window this instrument has data for. A candidate that fails on a
   given window is FAIL for that window specifically - failing some
   windows and not others is itself informative (which regime does the
   edge hold up in?), not just a pass/fail verdict to average away.
2. INDEPENDENT RE-DISCOVERY (separate check, unchanged): point
   run_full_sweep.py's SANDBOX_DB/RUNS_DB at data/sandbox_history.db
   restricted to one window (see FxStore.get_candles(start=, end=)) and
   compare its independently-found survivors to this script's PASS list
   by hand.

Judged against the SAME gate values as the original run, per window - not
a loosened bar: total_trades >= MIN_CLOSED_TRADES, THEN total_return_pct>0,
max_drawdown_pct<MAX_DRAWDOWN_CEILING_PCT, THEN bootstrap CI-90 lower
bound > 0 AND probability_of_loss < PROBABILITY_OF_LOSS_CEILING_PCT.

Run: python run_out_of_time_validation.py
"""

from datetime import datetime, timezone

from backtest.bootstrap import run_bootstrap_from_result
from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from backtest.periods import fixed_month_periods
from backtest.validation_orchestrator import (
    MAX_DRAWDOWN_CEILING_PCT,
    MIN_CLOSED_TRADES,
    PROBABILITY_OF_LOSS_CEILING_PCT,
)
from data.store import FxStore
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from strategy.rsi_macd_confluence import RsiMacdConfluenceStrategy
from strategy.rsi_strategy import RsiStrategy

HISTORY_DB = f"sqlite:///{sandbox_db_path(GLOBAL_START, discover_sandbox_end())}"
TARGET_NOTIONAL = 1_000.0
STARTING_BALANCE = 10_000.0
WINDOW_SIZE_MONTHS = 6

TRAINING_WINDOW_START = datetime(2025, 7, 1, tzinfo=timezone.utc)
TRAINING_WINDOW_END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

FX_COST_MODEL_JPY = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.01)
CRYPTO_COST_MODEL = CostModel(commission_pct=0.001, slippage_pct=0.0005)
FX_PPY = 252 * 24
CRYPTO_PPY = 365 * 24

INSTRUMENT_INFO = {
    "GBP_JPY": ("H1", FX_COST_MODEL_JPY, FX_PPY),
    "USD_JPY": ("H1", FX_COST_MODEL_JPY, FX_PPY),
    "LTC/USDT": ("1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    "LTC/GBP": ("1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
}

# The exact 17 persisted Tier 4 survivors from the 2025 H2 sweep
# (data/full_sweep_runs.db) - queried directly, not retyped from memory.
SURVIVORS = [
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "LTC/GBP", {"confirmation_window": 20}),
    ("RsiStrategy", RsiStrategy, "LTC/GBP", {"period": 10, "oversold": 30, "overbought": 70}),
    ("RsiStrategy", RsiStrategy, "LTC/GBP", {"period": 10, "oversold": 30, "overbought": 80}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "LTC/USDT", {"confirmation_window": 20}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "LTC/USDT", {"confirmation_window": 5}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "LTC/USDT", {"confirmation_window": 10}),
    ("RsiStrategy", RsiStrategy, "LTC/USDT", {"period": 14, "oversold": 25, "overbought": 70}),
    ("RsiStrategy", RsiStrategy, "LTC/USDT", {"period": 14, "oversold": 30, "overbought": 75}),
    ("RsiStrategy", RsiStrategy, "USD_JPY", {"period": 10, "oversold": 30, "overbought": 80}),
    ("RsiStrategy", RsiStrategy, "USD_JPY", {"period": 10, "oversold": 25, "overbought": 80}),
    ("RsiStrategy", RsiStrategy, "USD_JPY", {"period": 14, "oversold": 30, "overbought": 75}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "GBP_JPY", {"confirmation_window": 20}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, "GBP_JPY", {"confirmation_window": 10}),
    ("RsiStrategy", RsiStrategy, "GBP_JPY", {"period": 10, "oversold": 30, "overbought": 75}),
    ("RsiStrategy", RsiStrategy, "GBP_JPY", {"period": 10, "oversold": 20, "overbought": 75}),
    ("RsiStrategy", RsiStrategy, "GBP_JPY", {"period": 10, "oversold": 30, "overbought": 80}),
    ("RsiStrategy", RsiStrategy, "GBP_JPY", {"period": 10, "oversold": 25, "overbought": 80}),
]


def replay_one(strat_factory, params, candles, cost_model, periods_per_year):
    """Re-runs one frozen candidate on one window and judges it against
    the SAME gate values Tier 0/Tier 4 originally used - not a looser
    check."""
    units_per_trade = TARGET_NOTIONAL / candles[0].mid_close
    strategy = strat_factory(**params)
    engine = BacktestEngine(strategy, cost_model, STARTING_BALANCE, units_per_trade)
    result = engine.run(candles)
    metrics = compute_metrics(result, periods_per_year=periods_per_year)

    if metrics.total_trades < MIN_CLOSED_TRADES:
        return "FAIL", f"only {metrics.total_trades} trades (< {MIN_CLOSED_TRADES})"
    if metrics.total_return_pct <= 0:
        return "FAIL", f"return {metrics.total_return_pct:+.2f}% <= 0"
    if metrics.max_drawdown_pct >= MAX_DRAWDOWN_CEILING_PCT:
        return "FAIL", f"drawdown {metrics.max_drawdown_pct:.2f}% >= {MAX_DRAWDOWN_CEILING_PCT}%"

    boot = run_bootstrap_from_result(result, method="resample", n_iterations=2000, seed=42)
    ci_lo, _ci_hi = boot.confidence_interval("total_return_pct", 0.90)
    prob_loss = boot.probability_of_loss()
    if ci_lo > 0 and prob_loss < PROBABILITY_OF_LOSS_CEILING_PCT:
        return "PASS", f"CI-90 lower bound {ci_lo:+.2f}%, prob_loss {prob_loss:.1f}%, {metrics.total_trades} trades"
    return "FAIL", f"CI-90 lower bound {ci_lo:+.2f}%, prob_loss {prob_loss:.1f}%"


def main():
    store = FxStore(database_url=HISTORY_DB)

    windows_by_instrument = {
        instrument: fixed_month_periods(
            store, instrument, gran, window_months=WINDOW_SIZE_MONTHS,
            exclude_start=TRAINING_WINDOW_START, exclude_end=TRAINING_WINDOW_END,
        )
        for instrument, (gran, _cost, _ppy) in INSTRUMENT_INFO.items()
    }
    for instrument, windows in windows_by_instrument.items():
        label = ", ".join(f"{w.label}[{w.start.date()}..{w.end.date()}]" for w in windows) or "none found"
        print(f"{instrument}: {len(windows)} out-of-training window(s) - {label}")
    if not any(windows_by_instrument.values()):
        print("\nNo out-of-training data found for any survivor instrument yet - "
              "run fetch_sandbox_data.py / ingest_kraken_gbp_csv.py first.")
        return

    print(f"\n{'strategy':<26} {'instrument':<10} {'window':<10} {'params':<45} {'verdict':>7}  reason")
    print("-" * 145)
    tally: dict[tuple, list[str]] = {}
    for strat_name, strat_factory, instrument, params in SURVIVORS:
        granularity, cost_model, periods_per_year = INSTRUMENT_INFO[instrument]
        key = (strat_name, instrument, str(params))
        tally[key] = []
        for window in windows_by_instrument[instrument]:
            candles = store.get_candles(instrument, granularity, start=window.start.isoformat(), end=window.end.isoformat())
            if not candles:
                continue
            verdict, reason = replay_one(strat_factory, params, candles, cost_model, periods_per_year)
            tally[key].append(verdict)
            print(f"{strat_name:<26} {instrument:<10} {window.label:<10} {str(params):<45} {verdict:>7}  {reason}")

    print("-" * 145)
    print("Per-candidate summary (pass_count/windows_tested):")
    for (strat_name, instrument, params_str), verdicts in tally.items():
        passes = verdicts.count("PASS")
        print(f"  {strat_name:<26} {instrument:<10} {params_str:<45} {passes}/{len(verdicts)}"
              + ("  <- no windows available yet" if not verdicts else ""))

    print("\nNext: run run_full_sweep.py against data/sandbox_history.db (optionally sliced to one")
    print("window via FxStore.get_candles) for the independent re-discovery check.")


if __name__ == "__main__":
    main()
