from enum import IntEnum


class ServiceTier(IntEnum):
    BRONZE = 0
    SILVER = 1
    GOLD = 2


METRIC_SERVICE_TIER: dict[str, ServiceTier] = {
    "total_return_pct": ServiceTier.BRONZE,
    "total_trades": ServiceTier.BRONZE,
    "win_rate_pct": ServiceTier.BRONZE,
    "profit_factor": ServiceTier.BRONZE,
    "max_drawdown_pct": ServiceTier.BRONZE,
    "total_commission_paid": ServiceTier.BRONZE,
    "avg_win": ServiceTier.BRONZE,
    "avg_loss": ServiceTier.BRONZE,
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

BOOTSTRAP_METHOD_SERVICE_TIER: dict[str, ServiceTier] = {
    "resample": ServiceTier.SILVER,
    "shuffle": ServiceTier.SILVER,
}
ROLLING_METRICS_SERVICE_TIER = ServiceTier.GOLD
PORTFOLIO_ANALYSIS_SERVICE_TIER = ServiceTier.GOLD
# Period-comparison visualization (visualize_period_comparison.py,
# SUBTASK_VISUALIZATION.md) - static images are cheap and available by
# default; the interactive version is richer (hover, full-resolution
# equity curve, toggle between views) and gated the same way rolling/
# portfolio diagnostics already are, for consistency, not a new pattern.
PERIOD_COMPARISON_STATIC_SERVICE_TIER = ServiceTier.BRONZE
PERIOD_COMPARISON_INTERACTIVE_SERVICE_TIER = ServiceTier.GOLD

DEFAULT_SERVICE_TIER = ServiceTier.GOLD


def metrics_for_tier(metrics_dict: dict, tier: ServiceTier) -> dict:
    assert isinstance(tier, ServiceTier), f"tier must be a ServiceTier, got {type(tier)}"
    assert metrics_dict, "metrics_for_tier requires a non-empty dict"
    return {
        name: value for name, value in metrics_dict.items()
        if METRIC_SERVICE_TIER.get(name, DEFAULT_SERVICE_TIER) <= tier
    }
