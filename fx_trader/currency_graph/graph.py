# version: 261005
"""
currency_graph/graph.py

Three PROJECTIONS of one edge list (chat 14p1). The edge list is the single
stored truth; a projection decides which nodes exist and which edges join
them. The three are kept side by side so chat 14p4 can compare them on real
data (the stated deliverable of that comparison - ROADMAP 14p4):

- "asset":       node = asset. One latent value per asset (estimation view).
                 Drops VENUE edges. Cross-venue legs of one cycle are legal
                 here but flagged multi_venue by evaluate.py.
- "asset_venue": node = (asset, venue). Executability view: a cycle can only
                 cross venues through a VENUE (transfer) edge.
- "per_venue":   one independent graph per venue (asset nodes), edges whose
                 two endpoints are on that venue. Drops cross-venue BASIS
                 edges and all VENUE edges.

Every edge keeps its ORIGINAL index in the caller's list, so a cycle found
in any projection refers to the same edges. Edges that would be self-loops in
a projection are dropped from it.
"""

from dataclasses import dataclass

from currency_graph.model import Edge, EdgeType

MODES = ("asset", "asset_venue", "per_venue")
MAX_EDGES = 100_000
ALL_KEY = "*"


@dataclass
class Graph:
    mode: str
    key: str                      # "*" or, for per_venue, the venue name
    nodes: list                   # sorted
    ends: dict                    # edge index -> (node_a, node_b); d=+1 means a -> b
    adj: dict                     # node -> list of (edge index, other node, d)


def _graph_key(edge: Edge, mode: str) -> str | None:
    """Which graph an edge belongs to in this mode (None = dropped)."""
    assert mode in MODES, f"unknown mode {mode!r}"
    if mode == "asset_venue":
        return ALL_KEY
    if edge.edge_type is EdgeType.VENUE:
        return None
    if mode == "asset":
        return ALL_KEY
    return edge.venue if edge.venue_b in (None, edge.venue) else None


def _ends(edge: Edge, mode: str) -> tuple:
    assert mode in MODES, f"unknown mode {mode!r}"
    if mode == "asset_venue":
        return edge.end_a, edge.end_b
    return edge.base, edge.quote


def _make_graph(mode: str, key: str, ends: dict) -> Graph:
    assert ends, "a graph needs at least one edge"
    assert mode in MODES, f"unknown mode {mode!r}"
    adj: dict = {}
    for idx, (a, b) in ends.items():
        adj.setdefault(a, []).append((idx, b, 1))
        adj.setdefault(b, []).append((idx, a, -1))
    return Graph(mode=mode, key=key, nodes=sorted(adj), ends=dict(ends), adj=adj)


def build_graphs(edges: list, mode: str = "asset") -> dict:
    """{graph key: Graph}. One graph ("*") for asset / asset_venue; one per
    venue for per_venue. Deterministic for a given edge order."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    assert len(edges) <= MAX_EDGES, f"{len(edges)} edges exceeds MAX_EDGES={MAX_EDGES}"
    groups: dict = {}
    for idx, edge in enumerate(edges):
        key = _graph_key(edge, mode)
        if key is None:
            continue
        a, b = _ends(edge, mode)
        if a != b:
            groups.setdefault(key, {})[idx] = (a, b)
    return {key: _make_graph(mode, key, ends) for key, ends in sorted(groups.items())}
