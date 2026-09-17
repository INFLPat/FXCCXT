"""
backtest/service_tiers.py

Subscription service tiers (bronze/silver/gold) - deliberately a SEPARATE
axis from the validation cost-tier (Tier 0-4, VALIDATION_HIERARCHY.md).

Cost-tier answers "how expensive is this to compute, and when in the
pipeline can we afford to compute it for every candidate vs. only
survivors". Service-tier answers "which subscribers are entitled to see
it" - a pure display/entitlement decision, made after everything is
already computed (computing a metric is cheap; per-instrument backtests
are what's expensive, and those run once regardless of who's watching).

They are NOT the same axis, on purpose, per this project's own examples:
a Sortino ratio is cheap to compute (cost-tier: free, alongside Tier 1) but
might still be gated to silver+ (service-tier) purely as a product
decision. Conversely bootstrap resampling is genuinely expensive (cost-
tier: Tier 4) but the person may want it available to silver, not just
gold. Keeping this mapping in one small, deliberately dumb module - not
threaded through compute_metrics()/bootstrap.py/the orchestrator as
inline `if tier == ...` checks - is what makes it revisable later without
touching computation code, per the explicit request that this be flexible
for a not-yet-designed subscription model.

THIS IS A STARTING ALLOCATION, NOT A DECISION. Every metric is placed at a
reasonable-guess tier below; expect this to be revised once the actual
subscription tiers are designed in a dedicated conversation. Changing an
entry here changes nothing about how any metric is computed.
"""

from enum import IntEnum


class ServiceTier(IntEnum):
    """IntEnum so `tier >= ServiceTier.SILVER` orders naturally - gold
    subscribers see everything silver/bronze see, plus gold-only fields."""

    BRONZE = 0
    SILVER = 1
    GOLD = 2


# metric field name (must match a Metrics dataclass field, or a
# BootstrapResult-derived key, or "rolling_metrics"/"portfolio_analysis")
# -> minimum ServiceTier required to see it.
METRIC_SERVICE_TIER: dict[str, ServiceTier] = {
    # Bronze: the headline numbers any subscriber should see.
    "total_return_pct": ServiceTier.BRONZE,
    "total_trades": ServiceTier.BRONZE,
    "win_rate_pct": ServiceTier.BRONZE,
    "profit_factor": ServiceTier.BRONZE,
    "max_drawdown_pct": ServiceTier.BRONZE,
    "total_commission_paid": ServiceTier.BRONZE,
    "avg_win": ServiceTier.BRONZE,
    "avg_loss": ServiceTier.BRONZE,

    # Silver: risk-adjusted ratios and drawdown shape - the metrics that
    # distinguish "profitable" from "profitable and survivable".
    "sharpe_ratio": ServiceTier.SILVER,
    "sortino_ratio": ServiceTier.SILVER,
    "calmar_ratio": ServiceTier.SILVER,
    "recovery_factor": ServiceTier.SILVER,
    "max_drawdown_duration": ServiceTier.SILVER,
    "avg_drawdown_duration": ServiceTier.SILVER,
    "still_in_drawdown_at_end": ServiceTier.SILVER,
    "trade_expectancy": ServiceTier.SILVER,
    "trade_expectancy_pct": ServiceTier.SILVER,
    "payoff_ratio": ServiceTier.SILVER,
    "exposure_pct": ServiceTier.SILVER,
    "buy_hold_return_pct": ServiceTier.SILVER,
    "alpha_vs_buy_hold_pct": ServiceTier.SILVER,

    # Gold: tail-risk / distribution-shape metrics and anything needing
    # real interpretation care (Kelly) or extra compute (rolling, portfolio).
    "omega_ratio": ServiceTier.GOLD,
    "ulcer_index": ServiceTier.GOLD,
    "downside_deviation": ServiceTier.GOLD,
    "max_consecutive_wins": ServiceTier.GOLD,
    "max_consecutive_losses": ServiceTier.GOLD,
    "avg_trade_duration_hours": ServiceTier.GOLD,
    "cost_drag_pct": ServiceTier.GOLD,
    "var_95": ServiceTier.GOLD,
    "cvar_95": ServiceTier.GOLD,
    "tail_ratio": ServiceTier.GOLD,
    "skewness": ServiceTier.GOLD,
    "kurtosis": ServiceTier.GOLD,
    "kelly_fraction": ServiceTier.GOLD,
}

# Whole-feature gates, for things that aren't a single Metrics field.
BOOTSTRAP_METHOD_SERVICE_TIER: dict[str, ServiceTier] = {
    "resample": ServiceTier.SILVER,
    "shuffle": ServiceTier.SILVER,   # per the person's own example: shuffling need not be gold-only
}
ROLLING_METRICS_SERVICE_TIER = ServiceTier.GOLD
PORTFOLIO_ANALYSIS_SERVICE_TIER = ServiceTier.GOLD

DEFAULT_SERVICE_TIER = ServiceTier.GOLD  # fields with no explicit entry stay hidden below gold, not silently bronze-visible


def metrics_for_tier(metrics_dict: dict, tier: ServiceTier) -> dict:
    """Filters a Metrics.as_dict()-shaped dict down to what `tier` is
    entitled to see. A field missing from METRIC_SERVICE_TIER is treated
    as gold-only (DEFAULT_SERVICE_TIER) rather than silently bronze-
    visible - a new metric added to Metrics but not yet classified here
    should fail closed, not leak into the free tier."""
    assert isinstance(tier, ServiceTier), f"tier must be a ServiceTier, got {type(tier)}"
    assert metrics_dict, "metrics_for_tier requires a non-empty dict"
    return {
        name: value for name, value in metrics_dict.items()
        if METRIC_SERVICE_TIER.get(name, DEFAULT_SERVICE_TIER) <= tier
    }
