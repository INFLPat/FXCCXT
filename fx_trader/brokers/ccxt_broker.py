"""
brokers/ccxt_broker.py

Crypto exchange adapter built on ccxt (https://github.com/ccxt/ccxt), which
covers 100+ exchanges through one consistent API. This implements the same
BrokerAdapter interface as OandaBroker (brokers/base.py), so crypto exchanges
plug into the same strategy/backtest/router code FX does. Which exchange it
talks to is just a constructor argument (`exchange_id`, e.g. 'binance',
'kraken', 'coinbase') - adding a new exchange means one line, not a new file.

IMPORTANT DIFFERENCE FROM OandaBroker - historical bid/ask:
Crypto exchanges' historical OHLCV endpoints return a single trade-price
series, not separate bid/ask candles like OANDA provides. So historical
candles here set bid == ask == the exchange's reported price, and trading
costs are modeled entirely through CostModel's commission_pct (the
exchange's maker/taker fee) and slippage_pct, rather than a historical
spread. This is the same simplification most retail crypto backtesting
tools make, but it means your backtest's cost realism depends entirely on
setting commission_pct/slippage_pct to sensible values for your exchange
and pair - look up the actual taker fee rather than trusting a default.

Live quotes (get_quote) DO use the exchange's real bid/ask from its ticker,
where the exchange provides one.

Testing status: this sandbox has neither the ccxt library nor network
access, so nothing here has run against a real exchange. To still get real
verification rather than just careful writing, the exchange client is
injected via a constructor parameter - that let me test the OHLCV-mapping,
pagination, quote, order, and balance logic against a fake exchange object
(tests/test_ccxt_broker.py) without needing ccxt installed at all. That
logic is verified. What is NOT verified: that a real ccxt exchange object
actually behaves the way its documentation says. Test thoroughly against an
exchange's sandbox/testnet (most majors have one) before trusting this, and
before place_market_order ever touches a funded account.
"""

import os
import warnings
from datetime import datetime, timezone

from brokers.base import BrokerAdapter, OrderResult, Quote
from data.store import Candle

# Seconds per candle for each ccxt-style timeframe this adapter has been
# designed against. ccxt itself supports more; add entries here as needed -
# used only to advance pagination correctly.
TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

DEFAULT_OHLCV_LIMIT = 1000


class CcxtBroker(BrokerAdapter):
    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str | None = None,
        api_secret: str | None = None,
        sandbox: bool = True,
        quote_currency: str = "USDT",
        exchange=None,  # inject a real ccxt exchange instance, or a fake for testing
    ):
        """
        sandbox=True (default) points at the exchange's testnet/sandbox if
        ccxt supports one for it - deliberately the safe default, mirroring
        OandaBroker's environment='practice' default. Flip to False only
        once you've verified everything on sandbox.
        """
        self.exchange_id = exchange_id
        self.quote_currency = quote_currency
        self._sandbox = sandbox

        if exchange is not None:
            self.exchange = exchange
            return

        try:
            import ccxt
        except ImportError as exc:
            raise ImportError(
                "ccxt is required to use CcxtBroker against a real exchange. "
                "Install it with: pip install ccxt"
            ) from exc

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"ccxt has no exchange named '{exchange_id}'")

        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            "apiKey": api_key or os.environ.get(f"{exchange_id.upper()}_API_KEY"),
            "secret": api_secret or os.environ.get(f"{exchange_id.upper()}_API_SECRET"),
            "enableRateLimit": True,
        })

        if sandbox:
            if hasattr(self.exchange, "set_sandbox_mode"):
                self.exchange.set_sandbox_mode(True)
            else:
                warnings.warn(
                    f"ccxt's '{exchange_id}' has no sandbox mode - this client "
                    "will talk to the real exchange even with sandbox=True. "
                    "Double-check before placing any order.",
                    stacklevel=2,
                )

    @property
    def name(self) -> str:
        return f"ccxt:{self.exchange_id}"

    def fetch_candles(
        self, instrument: str, granularity: str, start: datetime, end: datetime
    ) -> list[Candle]:
        interval_s = TIMEFRAME_SECONDS.get(granularity)
        if interval_s is None:
            raise ValueError(
                f"Unrecognized granularity '{granularity}'. Known: {list(TIMEFRAME_SECONDS)}. "
                "Add it to TIMEFRAME_SECONDS if your exchange supports it."
            )

        all_candles: list[Candle] = []
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        while since_ms < end_ms:
            batch = self.exchange.fetch_ohlcv(
                instrument, timeframe=granularity, since=since_ms, limit=DEFAULT_OHLCV_LIMIT
            )
            if not batch:
                break

            for ts_ms, o, h, l, c, vol in batch:
                if ts_ms > end_ms:
                    break
                ts_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.000000000Z"
                )
                # no separate historical bid/ask available - see module docstring
                all_candles.append(
                    Candle(
                        instrument=instrument, granularity=granularity, timestamp=ts_iso,
                        bid_open=o, bid_high=h, bid_low=l, bid_close=c,
                        ask_open=o, ask_high=h, ask_low=l, ask_close=c,
                        volume=int(vol) if vol is not None else None,
                    )
                )

            last_ts_ms = batch[-1][0]
            if last_ts_ms <= since_ms:
                break  # safety: avoid infinite loop if pagination doesn't advance
            since_ms = last_ts_ms + interval_s * 1000

        return all_candles

    def get_quote(self, instrument: str) -> Quote:
        ticker = self.exchange.fetch_ticker(instrument)
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None:
            last = ticker.get("last")
            if last is None:
                raise RuntimeError(f"No usable price in ticker for {instrument} on {self.exchange_id}")
            print(
                f"[CcxtBroker] {self.exchange_id} ticker for {instrument} has no bid/ask "
                "- using last trade price for both (spread will read as zero)."
            )
            bid = ask = last

        return Quote(
            broker=self.name, instrument=instrument,
            bid=float(bid), ask=float(ask),
            timestamp=str(ticker.get("timestamp") or ""),
        )

    def place_market_order(self, instrument: str, units: float) -> OrderResult:
        """
        Places a market order. units > 0 buys, units < 0 sells; abs(units) is
        the amount of the base asset (e.g. BTC in BTC/USDT).
        UNTESTED against a real exchange - verify on sandbox/testnet first.
        """
        side = "buy" if units > 0 else "sell"
        amount = abs(units)
        response = self.exchange.create_order(instrument, "market", side, amount)

        raw_status = response.get("status")
        status = "FILLED" if raw_status in (None, "closed", "filled") else str(raw_status).upper()
        fill_price = response.get("average") or response.get("price")

        return OrderResult(
            broker=self.name, order_id=str(response.get("id", "")), instrument=instrument,
            units=units, fill_price=float(fill_price) if fill_price else None,
            status=status, raw_response=response,
        )

    def get_account_balance(self) -> float:
        """
        Returns the free (available to trade) balance of `quote_currency`
        (default USDT) - NOT a total portfolio value across all held assets.
        Crypto balances are inherently multi-asset; this mirrors "cash
        available for a new trade" rather than net worth.
        """
        balance = self.exchange.fetch_balance()
        free = balance.get("free", {})
        return float(free.get(self.quote_currency, 0.0))
