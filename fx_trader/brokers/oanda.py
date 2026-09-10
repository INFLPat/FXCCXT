"""
brokers/oanda.py

OANDA v20 REST API, implementing the BrokerAdapter interface (see
brokers/base.py) so it plugs into the same strategy/backtest/router code as
any other broker.

Setup:
1. Create a free practice account: https://www.oanda.com/demo-account/
2. In "My Account" > "Manage API Access", generate a personal access token.
3. export OANDA_API_TOKEN="your-token-here"
   export OANDA_ACCOUNT_ID="your-practice-account-id"

Testing status: fetch_candles was carried over from the original client and
follows OANDA's documented v20 candle endpoint. get_quote and
place_market_order are new and implement the documented pricing and order
endpoints, but - like everything broker-facing in this project - I have not
been able to run any of this against OANDA's servers from this sandbox (no
network access here). Test thoroughly against your practice account before
trusting it, and definitely before place_market_order ever touches a live
account.
"""

import os
from datetime import datetime, timezone

import requests

from brokers.base import BrokerAdapter, OrderResult, Quote
from data.store import Candle

BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

MAX_CANDLES_PER_REQUEST = 5000


class OandaBroker(BrokerAdapter):
    def __init__(
        self,
        api_token: str | None = None,
        account_id: str | None = None,
        environment: str = "practice",
    ):
        self.api_token = api_token or os.environ.get("OANDA_API_TOKEN")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID")
        if not self.api_token:
            raise ValueError("No API token provided. Set OANDA_API_TOKEN env var or pass api_token=.")
        if not self.account_id:
            raise ValueError("No account ID provided. Set OANDA_ACCOUNT_ID env var or pass account_id=.")
        if environment not in BASE_URLS:
            raise ValueError(f"environment must be one of {list(BASE_URLS)}")
        if environment == "live":
            import warnings
            warnings.warn(
                "OandaBroker initialized with environment='live' - this will "
                "place real trades with real money if place_market_order is called.",
                stacklevel=2,
            )

        self.environment = environment
        self.base_url = BASE_URLS[environment]
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        )

    @property
    def name(self) -> str:
        return "oanda"

    def fetch_candles(
        self, instrument: str, granularity: str, start: datetime, end: datetime
    ) -> list[Candle]:
        all_candles: list[Candle] = []
        cursor = start

        while cursor < end:
            params = {
                "price": "BA",
                "granularity": granularity,
                "from": cursor.astimezone(timezone.utc).isoformat(),
                "to": end.astimezone(timezone.utc).isoformat(),
                "count": MAX_CANDLES_PER_REQUEST,
            }
            resp = self.session.get(
                f"{self.base_url}/v3/instruments/{instrument}/candles", params=params
            )
            resp.raise_for_status()
            raw_candles = resp.json().get("candles", [])
            if not raw_candles:
                break

            batch = []
            for rc in raw_candles:
                if not rc.get("complete", True):
                    continue
                bid, ask = rc["bid"], rc["ask"]
                batch.append(
                    Candle(
                        instrument=instrument, granularity=granularity, timestamp=rc["time"],
                        bid_open=float(bid["o"]), bid_high=float(bid["h"]),
                        bid_low=float(bid["l"]), bid_close=float(bid["c"]),
                        ask_open=float(ask["o"]), ask_high=float(ask["h"]),
                        ask_low=float(ask["l"]), ask_close=float(ask["c"]),
                        volume=rc.get("volume"),
                    )
                )
            all_candles.extend(batch)

            if len(raw_candles) < MAX_CANDLES_PER_REQUEST:
                break
            last_time = datetime.fromisoformat(raw_candles[-1]["time"].replace("Z", "+00:00"))
            if last_time <= cursor:
                break
            cursor = last_time

        return all_candles

    def get_quote(self, instrument: str) -> Quote:
        """Current live bid/ask via OANDA's pricing endpoint."""
        resp = self.session.get(
            f"{self.base_url}/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument},
        )
        resp.raise_for_status()
        prices = resp.json().get("prices", [])
        if not prices:
            raise RuntimeError(f"No pricing returned for {instrument}")
        p = prices[0]
        # OANDA returns arrays of price "buckets" at different liquidity tiers;
        # the first bucket is the best available price
        return Quote(
            broker=self.name,
            instrument=instrument,
            bid=float(p["bids"][0]["price"]),
            ask=float(p["asks"][0]["price"]),
            timestamp=p["time"],
        )

    def place_market_order(self, instrument: str, units: float) -> OrderResult:
        """
        Places a market order. units > 0 buys, units < 0 sells.
        UNTESTED against live OANDA servers - verify against a practice
        account before relying on this, and before ever using environment='live'.
        """
        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(int(units)),  # OANDA expects a string; positive=buy, negative=sell
                "timeInForce": "FOK",       # fill-or-kill: avoids partial fills at an unexpected price
                "positionFill": "DEFAULT",
            }
        }
        resp = self.session.post(
            f"{self.base_url}/v3/accounts/{self.account_id}/orders", json=body
        )
        resp.raise_for_status()
        payload = resp.json()

        fill = payload.get("orderFillTransaction")
        if fill:
            return OrderResult(
                broker=self.name, order_id=fill.get("id", ""), instrument=instrument,
                units=units, fill_price=float(fill.get("price", 0)) or None,
                status="FILLED", raw_response=payload,
            )
        cancel = payload.get("orderCancelTransaction")
        if cancel:
            return OrderResult(
                broker=self.name, order_id=cancel.get("id", ""), instrument=instrument,
                units=units, fill_price=None, status="REJECTED", raw_response=payload,
            )
        return OrderResult(
            broker=self.name, order_id="", instrument=instrument,
            units=units, fill_price=None, status="PENDING", raw_response=payload,
        )

    def get_account_balance(self) -> float:
        resp = self.session.get(f"{self.base_url}/v3/accounts/{self.account_id}/summary")
        resp.raise_for_status()
        return float(resp.json()["account"]["balance"])
