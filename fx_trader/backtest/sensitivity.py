"""
backtest/sensitivity.py

Parameter sensitivity analysis: runs a full backtest across every combination
in a parameter grid and analyzes how performance varies across that grid -
not just which single combination scored best.

Why this matters more than "what's the best combination": a strategy with a
real, structural edge should perform reasonably well across a neighborhood of
similar parameters. If fast_period=10/slow_period=30 looks great but 9/29 and
11/31 are both bad, that's the signature of fitting to the noise of this one
historical path, not a real effect. A "plateau" of decent performance across
nearby parameters is a much stronger signal than a single isolated peak - and
is something a single "here's the best combination" backtest can't show you
at all, since it only ever reports the peak, never its surroundings.

This complements walk_forward.py rather than replacing it: walk-forward tests
whether a SELECTED parameter set holds up on unseen data over time;
sensitivity analysis tests whether the parameter SPACE itself is smooth
enough to trust the selection process in the first place. Run both.

Usable for any Strategy subclass and any number of varying parameters - not
hardcoded to SmaCrossoverStrategy or to exactly two dimensions. Two
parameters varying is the natural case for a heatmap; marginal_effect()
works regardless of how many parameters vary, by averaging over everything
else, so there's always at least one useful view of the results.
"""

import itertools
import math
from dataclasses import dataclass
from typing import Callable

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import Metrics, compute_metrics
from data.store import Candle
from strategy.base import Strategy


@dataclass
class GridPointResult:
    params: dict
    metrics: Metrics | None   # None if this combination was invalid (see `error`)
    error: str | None = None  # e.g. "fast_period must be smaller than slow_period"

    @property
    def is_valid(self) -> bool:
        return self.metrics is not None


@dataclass
class SensitivityResult:
    grid_points: list[GridPointResult]
    param_names: list[str]            # all param names, in grid order
    param_values: dict[str, list]     # param_name -> the values tried for it
    metric_name: str
    metric_fn: Callable[[Metrics], float | None]

    @property
    def varying_param_names(self) -> list[str]:
        """Params with more than one value tried - the ones actually being
        analyzed. A param fixed to a single value doesn't vary and is
        excluded from neighbor/heatmap logic."""
        return [name for name in self.param_names if len(self.param_values[name]) > 1]

    def _score(self, gp: GridPointResult | None) -> float | None:
        if gp is None or gp.metrics is None:
            return None
        return self.metric_fn(gp.metrics)

    def valid_points(self) -> list[GridPointResult]:
        return [gp for gp in self.grid_points if gp.is_valid]

    def all_scores(self) -> list[float]:
        scores = [self._score(gp) for gp in self.valid_points()]
        return [s for s in scores if s is not None]

    def _find_point(self, params: dict) -> GridPointResult | None:
        for gp in self.grid_points:
            if gp.params == params:
                return gp
        return None

    def best(self, n: int = 1) -> list[GridPointResult]:
        scored = [(self._score(gp), gp) for gp in self.valid_points()]
        scored = [(s, gp) for s, gp in scored if s is not None]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [gp for _, gp in scored[:n]]

    def worst(self, n: int = 1) -> list[GridPointResult]:
        scored = [(self._score(gp), gp) for gp in self.valid_points()]
        scored = [(s, gp) for s, gp in scored if s is not None]
        scored.sort(key=lambda pair: pair[0])
        return [gp for _, gp in scored[:n]]

    def neighbors(self, params: dict) -> list[GridPointResult]:
        """Grid points that differ from `params` by exactly one step along
        exactly one varying parameter's axis (a 'von Neumann' neighborhood -
        no diagonals). The natural notion of 'nearby' in parameter space."""
        found = []
        for name in self.varying_param_names:
            values = self.param_values[name]
            idx = values.index(params[name])
            for neighbor_idx in (idx - 1, idx + 1):
                if 0 <= neighbor_idx < len(values):
                    neighbor_params = dict(params)
                    neighbor_params[name] = values[neighbor_idx]
                    gp = self._find_point(neighbor_params)
                    if gp is not None:
                        found.append(gp)
        return found

    def neighbor_gap(self, params: dict) -> float | None:
        """score(params) minus the average score of its valid neighbors.

        Large and positive = an isolated spike - this point does much better
        than everything right next to it in parameter space, which is exactly
        the signature of fitting to this dataset's noise rather than finding
        a real, structural effect. Close to zero = a plateau - the
        neighborhood performs similarly, a real reason for more confidence.

        Returns None if the point doesn't exist, is invalid, or has no valid
        neighbors (e.g. it's fixed at a grid edge with no room to compare).
        """
        point = self._find_point(params)
        point_score = self._score(point)
        if point_score is None:
            return None
        neighbor_scores = [s for s in (self._score(n) for n in self.neighbors(params)) if s is not None]
        if not neighbor_scores:
            return None
        avg_neighbor = sum(neighbor_scores) / len(neighbor_scores)
        return point_score - avg_neighbor

    def marginal_effect(self, param_name: str) -> list[tuple]:
        """
        Average score for each value of `param_name`, averaged over every
        other varying parameter's values - i.e. this parameter's effect on
        its own, independent of exactly how many other parameters are in the
        grid. Works for a heatmap's 2 dimensions, but also for 1 or 5+
        varying parameters, where a full heatmap isn't practical to look at.

        Returns a list of (value, mean_score, sample_count), ordered by value.
        """
        if param_name not in self.param_values:
            raise ValueError(f"'{param_name}' is not a parameter in this grid: {self.param_names}")

        by_value: dict = {v: [] for v in self.param_values[param_name]}
        for gp in self.valid_points():
            score = self._score(gp)
            if score is not None:
                by_value[gp.params[param_name]].append(score)

        return [
            (value, (sum(scores) / len(scores) if scores else None), len(scores))
            for value, scores in by_value.items()
        ]

    def heatmap_grid(self) -> tuple[list, list, list]:
        """
        Only valid when exactly 2 parameters vary. Returns (x_values,
        y_values, z_matrix), where z_matrix[yi][xi] is the metric score at
        (x_values[xi], y_values[yi]), or None for an invalid/missing
        combination - suitable for feeding straight into a heatmap chart.
        """
        varying = self.varying_param_names
        if len(varying) != 2:
            raise ValueError(
                f"heatmap_grid() needs exactly 2 varying parameters, got {len(varying)}: {varying}. "
                "Fix all other parameters to a single value in param_grid to use this, "
                "or use marginal_effect() for a single parameter at a time instead."
            )
        x_name, y_name = varying
        x_values = self.param_values[x_name]
        y_values = self.param_values[y_name]
        fixed = {name: self.param_values[name][0] for name in self.param_names if name not in varying}

        z_matrix = []
        for yv in y_values:
            row = []
            for xv in x_values:
                params = dict(fixed)
                params[x_name] = xv
                params[y_name] = yv
                row.append(self._score(self._find_point(params)))
            z_matrix.append(row)
        return x_values, y_values, z_matrix

    def summary(self, top_n: int = 3) -> str:
        scores = self.all_scores()
        invalid_count = len(self.grid_points) - len(self.valid_points())
        lines = [
            f"Sensitivity analysis: {len(self.grid_points)} combinations "
            f"({invalid_count} invalid/skipped) | metric: {self.metric_name}",
        ]
        if not scores:
            lines.append("No valid combinations produced a score.")
            return "\n".join(lines)

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(variance)
        profitable = sum(1 for s in scores if s > 0)

        lines.append(
            f"Range: {min(scores):+.3f} to {max(scores):+.3f}  "
            f"(mean {mean:+.3f}, std {std:.3f})"
        )
        lines.append(f"Profitable combinations: {profitable}/{len(scores)} ({profitable / len(scores) * 100:.0f}%)")

        lines.append(f"\nTop {top_n}:")
        for gp in self.best(top_n):
            gap = self.neighbor_gap(gp.params)
            score = self._score(gp)
            if gap is None:
                gap_note = "no valid neighbors to compare"
            elif gap > std:
                gap_note = f"gap {gap:+.3f} vs. neighbors (> 1 std - looks like an isolated spike, treat with suspicion)"
            else:
                gap_note = f"gap {gap:+.3f} vs. neighbors (within 1 std - consistent with a plateau)"
            lines.append(f"  {gp.params} -> {score:+.3f}  |  {gap_note}")

        return "\n".join(lines)


def run_sensitivity_analysis(
    candles: list[Candle],
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list],
    cost_model: CostModel | None = None,
    starting_balance: float = 10_000.0,
    units_per_trade: float = 1_000.0,
    metric: Callable[[Metrics], float | None] = lambda m: m.total_return_pct,
    metric_name: str = "total_return_pct",
    periods_per_year: int = 252,
) -> SensitivityResult:
    """
    param_grid: dict of param_name -> list of values to try, e.g.
        {"fast_period": [5, 10, 15, 20], "slow_period": [20, 30, 40, 60]}
    Every combination in the Cartesian product of these lists is backtested
    on the FULL `candles` series (not split into windows - this asks "how
    sensitive is performance to this parameter across this whole dataset",
    a different question from walk-forward's "does a selected parameter set
    hold up over time"). A combination the strategy_factory rejects (e.g.
    SmaCrossoverStrategy requires fast_period < slow_period) is recorded as
    invalid rather than crashing the whole run.

    Each grid point is an independent full backtest, so this is O(grid size)
    backtests - fine for the kind of grids in the demo, but a genuinely huge
    grid (thousands of combinations) would be a natural candidate to
    parallelize with concurrent.futures, since each point here is already
    computed independently of every other. Not implemented here to keep this
    dependency-free and simple; the loop below is structured so that's a
    straightforward addition if you ever need it.
    """
    if not param_grid:
        raise ValueError("param_grid must have at least one parameter")

    cost_model = cost_model or CostModel()
    param_names = list(param_grid.keys())
    value_lists = [param_grid[name] for name in param_names]

    grid_points: list[GridPointResult] = []
    for combo in itertools.product(*value_lists):
        params = dict(zip(param_names, combo))
        try:
            strategy = strategy_factory(**params)
        except Exception as exc:
            grid_points.append(GridPointResult(params=params, metrics=None, error=str(exc)))
            continue

        engine = BacktestEngine(
            strategy=strategy, cost_model=cost_model,
            starting_balance=starting_balance, units_per_trade=units_per_trade,
        )
        result = engine.run(candles)
        metrics = compute_metrics(result, periods_per_year=periods_per_year)
        grid_points.append(GridPointResult(params=params, metrics=metrics))

    return SensitivityResult(
        grid_points=grid_points,
        param_names=param_names,
        param_values=dict(param_grid),
        metric_name=metric_name,
        metric_fn=metric,
    )
