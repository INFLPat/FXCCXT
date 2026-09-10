"""
backtest/bootstrap.py

Bootstrap resampling / Monte Carlo analysis on a backtest's completed trades.

Two complementary questions neither walk-forward testing nor sensitivity
analysis can answer:

  - RESAMPLE (bootstrap with replacement): if the same underlying process
    generated a fresh batch of trades statistically like these ones, what
    range of outcomes would be plausible? Produces a confidence interval
    around total return (and other metrics) instead of just the single
    number this one historical path happened to produce.

  - SHUFFLE (permutation, no replacement): the exact same trades, in a
    randomly reordered sequence. Total P&L summed simply is order-invariant,
    but max drawdown is NOT - the same wins and losses can arrive in a much
    friendlier or much harsher order. This isolates "path risk": how much of
    the reported drawdown is really about the trades themselves, versus just
    the luck of which order they happened to occur in.

  Because of that order-invariance, under SHUFFLE mode total_return_pct,
  ending_balance, and win_rate_pct are mathematically guaranteed to be
  IDENTICAL across every iteration - that's not a bug, it's the whole point.
  max_drawdown_pct (and profit_factor, which depends on the running state at
  the point losses land relative to wins) are the metrics that actually vary.
  Verified as an explicit invariant in tests/test_bootstrap.py, not just
  asserted here.

Both operate directly on BacktestResult.trades - the actual completed trades
from a real backtest - not a statistical model of them. Each resampled/
shuffled sequence is turned into a synthetic, trade-resolution BacktestResult
and run through the same backtest.metrics.compute_metrics() used everywhere
else in this project, so the same tested drawdown/return math applies here.

Deliberately NOT reported: an annualized Sharpe ratio per iteration.
compute_metrics()'s Sharpe assumes a fixed time interval between
equity_curve points (periods_per_year), which is true for the original
candle-level curve but meaningless for this trade-resolution curve, where
trades don't arrive at even time intervals. Reporting it here without heavy
caveats would be misleading, so total_return_pct and max_drawdown_pct - both
fully well-defined regardless of time resolution - are what this module
reports instead.
"""

import math
import random
from dataclasses import dataclass, field

from backtest.engine import BacktestResult, Trade
from backtest.metrics import compute_metrics

TRACKED_METRICS = ("total_return_pct", "max_drawdown_pct", "win_rate_pct", "profit_factor", "ending_balance")


def _build_synthetic_result(trades: list[Trade], starting_balance: float) -> BacktestResult:
    """Builds a trade-resolution BacktestResult from an already-ordered list
    of closed trades, purely by compounding realized_pnl() in that order -
    for feeding into compute_metrics()."""
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
    m = compute_metrics(result, periods_per_year=252)  # periods_per_year unused - Sharpe is discarded
    return {
        "total_return_pct": m.total_return_pct,
        "max_drawdown_pct": m.max_drawdown_pct,
        "win_rate_pct": m.win_rate_pct,
        "profit_factor": m.profit_factor,
        "ending_balance": result.ending_balance,
    }


@dataclass
class BootstrapResult:
    method: str            # "resample" or "shuffle"
    n_iterations: int
    n_trades: int           # number of closed trades this was run on
    starting_balance: float
    observed: dict[str, float | None]           # metric_name -> value from the ACTUAL, unresampled trade order
    distributions: dict[str, list[float | None]]  # metric_name -> one value per iteration (may include None)

    def _clean(self, metric: str) -> list[float]:
        if metric not in self.distributions:
            raise ValueError(f"'{metric}' was not tracked - choose from {TRACKED_METRICS}")
        return [v for v in self.distributions[metric] if v is not None]

    def percentile(self, metric: str, p: float) -> float:
        """p in [0, 100]. Dependency-free percentile via sorted interpolation."""
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
        """e.g. level=0.90 gives the 5th-95th percentile range."""
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
        """Fraction of iterations where `metric` fell below `threshold`, as a percentage."""
        values = self._clean(metric)
        return sum(1 for v in values if v < threshold) / len(values) * 100

    def probability_of_loss(self) -> float:
        """Convenience wrapper: probability_below('total_return_pct', 0.0)."""
        return self.probability_below("total_return_pct", 0.0)

    def observed_percentile_rank(self, metric: str) -> float | None:
        """
        What fraction of the resampled/shuffled distribution falls BELOW the
        actual observed value (from the real, unresampled trade order)? 50%
        is typical; far from 50% means the real historical path was unusual
        relative to the alternatives this method generated - for SHUFFLE
        mode's max_drawdown_pct specifically, this tells you whether the
        actual sequence of wins/losses was unusually painful (high
        percentile) or unusually forgiving (low percentile) compared to
        every other order the same trades could have arrived in.
        Returns None if the observed value itself is undefined (e.g.
        profit_factor when there were no losing trades at all).
        """
        obs = self.observed.get(metric)
        if obs is None:
            return None
        values = self._clean(metric)
        return sum(1 for v in values if v < obs) / len(values) * 100

    def summary(self) -> str:
        lines = [
            f"Bootstrap ({self.method}, {self.n_iterations} iterations, {self.n_trades} trades)",
        ]
        obs_return = self.observed["total_return_pct"]
        obs_dd = self.observed["max_drawdown_pct"]
        lines.append(f"Observed (actual historical order): return {obs_return:+.2f}%, max drawdown {obs_dd:.2f}%")

        if self.method == "resample":
            lo, hi = self.confidence_interval("total_return_pct", 0.90)
            lines.append(f"90% CI for total return if this process repeated: [{lo:+.2f}%, {hi:+.2f}%]")
            lines.append(f"Probability of a loss: {self.probability_of_loss():.1f}%")
            dd_lo, dd_hi = self.confidence_interval("max_drawdown_pct", 0.90)
            lines.append(f"90% CI for max drawdown: [{dd_lo:.2f}%, {dd_hi:.2f}%]")
        else:  # shuffle
            # sanity-check the order-invariance this mode guarantees, rather
            # than assuming it - if this were ever violated it'd indicate a bug
            return_values = self._clean("total_return_pct")
            invariant_ok = (max(return_values) - min(return_values)) < 1e-9
            lines.append(
                f"Total return is order-invariant under shuffling (as expected): "
                f"{'confirmed identical across all iterations' if invariant_ok else 'WARNING: varied - this should not happen'}"
            )
            rank = self.observed_percentile_rank("max_drawdown_pct")
            dd_lo, dd_hi = self.confidence_interval("max_drawdown_pct", 0.90)
            lines.append(f"90% CI for max drawdown across all reorderings: [{dd_lo:.2f}%, {dd_hi:.2f}%]")
            if rank is not None:
                if rank > 75:
                    verdict = "the actual sequence was on the PAINFUL end - most reorderings would have hurt less"
                elif rank < 25:
                    verdict = "the actual sequence was on the FORGIVING end - most reorderings would have hurt more"
                else:
                    verdict = "unremarkable - roughly in line with a typical reordering"
                lines.append(f"Observed drawdown sits at the {rank:.0f}th percentile of all reorderings: {verdict}")

        return "\n".join(lines)


def run_bootstrap(
    trades: list[Trade],
    starting_balance: float,
    method: str = "resample",
    n_iterations: int = 5000,
    sample_size: int | None = None,
    seed: int | None = None,
) -> BootstrapResult:
    """
    trades: typically BacktestResult.trades from a real backtest run. Open
        trades (if any) are dropped - only closed trades have a realized P&L
        to resample.
    method: "resample" draws `sample_size` trades WITH replacement, standard
        bootstrap style. "shuffle" permutes all trades WITHOUT replacement -
        sample_size is ignored for this mode, since a permutation must use
        every trade exactly once.
    sample_size: only used by "resample"; defaults to the original trade
        count (the standard bootstrap convention - same size as the original
        sample). Override to explore "what if I'd made fewer/more trades."
    seed: fixes the RNG for reproducible results (also what the tests use).
    """
    if method not in ("resample", "shuffle"):
        raise ValueError(f"method must be 'resample' or 'shuffle', got '{method}'")

    closed_trades = [t for t in trades if not t.is_open]
    if len(closed_trades) < 2:
        raise ValueError(
            f"Need at least 2 closed trades to run bootstrap analysis, got {len(closed_trades)}."
        )

    rng = random.Random(seed)
    n = len(closed_trades)
    draw_size = sample_size or n

    observed = _extract(closed_trades, starting_balance)
    distributions: dict[str, list] = {name: [] for name in TRACKED_METRICS}

    for _ in range(n_iterations):
        if method == "resample":
            sample = [closed_trades[rng.randrange(n)] for _ in range(draw_size)]
        else:  # shuffle
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
    """Convenience wrapper: unpacks trades/starting_balance from a
    BacktestResult you already have, e.g. straight from engine.run()."""
    return run_bootstrap(
        result.trades, result.starting_balance, method=method,
        n_iterations=n_iterations, sample_size=sample_size, seed=seed,
    )
