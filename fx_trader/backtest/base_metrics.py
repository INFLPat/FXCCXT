"""
backtest/base_metrics.py

Named candidate BASE METRICS for Phase 1 Step 2's scoring-engine weights
(CONFIDENCE_SIZING_DESIGN.md Section 4/5). Kept separate from metrics.py
(which computes per-backtest raw numbers) for the same reason rolling.py/
portfolio.py/service_tiers.py are separate: a different concern - this
module COMBINES already-computed metrics into scoring candidates, it
doesn't compute anything from a BacktestResult directly.

See SUBTASK_BASE_METRICS.md for the full role categorization of all ~25
Metrics fields and the reasoning behind which cross-role blends are worth
building. Short version: Metrics fields fall into five roles -
MAGNITUDE (how big is the edge), RELIABILITY (how much to trust the
magnitude number), DRAWDOWN-SHAPE (how painful is the ride), COST-REALISM
(how much of the edge is real vs. eaten by friction), and SIZING-
INFORMATIONAL-ONLY (Kelly - not a weight input). The design doc's existing
leaning (point estimate x trust discount) is MAGNITUDE x RELIABILITY,
already validated this session via the CI-lower-bound fix
(SUBTASK_CROSS_STRATEGY_CORRELATION.md's sibling defect). This module adds
the other meaningful cross-role blend that was previously a total gap:
MAGNITUDE x COST-REALISM - nothing in the current design discounted a
scoring weight for cost drag, even though a strategy can have a great
point estimate while a large share of its edge is already being eaten by
commission/slippage.

Every function here returns None when it can't be computed (too few
trades, zero variance, etc.) rather than raising - a scoring-weight
candidate that can't be computed for a given (strategy, instrument) should
read as "no confident weight," not crash the whole scoring pass.
"""

import math

from backtest.engine import Trade


def cost_adjusted(magnitude_value: float | None, cost_drag_pct: float | None) -> float | None:
    """Generic MAGNITUDE x COST-REALISM blend - discounts ANY magnitude
    metric (a CI lower bound, trade_expectancy_pct, Sortino, ...) by how
    much of the strategy's gross edge is already being eaten by costs.
    cost_drag_pct is a percentage (0-100+, can exceed 100 if costs exceed
    gross profit) - discount factor is (1 - cost_drag_pct/100), floored at
    0 so a cost-drag-only-loses-money case doesn't flip the sign."""
    if magnitude_value is None or cost_drag_pct is None:
        return None
    discount = max(1 - cost_drag_pct / 100, 0.0)
    return magnitude_value * discount


def effect_size_over_sample_size(closed_trades: list[Trade]) -> float | None:
    """CONFIDENCE_SIZING_DESIGN.md Section 5's explicitly-flagged-as-
    missing candidate: mean_trade_pnl / standard_error, i.e. a classical
    t-statistic for "is the mean trade P&L distinguishable from zero,
    given this many trades and this much spread" - a MAGNITUDE x
    RELIABILITY blend via classical statistics rather than via bootstrap
    resampling (bootstrap.py's approach). Genuinely different information
    than the bootstrap CI lower bound: this assumes the trade-P&L
    distribution's variance estimate is representative going forward
    (bootstrap doesn't need that assumption, since it resamples the
    ACTUAL trades) - worth comparing against the bootstrap-based
    candidate on real data specifically because the assumptions differ,
    not because one formula is more "correct" than the other."""
    if len(closed_trades) < 2:
        return None
    pnls = [t.realized_pnl() for t in closed_trades]
    n = len(pnls)
    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    standard_error = std / math.sqrt(n)
    return mean / standard_error


CANDIDATES = {
    "ci_lower_bound": "Bootstrap CI-90 lower bound of total_return_pct - MAGNITUDE x RELIABILITY, "
                       "already in use (the fixed base_metric from SUBTASK_CROSS_STRATEGY_CORRELATION.md's sibling fix)",
    "ci_lower_bound_cost_adjusted": "ci_lower_bound further discounted by cost_drag_pct - adds COST-REALISM as a third factor",
    "expectancy_cost_adjusted": "trade_expectancy_pct discounted by cost_drag_pct - cheaper than bootstrapping, "
                                 "no resampling needed, MAGNITUDE x COST-REALISM only (no reliability discount)",
    "effect_size": "mean_trade_pnl / standard_error - MAGNITUDE x RELIABILITY via classical statistics, "
                   "an independent cross-check against the bootstrap-based candidates",
}
