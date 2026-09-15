"""
backtest/engine.py

Event-driven backtest engine. Walks through candles one at a time, calling
the strategy, and executing any resulting trades against realistic prices.

Cost modeling:
1. SPREAD: buy at ASK, sell at BID, always - drawn from the candle's own
   bid/ask, not an approximated fixed-pip constant.
2. COMMISSION: optional per-trade cost, flat and/or per-unit, on top of spread.
3. SLIPPAGE: a configurable number of pips (or %) added against you on
   execution. Deterministic, not stochastic - a starting point, not a
   model of real fill variance.
"""

import copy
from dataclasses import dataclass, field

from data.store import Candle
from strategy.base import Signal, Strategy


@dataclass
class CostModel:
    # Pip-based costs (FX-style: fixed per-unit fee + pip-denominated slippage)
    commission_per_unit: float = 0.0   # e.g. 0.00002 = $0.02 per 1000 units traded
    commission_flat: float = 0.0       # flat fee per trade, in account currency
    slippage_pips: float = 0.0         # adverse price movement on execution, in pips
    pip_size: float = 0.0001           # 0.0001 for most pairs, 0.01 for JPY pairs

    # Percentage-based costs (crypto-style). Additive with the pip fields
    # above, so FX configs (left at 0) are unaffected.
    commission_pct: float = 0.0        # e.g. 0.001 = 0.1% of trade notional, per side
    slippage_pct: float = 0.0          # e.g. 0.0005 = 0.05% adverse move on execution


@dataclass
class Trade:
    side: str           # 'LONG' or 'SHORT'
    units: float
    entry_time: str
    entry_price: float  # price actually paid/received, post-slippage
    exit_time: str | None = None
    exit_price: float | None = None
    commission_paid: float = 0.0
    reason_open: str = ""
    reason_close: str = ""

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def realized_pnl(self) -> float:
        """P&L in account currency, net of commission, once closed."""
        if self.is_open:
            return 0.0
        direction = 1 if self.side == "LONG" else -1
        gross = (self.exit_price - self.entry_price) * direction * self.units
        return gross - self.commission_paid


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[str, float]]  # (timestamp, equity)
    starting_balance: float
    ending_balance: float
    # Position still open at the end of this run, captured BEFORE it gets
    # force-closed for reporting - None if nothing was open. Lets a caller
    # (e.g. walk-forward) carry a still-open position across a run()
    # boundary via `initial_trade` instead of it silently vanishing.
    open_trade_at_end: Trade | None = None

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if not t.is_open]


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        cost_model: CostModel | None = None,
        starting_balance: float = 10_000.0,
        units_per_trade: float = 1_000.0,
    ):
        self.strategy = strategy
        self.cost_model = cost_model or CostModel()
        self.starting_balance = starting_balance
        self.units_per_trade = units_per_trade

    def run(
        self,
        candles: list[Candle],
        warm_start: bool = False,
        initial_trade: Trade | None = None,
    ) -> BacktestResult:
        """
        warm_start=True skips resetting the strategy, so state (e.g. a
        rolling SMA window) survives across a previous run() call on this
        same engine, instead of paying a fresh warmup cost.

        initial_trade seeds an already-open position (e.g. from a previous
        run's `open_trade_at_end`) instead of starting flat.

        Regardless of either flag, balance/trades/equity_curve always start
        fresh for this call - only strategy state (warm_start) and a single
        carried position (initial_trade) can cross the boundary.
        """
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

        for candle in candles:
            history.append(candle)
            decision = self.strategy.on_candle(candle, history)
            open_trade, balance = self._apply_signal(decision, candle, open_trade, balance, trades)

            unrealized = self._mark_to_market_pnl(open_trade, candle) if open_trade is not None else 0.0
            equity_curve.append((candle.timestamp, balance + unrealized))

        open_trade_at_end: Trade | None = None
        if open_trade is not None:
            open_trade_at_end = copy.copy(open_trade)

        if open_trade is not None:
            self._close_trade(open_trade, candles[-1], "end of backtest")
            balance += open_trade.realized_pnl()

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            starting_balance=self.starting_balance,
            ending_balance=balance,
            open_trade_at_end=open_trade_at_end,
        )

    def _apply_signal(
        self, decision, candle: Candle, open_trade: Trade | None, balance: float, trades: list[Trade],
    ) -> tuple[Trade | None, float]:
        """Applies one strategy decision to the current position. Returns
        the (possibly new) open_trade and updated balance. `trades` is
        mutated in place (a new trade is appended when one opens) - every
        branch is an early return, so this stays at one level of nesting."""
        assert decision is not None, "_apply_signal requires a StrategyDecision"
        assert isinstance(trades, list), "_apply_signal requires a trades list to append into"

        if decision.signal == Signal.CLOSE:
            if open_trade is None:
                return open_trade, balance
            self._close_trade(open_trade, candle, decision.reason)
            return None, balance + open_trade.realized_pnl()

        if decision.signal not in (Signal.BUY, Signal.SELL):
            return open_trade, balance  # HOLD - no-op

        desired_side = "LONG" if decision.signal == Signal.BUY else "SHORT"
        if open_trade is not None and open_trade.side == desired_side:
            return open_trade, balance  # already positioned correctly

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
        """What `trade`'s P&L would be if closed on `candle` right now, using
        the same slippage/commission logic _close_trade uses. Doesn't mutate
        the trade - a hypothetical close for equity-curve purposes only.
        trade.commission_paid holds only entry-side commission while open;
        the hypothetical exit commission is added here."""
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
            price = candle.ask_close + slip  # buy at ask, slippage against you
        else:
            slip = self._slippage_amount(candle.bid_close)
            price = candle.bid_close - slip  # sell at bid, slippage against you

        commission = self._commission_amount(price, self.units_per_trade)
        return Trade(
            side=side,
            units=self.units_per_trade,
            entry_time=candle.timestamp,
            entry_price=price,
            commission_paid=commission,
            reason_open=reason,
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
