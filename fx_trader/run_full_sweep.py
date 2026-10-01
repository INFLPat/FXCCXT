# version: 261001
"""
run_full_sweep.py

Full CONTEXT_HANDOFF.md Section 4.1 run: all five strategies against all 16
real sandbox instruments through the Tier 0-4 hierarchy, plus the Section
4.2 checks against CONFIDENCE_SIZING_DESIGN.md:
  - real per-instrument cross-strategy correlation (vs. the synthetic-data
    numbers cited in Section 3: SMA/Bollinger -0.61, RSI/Bollinger +0.34,
    MACD ~uncorrelated)
  - a sanity check of the Section 4 strawman tier_multiplier weighting
    formula against real per-(strategy,instrument) results

CLI ARGS (settled 261001, chat 13 - the repo standard for run scripts; the
window and chat number are recorded in the run_monitor start record, and no
file edit per run keeps git_dirty false):

    python run_full_sweep.py --start 2025-07-01 --end 2025-12-31 --chat 13
    optional: --tag B   --sandbox-db data/sandbox_2025h2.db

--start/--end: inclusive UTC calendar dates; candles are sliced with
    FxStore.get_candles(start=, end=) using bounds in the STORED timestamp
    format (...000000000Z), not isoformat() (+00:00 sorts differently).
--sandbox-db: defaults to the sandbox named by sandbox_config.py.
--tag: optional suffix on the runs DB name (e.g. to tell repeat runs apart).
Runs DB (new file per run, refuses to reuse an existing one):
    data/sweep_runs_<start>to<end>_chat<N>_<run date, London>[_<tag>].db
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backtest.engine import BacktestEngine
from backtest.portfolio import compute_portfolio_metrics
from backtest.sensitivity import run_sensitivity_analysis
from backtest.validation_orchestrator import (
    MAX_TIER3_CANDIDATES,
    _tier0_and_tier1_survivors,
    _tier2_survivors,
    _tier3_finalists,
    _tier4_gate,
)
from data.run_store import RunStore
from data.store import FxStore
from instrument_config import INSTRUMENTS, STARTING_BALANCE, TARGET_NOTIONAL, WINDOW_SIZES
from run_monitor import LONDON, count, item, monitored_run, note
from strategy.bollinger_strategy import BollingerBandsStrategy
from strategy.macd_strategy import MacdStrategy
from strategy.rsi_macd_confluence import RsiMacdConfluenceStrategy
from strategy.rsi_strategy import RsiStrategy
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from strategy.sma_crossover import SmaCrossoverStrategy

STRATEGIES = [
    ("SmaCrossoverStrategy", SmaCrossoverStrategy, {
        "fast_period": [5, 8, 10, 12, 15, 20], "slow_period": [20, 25, 30, 40, 50, 60],
    }, {"fast_period": 10, "slow_period": 30}),
    ("RsiStrategy", RsiStrategy, {
        "period": [10, 14, 20], "oversold": [20, 25, 30], "overbought": [70, 75, 80],
    }, {"period": 14, "oversold": 30.0, "overbought": 70.0}),
    ("MacdStrategy", MacdStrategy, {
        "fast": [8, 12], "slow": [21, 26], "signal": [7, 9], "require_rsi_confirmation": [True, False],
    }, {"fast": 12, "slow": 26, "signal": 9}),
    ("RsiMacdConfluenceStrategy", RsiMacdConfluenceStrategy, {
        "confirmation_window": [0, 3, 5, 10, 20],
    }, {"confirmation_window": 5}),
    ("BollingerBandsStrategy", BollingerBandsStrategy, {
        "period": [10, 15, 20, 25], "num_std": [1.5, 2.0, 2.5],
    }, {"period": 20, "num_std": 2.0}),
]

TIER_MULTIPLIER = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.75, 4: 1.0}
ROLLING_WINDOW = 500


def parse_args(argv: list[str]) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    assert all(isinstance(a, str) for a in argv), "argv entries must be strings"
    parser = argparse.ArgumentParser(description="Full Tier 0-4 sweep over a date window.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive (UTC)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive (UTC)")
    parser.add_argument("--chat", type=int, required=True, help="chat number, used in the runs DB name")
    parser.add_argument("--sandbox-db", default=None, help="sqlite path; default = sandbox_config.py's sandbox")
    parser.add_argument("--tag", default="", help="optional runs-DB name suffix (letters, digits, hyphen)")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    """Validates args and derives bounds/paths. run_date is YYMMDD (London)."""
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    assert start <= end, f"--start {args.start} is after --end {args.end}"

    sandbox_path = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox_path).exists(), f"sandbox DB not found: {sandbox_path} (FxStore would silently create an empty one)"

    suffix = f"_{args.tag}" if args.tag else ""
    runs_path = f"data/sweep_runs_{start:%y%m%d}to{end:%y%m%d}_chat{args.chat}_{run_date}{suffix}.db"
    assert not Path(runs_path).exists(), f"runs DB already exists: {runs_path} - pick a different --tag"
    return {
        "start_ts": start.strftime("%Y-%m-%dT00:00:00.000000000Z"),
        "end_ts": end.strftime("%Y-%m-%dT23:59:59.000000000Z"),
        "sandbox_path": sandbox_path, "sandbox_url": f"sqlite:///{sandbox_path}",
        "runs_path": runs_path, "runs_url": f"sqlite:///{runs_path}",
    }


def run_one(instrument, granularity, cost_model, periods_per_year, candles, strat_name, strat_factory, grid, run_store):
    """Reimplements validation_orchestrator.run_validation_hierarchy but
    captures the intermediate base-metric needed for the Section 4.2
    weighting sanity check (best Tier-1 return, and Tier-4 bootstrap CI
    lower bound for the winning survivor), instead of just tier counts."""
    units_per_trade = TARGET_NOTIONAL / candles[0].mid_close

    sensitivity = run_sensitivity_analysis(
        candles, strat_factory, grid, cost_model=cost_model,
        starting_balance=STARTING_BALANCE, units_per_trade=units_per_trade,
        periods_per_year=periods_per_year,
    )
    tier01 = _tier0_and_tier1_survivors(sensitivity)
    best_tier1_return = max((gp.metrics.total_return_pct for gp in tier01), default=None)

    result = {
        "strategy": strat_name, "instrument": instrument, "grid_size": len(sensitivity.grid_points),
        "tier1": len(tier01), "tier2": 0, "tier3": 0, "tier4": 0,
        "best_tier1_return": best_tier1_return, "best_tier4_ci_lo": None,
        "highest_tier_cleared": 1 if tier01 else 0,
    }
    if not tier01:
        return result

    tier2 = _tier2_survivors(sensitivity, tier01)
    result["tier2"] = len(tier2)
    if not tier2:
        return result
    result["highest_tier_cleared"] = 2

    assert len(tier2) <= MAX_TIER3_CANDIDATES, f"{instrument}/{strat_name}: too many tier2 survivors"
    finalists, combined_oos_return = _tier3_finalists(
        candles, strat_factory, tier2, cost_model, STARTING_BALANCE,
        units_per_trade, periods_per_year, WINDOW_SIZES,
    )
    result["tier3"] = len(finalists)
    if not finalists:
        return result
    result["highest_tier_cleared"] = 3

    # FIX (Section 4.2 weighting-formula defect): capture bootstrap CI-90
    # lower bound for EVERY finalist that reaches this point, whether or
    # not it individually passes the Tier 4 gate - not just passing ones.
    # Previously a rejected finalist fell back to the raw (uncorrected)
    # Tier-1 grid-winner return as base_metric, which could - and did -
    # outscore an actual Tier 4 survivor (BollingerBandsStrategy/LTC_GBP
    # scored 9.96 on raw Tier-1 return vs RsiStrategy/LTC_GBP's real
    # Tier-4 weight of 5.62). Now every tier>=3 candidate uses the SAME
    # metric type (bootstrap CI lower bound), so the tier_multiplier is
    # the only thing doing the "trust less" work - not an inconsistent
    # metric choice underneath it.
    best_finalist_ci_lo = None
    for params in finalists:
        passed, detail = _tier4_gate(
            candles, strat_factory, params, cost_model, STARTING_BALANCE,
            units_per_trade, periods_per_year, ROLLING_WINDOW,
        )
        ci_lo = detail.get("summary", {}).get("ci_90_lo") if "summary" in detail else None
        if ci_lo is not None and (best_finalist_ci_lo is None or ci_lo > best_finalist_ci_lo):
            best_finalist_ci_lo = ci_lo
        if not passed:
            continue
        result["tier4"] += 1
        result["highest_tier_cleared"] = 4
        run_store.record_run(
            detail["result"], strategy_name=strat_name, params=params,
            instrument=instrument, granularity=granularity, cost_model=cost_model.__dict__,
            validation_stage="tier4_bootstrap", metrics=detail["summary"],
            period_start=candles[0].timestamp, period_end=candles[-1].timestamp,
        )
    result["best_tier4_ci_lo"] = best_finalist_ci_lo  # renamed meaning: best finalist CI, tier>=3
    return result


def correlation_for_instrument(candles, cost_model, periods_per_year):
    """Runs every strategy ONCE with a fixed default param set over the
    full candle history and returns their pairwise period-return
    correlation matrix - Section 4.2's check against the synthetic-data
    correlation numbers in CONFIDENCE_SIZING_DESIGN.md Section 3."""
    units_per_trade = TARGET_NOTIONAL / candles[0].mid_close
    returns_by_strategy = {}
    for strat_name, strat_factory, _grid, default_params in STRATEGIES:
        strategy = strat_factory(**default_params)
        engine = BacktestEngine(strategy, cost_model, STARTING_BALANCE, units_per_trade)
        result = engine.run(candles)
        values = [e for _, e in result.equity_curve]
        returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values)) if values[i - 1] != 0]
        returns_by_strategy[strat_name] = returns

    min_len = min(len(r) for r in returns_by_strategy.values())
    aligned = {name: r[:min_len] for name, r in returns_by_strategy.items()}
    return compute_portfolio_metrics(aligned, periods_per_year=periods_per_year)


def _sweep(cfg: dict) -> None:
    assert cfg["start_ts"] < cfg["end_ts"], "window bounds out of order"
    assert cfg["runs_path"] != cfg["sandbox_path"], "runs DB must differ from the sandbox DB"
    store = FxStore(database_url=cfg["sandbox_url"])
    note("sandbox_db", cfg["sandbox_url"])
    note("runs_db", cfg["runs_url"])
    note("window_start_ts", cfg["start_ts"])
    note("window_end_ts", cfg["end_ts"])
    note("n_strategies", len(STRATEGIES))
    note("n_instruments", len(INSTRUMENTS))
    run_store = RunStore(database_url=cfg["runs_url"])

    print(f"Window {cfg['start_ts']} .. {cfg['end_ts']}\nSandbox {cfg['sandbox_path']}\nRuns DB {cfg['runs_path']}")
    print(f"{'strategy':<26} {'instrument':<10} {'grid':>5} {'T1':>4} {'T2':>4} {'T3':>4} {'T4':>4} {'sec':>7}")
    print("-" * 80)

    all_results = []
    corr_matrices = {}
    total_start = time.time()

    for instrument, granularity, cost_model, periods_per_year in INSTRUMENTS:
        candles = store.get_candles(instrument, granularity, start=cfg["start_ts"], end=cfg["end_ts"])
        coverage = f"{len(candles)} candles {candles[0].timestamp} .. {candles[-1].timestamp}" if candles else "0 candles"
        note(f"coverage_{instrument}", coverage)
        print(f"  coverage {instrument}: {coverage}")
        if len(candles) < sum(WINDOW_SIZES):
            print(f"{instrument} SKIPPED - only {len(candles)} candles")
            continue

        for strat_name, strat_factory, grid, _default in STRATEGIES:
            t0 = time.time()
            r = run_one(instrument, granularity, cost_model, periods_per_year, candles, strat_name, strat_factory, grid, run_store)
            elapsed = time.time() - t0
            r["elapsed"] = elapsed
            item(f"{strat_name}/{instrument}", elapsed,
                 tier1=r["tier1"], tier2=r["tier2"], tier3=r["tier3"], tier4=r["tier4"])
            count("strategy_instrument_runs")
            all_results.append(r)
            print(f"{strat_name:<26} {instrument:<10} {r['grid_size']:>5} {r['tier1']:>4} {r['tier2']:>4} "
                  f"{r['tier3']:>4} {r['tier4']:>4} {elapsed:>7.1f}")

        corr_matrices[instrument] = correlation_for_instrument(candles, cost_model, periods_per_year)
        count("candles", len(candles))
        count("instruments")

    total_elapsed = time.time() - total_start
    print("-" * 80)
    print(f"Full sweep: {total_elapsed:.1f}s for {len(all_results)} (strategy, instrument) runs")

    print("\n=== TIER 4 SURVIVORS ===")
    survivors = [r for r in all_results if r["tier4"] > 0]
    note("tier4_survivor_pairs", len(survivors))
    if not survivors:
        print("None.")
    for r in survivors:
        print(f"  {r['strategy']} / {r['instrument']}: {r['tier4']} survivor(s), best CI-90 lower bound = {r['best_tier4_ci_lo']:+.3f}%")

    print("\n=== SECTION 4.2: TIER_MULTIPLIER WEIGHTING SANITY (strawman 0.25/0.5/0.75/1.0) ===")
    print(f"{'strategy':<26} {'instrument':<10} {'tier':>4} {'base_metric':>12} {'weight':>8}")
    for r in sorted(all_results, key=lambda x: (-x["highest_tier_cleared"], x["strategy"], x["instrument"])):
        tier = r["highest_tier_cleared"]
        base = r["best_tier4_ci_lo"] if tier >= 3 else r["best_tier1_return"]
        base_clipped = max(base, 0.0) if base is not None else 0.0
        weight = base_clipped * TIER_MULTIPLIER[tier]
        base_str = f"{base:+.3f}" if base is not None else "n/a"
        print(f"{r['strategy']:<26} {r['instrument']:<10} {tier:>4} {base_str:>12} {weight:>8.4f}")

    print("\n=== SECTION 4.2: CROSS-STRATEGY CORRELATION (real data, per instrument) ===")
    names = [s[0] for s in STRATEGIES]
    short = {n: n.replace("Strategy", "").replace("RsiMacdConfluence", "Confl") for n in names}
    avg_matrix = {a: {b: [] for b in names} for a in names}
    for instrument, pm in corr_matrices.items():
        print(f"\n{instrument}:")
        header = "         " + " ".join(f"{short[n]:>7}" for n in names)
        print(header)
        for a in names:
            row = f"{short[a]:<9}" + " ".join(f"{pm.correlation_matrix[a][b]:>7.2f}" for b in names)
            print(row)
            for b in names:
                avg_matrix[a][b].append(pm.correlation_matrix[a][b])

    print("\nAverage correlation across all instruments:")
    header = "         " + " ".join(f"{short[n]:>7}" for n in names)
    print(header)
    for a in names:
        row = f"{short[a]:<9}" + " ".join(f"{sum(avg_matrix[a][b]) / len(avg_matrix[a][b]):>7.2f}" for b in names)
        print(row)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run(
        "run_full_sweep", chat=args.chat, start=args.start, end=args.end, tag=args.tag,
        sandbox_db=cfg["sandbox_path"], runs_db=cfg["runs_path"],
    ):
        _sweep(cfg)


if __name__ == "__main__":
    main()
