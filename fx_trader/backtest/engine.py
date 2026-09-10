"""
backtest/engine.py

Event-driven backtest engine. Walks through candles one at a time, calling
the strategy, and executing any resulting trades against realistic prices.

Cost modeling (the part most homemade backtests get wrong):
1. SPREAD: you buy at the ASK and sell at the BID, always. Since our Candle
   objects store both sides, this happens naturally rather than being
   approximated with a fixed "spread in pips" constant - real FX spreads
   widen and narrow with volatility and time of day, and using the actual
   historical spread captures that.
2. COMMISSION: optional per-trade cost, either a flat fee or a per-unit rate,
   layered on top of spread (some brokers charge raw spread + commission).
3. SLIPPAGE: a configurable number of pips added against you on execution,
   modeling the fact that by the time your order reaches the broker, price
   may have moved. Real slippage is stochastic and worse in fast markets;
   this is a simple deterministic model - a reasonable starting point but
   worth revisiting once you have real fill data from paper trading.

Everything costs money is logged per-trade so you can see exactly how much
of your gross P&L was eaten by costs - this number is the difference between
a strategy that looks good on a spreadsheet and one that's actually viable.
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

    # Percentage-based costs (crypto-style: exchanges charge a % maker/taker fee
    # rather than a pip spread; slippage is more naturally a % of price too,
    # since "pips" doesn't mean anything for an asset like BTC). Additive with
    # the pip-based fields above, so FX configs (which leave these at 0) are
    # completely unaffected - set commission_pct/slippage_pct instead of the
    # pip fields for crypto, or combine both if you have a reason to.
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
    # Snapshot of the position that was open at the end of this run, captured
    # BEFORE it gets force-closed for reporting purposes below - None if
    # nothing was open. Exists so a caller (e.g. walk-forward testing) can
    # carry a still-open position across a run() boundary via `initial_trade`,
    # rather than the position silently vanishing at the boundary. Doesn't
    # affect trades/ending_balance above - those still reflect a clean,
    # fully-closed-out summary as before.
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
        warm_start=True skips resetting the strategy, so a strategy that
        already processed an earlier batch of candles (via a previous run()
        call on this same engine) continues with its internal state intact -
        e.g. SmaCrossoverStrategy's rolling SMA window - instead of paying a
        fresh warmup cost. Default False resets normally, unaffected.

        initial_trade lets the caller seed an already-open position (e.g.
        captured from a previous run's `open_trade_at_end`) instead of
        starting flat - without this, a position still logically open at a
        run() boundary would be force-closed and never reopen unless the
        strategy re-emits a fresh signal, silently understating exposure
        that should have continued. See BacktestResult.open_trade_at_end.

        Regardless of either flag, balance/trades/equity_curve always start
        fresh for this call - only strategy state (warm_start) and a single
        carried position (initial_trade) can cross the boundary.
        """
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

            # --- handle strategy decision ---
            if decision.signal == Signal.CLOSE and open_trade is not None:
                self._close_trade(open_trade, candle, decision.reason)
                balance += open_trade.realized_pnl()
                open_trade = None

            elif decision.signal in (Signal.BUY, Signal.SELL):
                desired_side = "LONG" if decision.signal == Signal.BUY else "SHORT"

                if open_trade is not None and open_trade.side != desired_side:
                    self._close_trade(open_trade, candle, decision.reason)
                    balance += open_trade.realized_pnl()
                    open_trade = None

                if open_trade is None:
                    open_trade = self._open_trade(desired_side, candle, decision.reason)
                    trades.append(open_trade)

            # --- mark-to-market equity (what the open position would net if closed now) ---
            unrealized = 0.0
            if open_trade is not None:
                unrealized = self._mark_to_market_pnl(open_trade, candle)
            equity_curve.append((candle.timestamp, balance + unrealized))

        # snapshot BEFORE force-closing, so a caller can carry this position
        # into a subsequent run() rather than losing it at the boundary
        open_trade_at_end: Trade | None = None
        if open_trade is not None:
            open_trade_at_end = copy.copy(open_trade)

        # close any position still open at the end of the data, at last price,
        # so reported balance/trades reflect total realized performance
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

    def _slippage_amount(self, reference_price: float) -> float:
        cm = self.cost_model
        return cm.slippage_pips * cm.pip_size + reference_price * cm.slippage_pct

    def _commission_amount(self, execution_price: float, units: float) -> float:
        cm = self.cost_model
        notional = execution_price * units
        return cm.commission_flat + cm.commission_per_unit * units + cm.commission_pct * notional

    def _mark_to_market_pnl(self, trade: Trade, candle: Candle) -> float:
        """
        What `trade`'s P&L would be if closed on `candle` right now - using
        the exact same slippage/commission logic _close_trade uses, so the
        equity curve reflects what you could actually walk away with, not an
        idealized zero-friction price difference. Doesn't mutate the trade;
        this is a hypothetical close purely for mark-to-market purposes.

        trade.commission_paid holds only the entry-side commission while a
        trade is open (see _open_trade) - the hypothetical exit commission
        is added here to get the true "if I closed now" net figure.
        """
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
            commission_paid=commission,  # entry-side commission; exit adds more on close
            reason_open=reason,
        )

    def _close_trade(self, trade: Trade, candle: Candle, reason: str) -> None:
        if trade.side == "LONG":
            slip = self._slippage_amount(candle.bid_close)
            price = candle.bid_close - slip  # sell at bid, slippage against you
        else:
            slip = self._slippage_amount(candle.ask_close)
            price = candle.ask_close + slip  # buy back at ask, slippage against you

        trade.exit_time = candle.timestamp
        trade.exit_price = price
        trade.reason_close = reason
        trade.commission_paid += self._commission_amount(price, trade.units)
