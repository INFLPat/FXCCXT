# version: 261005
"""
currency_graph/evaluate.py

Per-cycle numbers, in basis points, matching audit_sandbox_alignment.py's
evaluate_cycle formulas (differential-tested in tests/test_currency_graph.py;
after the 14p2 refactor this module is the canonical copy and the audit
imports it). For a cycle walked with directions d:
  residual   = sum d * ln(mid)                       (0 when mids are consistent)
  gain_fwd   = sum (ln bid if d > 0 else -ln ask)    (executable, walked as given)
  gain_rev   = the same loop walked the other way
  spread     = sum ln(ask / bid)
evaluate_quotes() is the fast numeric core (one call per bar); evaluate_cycle()
adds structure (venues, assumed legs, costs) and a VERDICT.

VERDICTS (first match wins; a fee is never defaulted):
  VENUE_BLOCKED          legs on more than one venue (or a VENUE edge) and
                         preposition=False
  UNRATED                some leg has no spread information (crypto bars,
                         BASIS) - cannot be tested, same as the audit
  NO_GAIN                executable gain <= 0 in both directions
  GAIN_COST_UNKNOWN      gain > 0 but some leg's taker cost is unknown
  EXECUTABLE_AFTER_COST  gain minus summed taker_bps > 0
  COST_EATS_GAIN         gain > 0 but not after costs
preposition=True is the CALLER'S assertion that inventory already sits on
every venue in the cycle: VENUE legs are then not traversed and are excluded
from rating and cost. Summed taker_bps approximates per-leg fees as bps of
equal notionals - indicative, not a fill simulation.
"""

import math
from dataclasses import dataclass

from currency_graph.model import BPS, EdgeType

# Provisional data-validity edges (CONTEXT_HANDOFF 4f.4), copied from the audit.
RATIO_CLEAN = 0.25
RATIO_SUSPECT = 1.0
ABS_SUSPECT_BPS = 10.0
MAX_LEGS = 64


@dataclass(frozen=True)
class CycleEval:
    residual_bps: float
    gain_fwd_bps: float
    gain_rev_bps: float
    spread_bps: float
    ratio: float | None
    tier: str
    rated: bool
    multi_venue: bool
    uses_assumed: bool
    cost_bps: float | None
    net_gain_bps: float | None
    verdict: str


def evaluate_quotes(legs: list) -> tuple:
    """legs: [(bid, ask, d)]. Returns (residual, gain_fwd, gain_rev, spread) in bps."""
    assert 1 <= len(legs) <= MAX_LEGS, f"cycle must have 1..{MAX_LEGS} legs"
    resid = fwd = rev = spread = 0.0
    for bid, ask, d in legs:
        resid += d * math.log((bid + ask) / 2)
        fwd += math.log(bid) if d > 0 else -math.log(ask)
        rev += math.log(bid) if d < 0 else -math.log(ask)
        spread += math.log(ask) - math.log(bid)
    return resid * BPS, fwd * BPS, rev * BPS, spread * BPS


def bar_tier(abs_bps: float, spread_bps: float) -> str:
    assert abs_bps >= 0, "abs_bps must be non-negative"
    if abs_bps > ABS_SUSPECT_BPS:
        return "suspect"
    if spread_bps <= 0:
        return "unrated"
    ratio = abs_bps / spread_bps
    if ratio <= RATIO_CLEAN:
        return "clean"
    return "noisy" if ratio <= RATIO_SUSPECT else "suspect"


def _leg_flags(legs: list, preposition: bool) -> tuple:
    """(rated, multi_venue, uses_assumed, cost_bps or None)."""
    assert legs, "no legs"
    rated, uses_assumed, costs_known, cost = True, False, True, 0.0
    venues: set = set()
    transfer = False
    for edge, _d in legs:
        uses_assumed = uses_assumed or edge.assumed
        if edge.edge_type is EdgeType.VENUE:
            transfer = True
            rated = rated and preposition
            continue
        venues.add(edge.venue)
        rated = rated and edge.edge_type is EdgeType.QUOTED and edge.spread_known
        taker = edge.cost.taker_bps if edge.cost else None
        costs_known = costs_known and taker is not None
        cost += taker or 0.0
    return rated, (transfer or len(venues) > 1), uses_assumed, (cost if costs_known else None)


def _verdict(rated: bool, multi_venue: bool, preposition: bool, best_gain: float, cost_bps: float | None) -> str:
    assert preposition in (True, False), "preposition must be a bool"
    if multi_venue and not preposition:
        return "VENUE_BLOCKED"
    if not rated:
        return "UNRATED"
    if best_gain <= 0:
        return "NO_GAIN"
    if cost_bps is None:
        return "GAIN_COST_UNKNOWN"
    return "EXECUTABLE_AFTER_COST" if best_gain - cost_bps > 0 else "COST_EATS_GAIN"


def evaluate_cycle(edges: list, cycle: tuple, preposition: bool = False) -> CycleEval:
    """Evaluate one cycle on the edges' CURRENT quotes (one time slice)."""
    assert cycle, "empty cycle"
    legs = [(edges[idx], d) for idx, d in cycle]
    resid, fwd, rev, spread = evaluate_quotes([(e.bid, e.ask, d) for e, d in legs])
    rated, multi_venue, uses_assumed, cost_bps = _leg_flags(legs, preposition)
    best_gain = max(fwd, rev)
    ratio = abs(resid) / spread if rated and spread > 0 else None
    return CycleEval(
        residual_bps=resid, gain_fwd_bps=fwd, gain_rev_bps=rev, spread_bps=spread, ratio=ratio,
        tier=bar_tier(abs(resid), spread) if rated else "unrated", rated=rated, multi_venue=multi_venue,
        uses_assumed=uses_assumed, cost_bps=cost_bps,
        net_gain_bps=(best_gain - cost_bps) if cost_bps is not None else None,
        verdict=_verdict(rated, multi_venue, preposition, best_gain, cost_bps),
    )
