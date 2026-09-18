"""
backtest/engine.py

Event-driven backtest engine. See CONTEXT_HANDOFF.md for the two bugs this
already had fixed (mark-to-market gap, walk-forward boundary carry).

CHANGE THIS SESSION: BacktestResult gained `periods_in_market` (an int
count, not a percentage - compute_metrics() turns it into exposure_pct%).
It's populated by counting, per candle, whether a position was open *after*
that candle's signal was applied - the same point equity_curve is appended,
so exposure_pct and equity_curve stay consistent with each other. This is
purely additive: existing fields, existing tests, and every other line of
run()'s control flow are unchanged.
"""

import copy
from dataclasses import dataclass, field

from data.store import Candle
from strategy.base import Signal, Strategy


@dataclass
class CostModel:
    commission_per_unit: float = 0.0
    commission_flat: float = 0.0
    slippage_pips: float = 0.0
    pip_size: float = 0.0001
    commission_pct: float = 0.0
    slippage_pct: float = 0.0


@dataclass
class Trade:
    side: str
    units: float
    entry_time: str
    entry_price: float
    exit_time: str | None = None
    exit_price: float | None = None
    commission_paid: float = 0.0
    reason_open: str = ""
    reason_close: str = ""

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def realized_pnl(self) -> float:
        if self.is_open:
            return 0.0
        direction = 1 if self.side == "LONG" else -1
        gross = (self.exit_price - self.entry_price) * direction * self.units
        return gross - self.commission_paid


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[str, float]]
    starting_balance: float
    ending_balance: float
    open_trade_at_end: Trade | None = None
    # Count of candles where a position was open, measured at the same
    # point equity_curve is appended each iteration. Used by
    # compute_metrics() for exposure_pct - "what fraction of the tested
    # period had capital actually at risk". Default 0 keeps every existing
    # caller/test that constructs a BacktestResult by hand unaffected.
    periods_in_market: int = 0

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if not t.is_open]


class BacktestEngine:
    def __init__(
        self, strategy: Strategy, cost_model: CostModel | None = None,
        starting_balance: float = 10_000.0, units_per_trade: float = 1_000.0,
    ):
        self.strategy = strategy
        self.cost_model = cost_model or CostModel()
        self.starting_balance = starting_balance
        self.units_per_trade = units_per_trade

    def run(
        self, candles: list[Candle], warm_start: bool = False, initial_trade: Trade | None = None,
    ) -> BacktestResult:
        assert candles, "BacktestEngine.run() requires at least one candle"
        assert self.units_per_trade > 0, "units_per_trade must be positive"

        if not warm_start:
            self.strategy.reset()

        balance = self.starting_balance
        trades: list[Trade] = []
        open_trade: Trade | None = initial_trade
        if open_trade is not None:
            trades.append(open_trade)
        equity_curve: list[tuple[str, float]] = []
        history: list[Candle] = []
        periods_in_market = 0

        for candle in candles:
            history.append(candle)
            decision = self.strategy.on_candle(candle, history)
            open_trade, balance = self._apply_signal(decision, candle, open_trade, balance, trades)

            if open_trade is not None:
                periods_in_market += 1
                unrealized = self._mark_to_market_pnl(open_trade, candle)
            else:
                unrealized = 0.0
            equity_curve.append((candle.timestamp, balance + unrealized))

        open_trade_at_end: Trade | None = None
        if open_trade is not None:
            open_trade_at_end = copy.copy(open_trade)

        if open_trade is not None:
            self._close_trade(open_trade, candles[-1], "end of backtest")
            balance += open_trade.realized_pnl()

        return BacktestResult(
            trades=trades, equity_curve=equity_curve,
            starting_balance=self.starting_balance, ending_balance=balance,
            open_trade_at_end=open_trade_at_end, periods_in_market=periods_in_market,
        )

    def _apply_signal(
        self, decision, candle: Candle, open_trade: Trade | None, balance: float, trades: list[Trade],
    ) -> tuple[Trade | None, float]:
        assert decision is not None, "_apply_signal requires a StrategyDecision"
        assert isinstance(trades, list), "_apply_signal requires a trades list to append into"

        if decision.signal == Signal.CLOSE:
            if open_trade is None:
                return open_trade, balance
            self._close_trade(open_trade, candle, decision.reason)
            return None, balance + open_trade.realized_pnl()

        if decision.signal not in (Signal.BUY, Signal.SELL):
            return open_trade, balance

        desired_side = "LONG" if decision.signal == Signal.BUY else "SHORT"
        if open_trade is not None and open_trade.side == desired_side:
            return open_trade, balance

        if open_trade is not None:
            self._close_trade(open_trade, candle, decision.reason)
            balance += open_trade.realized_pnl()
            open_trade = None

        open_trade = self._open_trade(desired_side, candle, decision.reason)
        trades.append(open_trade)
        return open_trade, balance

    def _slippage_amount(self, reference_price: float) -> float:
        cm = self.cost_model
        return cm.slippage_pips * cm.pip_size + reference_price * cm.slippage_pct

    def _commission_amount(self, execution_price: float, units: float) -> float:
        cm = self.cost_model
        notional = execution_price * units
        return cm.commission_flat + cm.commission_per_unit * units + cm.commission_pct * notional

    def _mark_to_market_pnl(self, trade: Trade, candle: Candle) -> float:
        assert trade.is_open, "_mark_to_market_pnl is only meaningful for an open trade"
        assert trade.side in ("LONG", "SHORT"), f"unknown trade side: {trade.side}"
        if trade.side == "LONG":
            slip = self._slippage_amount(candle.bid_close)
            would_be_exit_price = candle.bid_close - slip
        else:
            slip = self._slippage_amount(candle.ask_close)
            would_be_exit_price = candle.ask_close + slip

        direction = 1 if trade.side == "LONG" else -1
        gross = (would_be_exit_price - trade.entry_price) * direction * trade.units
        would_be_exit_commission = self._commission_amount(would_be_exit_price, trade.units)
        return gross - trade.commission_paid - would_be_exit_commission

    def _open_trade(self, side: str, candle: Candle, reason: str) -> Trade:
        assert side in ("LONG", "SHORT"), f"side must be 'LONG' or 'SHORT', got {side!r}"
        assert self.units_per_trade > 0, "units_per_trade must be positive"
        if side == "LONG":
            slip = self._slippage_amount(candle.ask_close)
            price = candle.ask_close + slip
        else:
            slip = self._slippage_amount(candle.bid_close)
            price = candle.bid_close - slip

        commission = self._commission_amount(price, self.units_per_trade)
        return Trade(
            side=side, units=self.units_per_trade, entry_time=candle.timestamp,
            entry_price=price, commission_paid=commission, reason_open=reason,
        )

    def _close_trade(self, trade: Trade, candle: Candle, reason: str) -> None:
        assert trade.is_open, "_close_trade called on a trade that is already closed"
        assert trade.side in ("LONG", "SHORT"), f"unknown trade side: {trade.side}"
        if trade.side == "LONG":
            slip = self._slippage_amount(candle.bid_close)
            price = candle.bid_close - slip
        else:
            slip = self._slippage_amount(candle.ask_close)
            price = candle.ask_close + slip

        trade.exit_time = candle.timestamp
        trade.exit_price = price
        trade.reason_close = reason
        trade.commission_paid += self._commission_amount(price, trade.units)
