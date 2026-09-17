from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class ScriptedStrategy(Strategy):
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
        make_candle("t0", bid=1.1000, ask=1.1002),
        make_candle("t1", bid=1.1050, ask=1.1052),
        make_candle("t2", bid=1.1050, ask=1.1052),
    ]
    cost_model = CostModel(commission_per_unit=0.00001, commission_flat=0.0, slippage_pips=0.5, pip_size=0.0001)
    engine = BacktestEngine(strategy=ScriptedStrategy(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry = 1.1002 + 0.5 * 0.0001
    expected_exit = 1.1050 - 0.5 * 0.0001
    assert abs(trade.entry_price - expected_entry) < 1e-9
    assert abs(trade.exit_price - expected_exit) < 1e-9
    expected_commission = 0.00001 * 1000 * 2
    assert abs(trade.commission_paid - expected_commission) < 1e-9
    gross = (expected_exit - expected_entry) * 1000
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-9
    expected_balance = 10_000.0 + expected_pnl
    assert abs(result.ending_balance - expected_balance) < 1e-9
    print("All assertions passed.")


def test_pct_based_cost_math():
    candles = [
        make_candle("t0", bid=60000.0, ask=60006.0),
        make_candle("t1", bid=61000.0, ask=61006.0),
        make_candle("t2", bid=61000.0, ask=61006.0),
    ]
    cost_model = CostModel(commission_pct=0.001, slippage_pct=0.0005)
    engine = BacktestEngine(strategy=ScriptedStrategy(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=0.1)
    result = engine.run(candles)
    trade = result.trades[0]
    expected_entry = 60006.0 * 1.0005
    expected_exit = 61000.0 * (1 - 0.0005)
    assert abs(trade.entry_price - expected_entry) < 1e-6
    assert abs(trade.exit_price - expected_exit) < 1e-6
    expected_commission = (expected_entry * 0.1 * 0.001) + (expected_exit * 0.1 * 0.001)
    assert abs(trade.commission_paid - expected_commission) < 1e-6
    gross = (expected_exit - expected_entry) * 0.1
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-6
    print("All pct-cost assertions passed.")


def test_mark_to_market_matches_realized_when_price_unchanged():
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
    cost_model = CostModel(commission_per_unit=0.01)
    engine = BacktestEngine(strategy=BuyThenHoldThenClose(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=100.0)
    result = engine.run(candles)
    equity_while_open = result.equity_curve[1][1]
    equity_after_close = result.equity_curve[2][1]
    assert abs(equity_while_open - equity_after_close) < 1e-9
    expected = 10_000.0 - (0.01 * 100 * 2)
    assert abs(equity_while_open - expected) < 1e-9
    print("Mark-to-market continuity assertion passed.")


def test_periods_in_market_counts_only_candles_with_an_open_position():
    """NEW this session. BuyThenHoldThenClose: flat at t0 (BUY opens it -
    counted), open at t1 (HOLD - counted), closed at t2 (CLOSE - NOT
    counted, since open_trade is None immediately after that candle's
    signal is applied). Expected periods_in_market = 2 of 3."""
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

    candles = [make_candle(f"t{i}", bid=100.0, ask=100.0) for i in range(3)]
    engine = BacktestEngine(strategy=BuyThenHoldThenClose(), starting_balance=10_000.0, units_per_trade=1.0)
    result = engine.run(candles)
    assert result.periods_in_market == 2, f"expected 2, got {result.periods_in_market}"
    print(f"periods_in_market={result.periods_in_market} (expected 2)")
    print("periods_in_market assertion passed.")


if __name__ == "__main__":
    test_long_trade_cost_math()
    test_pct_based_cost_math()
    test_mark_to_market_matches_realized_when_price_unchanged()
    test_periods_in_market_counts_only_candles_with_an_open_position()
    cost_model = CostModel(commission_per_unit=0.00001, commission_flat=0.0, slippage_pips=0.5, pip_size=0.0001)
    engine = BacktestEngine(strategy=ScriptedStrategy(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    expected_entry = 1.1002 + 0.5 * 0.0001
    expected_exit = 1.1050 - 0.5 * 0.0001
    assert abs(trade.entry_price - expected_entry) < 1e-9
    assert abs(trade.exit_price - expected_exit) < 1e-9
    expected_commission = 0.00001 * 1000 * 2
    assert abs(trade.commission_paid - expected_commission) < 1e-9
    gross = (expected_exit - expected_entry) * 1000
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-9
    expected_balance = 10_000.0 + expected_pnl
    assert abs(result.ending_balance - expected_balance) < 1e-9
    print("All assertions passed.")


def test_pct_based_cost_math():
    candles = [
        make_candle("t0", bid=60000.0, ask=60006.0),
        make_candle("t1", bid=61000.0, ask=61006.0),
        make_candle("t2", bid=61000.0, ask=61006.0),
    ]
    cost_model = CostModel(commission_pct=0.001, slippage_pct=0.0005)
    engine = BacktestEngine(strategy=ScriptedStrategy(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=0.1)
    result = engine.run(candles)
    trade = result.trades[0]
    expected_entry = 60006.0 * 1.0005
    expected_exit = 61000.0 * (1 - 0.0005)
    assert abs(trade.entry_price - expected_entry) < 1e-6
    assert abs(trade.exit_price - expected_exit) < 1e-6
    expected_commission = (expected_entry * 0.1 * 0.001) + (expected_exit * 0.1 * 0.001)
    assert abs(trade.commission_paid - expected_commission) < 1e-6
    gross = (expected_exit - expected_entry) * 0.1
    expected_pnl = gross - expected_commission
    assert abs(trade.realized_pnl() - expected_pnl) < 1e-6
    print("All pct-cost assertions passed.")


def test_mark_to_market_matches_realized_when_price_unchanged():
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
    cost_model = CostModel(commission_per_unit=0.01)
    engine = BacktestEngine(strategy=BuyThenHoldThenClose(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=100.0)
    result = engine.run(candles)
    equity_while_open = result.equity_curve[1][1]
    equity_after_close = result.equity_curve[2][1]
    assert abs(equity_while_open - equity_after_close) < 1e-9
    expected = 10_000.0 - (0.01 * 100 * 2)
    assert abs(equity_while_open - expected) < 1e-9
    print("Mark-to-market continuity assertion passed.")


def test_periods_in_market_counts_only_candles_with_an_open_position():
    """NEW this session. BuyThenHoldThenClose: flat at t0 (BUY opens it -
    counted), open at t1 (HOLD - counted), closed at t2 (CLOSE - NOT
    counted, since open_trade is None immediately after that candle's
    signal is applied). Expected periods_in_market = 2 of 3."""
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

    candles = [make_candle(f"t{i}", bid=100.0, ask=100.0) for i in range(3)]
    engine = BacktestEngine(strategy=BuyThenHoldThenClose(), starting_balance=10_000.0, units_per_trade=1.0)
    result = engine.run(candles)
    assert result.periods_in_market == 2, f"expected 2, got {result.periods_in_market}"
    print(f"periods_in_market={result.periods_in_market} (expected 2)")
    print("periods_in_market assertion passed.")


if __name__ == "__main__":
    test_long_trade_cost_math()
    test_pct_based_cost_math()
    test_mark_to_market_matches_realized_when_price_unchanged()
    test_periods_in_market_counts_only_candles_with_an_open_position()
