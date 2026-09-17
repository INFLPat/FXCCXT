"""
backtest/bootstrap.py

Bootstrap resampling / Monte Carlo analysis on a backtest's completed trades.

RESAMPLE (with replacement) vs SHUFFLE (permutation) - see original design
notes below, unchanged this session.

EXTENDED THIS SESSION: TRACKED_METRICS grew from 5 fields to include most
of the new metrics.py additions, riding through the SAME resample/shuffle
loop that already existed - genuinely free, no new backtest runs.

DELIBERATELY NOT ADDED, and why (same reasoning that already excluded an
annualized Sharpe here, now applied consistently to its new siblings):
- sortino_ratio, calmar_ratio, omega_ratio: all annualized against
  periods_per_year, which assumes a fixed time interval between
  equity_curve points. _build_synthetic_result() gives one equity point
  PER TRADE, not per candle - "periods_per_year" has no coherent meaning
  on that curve, exactly like the Sharpe case this file already documented.
- exposure_pct: needs BacktestResult.periods_in_market, which
  _build_synthetic_result() never sets (defaults to 0) - reporting it
  would silently read as a constant, meaningless 0% every iteration.
- buy_hold_return_pct / alpha_vs_buy_hold_pct: need the original candles,
  which aren't available at this trade-only stage - always None here.
- still_in_drawdown_at_end: a bool, not a natural fit for the
  percentile/CI numeric API the rest of this module is built around.

max_drawdown_duration / avg_drawdown_duration / ulcer_index ARE included
despite being nominally "time-based" - they're computed from the same
trade-resolution equity curve max_drawdown_pct already used here, so
duration means "trades", not periods/candles. Same caveat that already
applied to max_drawdown_pct, extended consistently rather than singled out.
"""

import math
import random
from dataclasses import dataclass, field

from backtest.engine import BacktestResult, Trade
from backtest.metrics import compute_metrics

TRACKED_METRICS = (
    "total_return_pct", "max_drawdown_pct", "win_rate_pct", "profit_factor", "ending_balance",
    "trade_expectancy", "trade_expectancy_pct", "payoff_ratio", "recovery_factor",
    "ulcer_index", "max_drawdown_duration", "avg_drawdown_duration",
    "var_95", "cvar_95", "tail_ratio", "skewness", "kurtosis", "kelly_fraction",
    "cost_drag_pct", "avg_trade_duration_hours",
    "max_consecutive_wins", "max_consecutive_losses",
)


def _build_synthetic_result(trades: list[Trade], starting_balance: float) -> BacktestResult:
    """Builds a trade-resolution BacktestResult from an already-ordered list
    of closed trades, purely by compounding realized_pnl() in order - for
    feeding into compute_metrics()."""
    balance = starting_balance
    equity_curve = [("start", starting_balance)]
    for i, trade in enumerate(trades):
        balance += trade.realized_pnl()
        equity_curve.append((f"trade_{i + 1}", balance))
    return BacktestResult(
        trades=trades, equity_curve=equity_curve,
        starting_balance=starting_balance, ending_balance=balance,
    )


def _extract(trades: list[Trade], starting_balance: float) -> dict[str, float | None]:
    result = _build_synthetic_result(trades, starting_balance)
    # periods_per_year is a required compute_metrics() arg but unused by
    # anything in TRACKED_METRICS - nothing tracked here is annualized,
    # see module docstring for why.
    m = compute_metrics(result, periods_per_year=252)
    values = m.as_dict()
    values["ending_balance"] = result.ending_balance
    return {name: values[name] for name in TRACKED_METRICS}


@dataclass
class BootstrapResult:
    method: str
    n_iterations: int
    n_trades: int
    starting_balance: float
    observed: dict[str, float | None]
    distributions: dict[str, list[float | None]]

    def _clean(self, metric: str) -> list[float]:
        if metric not in self.distributions:
            raise ValueError(f"'{metric}' was not tracked - choose from {TRACKED_METRICS}")
        return [v for v in self.distributions[metric] if v is not None]

    def percentile(self, metric: str, p: float) -> float:
        values = self._clean(metric)
        if not values:
            raise ValueError(f"no valid (non-None) values for '{metric}' to compute a percentile from")
        s = sorted(values)
        if len(s) == 1:
            return s[0]
        rank = (p / 100) * (len(s) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (rank - lo)

    def confidence_interval(self, metric: str, level: float = 0.90) -> tuple[float, float]:
        tail = (1 - level) / 2 * 100
        return self.percentile(metric, tail), self.percentile(metric, 100 - tail)

    def mean(self, metric: str) -> float:
        values = self._clean(metric)
        return sum(values) / len(values)

    def std(self, metric: str) -> float:
        values = self._clean(metric)
        m = self.mean(metric)
        return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))

    def probability_below(self, metric: str, threshold: float) -> float:
        values = self._clean(metric)
        return sum(1 for v in values if v < threshold) / len(values) * 100

    def probability_of_loss(self) -> float:
        return self.probability_below("total_return_pct", 0.0)

    def observed_percentile_rank(self, metric: str) -> float | None:
        obs = self.observed.get(metric)
        if obs is None:
            return None
        values = self._clean(metric)
        return sum(1 for v in values if v < obs) / len(values) * 100

    def summary(self) -> str:
        assert self.method in ("resample", "shuffle"), f"unknown method: {self.method}"
        assert "total_return_pct" in self.observed, "summary requires observed metrics"
        lines = [
            f"Bootstrap ({self.method}, {self.n_iterations} iterations, {self.n_trades} trades)",
            f"Observed (actual historical order): return {self.observed['total_return_pct']:+.2f}%, "
            f"max drawdown {self.observed['max_drawdown_pct']:.2f}%",
        ]
        if self.method == "resample":
            lines.extend(self._resample_summary_lines())
        else:
            lines.extend(self._shuffle_summary_lines())
        return "\n".join(lines)

    def _resample_summary_lines(self) -> list[str]:
        lo, hi = self.confidence_interval("total_return_pct", 0.90)
        dd_lo, dd_hi = self.confidence_interval("max_drawdown_pct", 0.90)
        return [
            f"90% CI for total return if this process repeated: [{lo:+.2f}%, {hi:+.2f}%]",
            f"Probability of a loss: {self.probability_of_loss():.1f}%",
            f"90% CI for max drawdown: [{dd_lo:.2f}%, {dd_hi:.2f}%]",
        ]

    def _shuffle_summary_lines(self) -> list[str]:
        return_values = self._clean("total_return_pct")
        invariant_ok = (max(return_values) - min(return_values)) < 1e-9
        dd_lo, dd_hi = self.confidence_interval("max_drawdown_pct", 0.90)
        lines = [
            "Total return is order-invariant under shuffling (as expected): "
            + ("confirmed identical across all iterations" if invariant_ok else "WARNING: varied - this should not happen"),
            f"90% CI for max drawdown across all reorderings: [{dd_lo:.2f}%, {dd_hi:.2f}%]",
        ]
        rank = self.observed_percentile_rank("max_drawdown_pct")
        if rank is not None:
            lines.append(f"Observed drawdown sits at the {rank:.0f}th percentile of all reorderings: {self._drawdown_rank_verdict(rank)}")
        return lines

    @staticmethod
    def _drawdown_rank_verdict(rank: float) -> str:
        assert 0 <= rank <= 100, f"percentile rank must be in [0, 100], got {rank}"
        if rank > 75:
            return "the actual sequence was on the PAINFUL end - most reorderings would have hurt less"
        if rank < 25:
            return "the actual sequence was on the FORGIVING end - most reorderings would have hurt more"
        return "unremarkable - roughly in line with a typical reordering"


def run_bootstrap(
    trades: list[Trade], starting_balance: float, method: str = "resample",
    n_iterations: int = 5000, sample_size: int | None = None, seed: int | None = None,
) -> BootstrapResult:
    if method not in ("resample", "shuffle"):
        raise ValueError(f"method must be 'resample' or 'shuffle', got '{method}'")

    closed_trades = [t for t in trades if not t.is_open]
    if len(closed_trades) < 2:
        raise ValueError(f"Need at least 2 closed trades to run bootstrap analysis, got {len(closed_trades)}.")

    rng = random.Random(seed)
    n = len(closed_trades)
    draw_size = sample_size or n

    observed = _extract(closed_trades, starting_balance)
    distributions: dict[str, list] = {name: [] for name in TRACKED_METRICS}

    for _ in range(n_iterations):
        if method == "resample":
            sample = [closed_trades[rng.randrange(n)] for _ in range(draw_size)]
        else:
            sample = closed_trades[:]
            rng.shuffle(sample)

        values = _extract(sample, starting_balance)
        for name in TRACKED_METRICS:
            distributions[name].append(values[name])

    return BootstrapResult(
        method=method, n_iterations=n_iterations, n_trades=n, starting_balance=starting_balance,
        observed=observed, distributions=distributions,
    )


def run_bootstrap_from_result(
    result: BacktestResult, method: str = "resample", n_iterations: int = 5000,
    sample_size: int | None = None, seed: int | None = None,
) -> BootstrapResult:
    return run_bootstrap(
        result.trades, result.starting_balance, method=method,
        n_iterations=n_iterations, sample_size=sample_size, seed=seed,
    )
