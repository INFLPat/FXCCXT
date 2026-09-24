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
    # Section 9.2 live-execution realism - optional, default None means "no
    # constraint" since real per-instrument limits aren't sourced yet (see
    # CONFIDENCE_SIZING_DESIGN.md Section 9.1, still blocked on real broker/
    # exchange credentials). Never guessed/hardcoded here.
    min_order_size: float | None = None
    lot_step: float | None = None


@dataclass
class Fill:
    """One individual execution against an open position - Phase 1's
    weighted-average-entry partial-fill accounting
    (CONFIDENCE_SIZING_DESIGN.md Section 8). `units` is SIGNED relative to
    the position's own magnitude, not absolute direction: positive always
    INCREASES the position (moves the weighted-average entry price),
    negative always REDUCES it (realizes P&L against the current average) -
    this holds for both LONG and SHORT trades, with `Trade.side` supplying
    the direction separately. `price` already includes slippage."""

    units: float
    price: float
    timestamp: str
    commission: float
    reason: str = ""


@dataclass
class Trade:
    side: str
    units: float          # size of the trade's initial opening fill - unchanged meaning, not mutated by later rescales
    entry_time: str
    entry_price: float          # weighted-average across all increasing fills
    exit_time: str | None = None
    exit_price: float | None = None      # price of the fill that brought net units to 0
    commission_paid: float = 0.0          # sum of every fill's commission
    reason_open: str = ""
    reason_close: str = ""
    # NEW (Phase 1 Step 1). Empty by default - every Trade constructed
    # directly (as the whole existing test suite does) keeps behaving
    # exactly as before via realized_pnl()'s legacy fallback branch below.
    # BacktestEngine populates this for every trade it creates.
    fills: list[Fill] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def net_units(self) -> float:
        """Current open size, 0 once fully closed. Falls back to `units`
        for a hand-constructed Trade with no fill history (only meaningful
        while open - a legacy closed Trade has no fills to sum)."""
        if self.fills:
            return sum(f.units for f in self.fills)
        return self.units if self.is_open else 0.0

    def realized_pnl(self) -> float:
        if self.is_open:
            return 0.0
        direction = 1 if self.side == "LONG" else -1
        if not self.fills:
            # Legacy path: no fill history (every hand-constructed Trade
            # in the existing test suite) - identical to the pre-Phase-1
            # formula, byte-for-byte.
            gross = (self.exit_price - self.entry_price) * direction * self.units
            return gross - self.commission_paid
        return self._fills_realized_pnl(direction)

    def _fills_realized_pnl(self, direction: int) -> float:
        assert self.fills, "_fills_realized_pnl requires a non-empty fill history"
        assert direction in (1, -1), f"direction must be 1 or -1, got {direction}"
        gross = 0.0
        avg_entry = 0.0
        open_units = 0.0
        for f in self.fills:
            if f.units > 0:
                avg_entry = (avg_entry * open_units + f.price * f.units) / (open_units + f.units)
                open_units += f.units
            else:
                reduce_units = -f.units
                gross += (f.price - avg_entry) * direction * reduce_units
                open_units -= reduce_units
        total_commission = sum(f.commission for f in self.fills)
        return gross - total_commission


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[str, float]]
    starting_balance: float
    ending_balance: float
    open_trade_at_end: Trade | None = None
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
            # deepcopy, not copy: `fills` is now a mutable list - a shallow
            # copy would share it by reference with the live `open_trade`,
            # so the synthetic close fill added just below would leak into
            # this snapshot too (corrupts walk_forward.py's carry-forward).
            open_trade_at_end = copy.deepcopy(open_trade)

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

    def rescale_trade(self, trade: Trade, delta_units: float, candle: Candle, reason: str) -> Trade:
        """Adds to (delta_units > 0) or reduces (delta_units < 0) an open
        position by exactly delta_units, as ONE new fill with its own
        commission (CONFIDENCE_SIZING_DESIGN.md Section 8.2). Not yet
        called from run()'s signal loop - built and tested standalone for
        Phase 1 Step 3 (PositionManager) to call once that layer exists.
        If delta_units fully flattens the position, sets exit_time/
        exit_price/reason_close exactly as _close_trade does, so a caller
        doesn't need two different code paths for "reduce" vs "close"."""
        assert trade.is_open, "rescale_trade requires an open trade"
        assert delta_units != 0, "rescale_trade requires a non-zero delta_units"

        rounded_delta = self._apply_lot_constraints(delta_units)
        assert rounded_delta != 0, "delta_units rounded to zero by lot_step - reject before calling rescale_trade"

        if not trade.fills:
            trade.fills.append(Fill(
                units=trade.units, price=trade.entry_price, timestamp=trade.entry_time,
                commission=trade.commission_paid, reason=trade.reason_open,
            ))

        current_net = trade.net_units()
        increasing = rounded_delta > 0
        price = self._increasing_price(trade.side, candle) if increasing else self._decreasing_price(trade.side, candle)
        commission = self._commission_amount(price, abs(rounded_delta))

        trade.fills.append(Fill(units=rounded_delta, price=price, timestamp=candle.timestamp, commission=commission, reason=reason))
        trade.commission_paid += commission

        if increasing:
            trade.entry_price = (trade.entry_price * current_net + price * rounded_delta) / (current_net + rounded_delta)

        new_net = trade.net_units()
        if new_net <= 1e-9:
            trade.exit_time = candle.timestamp
            trade.exit_price = price
            trade.reason_close = reason
        return trade

    def _apply_lot_constraints(self, units: float) -> float:
        """Section 9.2: round toward zero to the nearest lot_step, and
        reject (return 0) anything under min_order_size. No-op (returns
        units unchanged) when either constraint is unset - the default,
        until real per-instrument limits are sourced (see CostModel)."""
        cm = self.cost_model
        magnitude = abs(units)
        if cm.min_order_size is not None and magnitude < cm.min_order_size:
            return 0.0
        if cm.lot_step is not None and cm.lot_step > 0:
            steps = round(magnitude / cm.lot_step)
            magnitude = steps * cm.lot_step
        return magnitude if units > 0 else -magnitude

    def _increasing_price(self, side: str, candle: Candle) -> float:
        assert side in ("LONG", "SHORT"), f"unknown trade side: {side}"
        if side == "LONG":
            return candle.ask_close + self._slippage_amount(candle.ask_close)
        return candle.bid_close - self._slippage_amount(candle.bid_close)

    def _decreasing_price(self, side: str, candle: Candle) -> float:
        assert side in ("LONG", "SHORT"), f"unknown trade side: {side}"
        if side == "LONG":
            return candle.bid_close - self._slippage_amount(candle.bid_close)
        return candle.ask_close + self._slippage_amount(candle.ask_close)

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
        would_be_exit_price = self._decreasing_price(trade.side, candle)
        net_units = trade.net_units() if trade.fills else trade.units

        direction = 1 if trade.side == "LONG" else -1
        gross = (would_be_exit_price - trade.entry_price) * direction * net_units
        would_be_exit_commission = self._commission_amount(would_be_exit_price, net_units)
        return gross - trade.commission_paid - would_be_exit_commission

    def _open_trade(self, side: str, candle: Candle, reason: str) -> Trade:
        assert side in ("LONG", "SHORT"), f"side must be 'LONG' or 'SHORT', got {side!r}"
        assert self.units_per_trade > 0, "units_per_trade must be positive"
        price = self._increasing_price(side, candle)
        commission = self._commission_amount(price, self.units_per_trade)
        trade = Trade(
            side=side, units=self.units_per_trade, entry_time=candle.timestamp,
            entry_price=price, commission_paid=commission, reason_open=reason,
        )
        trade.fills.append(Fill(units=self.units_per_trade, price=price, timestamp=candle.timestamp, commission=commission, reason=reason))
        return trade

    def _close_trade(self, trade: Trade, candle: Candle, reason: str) -> None:
        assert trade.is_open, "_close_trade called on a trade that is already closed"
        assert trade.side in ("LONG", "SHORT"), f"unknown trade side: {trade.side}"
        price = self._decreasing_price(trade.side, candle)
        net_units = trade.net_units() if trade.fills else trade.units

        trade.exit_time = candle.timestamp
        trade.exit_price = price
        trade.reason_close = reason
        close_commission = self._commission_amount(price, net_units)
        trade.commission_paid += close_commission
        trade.fills.append(Fill(units=-net_units, price=price, timestamp=candle.timestamp, commission=close_commission, reason=reason))
