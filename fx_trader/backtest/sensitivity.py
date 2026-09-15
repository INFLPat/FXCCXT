"""
backtest/sensitivity.py

Runs a full backtest across every combination in a parameter grid and
analyzes how performance varies across that grid - not just which single
combination scored best. A real, structural edge should perform reasonably
across a neighborhood of similar parameters; an isolated peak surrounded by
bad neighbors is the signature of fitting to this dataset's noise.

Complements walk_forward.py rather than replacing it: walk-forward tests
whether a SELECTED parameter set holds up on unseen data over time;
sensitivity analysis tests whether the parameter SPACE itself is smooth
enough to trust the selection process in the first place.

Works for any Strategy subclass and any number of varying parameters.
heatmap_grid() needs exactly 2 varying parameters; marginal_effect() works
for any number by averaging over everything else.
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
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.metrics is not None


@dataclass
class SensitivityResult:
    grid_points: list[GridPointResult]
    param_names: list[str]
    param_values: dict[str, list]
    metric_name: str
    metric_fn: Callable[[Metrics], float | None]

    @property
    def varying_param_names(self) -> list[str]:
        """Params with more than one value tried - a param fixed to a single
        value doesn't vary and is excluded from neighbor/heatmap logic."""
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
        """Grid points one step away along exactly one varying parameter's
        axis (von Neumann neighborhood, no diagonals)."""
        assert params, "neighbors requires a non-empty params dict"
        assert self.varying_param_names, "neighbors requires at least one varying parameter"
        found = []
        for name in self.varying_param_names:
            found.extend(self._neighbors_along_axis(params, name))
        return found

    def _neighbors_along_axis(self, params: dict, name: str) -> list[GridPointResult]:
        """Grid points one step away from `params` along `name`'s axis only,
        in both directions."""
        assert name in self.param_values, f"'{name}' is not a parameter in this grid"
        values = self.param_values[name]
        idx = values.index(params[name])
        candidate_indices = [i for i in (idx - 1, idx + 1) if 0 <= i < len(values)]

        found = []
        for neighbor_idx in candidate_indices:
            neighbor_params = dict(params)
            neighbor_params[name] = values[neighbor_idx]
            gp = self._find_point(neighbor_params)
            if gp is not None:
                found.append(gp)
        return found

    def neighbor_gap(self, params: dict) -> float | None:
        """score(params) minus the average score of its valid neighbors.
        Large positive = isolated spike (fitted to noise). Near zero =
        plateau (real, structural effect). None if no valid neighbors."""
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
        """Average score for each value of `param_name`, averaged over every
        other varying parameter. Returns (value, mean_score, sample_count)."""
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
        """Only valid with exactly 2 varying parameters. Returns (x_values,
        y_values, z_matrix) where z_matrix[yi][xi] is the score at
        (x_values[xi], y_values[yi])."""
        varying = self.varying_param_names
        if len(varying) != 2:
            raise ValueError(
                f"heatmap_grid() needs exactly 2 varying parameters, got {len(varying)}: {varying}. "
                "Fix other parameters to a single value in param_grid, or use marginal_effect() instead."
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
    param_grid: dict of param_name -> list of values, e.g.
        {"fast_period": [5, 10, 15, 20], "slow_period": [20, 30, 40, 60]}
    Every combination in the Cartesian product runs a FULL backtest on all
    of `candles` (not split into windows). A combination strategy_factory
    rejects (e.g. fast_period >= slow_period) is recorded as invalid, not
    a crash. O(grid size) backtests - each point is independent, so a huge
    grid is a natural candidate for concurrent.futures if ever needed.
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
