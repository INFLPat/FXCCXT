"""
strategy/base.py

All strategies implement this interface. The backtest engine and the (future)
live executor both call `on_candle`, so a strategy written once works
identically in backtest and live/paper trading - this is important: it means
your backtest is actually testing the code that will run live, not a
different reimplementation of it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from data.store import Candle


class Signal(Enum):
    HOLD = "HOLD"
    BUY = "BUY"       # open/flip to a long position
    SELL = "SELL"      # open/flip to a short position
    CLOSE = "CLOSE"     # close any open position, stay flat


@dataclass
class StrategyDecision:
    signal: Signal
    reason: str = ""  # human-readable, useful for debugging/logging


class Strategy(ABC):
    """Base class for all strategies. Subclasses hold their own indicator state."""

    @abstractmethod
    def on_candle(self, candle: Candle, history: list[Candle]) -> StrategyDecision:
        """
        Called once per new candle, in chronological order.

        `candle` is the latest completed candle.
        `history` is all candles up to and including `candle` (oldest first) -
        provided for convenience so strategies don't need to maintain their
        own buffer, though for performance on large datasets you may want to.

        IMPORTANT: only use `candle`/`history` data - never peek at future
        candles. The engine only gives you what's happened so far, but it's
        worth being deliberate about this since it's the single easiest way
        to accidentally invalidate a backtest (lookahead bias).
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Called before each backtest run to clear any internal state."""
        pass
