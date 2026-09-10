"""
tests/test_walk_forward.py

Hand-verified test of the walk-forward machinery itself - deliberately using
a trivial, fully-predictable strategy (not SmaCrossoverStrategy) so every
number can be checked by hand, the same approach test_engine.py uses.

This specifically targets the bug described in backtest/walk_forward.py's
docstring: does a position that's still open at the in-sample/out-of-sample
boundary actually carry forward, instead of silently vanishing because the
strategy only signals on a *change* of state (e.g. "just crossed") rather
than "I am currently bullish"?

Run with: python -m tests.test_walk_forward
"""

from backtest.engine import CostModel
from backtest.walk_forward import run_walk_forward
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class AlwaysBuyStrategy(Strategy):
    """
    Buys exactly once across its entire lifetime (respecting warm_start
    continuity - "have I EVER bought" is remembered, not per-run) and then
    holds forever. If should_trade=False, never trades at all. Deliberately
    the simplest possible strategy with state that must survive a warm_start
    boundary and a position that must survive a run() boundary.
    """

    def __init__(self, should_trade: bool = True):
        self.should_trade = should_trade
        self._has_bought = False

    def reset(self):
        self._has_bought = False

    def on_candle(self, candle, history):
        if not self.should_trade:
            return StrategyDecision(Signal.HOLD, "flat by design")
        if not self._has_bought:
            self._has_bought = True
            return StrategyDecision(Signal.BUY, "buy once, ever")
        return StrategyDecision(Signal.HOLD, "already holding - no new signal expected")


def make_candle(ts: str, price: float) -> Candle:
    # bid == ask (no spread) so P&L is pure price difference - isolates the
    # walk-forward machinery from cost-model math, which is tested elsewhere
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts,
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price,
        volume=1,
    )


def test_selection_and_position_continuity_across_boundary():
    """
    One window, in_sample_size=3, out_of_sample_size=2. Prices rise during
    in-sample (100 -> 101 -> 103), so buying should out-score staying flat
    (0% return) and win selection. The winning position - bought at 100,
    still open when in-sample ends - must then carry into out-of-sample
    (104, 102) rather than vanishing, since AlwaysBuyStrategy never re-signals
    BUY after its first-ever purchase.
    """
    prices = [100.0, 101.0, 103.0, 104.0, 102.0]
    candles = [make_candle(f"t{i}", p) for i, p in enumerate(prices)]

    param_grid = [{"should_trade": True}, {"should_trade": False}]
    starting_balance = 1000.0

    result = run_walk_forward(
        candles, AlwaysBuyStrategy, param_grid,
        in_sample_size=3, out_of_sample_size=2,
        cost_model=CostModel(),  # zero costs - pure price math
        starting_balance=starting_balance, units_per_trade=1.0,
    )

    assert len(result.windows) == 1
    w = result.windows[0]

    # --- selection: True should win (price rose 100->103, +3 vs 0 for flat) ---
    assert w.chosen_params == {"should_trade": True}, f"expected should_trade=True to win in-sample, got {w.chosen_params}"
    assert abs(w.in_sample_score - 0.3) < 1e-9, f"in-sample return should be 3/1000=0.3%, got {w.in_sample_score}"

    # --- position continuity: bought at 100 in-sample, carried through to
    #     out-of-sample's close at 102 (last out-of-sample candle), NOT
    #     re-bought and NOT silently dropped ---
    oos_trades = w.out_of_sample_result.trades
    assert len(oos_trades) == 1, f"expected exactly 1 trade carried into out-of-sample (not re-opened, not dropped), got {len(oos_trades)}"
    carried_trade = oos_trades[0]
    assert abs(carried_trade.entry_price - 100.0) < 1e-9, f"carried trade should keep its ORIGINAL entry price (100), got {carried_trade.entry_price}"
    assert abs(carried_trade.exit_price - 102.0) < 1e-9, f"carried trade should close at the last out-of-sample price (102), got {carried_trade.exit_price}"

    expected_oos_pnl = (102.0 - 100.0) * 1.0  # units=1, zero costs
    assert abs(carried_trade.realized_pnl() - expected_oos_pnl) < 1e-9

    expected_oos_return_pct = expected_oos_pnl / starting_balance * 100
    assert abs(w.out_of_sample_metrics.total_return_pct - expected_oos_return_pct) < 1e-9

    # --- combined equity curve should reflect the same compounded result ---
    assert abs(result.ending_balance - (starting_balance + expected_oos_pnl)) < 1e-9

    print(f"Selected params: {w.chosen_params} (in-sample score {w.in_sample_score:.3f}%)")
    print(f"Carried trade: entry={carried_trade.entry_price} exit={carried_trade.exit_price} pnl={carried_trade.realized_pnl()}")
    print(f"Combined ending balance: {result.ending_balance} (expected {starting_balance + expected_oos_pnl})")
    print("Selection + position continuity assertions passed.")


def test_no_lookahead_between_in_sample_and_out_of_sample():
    """Structural check: every window's out-of-sample slice starts strictly
    after its in-sample slice ends - proves windows don't overlap into the
    future data they're meant to be blind to."""
    from tests.synthetic_data import generate_synthetic_candles
    from strategy.sma_crossover import SmaCrossoverStrategy

    candles = generate_synthetic_candles(n_candles=500)
    result = run_walk_forward(
        candles, SmaCrossoverStrategy,
        param_grid=[{"fast_period": 10, "slow_period": 30}],
        in_sample_size=150, out_of_sample_size=50,
    )

    assert len(result.windows) >= 2, "expected at least 2 windows for this test to be meaningful"
    for w in result.windows:
        assert w.out_of_sample_start > w.in_sample_end, (
            f"window {w.window_index}: out-of-sample start ({w.out_of_sample_start}) "
            f"must be strictly after in-sample end ({w.in_sample_end})"
        )
    print(f"Checked {len(result.windows)} windows - no in-sample/out-of-sample overlap in any of them.")
    print("No-lookahead structural assertion passed.")


if __name__ == "__main__":
    test_selection_and_position_continuity_across_boundary()
    test_no_lookahead_between_in_sample_and_out_of_sample()
    print("\nAll walk-forward tests passed.")
