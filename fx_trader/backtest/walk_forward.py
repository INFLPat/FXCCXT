"""
backtest/walk_forward.py

Walk-forward (out-of-sample) testing: the honest way to check whether a
strategy's backtest performance reflects a real, persistent edge rather than
parameters fitted to the noise of one historical period.

The idea: instead of picking fast_period=10, slow_period=30 once and running
the whole history, slide a window through time. In each window:
  1. Try every parameter combination in `param_grid` on the IN-SAMPLE slice,
     score each with `selection_metric`, and keep the winner.
  2. Run ONLY the winning parameters, completely unchanged, on the following
     OUT-OF-SAMPLE slice - data those parameters never influenced or saw.
  3. Slide forward by `step` and repeat.

Every number in `WalkForwardResult.windows[i].out_of_sample_metrics` comes
from step 2, never step 1. If a strategy's performance looks great in-sample
but falls apart out-of-sample - consistently, across many windows - that's
the clearest sign the whole exercise is fitting noise rather than finding
something real. Compare `in_sample_score` against `out_of_sample_metrics`
per window to see this directly.

State AND position continuity: the winning strategy instance (with its
internal state, e.g. SmaCrossoverStrategy's rolling SMA window) carries
directly from the in-sample scoring run into the out-of-sample run via
`warm_start`, and if that strategy still had a position open at the moment
in-sample ended, that position carries forward too via `initial_trade` -
otherwise a trend-following strategy's position would silently vanish at
every window boundary unless a fresh signal happened to fire right there
(see backtest/engine.py's BacktestResult.open_trade_at_end for why this
matters). Balance/trades do NOT carry over - every window's out-of-sample
run is scored starting fresh at `starting_balance`, so each window's result
is independently interpretable; `combined_equity_curve` below is what
stitches per-window returns into one continuous, compounded picture.

Known limitation: `history` (the second argument to Strategy.on_candle) only
ever contains candles from the *current* run() call, even with warm_start -
fine for strategies like SmaCrossoverStrategy that maintain their own
internal state and ignore `history`, but something to be aware of if you
write a strategy that relies on `history` directly for lookback logic.
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
    param_grid, e.g. pass the SmaCrossoverStrategy class itself.
    param_grid: list of kwarg dicts, e.g.
        [{"fast_period": 5, "slow_period": 20}, {"fast_period": 10, "slow_period": 30}]
    step: how far the window slides each iteration; defaults to
        out_of_sample_size, giving non-overlapping out-of-sample periods
        (in-sample periods commonly do overlap between consecutive windows -
        that's normal for a trailing-lookback walk-forward setup).
    selection_metric: scores a candidate's in-sample Metrics to pick the
        winner; defaults to total_return_pct. None values (e.g. Sharpe with
        too few trades) are treated as the worst possible score, not an error.
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

    window_index = 0
    cursor = 0
    while cursor + in_sample_size + out_of_sample_size <= len(candles):
        in_sample = candles[cursor : cursor + in_sample_size]
        out_of_sample = candles[cursor + in_sample_size : cursor + in_sample_size + out_of_sample_size]

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

        best_score, best_params, best_in_sample_metrics, best_engine, best_in_sample_result = best

        # Continue the winning strategy's internal state AND any position it
        # still held at the in-sample boundary - see module docstring.
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
        # balance (compounding), rescaling its equity curve to sit on one
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
