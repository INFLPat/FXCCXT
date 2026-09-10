"""
tests/test_ccxt_broker.py

Tests CcxtBroker's own logic - OHLCV-to-Candle mapping, pagination cursor
advancement, quote/order/balance handling - against a fake exchange object.
This verifies everything CcxtBroker does with whatever an exchange gives it;
it does NOT verify that a real ccxt exchange actually behaves this way (see
brokers/ccxt_broker.py docstring).

Run with: python -m tests.test_ccxt_broker
"""

from datetime import datetime, timezone

from brokers.ccxt_broker import CcxtBroker
from tests.fake_ccxt_exchange import FakeCcxtExchange


def test_fetch_candles_pagination_and_mapping():
    # two pages: first page full (hits the limit, so the broker should ask for
    # a second page), second page short (signals "last page")
    page1 = [
        [1704067200000, 42000.0, 42100.0, 41900.0, 42050.0, 12.5],  # 2024-01-01T00:00:00Z
        [1704070800000, 42050.0, 42200.0, 42000.0, 42150.0, 9.8],   # 2024-01-01T01:00:00Z
    ]
    page2 = [
        [1704074400000, 42150.0, 42300.0, 42100.0, 42250.0, 7.1],   # 2024-01-01T02:00:00Z
    ]
    fake = FakeCcxtExchange(ohlcv_pages=[page1, page2])
    broker = CcxtBroker(exchange=fake)

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 3, tzinfo=timezone.utc)
    candles = broker.fetch_candles("BTC/USDT", "1h", start, end)

    assert len(candles) == 3, f"expected 3 candles across 2 pages, got {len(candles)}"
    assert candles[0].timestamp == "2024-01-01T00:00:00.000000000Z"
    assert candles[0].bid_close == candles[0].ask_close == 42050.0, "bid/ask should equal the single OHLCV price"
    assert candles[-1].bid_close == 42250.0

    # pagination should have advanced `since` based on the last candle in
    # page 1, not just called fetch_ohlcv once
    assert len(fake.ohlcv_calls) == 2, f"expected 2 fetch_ohlcv calls (pagination), got {len(fake.ohlcv_calls)}"
    assert fake.ohlcv_calls[1]["since"] == 1704070800000 + 3600 * 1000, "cursor should advance by one interval past the last candle received"

    print("Pagination/mapping assertions passed.")


def test_get_quote_uses_real_bid_ask_when_available():
    fake = FakeCcxtExchange(ticker={"bid": 42000.0, "ask": 42010.0, "timestamp": 1704067200000})
    broker = CcxtBroker(exchange=fake)
    quote = broker.get_quote("BTC/USDT")

    assert quote.bid == 42000.0
    assert quote.ask == 42010.0
    assert abs(quote.spread - 10.0) < 1e-9
    print("get_quote (real bid/ask) assertions passed.")


def test_get_quote_falls_back_to_last_price():
    fake = FakeCcxtExchange(ticker={"bid": None, "ask": None, "last": 42005.0, "timestamp": 1704067200000})
    broker = CcxtBroker(exchange=fake)
    quote = broker.get_quote("BTC/USDT")

    assert quote.bid == quote.ask == 42005.0, "should fall back to last price for both sides"
    print("get_quote (fallback) assertions passed.")


def test_place_market_order_side_mapping():
    fake = FakeCcxtExchange(order_response={"id": "abc123", "status": "closed", "average": 42030.0})
    broker = CcxtBroker(exchange=fake)

    buy_result = broker.place_market_order("BTC/USDT", 0.5)
    assert fake.orders_placed[-1]["side"] == "buy"
    assert fake.orders_placed[-1]["amount"] == 0.5
    assert buy_result.status == "FILLED"
    assert buy_result.fill_price == 42030.0

    sell_result = broker.place_market_order("BTC/USDT", -0.25)
    assert fake.orders_placed[-1]["side"] == "sell"
    assert fake.orders_placed[-1]["amount"] == 0.25
    assert sell_result.units == -0.25

    print("place_market_order assertions passed.")


def test_get_account_balance_uses_quote_currency():
    fake = FakeCcxtExchange(balance={"free": {"USDT": 1234.56, "BTC": 0.1}})
    broker = CcxtBroker(exchange=fake, quote_currency="USDT")
    assert broker.get_account_balance() == 1234.56

    broker_missing_currency = CcxtBroker(exchange=fake, quote_currency="EUR")
    assert broker_missing_currency.get_account_balance() == 0.0, "should return 0.0, not error, if currency isn't held"

    print("get_account_balance assertions passed.")


if __name__ == "__main__":
    test_fetch_candles_pagination_and_mapping()
    test_get_quote_uses_real_bid_ask_when_available()
    test_get_quote_falls_back_to_last_price()
    test_place_market_order_side_mapping()
    test_get_account_balance_uses_quote_currency()
    print("\nAll CcxtBroker tests passed.")
