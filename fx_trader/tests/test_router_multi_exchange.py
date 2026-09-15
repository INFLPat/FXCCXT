"""
tests/test_router_multi_exchange.py

Proves BrokerRouter routes to the better-priced of two exchanges quoting the
same instrument differently - more meaningful for crypto than FX, since real
cross-exchange crypto price gaps are often larger than fraction-of-a-pip FX
broker differences.

Run: python -m tests.test_router_multi_exchange
"""

from brokers.ccxt_broker import CcxtBroker
from brokers.router import BrokerRouter
from tests.fake_ccxt_exchange import FakeCcxtExchange


def test_router_picks_cheaper_ask_for_a_buy():
    exchange_a = FakeCcxtExchange(ticker={"bid": 42000.0, "ask": 42015.0, "timestamp": 1})
    exchange_b = FakeCcxtExchange(ticker={"bid": 41995.0, "ask": 42005.0, "timestamp": 1})

    broker_a = CcxtBroker(exchange_id="exchange_a", exchange=exchange_a)
    broker_a.exchange_id = "exchange_a"
    broker_b = CcxtBroker(exchange_id="exchange_b", exchange=exchange_b)
    broker_b.exchange_id = "exchange_b"

    router = BrokerRouter([broker_a, broker_b])
    decision = router.best_quote_for_buy("BTC/USDT")

    assert decision.chosen_broker == "ccxt:exchange_b", f"expected the cheaper ask exchange, got {decision.chosen_broker}"
    assert decision.quote.ask == 42005.0
    assert len(decision.all_quotes) == 2
    print("Router buy-routing assertions passed.")


def test_router_picks_better_bid_for_a_sell():
    exchange_a = FakeCcxtExchange(ticker={"bid": 42000.0, "ask": 42015.0, "timestamp": 1})
    exchange_b = FakeCcxtExchange(ticker={"bid": 41990.0, "ask": 42005.0, "timestamp": 1})

    broker_a = CcxtBroker(exchange_id="exchange_a", exchange=exchange_a)
    broker_a.exchange_id = "exchange_a"
    broker_b = CcxtBroker(exchange_id="exchange_b", exchange=exchange_b)
    broker_b.exchange_id = "exchange_b"

    router = BrokerRouter([broker_a, broker_b])
    decision = router.best_quote_for_sell("BTC/USDT")

    assert decision.chosen_broker == "ccxt:exchange_a", f"expected the better bid exchange, got {decision.chosen_broker}"
    assert decision.quote.bid == 42000.0
    print("Router sell-routing assertions passed.")


def test_router_skips_a_failing_exchange_gracefully():
    class BrokenExchange(FakeCcxtExchange):
        def fetch_ticker(self, symbol):
            raise ConnectionError("simulated API outage")

    working = FakeCcxtExchange(ticker={"bid": 100.0, "ask": 101.0, "timestamp": 1})
    broken = BrokenExchange()

    broker_working = CcxtBroker(exchange_id="working", exchange=working)
    broker_working.exchange_id = "working"
    broker_broken = CcxtBroker(exchange_id="broken", exchange=broken)
    broker_broken.exchange_id = "broken"

    router = BrokerRouter([broker_working, broker_broken])
    quotes = router.get_all_quotes("BTC/USDT")

    assert len(quotes) == 1, "the broken exchange should be skipped, not crash the router"
    assert quotes[0].broker == "ccxt:working"
    print("Router graceful-failure assertions passed.")


if __name__ == "__main__":
    test_router_picks_cheaper_ask_for_a_buy()
    test_router_picks_better_bid_for_a_sell()
    test_router_skips_a_failing_exchange_gracefully()
