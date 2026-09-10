"""
tests/fake_ccxt_exchange.py

A minimal stand-in for a ccxt exchange object, implementing just the methods
CcxtBroker calls (fetch_ohlcv, fetch_ticker, create_order, fetch_balance).
Lets CcxtBroker's own logic - OHLCV-to-Candle mapping, pagination, quote/order/
balance handling - be tested with no network access and without the ccxt
package installed at all, by injecting this instead of a real exchange.
"""


class FakeCcxtExchange:
    def __init__(self, ohlcv_pages=None, ticker=None, order_response=None, balance=None):
        # each call to fetch_ohlcv pops the next page, so pagination can be exercised
        self._ohlcv_pages = list(ohlcv_pages) if ohlcv_pages else []
        self._ticker = ticker or {}
        self._order_response = order_response or {}
        self._balance = balance or {"free": {}}
        self.ohlcv_calls = []
        self.orders_placed = []

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.ohlcv_calls.append({"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit})
        if not self._ohlcv_pages:
            return []
        return self._ohlcv_pages.pop(0)

    def fetch_ticker(self, symbol):
        return self._ticker

    def create_order(self, symbol, type_, side, amount):
        self.orders_placed.append({"symbol": symbol, "type": type_, "side": side, "amount": amount})
        return self._order_response

    def fetch_balance(self):
        return self._balance
