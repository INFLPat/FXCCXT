"""
tests/test_rescale.py

Proves the NEW Phase 1 Step 1 capability (CONFIDENCE_SIZING_DESIGN.md
Section 8): weighted-average-entry partial fills via
BacktestEngine.rescale_trade(), and Section 9.2's lot-size/min-order
constraints. Not yet wired into run()'s signal loop (that's Phase 1 Step
3) - tested standalone by calling _open_trade/rescale_trade directly.

Every P&L number here is hand-computed, not just asserted non-None - same
discipline as test_engine.py.

Run: python -m tests.test_rescale
"""

from backtest.engine import BacktestEngine, CostModel
from data.store import Candle
from strategy.base import Signal, Strategy, StrategyDecision


class NeverTradeStrategy(Strategy):
    """rescale_trade is called directly in these tests, not through
    run()'s signal loop - this strategy exists only so BacktestEngine can
    be constructed; on_candle is never actually invoked in these tests."""

    def on_candle(self, candle, history):
        return StrategyDecision(Signal.HOLD)


def make_candle(ts: str, price: float) -> Candle:
    return Candle(
        instrument="TEST", granularity="H1", timestamp=ts,
        bid_open=price, bid_high=price, bid_low=price, bid_close=price,
        ask_open=price, ask_high=price, ask_low=price, ask_close=price,
        volume=1,
    )


def test_weighted_average_entry_and_realized_pnl_long():
    """LONG: open 10 @100, add 10 @110 (avg -> 105 exactly), reduce 8 @120
    (gross +120), reduce remaining 12 @90 (gross -180), fully closes.
    commission_per_unit=0.01 on every fill's own size.
    Hand-computed: gross = 120 - 180 = -60. commission = 0.1+0.1+0.08+0.12 = 0.4.
    realized_pnl = -60 - 0.4 = -60.4 exactly."""
    engine = BacktestEngine(
        strategy=NeverTradeStrategy(), cost_model=CostModel(commission_per_unit=0.01),
        starting_balance=10_000.0, units_per_trade=10.0,
    )
    trade = engine._open_trade("LONG", make_candle("t0", 100.0), "open")
    assert abs(trade.entry_price - 100.0) < 1e-9
    assert abs(trade.commission_paid - 0.1) < 1e-9

    engine.rescale_trade(trade, 10.0, make_candle("t1", 110.0), "add")
    assert abs(trade.entry_price - 105.0) < 1e-9, f"expected weighted-avg 105.0, got {trade.entry_price}"
    assert abs(trade.net_units() - 20.0) < 1e-9
    assert abs(trade.commission_paid - 0.2) < 1e-9

    engine.rescale_trade(trade, -8.0, make_candle("t2", 120.0), "reduce")
    assert trade.is_open, "reducing by 8 of 20 should still leave the trade open"
    assert abs(trade.net_units() - 12.0) < 1e-9
    assert abs(trade.entry_price - 105.0) < 1e-9, "a reduce must NOT move the weighted-average entry"
    assert abs(trade.commission_paid - 0.28) < 1e-9

    engine.rescale_trade(trade, -12.0, make_candle("t3", 90.0), "close")
    assert not trade.is_open, "reducing the remaining 12 to 0 should close the trade"
    assert abs(trade.exit_price - 90.0) < 1e-9
    assert abs(trade.commission_paid - 0.40) < 1e-9
    assert abs(trade.realized_pnl() - (-60.4)) < 1e-9, f"expected -60.4, got {trade.realized_pnl()}"
    assert len(trade.fills) == 4
    print(f"realized_pnl={trade.realized_pnl()} (expected -60.4)")
    print("Weighted-average entry + multi-fill realized_pnl (LONG) assertions passed.")


def test_weighted_average_entry_and_realized_pnl_short():
    """SHORT: open 10 @100, add 10 @90 (avg -> 95 exactly - a SHORT still
    averages its entry PRICE the same way, direction is applied separately
    in realized_pnl), then fully close 20 @80.
    gross = (80-95)*(-1)*20 = 300. commission = 0.1+0.1+0.2 = 0.4.
    realized_pnl = 300 - 0.4 = 299.6 exactly."""
    engine = BacktestEngine(
        strategy=NeverTradeStrategy(), cost_model=CostModel(commission_per_unit=0.01),
        starting_balance=10_000.0, units_per_trade=10.0,
    )
    trade = engine._open_trade("SHORT", make_candle("t0", 100.0), "open")
    engine.rescale_trade(trade, 10.0, make_candle("t1", 90.0), "add")
    assert abs(trade.entry_price - 95.0) < 1e-9

    engine.rescale_trade(trade, -20.0, make_candle("t2", 80.0), "close")
    assert not trade.is_open
    assert abs(trade.realized_pnl() - 299.6) < 1e-9, f"expected 299.6, got {trade.realized_pnl()}"
    print(f"realized_pnl={trade.realized_pnl()} (expected 299.6)")
    print("Weighted-average entry + multi-fill realized_pnl (SHORT) assertions passed.")


def test_mark_to_market_reflects_current_net_units_mid_rescale():
    """Mark-to-market must use the CURRENT net open size (after partial
    fills), not the original opening size - a stale-size bug here would
    silently misstate equity for as long as a rescaled position stays open."""
    engine = BacktestEngine(
        strategy=NeverTradeStrategy(), cost_model=CostModel(commission_per_unit=0.0),
        starting_balance=10_000.0, units_per_trade=10.0,
    )
    trade = engine._open_trade("LONG", make_candle("t0", 100.0), "open")
    engine.rescale_trade(trade, -6.0, make_candle("t1", 100.0), "reduce")
    assert abs(trade.net_units() - 4.0) < 1e-9

    mtm = engine._mark_to_market_pnl(trade, make_candle("t2", 100.0))
    assert abs(mtm - 0.0) < 1e-9, f"flat price, zero cost -> mark-to-market must be exactly 0, got {mtm}"

    trade2 = engine._open_trade("LONG", make_candle("t0", 100.0), "open")
    engine.rescale_trade(trade2, -6.0, make_candle("t1", 105.0), "reduce")
    mtm2 = engine._mark_to_market_pnl(trade2, make_candle("t2", 110.0))
    expected_mtm2 = (110.0 - 100.0) * 1 * 4.0
    assert abs(mtm2 - expected_mtm2) < 1e-9, f"expected {expected_mtm2} (using remaining 4 units, not original 10), got {mtm2}"
    print("Mark-to-market mid-rescale assertions passed.")


def test_lot_step_rounds_and_min_order_size_rejects():
    cost_model = CostModel(commission_per_unit=0.0, min_order_size=1.0, lot_step=0.5)
    engine = BacktestEngine(strategy=NeverTradeStrategy(), cost_model=cost_model, starting_balance=10_000.0, units_per_trade=10.0)

    assert abs(engine._apply_lot_constraints(3.3) - 3.5) < 1e-9, "3.3 should round to the nearest 0.5 lot step (3.5)"
    assert abs(engine._apply_lot_constraints(-3.3) - (-3.5)) < 1e-9, "sign must be preserved when rounding"
    assert engine._apply_lot_constraints(0.4) == 0.0, "below min_order_size must be rejected (returns 0)"

    unconstrained = CostModel(commission_per_unit=0.0)
    engine2 = BacktestEngine(strategy=NeverTradeStrategy(), cost_model=unconstrained, starting_balance=10_000.0, units_per_trade=10.0)
    assert abs(engine2._apply_lot_constraints(3.3) - 3.3) < 1e-9, "unset min_order_size/lot_step must be a no-op"
    print("Lot-step rounding / min-order-size rejection assertions passed.")


def test_bare_trade_without_fills_keeps_legacy_realized_pnl_formula():
    """A hand-constructed Trade (no fills, exactly what the whole existing
    test suite - test_bootstrap.py etc. - does) must use the ORIGINAL
    (exit-entry)*direction*units - commission_paid formula untouched,
    proving Phase 1's backward-compatibility guarantee at the unit level,
    not just via the full regression suite."""
    from backtest.engine import Trade

    t = Trade(side="LONG", units=5.0, entry_time="e", entry_price=100.0, exit_time="x", exit_price=110.0, commission_paid=2.0)
    assert not t.fills, "a hand-constructed Trade must have an empty fill list by default"
    expected = (110.0 - 100.0) * 1 * 5.0 - 2.0
    assert abs(t.realized_pnl() - expected) < 1e-9
    assert abs(t.net_units() - 0.0) < 1e-9, "net_units() on a closed legacy Trade (no fills) must be 0"
    print("Legacy (no-fills) realized_pnl formula assertion passed.")


if __name__ == "__main__":
    test_weighted_average_entry_and_realized_pnl_long()
    test_weighted_average_entry_and_realized_pnl_short()
    test_mark_to_market_reflects_current_net_units_mid_rescale()
    test_lot_step_rounds_and_min_order_size_rejects()
    test_bare_trade_without_fills_keeps_legacy_realized_pnl_formula()
    print("\nAll rescale/Fill tests passed.")
