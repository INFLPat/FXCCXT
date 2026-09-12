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
endpoints.

CONFIRMED BUG, FOUND AND FIXED (first real run against a practice account):
fetch_candles previously built its 'from'/'to' request parameters with
Python's datetime.isoformat(), which renders a UTC-aware datetime as
"...+00:00" (e.g. "2026-01-01T00:00:00+00:00"). OANDA's v20 API expects a
literal "Z" suffix for UTC instead (its own documented examples use
"2017-01-01T00:00:00Z", and its own candle responses are always Z-suffixed)
- sending "+00:00" produced an immediate 400 Bad Request on every single
FX instrument, every time, which is exactly what a real run surfaced. Fixed
via _format_oanda_time() below. This had been sitting untested since this
sandbox has no network access to catch it sooner - now genuinely verified
against a live 400 error and a reasoned fix, though the FIX ITSELF still
needs a real re-run to confirm it actually resolves it (I still can't reach
OANDA's servers to confirm this myself).

get_quote and place_market_order have not been exercised against a real
account at all yet - treat them with the same caution fetch_candles had
until this fix, since they build request bodies by hand in the same way.
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


def _format_oanda_time(dt: datetime) -> str:
    """OANDA's v20 API expects RFC3339 timestamps with a literal 'Z' suffix
    for UTC - NOT Python's default isoformat(), which produces '+00:00'.
    Confirmed by a real 400 Bad Request against a practice account before
    this fix - see module docstring."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


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
        """
        SECOND CONFIRMED BUG, FOUND AND FIXED (same real-run process as the
        timestamp fix above): OANDA's own OpenAPI spec states explicitly -
        "Count should not be specified if both the start and end parameters
        are provided, as the time range combined with the granularity will
        determine the number of candlesticks to return." This method used to
        send `from`, `to`, AND `count` together on every request, which is
        exactly the combination OANDA's spec says not to use - and matches
        the 400 Bad Request that persisted even after the timestamp-format
        fix alone. Fixed by never sending `to` to the API at all: paginate
        with `from`+`count` only (the standard, spec-compliant pattern), and
        filter each response against `end` client-side instead.
        """
        all_candles: list[Candle] = []
        cursor = start

        while cursor < end:
            params = {
                "price": "BA",
                "granularity": granularity,
                "from": _format_oanda_time(cursor),
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
                rc_time = datetime.fromisoformat(rc["time"].replace("Z", "+00:00"))
                if rc_time > end:
                    continue  # past the requested range - skip, don't assume ordering
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

            last_time = datetime.fromisoformat(raw_candles[-1]["time"].replace("Z", "+00:00"))
            if len(raw_candles) < MAX_CANDLES_PER_REQUEST or last_time >= end:
                break
            if last_time <= cursor:
                break  # safety: avoid infinite loop if pagination doesn't advance
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
                "units": str(int(units)),
                "timeInForce": "FOK",
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
