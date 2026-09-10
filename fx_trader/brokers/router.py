"""
brokers/router.py

Holds multiple BrokerAdapter instances and picks the best one for a given
trade. Currently this project only has one working adapter (OANDA) - this
class is here so the architecture is ready for a second and third adapter
without restructuring anything else. Add a broker by writing an adapter that
implements BrokerAdapter, then pass it into BrokerRouter alongside OANDA.

Read this before expecting much from "best price" routing on FX majors:
live spread differences between reputable brokers are typically well under a
pip. Routing genuinely helps with *redundancy* (fall back to broker B if
broker A's API is down) and *access* (different brokers offer different
instruments or cost structures) more than it helps with tick-by-tick price
arbitrage. Don't expect routing alone to meaningfully move your returns.
"""

from dataclasses import dataclass

from brokers.base import BrokerAdapter, OrderResult, Quote


@dataclass
class RoutingDecision:
    chosen_broker: str
    quote: Quote
    all_quotes: list[Quote]
    reason: str


class BrokerRouter:
    def __init__(self, adapters: list[BrokerAdapter]):
        if not adapters:
            raise ValueError("BrokerRouter needs at least one adapter")
        self.adapters = {a.name: a for a in adapters}

    def get_all_quotes(self, instrument: str) -> list[Quote]:
        """Queries every connected broker for a live quote. If a broker's API
        call fails, it's skipped (logged, not raised) so one broker outage
        doesn't take down the whole router."""
        quotes = []
        for adapter in self.adapters.values():
            try:
                quotes.append(adapter.get_quote(instrument))
            except Exception as exc:  # noqa: BLE001 - deliberately broad: one broker failing shouldn't crash routing
                print(f"[BrokerRouter] {adapter.name} quote failed for {instrument}: {exc}")
        return quotes

    def best_quote_for_buy(self, instrument: str) -> RoutingDecision:
        """For buying, lower ask price is better."""
        quotes = self.get_all_quotes(instrument)
        if not quotes:
            raise RuntimeError(f"No brokers returned a quote for {instrument}")
        best = min(quotes, key=lambda q: q.ask)
        return RoutingDecision(
            chosen_broker=best.broker, quote=best, all_quotes=quotes,
            reason=f"lowest ask ({best.ask}) among {[q.broker for q in quotes]}",
        )

    def best_quote_for_sell(self, instrument: str) -> RoutingDecision:
        """For selling, higher bid price is better."""
        quotes = self.get_all_quotes(instrument)
        if not quotes:
            raise RuntimeError(f"No brokers returned a quote for {instrument}")
        best = max(quotes, key=lambda q: q.bid)
        return RoutingDecision(
            chosen_broker=best.broker, quote=best, all_quotes=quotes,
            reason=f"highest bid ({best.bid}) among {[q.broker for q in quotes]}",
        )

    def place_best_market_order(self, instrument: str, units: float) -> OrderResult:
        """Routes a market order to whichever connected broker currently
        quotes the best price for the requested side."""
        decision = (
            self.best_quote_for_buy(instrument) if units > 0
            else self.best_quote_for_sell(instrument)
        )
        adapter = self.adapters[decision.chosen_broker]
        print(f"[BrokerRouter] Routing {instrument} order to {decision.chosen_broker}: {decision.reason}")
        return adapter.place_market_order(instrument, units)
