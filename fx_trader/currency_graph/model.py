# version: 261005
"""
currency_graph/model.py

Edge data model for the currency graph (chat 14p1). Design record:
CONTEXT_HANDOFF.md Section 4g; definitions: RESEARCH_NOTES.md Sections 2-4.
Pure stdlib and imports nothing from the repo - in particular NOT
instrument_config, which asserts exactly 16 instruments (19p1 replaces that).

EDGE TYPES
- QUOTED: a direct pair on one venue, with bid and ask.
- BASIS: an unquoted equivalence between two assets (e.g. USDT~USD). Carries
  an explicit `assumed` flag and a rate of exp(basis_bps/1e4) (default 0 bps:
  the alias). `weight` is the soft-constraint weight for later least-squares
  work (None = hard alias). If a venue later QUOTES the pair (INV-16), swap
  the BASIS edge for a QUOTED one - nothing else changes.
- VENUE: the same asset on two venues (transfer friction, not a price). Only
  present in the (asset, venue) projection; ignored by the asset projection.

ORIENTATION: an edge's rate is quote-per-base, so GBP_USD means USD per GBP.
canonicalise() makes the orientation deterministic (base < quote, VENUE:
venue < venue_b) by inverting (bid' = 1/ask, ask' = 1/bid). A quote and its
inverse feed are then identical edges.

ENDPOINTS: every edge joins (base, venue) to (quote, venue_b or venue). That
one rule lets a BASIS edge join USDT on one venue to USD on another.

COSTS: `cost` is None unless the caller supplies a CostSpec; every CostSpec
field is None = unknown. No fee is ever defaulted here. Costs gate: no
profitability claim until real costs exist (ROADMAP chat 33).

QUOTE FIELDS: `spread_known` False means bid == ask carries no spread
information (crypto bars; BASIS; VENUE) - evaluate.py then returns UNRATED
rather than a false pass.
"""

import math
from dataclasses import dataclass, field, replace
from enum import Enum

BPS = 1e4
SEPARATORS = ("/", "_")


class EdgeType(Enum):
    QUOTED = "QUOTED"
    BASIS = "BASIS"
    VENUE = "VENUE"


@dataclass(frozen=True)
class CostSpec:
    """Per-edge trading cost. None = unknown (never defaulted)."""

    taker_bps: float | None = None
    maker_bps: float | None = None
    fixed: float | None = None   # account currency; needs a notional to compare, unused by evaluate.py today

    def __post_init__(self):
        for name in ("taker_bps", "maker_bps", "fixed"):
            value = getattr(self, name)
            if value is not None and not value >= 0:
                raise ValueError(f"CostSpec.{name} must be >= 0 or None, got {value}")


@dataclass(frozen=True)
class Edge:
    edge_type: EdgeType
    base: str
    quote: str
    venue: str
    instrument: str
    bid: float
    ask: float
    spread_known: bool = True
    venue_b: str | None = None
    ts_epoch: int | None = None
    bar_convention: str = "open"            # AR-5: stamp = bar OPEN time
    source: str = ""
    data_object: str = "bar_close_bid_ask"  # RESEARCH_NOTES Section 2
    volume: float | None = None
    cost: CostSpec | None = None
    assumed: bool = False
    weight: float | None = None
    meta: dict = field(default_factory=dict)  # staleness, tradeable, qc_flag, depth... added when their chat arrives

    def __post_init__(self):
        if not self.base or not self.quote or not self.venue:
            raise ValueError("base, quote and venue must be non-empty")
        if not (0 < self.bid <= self.ask):
            raise ValueError(f"need 0 < bid <= ask, got {self.bid}, {self.ask} ({self.instrument})")
        if self.edge_type is EdgeType.VENUE:
            if self.base != self.quote or not self.venue_b or self.venue_b == self.venue:
                raise ValueError("VENUE edge needs base == quote and two different venues")
        elif self.base == self.quote:
            raise ValueError(f"{self.edge_type.value} edge needs two different assets, got {self.base}")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def log_mid(self) -> float:
        return math.log(self.mid)

    @property
    def spread_bps(self) -> float:
        return (math.log(self.ask) - math.log(self.bid)) * BPS

    @property
    def end_a(self) -> tuple[str, str]:
        return (self.base, self.venue)

    @property
    def end_b(self) -> tuple[str, str]:
        return (self.quote, self.venue_b or self.venue)


def parse_instrument(name: str) -> tuple[str, str]:
    """'GBP_USD' / 'BTC/GBP' -> (base, quote)."""
    assert name, "instrument name required"
    parts = name.split("/" if "/" in name else "_")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"cannot split instrument {name!r} into base/quote")
    return parts[0], parts[1]


def quoted_edge(instrument: str, venue: str, bid: float, ask: float, spread_known: bool | None = None, **fields) -> Edge:
    """spread_known defaults to ask > bid; pass it explicitly for a genuine
    zero-spread FX bar. **fields: ts_epoch, volume, cost, source, ..."""
    base, quote = parse_instrument(instrument)
    known = (ask > bid) if spread_known is None else spread_known
    return Edge(EdgeType.QUOTED, base, quote, venue, instrument, bid, ask, spread_known=known, **fields)


def basis_edge(
    base: str, quote: str, venue: str, basis_bps: float = 0.0, venue_b: str | None = None,
    weight: float | None = None, assumed: bool = True, **fields,
) -> Edge:
    """Unquoted equivalence: 1 base = exp(basis_bps/1e4) quote. Default 0
    bps = the alias, flagged assumed (never silently zero)."""
    assert math.isfinite(basis_bps), "basis_bps must be finite"
    rate = math.exp(basis_bps / BPS)
    return Edge(EdgeType.BASIS, base, quote, venue, f"{base}~{quote}", rate, rate, spread_known=False,
                venue_b=venue_b, assumed=assumed, weight=weight, **fields)


def venue_edge(asset: str, venue_a: str, venue_b: str, cost: CostSpec | None = None, **fields) -> Edge:
    """Same asset on two venues; rate 1, no spread information."""
    return Edge(EdgeType.VENUE, asset, asset, venue_a, f"{asset}@{venue_a}->{venue_b}", 1.0, 1.0,
                spread_known=False, venue_b=venue_b, cost=cost, **fields)


def invert_edge(edge: Edge) -> Edge:
    """The same market seen from the other side: bid' = 1/ask, ask' = 1/bid,
    base and quote (and their venues) swapped. Involution (twice = original)."""
    assert edge.bid > 0 and edge.ask > 0, "non-positive quote"
    if edge.edge_type is EdgeType.VENUE:
        return replace(edge, venue=edge.venue_b, venue_b=edge.venue)
    meta = dict(edge.meta)
    meta["inverted"] = not meta.get("inverted", False)
    return replace(
        edge, base=edge.quote, quote=edge.base, bid=1 / edge.ask, ask=1 / edge.bid, meta=meta,
        venue=edge.venue_b or edge.venue, venue_b=edge.venue if edge.venue_b else None,
    )


def canonicalise(edge: Edge) -> Edge:
    """Deterministic orientation: base < quote (VENUE: venue < venue_b).
    The ordering is arbitrary but fixed; `meta['inverted']` records a flip."""
    assert edge.base and edge.quote, "empty asset name"
    if edge.edge_type is EdgeType.VENUE:
        return edge if edge.venue <= edge.venue_b else invert_edge(edge)
    return edge if edge.base < edge.quote else invert_edge(edge)


def with_quote(edge: Edge, bid: float, ask: float, ts_epoch: int | None = None, volume: float | None = None) -> Edge:
    """Same edge, new bar's quote - for building per-bar slices from a
    stored topology."""
    assert edge.edge_type is EdgeType.QUOTED, "only QUOTED edges take a new quote"
    return replace(edge, bid=bid, ask=ask, ts_epoch=ts_epoch, volume=volume)
