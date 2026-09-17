"""
backtest/portfolio.py

Cross-instrument portfolio diagnostics: a correlation matrix between
instruments' period returns, plus simple equal-weight portfolio-level
metrics (portfolio volatility/Sharpe, a diversification ratio).

THIS IS THE GENUINELY EXPENSIVE TIER - not because any one calculation is
slow (it's all O(n) per pair), but because it needs MULTIPLE instruments'
return series together, at the same points in time. That's a different
shape of question from "did this one candidate graduate the hierarchy",
so this stays a standalone module you call explicitly on whatever
instrument set you choose, rather than a step wired into
validation_orchestrator.py's per-instrument gate.

CURRENT STATUS (see CONTEXT_HANDOFF.md): 0/16 sandbox instruments have a
Tier 4 survivor yet, so there's nothing downstream of the hierarchy to feed
this. It's still useful run directly against raw instrument returns -
"how correlated are the 4 GBP-crypto pairs with each other and with FX
majors" is informative on its own, independent of whether any SMA-
crossover parameter set ever clears Tier 4.

ALIGNMENT IS THE CALLER'S JOB: every instrument's return list must be the
same length and index-aligned to the same timestamps, or the correlation
is silently wrong. This module has no way to detect that on its own -
FxStore.get_candles_multi() returns per-instrument lists that are NOT
guaranteed aligned (Kraken only records hours with an actual trade; OANDA/
Binance calendars differ too) - this is the same multi-instrument
timestamp-alignment problem already flagged as an open item in
CONTEXT_HANDOFF.md for the future cross-pair/pairs-trading work, not
solved here.

SERVICE-TIER: gold-only by default (service_tiers.PORTFOLIO_ANALYSIS_SERVICE_TIER).
"""

import math
from dataclasses import dataclass


@dataclass
class PortfolioMetricsResult:
    instruments: list[str]
    correlation_matrix: dict[str, dict[str, float]]
    equal_weight_return_pct: float          # mean per-period return of the weighted portfolio, in %
    equal_weight_volatility_pct: float      # per-period std dev of the weighted portfolio's returns, in %
    equal_weight_sharpe: float | None       # annualized - see periods_per_year note on compute_portfolio_metrics
    diversification_ratio: float | None     # weighted avg of individual vols / portfolio vol. 1.0 = no benefit, >1 = real benefit

    def most_correlated_pair(self) -> tuple[str, str, float] | None:
        return self._extreme_pair(want_max=True)

    def least_correlated_pair(self) -> tuple[str, str, float] | None:
        return self._extreme_pair(want_max=False)

    def _extreme_pair(self, want_max: bool) -> tuple[str, str, float] | None:
        best = None
        for a in self.instruments:
            for b in self.instruments:
                if a >= b:
                    continue
                corr = self.correlation_matrix[a][b]
                if best is None or (corr > best[2] if want_max else corr < best[2]):
                    best = (a, b, corr)
        return best


def compute_portfolio_metrics(
    returns_by_instrument: dict[str, list[float]],
    weights: dict[str, float] | None = None,
    periods_per_year: int = 252,
) -> PortfolioMetricsResult:
    """
    returns_by_instrument: instrument -> PERIOD RETURNS as fractions (not
    prices, not prices-as-percent) - e.g. from returns_from_mid_close()
    below, or a strategy's own equity-curve returns. See module docstring
    on alignment - this function trusts the caller did it.
    weights: defaults to equal-weight if omitted. Must sum to 1.0.
    """
    assert returns_by_instrument, "compute_portfolio_metrics requires at least one instrument"
    instruments = list(returns_by_instrument.keys())
    n = len(returns_by_instrument[instruments[0]])
    assert n >= 2, "need at least 2 period returns per instrument"
    assert all(len(returns_by_instrument[i]) == n for i in instruments), (
        "every instrument must have the SAME number of period returns, index-aligned - "
        "a length mismatch means they weren't aligned before calling this"
    )

    weights = weights or {i: 1 / len(instruments) for i in instruments}
    assert abs(sum(weights.values()) - 1.0) < 1e-6, "weights must sum to 1.0"

    correlation_matrix = _correlation_matrix(returns_by_instrument, instruments)
    portfolio_returns = [
        sum(weights[i] * returns_by_instrument[i][t] for i in instruments) for t in range(n)
    ]
    portfolio_mean = sum(portfolio_returns) / n
    portfolio_vol = _sample_std(portfolio_returns, portfolio_mean)
    equal_weight_sharpe = (
        (portfolio_mean / portfolio_vol) * math.sqrt(periods_per_year) if portfolio_vol > 0 else None
    )

    individual_vols = {
        i: _sample_std(returns_by_instrument[i], sum(returns_by_instrument[i]) / n) for i in instruments
    }
    weighted_avg_vol = sum(weights[i] * individual_vols[i] for i in instruments)
    diversification_ratio = (weighted_avg_vol / portfolio_vol) if portfolio_vol > 0 else None

    return PortfolioMetricsResult(
        instruments=instruments, correlation_matrix=correlation_matrix,
        equal_weight_return_pct=portfolio_mean * 100, equal_weight_volatility_pct=portfolio_vol * 100,
        equal_weight_sharpe=equal_weight_sharpe, diversification_ratio=diversification_ratio,
    )


def returns_from_mid_close(candles: list) -> list[float]:
    """Simple period-over-period mid_close % change, as a fraction (the
    format compute_portfolio_metrics expects) - NOT timestamp-aware. See
    module docstring: align candle lists across instruments before calling
    this, this function can't detect a misaligned calendar on its own."""
    assert len(candles) >= 2, "returns_from_mid_close needs at least 2 candles"
    closes = [c.mid_close for c in candles]
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes)) if closes[i - 1] != 0
    ]


def _sample_std(values: list[float], mean: float) -> float:
    assert len(values) >= 2, "_sample_std requires at least 2 values"
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _correlation_matrix(
    returns_by_instrument: dict[str, list[float]], instruments: list[str],
) -> dict[str, dict[str, float]]:
    assert instruments, "_correlation_matrix requires at least one instrument"
    matrix: dict[str, dict[str, float]] = {a: {} for a in instruments}
    for a in instruments:
        for b in instruments:
            matrix[a][b] = 1.0 if a == b else _pearson(returns_by_instrument[a], returns_by_instrument[b])
    return matrix


def _pearson(x: list[float], y: list[float]) -> float:
    assert len(x) == len(y), "_pearson requires equal-length series"
    assert len(x) >= 2, "_pearson requires at least 2 points"
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((v - mean_x) ** 2 for v in x)
    var_y = sum((v - mean_y) ** 2 for v in y)
    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom
