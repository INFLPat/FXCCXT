"""
tests/test_engine.py

Sanity-check the engine's cost math against hand-computed numbers, using a
tiny scripted strategy (not SMA crossover) so the trade timing is fully
predictable and we can verify P&L by hand.

Run with: python -m tests.test_engine
"""

from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class ScriptedStrategy(Strategy):
    """Buys on candle index 0, closes on candle index 1. Nothing else."""

    def __init__(self):
        self._i = -1

    def reset(self):
        self._i = -1

    def on_candle(self, candle, history):
        self._i += 1
        if self._i == 0:
            return StrategyDecision(Signal.BUY, "test entry")
        if self._i == 1:
            return StrategyDecision(Signal.CLOSE, "test exit")
        return StrategyDecision(Signal.HOLD)


def make_candle(ts: str, bid: float, ask: float) -> Candle:
    return Candle(
        instrument="EUR_USD", granularity="H1", timestamp=ts,
        bid_open=bid, bid_high=bid, bid_low=bid, bid_close=bid,
        ask_open=ask, ask_high=ask, ask_low=ask, ask_close=ask,
        volume=100,
    )


def test_long_trade_cost_math():
    candles = [
        make_candle("t0", bid=1.1000, ask=1.1002),  # entry candle: buy at ask
        make_candle("t1", bid=1.1050, ask=1.1052),  # exit candle: sell at bid
        make_candle("t2", bid=1.1050, ask=1.1052),  # trailing candle, no-op
    ]

    cost_model = CostModel(
        commission_per_unit=0.00001,  # $0.01 per 1000 units, per side
        commission_flat=0.0,
        slippage_pips=0.5,
        pip_size=0.0001,
    )
    engine = BacktestEngine(
        strategy=ScriptedStrategy(),
        cost_model=cost_model,
        starting_balance=10_000.0,
        units_per_trade=1_000.0,
    )
    result = engine.run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]

    # Entry: buy at ask (1.1002) + 0.5 pip slippage (0.00005) = 1.10025
    expected_entry = 1.1002 + 0.5 * 0.0001
    # Exit: sell at bid (1.1050) - 0.5 pip slippage (0.00005) = 1.10495
    expected_exit = 1.1050 - 0.5 * 0.0001

    assert abs(trade.entry_price - expected_entry) < 1e-9
    assert abs(trade.exit_price - expected_exit) < 1e-9

    # commission: 0.00001 * 1000 units = 0.01 per side -> 0.02 total
    expected_commission = 0.00001 * 1000 * 2
    assert abs(trade.commission_paid - expected_commission) < 1e-9

    gross = (expected_exit - expected_entry) * 1000
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-9

    expected_balance = 10_000.0 + expected_pnl
    assert abs(result.ending_balance - expected_balance) < 1e-9

    print("All assertions passed.")
    print(f"Entry: {trade.entry_price:.5f} (expected {expected_entry:.5f})")
    print(f"Exit:  {trade.exit_price:.5f} (expected {expected_exit:.5f})")
    print(f"Commission: {trade.commission_paid:.5f} (expected {expected_commission:.5f})")
    print(f"P&L: {trade.realized_pnl():.5f} (expected {expected_pnl:.5f})")


def test_pct_based_cost_math():
    """Verifies the crypto-style percentage commission/slippage path, same
    hand-computed approach as the pip-based test above."""
    candles = [
        make_candle("t0", bid=60000.0, ask=60006.0),   # entry: buy at ask
        make_candle("t1", bid=61000.0, ask=61006.0),   # exit: sell at bid
        make_candle("t2", bid=61000.0, ask=61006.0),
    ]

    cost_model = CostModel(
        commission_pct=0.001,   # 0.1% taker fee, per side
        slippage_pct=0.0005,    # 0.05% adverse move on execution
    )
    engine = BacktestEngine(
        strategy=ScriptedStrategy(),
        cost_model=cost_model,
        starting_balance=10_000.0,
        units_per_trade=0.1,  # e.g. 0.1 BTC
    )
    result = engine.run(candles)
    trade = result.trades[0]

    # Entry: buy at ask (60006.0), +0.05% slippage against us
    expected_entry = 60006.0 * 1.0005
    # Exit: sell at bid (61000.0), -0.05% slippage against us
    expected_exit = 61000.0 * (1 - 0.0005)

    assert abs(trade.entry_price - expected_entry) < 1e-6
    assert abs(trade.exit_price - expected_exit) < 1e-6

    # commission: 0.1% of notional (price * units), charged on entry and exit
    expected_commission = (expected_entry * 0.1 * 0.001) + (expected_exit * 0.1 * 0.001)
    assert abs(trade.commission_paid - expected_commission) < 1e-6

    gross = (expected_exit - expected_entry) * 0.1
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-6

    print("All pct-cost assertions passed.")
    print(f"Entry: {trade.entry_price:.4f} (expected {expected_entry:.4f})")
    print(f"Exit:  {trade.exit_price:.4f} (expected {expected_exit:.4f})")
    print(f"Commission: {trade.commission_paid:.4f} (expected {expected_commission:.4f})")
    print(f"P&L: {trade.realized_pnl():.4f} (expected {expected_pnl:.4f})")


def test_mark_to_market_matches_realized_when_price_unchanged():
    """
    Proves the equity-curve fix: if price hasn't moved between a candle where
    a trade is still open and a later candle where it closes, the 'equity if
    closed now' figure recorded while open should already match the realized
    equity after the close - no artificial gap purely from switching
    accounting methods at the close instant.
    """

    class BuyThenHoldThenClose(Strategy):
        def __init__(self):
            self._i = -1

        def reset(self):
            self._i = -1

        def on_candle(self, candle, history):
            self._i += 1
            if self._i == 0:
                return StrategyDecision(Signal.BUY, "entry")
            if self._i == 2:
                return StrategyDecision(Signal.CLOSE, "exit")
            return StrategyDecision(Signal.HOLD, "holding")

    flat_price = 100.0
    candles = [make_candle(f"t{i}", bid=flat_price, ask=flat_price) for i in range(3)]

    cost_model = CostModel(commission_per_unit=0.01)  # no slippage - isolates the commission effect
    engine = BacktestEngine(
        strategy=BuyThenHoldThenClose(), cost_model=cost_model,
        starting_balance=10_000.0, units_per_trade=100.0,
    )
    result = engine.run(candles)

    equity_while_open = result.equity_curve[1][1]   # candle 1: still holding
    equity_after_close = result.equity_curve[2][1]  # candle 2: just closed, same price

    assert abs(equity_while_open - equity_after_close) < 1e-9, (
        f"unrealized equity while open ({equity_while_open}) should already match "
        f"realized equity after close ({equity_after_close}) when price hasn't moved"
    )
    # both should reflect the round-trip commission (entry + exit), not zero
    expected = 10_000.0 - (0.01 * 100 * 2)
    assert abs(equity_while_open - expected) < 1e-9
    print(f"Equity while open: {equity_while_open} | after close: {equity_after_close} | both match expected {expected}")
    print("Mark-to-market continuity assertion passed.")


if __name__ == "__main__":
    test_long_trade_cost_math()
    test_pct_based_cost_math()
    test_mark_to_market_matches_realized_when_price_unchanged()
