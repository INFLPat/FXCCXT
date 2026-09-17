"""
backtest/rolling.py

Rolling-window performance/risk diagnostics over an equity curve: rolling
Sharpe, rolling Sortino, rolling max-drawdown-within-window. A DIAGNOSTIC,
not a validation-hierarchy gate - answers "did this candidate's edge hold
up steadily over time, or was it one good patch carrying an otherwise flat
line", which a single scalar Sharpe/Sortino over the whole period can hide.

COST-TIER PLACEMENT (see VALIDATION_HIERARCHY.md): computing this for ONE
candidate is cheap - O(n * window), same order of cheapness as the rest of
metrics.py. What is NOT cheap is computing and STORING it for every point
of a large grid: 200 grid points x 16 instruments x a full rolling series
each is a lot of data for something that's a chart, not a gate. So
validation_orchestrator.py only calls this for Tier 3 finalists (a
handful of survivors), never the full Tier 1 grid - kept in its own module
specifically so that boundary is a one-line import decision, not something
buried inside metrics.py.

DELIBERATELY O(n * window), not a true streaming O(n) sliding-window
algorithm: this only ever runs on a handful of finalists (not the full
grid), so the simpler, obviously-correct implementation was chosen over a
faster but fiddlier one - same tradeoff call already made elsewhere in this
project (see sensitivity.py's docstring on concurrent.futures).

SERVICE-TIER: gold-only by default (service_tiers.ROLLING_METRICS_SERVICE_TIER)
- an independent decision from the above, see service_tiers.py.
"""

import math
from dataclasses import dataclass


@dataclass
class RollingMetricsResult:
    window: int
    timestamps: list[str]
    rolling_sharpe: list[float | None]
    rolling_sortino: list[float | None]
    rolling_max_drawdown_pct: list[float | None]

    def latest(self) -> dict:
        """The most recent DEFINED value of each series - a single
        "current" number for a dashboard, rather than the full series."""
        return {
            "rolling_sharpe": self._last_defined(self.rolling_sharpe),
            "rolling_sortino": self._last_defined(self.rolling_sortino),
            "rolling_max_drawdown_pct": self._last_defined(self.rolling_max_drawdown_pct),
        }

    def summary_stats(self) -> dict:
        """min/max/mean of each series, None-filtered - compact enough to
        persist (e.g. into RunStore's JSON metrics blob) without storing
        the full per-candle series for every survivor."""
        return {
            "rolling_sharpe": self._stats(self.rolling_sharpe),
            "rolling_sortino": self._stats(self.rolling_sortino),
            "rolling_max_drawdown_pct": self._stats(self.rolling_max_drawdown_pct),
        }

    @staticmethod
    def _last_defined(series: list[float | None]) -> float | None:
        for v in reversed(series):
            if v is not None:
                return v
        return None

    @staticmethod
    def _stats(series: list[float | None]) -> dict:
        values = [v for v in series if v is not None]
        if not values:
            return {"min": None, "max": None, "mean": None}
        return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}


def compute_rolling_metrics(
    equity_curve: list[tuple[str, float]], window: int, periods_per_year: int = 252,
) -> RollingMetricsResult:
    """
    One value per equity_curve point from index `window - 1` onward -
    points before the first full window are None (not dropped), so every
    returned series stays index-aligned 1:1 with equity_curve for plotting.
    """
    assert equity_curve, "compute_rolling_metrics requires a non-empty equity_curve"
    assert window >= 3, "window must be at least 3 (need >=2 returns inside it to compute a ratio)"
    assert periods_per_year > 0, "periods_per_year must be positive"

    timestamps = [ts for ts, _ in equity_curve]
    n = len(equity_curve)
    rolling_sharpe: list[float | None] = [None] * n
    rolling_sortino: list[float | None] = [None] * n
    rolling_dd: list[float | None] = [None] * n

    for end in range(window - 1, n):
        window_slice = equity_curve[end - window + 1: end + 1]
        rolling_sharpe[end] = _windowed_sharpe(window_slice, periods_per_year)
        rolling_sortino[end] = _windowed_sortino(window_slice, periods_per_year)
        rolling_dd[end] = _windowed_max_drawdown_pct(window_slice)

    return RollingMetricsResult(
        window=window, timestamps=timestamps, rolling_sharpe=rolling_sharpe,
        rolling_sortino=rolling_sortino, rolling_max_drawdown_pct=rolling_dd,
    )


def _windowed_returns(window_slice: list[tuple[str, float]]) -> list[float]:
    values = [e for _, e in window_slice]
    return [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values)) if values[i - 1] != 0
    ]


def _windowed_sharpe(window_slice: list[tuple[str, float]], periods_per_year: int) -> float | None:
    returns = _windowed_returns(window_slice)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)


def _windowed_sortino(window_slice: list[tuple[str, float]], periods_per_year: int, mar: float = 0.0) -> float | None:
    returns = _windowed_returns(window_slice)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    downside_sq = sum(min(r - mar, 0) ** 2 for r in returns)
    downside_dev = math.sqrt(downside_sq / len(returns))
    if downside_dev == 0:
        return None
    return ((mean - mar) / downside_dev) * math.sqrt(periods_per_year)


def _windowed_max_drawdown_pct(window_slice: list[tuple[str, float]]) -> float | None:
    if not window_slice:
        return None
    peak = window_slice[0][1]
    max_dd = 0.0
    for _, equity in window_slice:
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd
