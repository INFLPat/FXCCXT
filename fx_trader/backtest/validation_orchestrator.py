"""
backtest/validation_orchestrator.py

Implements VALIDATION_HIERARCHY.md's Tier 0-4 gate end to end for one
instrument: sanity -> light metrics (full grid) -> neighbor-gap plateau
check -> walk-forward -> bootstrap -> persist survivors via RunStore.

Reuses existing, tested tools rather than reimplementing their math:
Tier 0+1 both come from a single run_sensitivity_analysis() call (per
VALIDATION_HIERARCHY.md: "Tier 1 = run the grid, keep only points clearing
the bar" - Tier 0's trade-count floor is just an extra filter on the same
grid, since Metrics.total_trades is already computed there). Tier 2 reuses
that same grid for neighbor_gap(). Tier 3 wraps run_walk_forward(). Tier 4
wraps run_bootstrap_from_result().

RECONCILING A GAP IN VALIDATION_HIERARCHY.md: walk-forward doesn't naturally
produce "one winning candidate" - it selects per-window from the surviving
grid. This orchestrator treats a Tier 3 "finalist" as any param set chosen
as a window winner at least once, provided the walk-forward run's COMBINED
out-of-sample return is positive. Each finalist is then re-run once against
the full candle history (same params/cost model as its Tier 1 grid point)
to get a concrete trade list for Tier 4 bootstrapping.

Thresholds (all revisit-once-you-have-more-data, per VALIDATION_HIERARCHY.md):
MIN_CLOSED_TRADES=20, MAX_DRAWDOWN_CEILING_PCT=20, bootstrap probability-of-
loss ceiling=40%.
"""

from dataclasses import dataclass, field
from typing import Callable

from backtest.bootstrap import run_bootstrap_from_result
from backtest.engine import BacktestEngine, CostModel
from backtest.sensitivity import GridPointResult, SensitivityResult, run_sensitivity_analysis
from backtest.walk_forward import run_walk_forward
from data.run_store import RunStore
from data.store import Candle
from strategy.base import Strategy

MIN_CLOSED_TRADES = 20
MAX_DRAWDOWN_CEILING_PCT = 20.0
MAX_GRID_COMBINATIONS = 200          # explicit ceiling: grid_size must not exceed this
MAX_TIER3_CANDIDATES = 50            # explicit ceiling: how many Tier 1+2 survivors can enter walk-forward
PROBABILITY_OF_LOSS_CEILING_PCT = 40.0


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


def _tier0_and_tier1_survivors(sensitivity: SensitivityResult) -> list[GridPointResult]:
    """One grid, two filters: Tier 0's trade-count floor, then Tier 1's
    return/profit-factor/drawdown bar. Returns points clearing both."""
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
    """Plateau check: keep a Tier 1 survivor only if it has a defined
    neighbor_gap (not a grid edge with nothing to compare against) that
    sits within one std dev of the full grid's score spread."""
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
    candles: list[Candle],
    strategy_factory: Callable[..., Strategy],
    tier2: list[GridPointResult],
    cost_model: CostModel,
    starting_balance: float,
    units_per_trade: float,
    periods_per_year: int,
    window_sizes: tuple[int, int],
) -> tuple[list[dict], float]:
    """Walk-forward on the Tier 2 survivor set only. Returns (finalist
    param dicts, combined out-of-sample total_return_pct)."""
    assert tier2, "tier3 requires at least one Tier 2 survivor"
    assert len(window_sizes) == 2, "window_sizes must be (in_sample_size, out_of_sample_size)"

    in_sample_size, out_of_sample_size = window_sizes
    param_grid = [gp.params for gp in tier2]
    result = run_walk_forward(
        candles, strategy_factory, param_grid,
        in_sample_size=in_sample_size, out_of_sample_size=out_of_sample_size,
        cost_model=cost_model, starting_balance=starting_balance,
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
    candles: list[Candle],
    strategy_factory: Callable[..., Strategy],
    params: dict,
    cost_model: CostModel,
    starting_balance: float,
    units_per_trade: float,
) -> tuple[bool, dict]:
    """Re-runs one finalist against the full candle history, bootstraps its
    trades, and applies the Tier 4 gate. Returns (passed, summary_dict)."""
    assert candles, "tier4 needs candle history to re-run the finalist"
    assert params, "tier4 needs a non-empty params dict"

    strategy = strategy_factory(**params)
    engine = BacktestEngine(strategy, cost_model, starting_balance, units_per_trade)
    result = engine.run(candles)
    if len(result.closed_trades) < 2:
        return False, {"reason": "fewer than 2 closed trades - cannot bootstrap"}

    boot = run_bootstrap_from_result(result, method="resample", n_iterations=2000, seed=42)
    ci_lo, ci_hi = boot.confidence_interval("total_return_pct", 0.90)
    prob_loss = boot.probability_of_loss()

    passed = ci_lo > 0 and prob_loss < PROBABILITY_OF_LOSS_CEILING_PCT
    summary = {
        "total_return_pct": boot.observed["total_return_pct"],
        "ci_90_lo": ci_lo, "ci_90_hi": ci_hi,
        "probability_of_loss_pct": prob_loss,
        "max_drawdown_pct": boot.observed["max_drawdown_pct"],
        "total_trades": len(result.closed_trades),
    }
    return passed, {"result": result, "summary": summary, **({} if passed else {"reason": "CI or loss-probability gate failed"})}


def run_validation_hierarchy(
    instrument: str,
    candles: list[Candle],
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list],
    cost_model: CostModel,
    starting_balance: float,
    units_per_trade: float,
    periods_per_year: int,
    window_sizes: tuple[int, int],
    run_store: RunStore,
    granularity: str,
) -> OrchestratorResult:
    """Drives one instrument through Tiers 0-4, persisting only Tier 4
    survivors. See module docstring for how each tier maps onto existing
    tools."""
    assert candles, f"{instrument}: no candles to validate against"
    assert param_grid, f"{instrument}: param_grid must not be empty"

    grid_size = 1
    for values in param_grid.values():
        grid_size *= len(values)
    assert grid_size <= MAX_GRID_COMBINATIONS, (
        f"{instrument}: grid of {grid_size} combinations exceeds the "
        f"explicit ceiling of {MAX_GRID_COMBINATIONS} - narrow param_grid"
    )

    sensitivity = run_sensitivity_analysis(
        candles, strategy_factory, param_grid, cost_model=cost_model,
        starting_balance=starting_balance, units_per_trade=units_per_trade,
        periods_per_year=periods_per_year,
    )

    tier01 = _tier0_and_tier1_survivors(sensitivity)
    notes = [f"Tier 0+1: {len(tier01)}/{grid_size} combinations survived (trades>={MIN_CLOSED_TRADES}, "
             f"return>0, profit_factor>1 or None, drawdown<{MAX_DRAWDOWN_CEILING_PCT}%)"]

    tier2 = _tier2_survivors(sensitivity, tier01) if tier01 else []
    notes.append(f"Tier 2 (plateau check): {len(tier2)}/{len(tier01)} survived")

    outcome = OrchestratorResult(
        instrument=instrument, grid_size=grid_size,
        tier0_survivors=len(tier01), tier1_survivors=len(tier01),
        tier2_survivors=len(tier2), tier3_finalists=0, tier4_survivors=0, notes=notes,
    )
    if not tier2:
        return outcome

    assert len(tier2) <= MAX_TIER3_CANDIDATES, (
        f"{instrument}: {len(tier2)} Tier 2 survivors exceeds the explicit "
        f"ceiling of {MAX_TIER3_CANDIDATES} for walk-forward"
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
        passed, detail = _tier4_gate(candles, strategy_factory, params, cost_model, starting_balance, units_per_trade)
        if not passed:
            outcome.notes.append(f"Tier 4 REJECTED {params}: {detail.get('reason', 'gate failed')}")
            continue
        outcome.tier4_survivors += 1
        run_id = run_store.record_run(
            detail["result"], strategy_name=strategy_factory.__name__, params=params,
            instrument=instrument, granularity=granularity,
            cost_model=cost_model.__dict__, validation_stage="tier4_bootstrap",
            metrics=detail["summary"],
            period_start=candles[0].timestamp, period_end=candles[-1].timestamp,
        )
        outcome.persisted_run_ids.append(run_id)
        outcome.notes.append(f"Tier 4 PASSED {params}: persisted as {run_id}")

    return outcome
