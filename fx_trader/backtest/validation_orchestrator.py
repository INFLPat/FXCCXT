"""
backtest/validation_orchestrator.py

Implements VALIDATION_HIERARCHY.md's Tier 0-4 gate end to end for one
instrument. See VALIDATION_HIERARCHY.md for the full tier list, including
the extended-metrics tiering added this session.

EXTENDED THIS SESSION:
- Tier 4's re-run of each finalist (already happening, to get a concrete
  trade list for bootstrapping) now ALSO feeds backtest/rolling.py - no
  extra BacktestEngine.run() call, just reusing the equity_curve that
  re-run already produced. Rolling metrics are recorded for every
  finalist Tier 4 examines, pass or fail - useful before knowing whether
  a candidate survives bootstrapping.
- Persisted metrics (for Tier 4 survivors only, same as before) now carry
  the bootstrap-derived observed value + 90% CI for every entry in
  bootstrap.TRACKED_METRICS, not just the original three (return, drawdown,
  trade count) - built via a loop over TRACKED_METRICS rather than a fixed
  list, so it stays in sync automatically if that tuple grows later.
- Tier 0/1/2/3 GATING LOGIC (_tier0_and_tier1_survivors, _tier2_survivors,
  _tier3_finalists's pass/fail conditions) is UNCHANGED - the new metrics
  from metrics.py are reported (available in sensitivity's per-point
  Metrics and in the persisted summary), not gated on. Turning any of them
  into a hard gate is a deliberate future decision, not made here - see
  VALIDATION_HIERARCHY.md's note on why.

Thresholds (all revisit-once-you-have-more-data, per VALIDATION_HIERARCHY.md):
MIN_CLOSED_TRADES=20, MAX_DRAWDOWN_CEILING_PCT=20, bootstrap probability-of-
loss ceiling=40%.
"""

from dataclasses import dataclass, field
from typing import Callable

from backtest.bootstrap import TRACKED_METRICS, run_bootstrap_from_result
from backtest.engine import BacktestEngine, CostModel
from backtest.rolling import RollingMetricsResult, compute_rolling_metrics
from backtest.sensitivity import GridPointResult, SensitivityResult, run_sensitivity_analysis
from backtest.walk_forward import run_walk_forward
from data.run_store import RunStore
from data.store import Candle
from strategy.base import Strategy

MIN_CLOSED_TRADES = 20
MAX_DRAWDOWN_CEILING_PCT = 20.0
MAX_GRID_COMBINATIONS = 200
MAX_TIER3_CANDIDATES = 50
PROBABILITY_OF_LOSS_CEILING_PCT = 40.0
DEFAULT_ROLLING_WINDOW = 500  # periods (candles) - see VALIDATION_HIERARCHY.md's note on why this isn't per-grid-point


@dataclass
class OrchestratorResult:
    instrument: str
    grid_size: int
    tier0_survivors: int
    tier1_survivors: int
    tier2_survivors: int
    tier3_finalists: int
    tier4_survivors: int
    persisted_run_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Keyed by str(params) - one entry per Tier 3 finalist examined by Tier
    # 4, whether or not it ultimately passed. Full series, not just a
    # summary - this dict is NOT persisted to RunStore verbatim (see
    # module docstring); it's here for in-session inspection/plotting.
    rolling_metrics: dict[str, RollingMetricsResult] = field(default_factory=dict)


def _tier0_and_tier1_survivors(sensitivity: SensitivityResult) -> list[GridPointResult]:
    assert sensitivity.grid_points, "sensitivity grid must not be empty"
    assert MIN_CLOSED_TRADES > 0, "MIN_CLOSED_TRADES must be a positive floor"
    survivors = []
    for gp in sensitivity.valid_points():
        m = gp.metrics
        if m.total_trades < MIN_CLOSED_TRADES:
            continue
        if m.total_return_pct <= 0:
            continue
        if m.profit_factor is not None and m.profit_factor <= 1:
            continue
        if m.max_drawdown_pct >= MAX_DRAWDOWN_CEILING_PCT:
            continue
        survivors.append(gp)
    return survivors


def _tier2_survivors(sensitivity: SensitivityResult, tier1: list[GridPointResult]) -> list[GridPointResult]:
    assert sensitivity.grid_points, "sensitivity grid must not be empty"
    all_scores = sensitivity.all_scores()
    if len(all_scores) < 2:
        return []
    mean = sum(all_scores) / len(all_scores)
    variance = sum((s - mean) ** 2 for s in all_scores) / len(all_scores)
    std = variance ** 0.5
    survivors = []
    for gp in tier1:
        gap = sensitivity.neighbor_gap(gp.params)
        if gap is not None and gap <= std:
            survivors.append(gp)
    return survivors


def _tier3_finalists(
    candles: list[Candle], strategy_factory: Callable[..., Strategy], tier2: list[GridPointResult],
    cost_model: CostModel, starting_balance: float, units_per_trade: float,
    periods_per_year: int, window_sizes: tuple[int, int],
) -> tuple[list[dict], float]:
    assert tier2, "tier3 requires at least one Tier 2 survivor"
    assert len(window_sizes) == 2, "window_sizes must be (in_sample_size, out_of_sample_size)"
    in_sample_size, out_of_sample_size = window_sizes
    param_grid = [gp.params for gp in tier2]
    result = run_walk_forward(
        candles, strategy_factory, param_grid, in_sample_size=in_sample_size,
        out_of_sample_size=out_of_sample_size, cost_model=cost_model, starting_balance=starting_balance,
        units_per_trade=units_per_trade, periods_per_year=periods_per_year,
    )
    combined_return = result.combined_metrics.total_return_pct
    if combined_return <= 0:
        return [], combined_return
    seen = []
    for w in result.windows:
        if w.chosen_params not in seen:
            seen.append(w.chosen_params)
    return seen, combined_return


def _tier4_gate(
    candles: list[Candle], strategy_factory: Callable[..., Strategy], params: dict,
    cost_model: CostModel, starting_balance: float, units_per_trade: float,
    periods_per_year: int, rolling_window: int,
) -> tuple[bool, dict]:
    """Re-runs one finalist against the full candle history, bootstraps its
    trades, and applies the Tier 4 gate. ALSO computes rolling metrics off
    that same re-run's equity_curve - no extra BacktestEngine.run() call.
    Returns (passed, summary_dict) where summary_dict always carries
    'rolling' (a RollingMetricsResult, possibly with all-None series if
    the history is shorter than rolling_window) regardless of pass/fail."""
    assert candles, "tier4 needs candle history to re-run the finalist"
    assert params, "tier4 needs a non-empty params dict"

    strategy = strategy_factory(**params)
    engine = BacktestEngine(strategy, cost_model, starting_balance, units_per_trade)
    result = engine.run(candles)

    effective_window = min(rolling_window, len(result.equity_curve))
    rolling = (
        compute_rolling_metrics(result.equity_curve, window=effective_window, periods_per_year=periods_per_year)
        if effective_window >= 3 else None
    )

    if len(result.closed_trades) < 2:
        return False, {"reason": "fewer than 2 closed trades - cannot bootstrap", "rolling": rolling}

    boot = run_bootstrap_from_result(result, method="resample", n_iterations=2000, seed=42)
    ci_lo, ci_hi = boot.confidence_interval("total_return_pct", 0.90)
    prob_loss = boot.probability_of_loss()
    passed = ci_lo > 0 and prob_loss < PROBABILITY_OF_LOSS_CEILING_PCT

    metrics_ci = {}
    for name in TRACKED_METRICS:
        try:
            lo, hi = boot.confidence_interval(name, 0.90)
        except ValueError:
            lo, hi = None, None
        metrics_ci[name] = {"observed": boot.observed.get(name), "ci_90_lo": lo, "ci_90_hi": hi}

    summary = {
        "total_return_pct": boot.observed["total_return_pct"],
        "ci_90_lo": ci_lo, "ci_90_hi": ci_hi,
        "probability_of_loss_pct": prob_loss,
        "max_drawdown_pct": boot.observed["max_drawdown_pct"],
        "total_trades": len(result.closed_trades),
        "metrics_ci": metrics_ci,
        "rolling_summary": rolling.summary_stats() if rolling is not None else None,
    }
    detail = {"result": result, "summary": summary, "rolling": rolling}
    if not passed:
        detail["reason"] = "CI or loss-probability gate failed"
    return passed, detail


def run_validation_hierarchy(
    instrument: str, candles: list[Candle], strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list], cost_model: CostModel, starting_balance: float,
    units_per_trade: float, periods_per_year: int, window_sizes: tuple[int, int],
    run_store: RunStore, granularity: str, rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> OrchestratorResult:
    """Drives one instrument through Tiers 0-4, persisting only Tier 4
    survivors. `rolling_window` (default 500 periods) is only used for the
    Tier 3 finalists that reach Tier 4 - see module docstring for why it's
    not computed for the full grid."""
    assert candles, f"{instrument}: no candles to validate against"
    assert param_grid, f"{instrument}: param_grid must not be empty"

    grid_size = 1
    for values in param_grid.values():
        grid_size *= len(values)
    assert grid_size <= MAX_GRID_COMBINATIONS, (
        f"{instrument}: grid of {grid_size} combinations exceeds the explicit ceiling of "
        f"{MAX_GRID_COMBINATIONS} - narrow param_grid"
    )

    sensitivity = run_sensitivity_analysis(
        candles, strategy_factory, param_grid, cost_model=cost_model,
        starting_balance=starting_balance, units_per_trade=units_per_trade, periods_per_year=periods_per_year,
    )

    tier01 = _tier0_and_tier1_survivors(sensitivity)
    notes = [f"Tier 0+1: {len(tier01)}/{grid_size} combinations survived (trades>={MIN_CLOSED_TRADES}, "
             f"return>0, profit_factor>1 or None, drawdown<{MAX_DRAWDOWN_CEILING_PCT}%)"]

    tier2 = _tier2_survivors(sensitivity, tier01) if tier01 else []
    notes.append(f"Tier 2 (plateau check): {len(tier2)}/{len(tier01)} survived")

    outcome = OrchestratorResult(
        instrument=instrument, grid_size=grid_size, tier0_survivors=len(tier01), tier1_survivors=len(tier01),
        tier2_survivors=len(tier2), tier3_finalists=0, tier4_survivors=0, notes=notes,
    )
    if not tier2:
        return outcome

    assert len(tier2) <= MAX_TIER3_CANDIDATES, (
        f"{instrument}: {len(tier2)} Tier 2 survivors exceeds the explicit ceiling of "
        f"{MAX_TIER3_CANDIDATES} for walk-forward"
    )
    finalists, combined_oos_return = _tier3_finalists(
        candles, strategy_factory, tier2, cost_model, starting_balance,
        units_per_trade, periods_per_year, window_sizes,
    )
    outcome.tier3_finalists = len(finalists)
    outcome.notes.append(
        f"Tier 3 (walk-forward): combined OOS return {combined_oos_return:+.2f}%, "
        f"{len(finalists)} unique finalist param set(s)"
    )
    if not finalists:
        return outcome

    for params in finalists:
        passed, detail = _tier4_gate(
            candles, strategy_factory, params, cost_model, starting_balance,
            units_per_trade, periods_per_year, rolling_window,
        )
        if detail.get("rolling") is not None:
            outcome.rolling_metrics[str(params)] = detail["rolling"]

        if not passed:
            outcome.notes.append(f"Tier 4 REJECTED {params}: {detail.get('reason', 'gate failed')}")
            continue
        outcome.tier4_survivors += 1
        run_id = run_store.record_run(
            detail["result"], strategy_name=strategy_factory.__name__, params=params,
            instrument=instrument, granularity=granularity, cost_model=cost_model.__dict__,
            validation_stage="tier4_bootstrap", metrics=detail["summary"],
            period_start=candles[0].timestamp, period_end=candles[-1].timestamp,
        )
        outcome.persisted_run_ids.append(run_id)
        outcome.notes.append(f"Tier 4 PASSED {params}: persisted as {run_id}")

    return outcome
