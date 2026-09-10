"""
tests/test_sensitivity.py

Two layers of testing, same approach as test_walk_forward.py:
1. Hand-verified math against a directly-constructed 3x3 grid with known
   scores (bypassing the backtest engine entirely) - proves neighbor_gap,
   heatmap_grid, best/worst, and marginal_effect are all computed correctly.
2. An integration test using the real engine and a trivial strategy, to
   prove run_sensitivity_analysis wires everything together correctly,
   including gracefully handling a parameter combination the strategy
   rejects.

Run with: python -m tests.test_sensitivity
"""

from backtest.engine import CostModel
from backtest.metrics import Metrics
from backtest.sensitivity import GridPointResult, SensitivityResult, run_sensitivity_analysis
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


def _metrics(return_pct: float) -> Metrics:
    """Placeholder Metrics with a controlled total_return_pct - the only
    field the default metric_fn (and these tests) actually look at."""
    return Metrics(
        total_return_pct=return_pct, total_trades=1, win_rate_pct=0.0,
        profit_factor=None, max_drawdown_pct=0.0, sharpe_ratio=None,
        total_commission_paid=0.0, avg_win=0.0, avg_loss=0.0,
    )


def _make_3x3_spike_result() -> SensitivityResult:
    """
    3x3 grid, param_a and param_b both in [0, 1, 2]. Every point scores 1.0
    EXCEPT the dead center (1, 1), which scores 10.0 - a deliberate, obvious
    isolated spike surrounded on all 4 sides by ordinary points.
    """
    grid_points = []
    for a in (0, 1, 2):
        for b in (0, 1, 2):
            score = 10.0 if (a, b) == (1, 1) else 1.0
            grid_points.append(GridPointResult(params={"param_a": a, "param_b": b}, metrics=_metrics(score)))

    return SensitivityResult(
        grid_points=grid_points,
        param_names=["param_a", "param_b"],
        param_values={"param_a": [0, 1, 2], "param_b": [0, 1, 2]},
        metric_name="total_return_pct",
        metric_fn=lambda m: m.total_return_pct,
    )


def test_neighbor_gap_flags_isolated_spike():
    result = _make_3x3_spike_result()

    # center point: 4 neighbors, all scoring 1.0 -> gap = 10 - 1 = 9
    center_gap = result.neighbor_gap({"param_a": 1, "param_b": 1})
    assert center_gap is not None
    assert abs(center_gap - 9.0) < 1e-9, f"expected neighbor_gap of 9.0 for the spike, got {center_gap}"

    # corner point (0,0): only 2 neighbors ((1,0) and (0,1)), both 1.0 -> gap = 1 - 1 = 0
    corner_gap = result.neighbor_gap({"param_a": 0, "param_b": 0})
    assert corner_gap is not None
    assert abs(corner_gap - 0.0) < 1e-9, f"expected neighbor_gap of 0.0 for a flat corner, got {corner_gap}"

    print(f"Center (spike) neighbor_gap: {center_gap} (expected 9.0)")
    print(f"Corner (flat) neighbor_gap: {corner_gap} (expected 0.0)")
    print("Neighbor-gap spike detection assertions passed.")


def test_best_worst_and_heatmap_grid():
    result = _make_3x3_spike_result()

    best = result.best(1)
    assert len(best) == 1
    assert best[0].params == {"param_a": 1, "param_b": 1}
    assert abs(result._score(best[0]) - 10.0) < 1e-9

    worst = result.worst(1)
    assert abs(result._score(worst[0]) - 1.0) < 1e-9  # any of the 8 non-spike points

    x_values, y_values, z_matrix = result.heatmap_grid()
    assert x_values == [0, 1, 2]
    assert y_values == [0, 1, 2]
    # z_matrix[yi][xi] -> center of the matrix (y=1, x=1) should be the spike
    assert abs(z_matrix[1][1] - 10.0) < 1e-9
    assert abs(z_matrix[0][0] - 1.0) < 1e-9
    assert abs(z_matrix[2][2] - 1.0) < 1e-9

    print(f"Best: {best[0].params} -> {result._score(best[0])}")
    print(f"Heatmap center z_matrix[1][1] = {z_matrix[1][1]} (expected 10.0)")
    print("Best/worst/heatmap_grid assertions passed.")


def test_marginal_effect():
    result = _make_3x3_spike_result()

    # averaging over param_b for each param_a value:
    #   a=0: scores [1,1,1] -> mean 1.0 | a=1: scores [1,10,1] -> mean 4.0 | a=2: scores [1,1,1] -> mean 1.0
    marginal_a = dict((v, mean) for v, mean, _count in result.marginal_effect("param_a"))
    assert abs(marginal_a[0] - 1.0) < 1e-9
    assert abs(marginal_a[1] - 4.0) < 1e-9
    assert abs(marginal_a[2] - 1.0) < 1e-9

    print(f"Marginal effect of param_a: {marginal_a} (expected {{0: 1.0, 1: 4.0, 2: 1.0}})")
    print("marginal_effect assertions passed.")


class ToggleStrategy(Strategy):
    """
    Buys once (if should_trade=True) and holds, or stays flat. Raises if
    should_trade is None - used to test that run_sensitivity_analysis
    handles a rejected parameter combination gracefully instead of crashing.
    """

    def __init__(self, should_trade):
        if should_trade is None:
            raise ValueError("should_trade cannot be None")
        self.should_trade = should_trade
        self._has_bought = False

    def reset(self):
        self._has_bought = False

    def on_candle(self, candle, history):
        if not self.should_trade:
            return StrategyDecision(Signal.HOLD)
        if not self._has_bought:
            self._has_bought = True
            return StrategyDecision(Signal.BUY)
        return StrategyDecision(Signal.HOLD)


def _make_candle(ts: str, price: float) -> Candle:
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts,
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price,
        volume=1,
    )


def test_run_sensitivity_analysis_handles_invalid_combo():
    # steadily rising price - buying should be profitable, staying flat should be 0%
    candles = [_make_candle(f"t{i}", 100.0 + i) for i in range(10)]

    result = run_sensitivity_analysis(
        candles, ToggleStrategy,
        param_grid={"should_trade": [True, False, None]},
        cost_model=CostModel(), starting_balance=1000.0, units_per_trade=1.0,
    )

    assert len(result.grid_points) == 3
    assert len(result.valid_points()) == 2, "the should_trade=None combo should be caught, not crash the run"

    invalid_points = [gp for gp in result.grid_points if not gp.is_valid]
    assert len(invalid_points) == 1
    assert invalid_points[0].params == {"should_trade": None}
    assert "should_trade cannot be None" in invalid_points[0].error

    true_point = result._find_point({"should_trade": True})
    false_point = result._find_point({"should_trade": False})
    assert result._score(true_point) > 0, "buying into a steady rise should be profitable"
    assert abs(result._score(false_point) - 0.0) < 1e-9, "never trading should be exactly 0% return"

    # heatmap_grid should refuse cleanly - only 1 varying parameter here, not 2
    try:
        result.heatmap_grid()
        raise AssertionError("expected heatmap_grid() to raise with only 1 varying parameter")
    except ValueError as e:
        assert "2 varying parameters" in str(e)

    print(f"True: {result._score(true_point):.2f}% | False: {result._score(false_point):.2f}% | invalid combo correctly skipped")
    print("Integration + invalid-combo-handling assertions passed.")


if __name__ == "__main__":
    test_neighbor_gap_flags_isolated_spike()
    test_best_worst_and_heatmap_grid()
    test_marginal_effect()
    test_run_sensitivity_analysis_handles_invalid_combo()
    print("\nAll sensitivity analysis tests passed.")
