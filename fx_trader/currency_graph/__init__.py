# version: 261005
"""
currency_graph - typed-edge currency graph engine (chat 14p1, stdlib only).

model     Edge, CostSpec, EdgeType, orientation (canonicalise / invert_edge)
graph     three projections: asset, asset_venue, per_venue
cycles    enumerate_cycles, cycle_basis, find_arbitrage_cycles (Bellman-Ford)
evaluate  residual / executable gain / spread / verdict per cycle

Coming (ROADMAP): strengths + leave-one-out (14p3), numpy path and the
three-projection comparison (14p4). See CONTEXT_HANDOFF.md Section 4g.
"""

from currency_graph.cycles import (
    CycleCeilingError, CycleSet, check_closed, cycle_basis, cycle_legs, cycle_signature,
    enumerate_cycles, find_arbitrage_cycles,
)
from currency_graph.evaluate import CycleEval, bar_tier, evaluate_cycle, evaluate_quotes
from currency_graph.graph import MODES, Graph, build_graphs
from currency_graph.model import (
    CostSpec, Edge, EdgeType, basis_edge, canonicalise, invert_edge, parse_instrument,
    quoted_edge, venue_edge, with_quote,
)
