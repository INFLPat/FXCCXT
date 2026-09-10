"""
brokers/base.py

Common interface every broker integration implements. The point of this file:
strategies, the backtest engine, and (eventually) a multi-broker router should
never need to know which broker they're talking to - they just need "give me
candles", "give me a live quote", "place this order". Each broker's quirks
get absorbed inside its own adapter class.

This is what makes "use whichever broker is best at the time" achievable
later without a rewrite: a router just holds a list of BrokerAdapter
instances and picks between them using this same interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from data.store import Candle


@dataclass
class Quote:
    broker: str
    instrument: str
    bid: float
    ask: float
    timestamp: str

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class OrderResult:
    broker: str
    order_id: str
    instrument: str
    units: float          # positive = bought, negative = sold
    fill_price: float | None
    status: str            # e.g. 'FILLED', 'PENDING', 'REJECTED'
    raw_response: dict | None = None


class BrokerAdapter(ABC):
    """Base class every broker integration (OANDA, IBKR, FXCM, ...) implements."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short broker identifier, e.g. 'oanda'."""
        raise NotImplementedError

    @abstractmethod
    def fetch_candles(
        self, instrument: str, granularity: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Historical OHLC candles for backtesting."""
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, instrument: str) -> Quote:
        """Current live bid/ask, for both trading decisions and best-price routing."""
        raise NotImplementedError

    @abstractmethod
    def place_market_order(self, instrument: str, units: float) -> OrderResult:
        """
        Place a market order. Positive units = buy, negative units = sell.

        Every adapter implementing this should be tested extensively against
        that broker's practice/demo/paper environment before ever being
        pointed at a live account.
        """
        raise NotImplementedError

    @abstractmethod
    def get_account_balance(self) -> float:
        """Current account balance in the account's home currency."""
        raise NotImplementedError
