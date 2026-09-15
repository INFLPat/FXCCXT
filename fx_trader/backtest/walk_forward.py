"""
backtest/walk_forward.py

Walk-forward (out-of-sample) testing: slides a window through time. In each
window: (1) try every combo in `param_grid` on the IN-SAMPLE slice, score
with `selection_metric`, keep the winner; (2) run ONLY the winner, unchanged,
on the following OUT-OF-SAMPLE slice - data it never saw; (3) slide forward
by `step`, repeat. Compare in-sample vs out-of-sample per window: a
consistent gap is the signature of fitting noise, not finding an edge.

State AND position continuity: the winning strategy instance carries its
internal state into the out-of-sample run via `warm_start`, and any position
still open at the in-sample boundary carries forward via `initial_trade` -
otherwise a trend-following strategy's position would silently vanish at
every window boundary. Balance/trades do NOT carry over - each window's
out-of-sample run starts fresh at `starting_balance`; `combined_equity_curve`
stitches per-window returns into one continuous, compounded picture.

Known limitation: `history` (Strategy.on_candle's second argument) only ever
contains candles from the *current* run() call, even with warm_start - fine
for strategies that maintain their own state (e.g. SmaCrossoverStrategy) and
ignore `history`, not fine for one that relies on `history` for lookback.
"""

from dataclasses import dataclass
from typing import Callable

from backtest.engine import BacktestEngine, BacktestResult, CostModel, Trade
from backtest.metrics import Metrics, compute_metrics
from data.store import Candle
from strategy.base import Strategy


@dataclass
class WindowResult:
    window_index: int
    in_sample_start: str
    in_sample_end: str
    out_of_sample_start: str
    out_of_sample_end: str
    chosen_params: dict
    in_sample_score: float
    in_sample_metrics: Metrics
    out_of_sample_result: BacktestResult
    out_of_sample_metrics: Metrics


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    combined_equity_curve: list[tuple[str, float]]
    combined_metrics: Metrics
    starting_balance: float
    ending_balance: float


def _select_best_candidate(
    in_sample: list[Candle],
    param_grid: list[dict],
    strategy_factory: Callable[..., Strategy],
    cost_model: CostModel,
    starting_balance: float,
    units_per_trade: float,
    selection_metric: Callable[[Metrics], float | None],
    periods_per_year: int,
) -> tuple[float, dict, Metrics, BacktestEngine, BacktestResult]:
    """Backtests every candidate in param_grid on `in_sample` and returns the
    highest-scoring one, as (score, params, metrics, engine, result). A None
    score (e.g. Sharpe with too few trades) is treated as the worst possible
    score, never as a crash."""
    assert in_sample, "_select_best_candidate requires a non-empty in-sample slice"
    assert param_grid, "_select_best_candidate requires at least one candidate"

    best = None  # (score, params, in_sample_metrics, engine, in_sample_result)
    for params in param_grid:
        strategy = strategy_factory(**params)
        engine = BacktestEngine(
            strategy=strategy, cost_model=cost_model,
            starting_balance=starting_balance, units_per_trade=units_per_trade,
        )
        in_sample_result = engine.run(in_sample)
        in_sample_metrics = compute_metrics(in_sample_result, periods_per_year=periods_per_year)

        raw_score = selection_metric(in_sample_metrics)
        score = raw_score if raw_score is not None else float("-inf")

        if best is None or score > best[0]:
            best = (score, params, in_sample_metrics, engine, in_sample_result)

    assert best is not None, "at least one candidate must produce a result"
    return best


def run_walk_forward(
    candles: list[Candle],
    strategy_factory: Callable[..., Strategy],
    param_grid: list[dict],
    in_sample_size: int,
    out_of_sample_size: int,
    step: int | None = None,
    cost_model: CostModel | None = None,
    starting_balance: float = 10_000.0,
    units_per_trade: float = 1_000.0,
    selection_metric: Callable[[Metrics], float | None] = lambda m: m.total_return_pct,
    periods_per_year: int = 252,
) -> WalkForwardResult:
    """
    strategy_factory: called as strategy_factory(**params) for each entry in
        param_grid, e.g. the SmaCrossoverStrategy class itself.
    step: defaults to out_of_sample_size (non-overlapping out-of-sample
        periods; in-sample periods commonly do overlap between consecutive
        windows - normal for a trailing-lookback setup).
    selection_metric: scores in-sample Metrics to pick the winner; None
        values (e.g. Sharpe with too few trades) are the worst possible score.
    """
    if len(candles) < in_sample_size + out_of_sample_size:
        raise ValueError(
            f"Not enough candles ({len(candles)}) for even one window "
            f"(need at least {in_sample_size + out_of_sample_size})"
        )
    if not param_grid:
        raise ValueError("param_grid must contain at least one parameter combination")

    step = step or out_of_sample_size
    cost_model = cost_model or CostModel()

    windows: list[WindowResult] = []
    combined_balance = starting_balance
    combined_equity_curve: list[tuple[str, float]] = []
    all_out_of_sample_trades: list[Trade] = []

    MAX_WINDOWS = 10_000  # explicit ceiling on the sliding-window loop
    window_index = 0
    cursor = 0
    while cursor + in_sample_size + out_of_sample_size <= len(candles) and window_index < MAX_WINDOWS:
        in_sample = candles[cursor : cursor + in_sample_size]
        out_of_sample = candles[cursor + in_sample_size : cursor + in_sample_size + out_of_sample_size]

        best_score, best_params, best_in_sample_metrics, best_engine, best_in_sample_result = _select_best_candidate(
            in_sample, param_grid, strategy_factory, cost_model,
            starting_balance, units_per_trade, selection_metric, periods_per_year,
        )

        out_of_sample_result = best_engine.run(
            out_of_sample, warm_start=True,
            initial_trade=best_in_sample_result.open_trade_at_end,
        )
        out_of_sample_metrics = compute_metrics(out_of_sample_result, periods_per_year=periods_per_year)

        windows.append(WindowResult(
            window_index=window_index,
            in_sample_start=in_sample[0].timestamp,
            in_sample_end=in_sample[-1].timestamp,
            out_of_sample_start=out_of_sample[0].timestamp,
            out_of_sample_end=out_of_sample[-1].timestamp,
            chosen_params=best_params,
            in_sample_score=best_score,
            in_sample_metrics=best_in_sample_metrics,
            out_of_sample_result=out_of_sample_result,
            out_of_sample_metrics=out_of_sample_metrics,
        ))

        # Chain this window's out-of-sample return onto the running combined
        # balance (compounding), rescaling its equity curve onto one
        # continuous line rather than each window restarting at starting_balance.
        window_start_balance = combined_balance
        scale = window_start_balance / starting_balance
        for ts, equity in out_of_sample_result.equity_curve:
            combined_equity_curve.append((ts, equity * scale))
        combined_balance = window_start_balance * (out_of_sample_result.ending_balance / starting_balance)

        all_out_of_sample_trades.extend(out_of_sample_result.trades)

        cursor += step
        window_index += 1

    combined_result = BacktestResult(
        trades=all_out_of_sample_trades,
        equity_curve=combined_equity_curve,
        starting_balance=starting_balance,
        ending_balance=combined_balance,
    )
    combined_metrics = compute_metrics(combined_result, periods_per_year=periods_per_year)

    return WalkForwardResult(
        windows=windows,
        combined_equity_curve=combined_equity_curve,
        combined_metrics=combined_metrics,
        starting_balance=starting_balance,
        ending_balance=combined_balance,
    )
