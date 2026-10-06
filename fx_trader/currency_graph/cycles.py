# version: 261005
"""
currency_graph/cycles.py

Cycle machinery over any projection (graph.py). A CYCLE is a tuple of
(edge_index, d) arcs; d = +1 means the arc is walked from the edge's first
endpoint to its second (base -> quote in the asset projection), -1 the other
way. Indices refer to the caller's original edge list.

Three tools, deliberately separate (RESEARCH_NOTES Section 3, step 4):
- enumerate_cycles: every simple cycle up to max_len edges, each undirected
  cycle once. Needed for per-loop residuals and executability. Combinatorial
  on large universes, hence three explicit ceilings (cycle count, DFS steps,
  max_len <= MAX_CYCLE_LEN); exceeding the cycle ceiling RAISES unless
  allow_truncate=True, which sets CycleSet.truncated (never silent).
  Length-2 cycles are two parallel edges (same pair on two venues).
- cycle_basis: a fundamental cycle basis (E - N + C cycles). Enough for
  least-squares residual work (14p3), cheap on any universe size.
- find_arbitrage_cycles: Bellman-Ford on -ln(executable rate). A DETECTOR:
  returns negative-cost loops (executable gain > 0 before fees), not every
  loop, and not exhaustive across overlapping loops (max_found caps it).
  By default only rated QUOTED arcs take part (bid == ask arcs would
  otherwise report their own mid inconsistency as free money).
"""

import math
from dataclasses import dataclass, field

from currency_graph.graph import Graph, build_graphs
from currency_graph.model import Edge, EdgeType

Cycle = tuple  # tuple of (edge_index, d)

MAX_CYCLE_LEN = 8
MAX_STEPS = 2_000_000
DEFAULT_MAX_CYCLES = 5_000
EPS = 1e-12


class CycleCeilingError(RuntimeError):
    """Raised when enumeration would exceed max_cycles (or MAX_STEPS)."""


@dataclass
class CycleSet:
    mode: str
    max_len: int
    cycles: list = field(default_factory=list)
    truncated: bool = False


# ------------------------------------------------------------ enumeration
def _can_close(path: tuple, idx: int) -> bool:
    """Closing arc `idx` back to the start. The first < last index rule keeps
    each undirected cycle exactly once (the reverse walk fails it)."""
    assert path, "cannot close an empty path"
    return path[0][0] < idx and idx not in {i for i, _ in path}


def _step(graph: Graph, start, node, seen: frozenset, path: tuple, max_len: int) -> tuple:
    """Arcs out of `node`: (closed cycles, extended partial paths)."""
    closed, extended = [], []
    for idx, other, d in graph.adj[node]:
        if other == start:
            if path and _can_close(path, idx):
                closed.append(path + ((idx, d),))
        elif other > start and other not in seen and len(path) + 2 <= max_len:
            extended.append((other, seen | {other}, path + ((idx, d),)))
    return closed, extended


def _enumerate_graph(graph: Graph, max_len: int, max_cycles: int, allow_truncate: bool, out: CycleSet, steps: list) -> bool:
    """Appends to out.cycles. Returns False when truncated."""
    for start in graph.nodes:
        stack = [(start, frozenset((start,)), ())]
        while stack:
            steps[0] += 1
            if steps[0] > MAX_STEPS:
                raise CycleCeilingError(f"cycle search exceeded MAX_STEPS={MAX_STEPS}; lower max_len or narrow the universe")
            node, seen, path = stack.pop()
            closed, extended = _step(graph, start, node, seen, path, max_len)
            room = max_cycles - len(out.cycles)
            if len(closed) > room:
                if not allow_truncate:
                    raise CycleCeilingError(f"more than max_cycles={max_cycles} cycles; raise it, lower max_len, or pass allow_truncate=True")
                out.cycles.extend(closed[:room])
                out.truncated = True
                return False
            out.cycles.extend(closed)
            stack.extend(extended)
    return True


def enumerate_cycles(
    edges: list, mode: str = "asset", max_len: int = 3, max_cycles: int = DEFAULT_MAX_CYCLES,
    allow_truncate: bool = False,
) -> CycleSet:
    if not 2 <= max_len <= MAX_CYCLE_LEN:
        raise ValueError(f"max_len must be in [2, {MAX_CYCLE_LEN}], got {max_len}")
    assert max_cycles > 0, "max_cycles must be positive"
    out = CycleSet(mode=mode, max_len=max_len)
    steps = [0]
    for graph in build_graphs(edges, mode).values():
        if not _enumerate_graph(graph, max_len, max_cycles, allow_truncate, out, steps):
            break
    return out


# ------------------------------------------------------------ cycle basis
def _bfs_forest(graph: Graph) -> tuple:
    """(parent, depth, tree_edge_indices). parent[node] = (parent_node,
    edge index, d for walking node -> parent) or None for a root."""
    parent: dict = {}
    depth: dict = {}
    tree: set = set()
    for root in graph.nodes:
        if root in depth:
            continue
        depth[root], parent[root] = 0, None
        queue, head = [root], 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            for idx, other, d in graph.adj[cur]:
                if other not in depth:
                    depth[other] = depth[cur] + 1
                    parent[other] = (cur, idx, -d)
                    tree.add(idx)
                    queue.append(other)
    assert len(depth) == len(graph.nodes), "BFS forest must reach every node"
    return parent, depth, tree


def _tree_path(parent: dict, depth: dict, u, v) -> list:
    """Arcs walking v up to the common ancestor, then down to u."""
    up_v, down_u = [], []
    x, y = u, v
    for _ in range(2 * len(depth) + 1):
        if x == y:
            return up_v + list(reversed(down_u))
        if depth[x] >= depth[y]:
            p, idx, d_up = parent[x]
            down_u.append((idx, -d_up))
            x = p
        else:
            p, idx, d_up = parent[y]
            up_v.append((idx, d_up))
            y = p
    raise AssertionError("tree path did not converge - corrupt forest")


def cycle_basis(edges: list, mode: str = "asset") -> list:
    """One fundamental cycle per non-tree edge: len == E - N + C per graph."""
    cycles = []
    for graph in build_graphs(edges, mode).values():
        parent, depth, tree = _bfs_forest(graph)
        for idx in sorted(set(graph.ends) - tree):
            u, v = graph.ends[idx]
            cycles.append(((idx, 1),) + tuple(_tree_path(parent, depth, u, v)))
    return cycles


def check_closed(edges: list, cycle: Cycle, mode: str = "asset") -> bool:
    """True iff the arcs chain head-to-tail and return to the start node."""
    assert cycle, "empty cycle"
    graphs = build_graphs(edges, mode)
    ends = {}
    for graph in graphs.values():
        ends.update(graph.ends)
    node = None
    first = None
    for idx, d in cycle:
        a, b = ends[idx]
        tail, head = (a, b) if d > 0 else (b, a)
        if node is not None and tail != node:
            return False
        first = tail if first is None else first
        node = head
    return node == first


# --------------------------------------------------------- Bellman-Ford
def _arc_weight(edge: Edge, d: int) -> float:
    """-ln(executable rate): walking base->quote earns bid, quote->base pays ask."""
    return -math.log(edge.bid) if d > 0 else math.log(edge.ask)


def _bf_arcs(edges: list, graph: Graph, require_rated: bool, banned: set) -> list:
    arcs = []
    for idx, (a, b) in graph.ends.items():
        edge = edges[idx]
        if require_rated and not (edge.edge_type is EdgeType.QUOTED and edge.spread_known):
            continue
        for tail, head, d in ((a, b, 1), (b, a, -1)):
            if (idx, d) not in banned:
                arcs.append((tail, head, _arc_weight(edge, d), idx, d))
    return arcs


def _bf_trace(pred: dict, last, n: int) -> Cycle:
    x = last
    for _ in range(n):
        x = pred[x][0]
    arcs, y = [], x
    for _ in range(n + 1):
        u, idx, d, _w = pred[y]
        arcs.append((idx, d))
        y = u
        if y == x:
            return tuple(reversed(arcs))
    raise AssertionError("Bellman-Ford trace did not close - corrupt predecessor map")


def _bf_find(nodes: list, arcs: list) -> tuple | None:
    """One negative cycle as (cycle, total weight), or None."""
    n = len(nodes)
    dist = {node: 0.0 for node in nodes}
    pred: dict = {}
    last = None
    for _ in range(n):
        last = None
        for tail, head, w, idx, d in arcs:
            if dist[tail] + w < dist[head] - EPS:
                dist[head] = dist[tail] + w
                pred[head] = (tail, idx, d, w)
                last = head
        if last is None:
            return None
    if last is None:
        return None
    cycle = _bf_trace(pred, last, n)
    weights = {(idx, d): w for _t, _h, w, idx, d in arcs}
    total = sum(weights[arc] for arc in cycle)
    assert total < 0, f"traced cycle is not negative ({total})"
    return cycle, total


def find_arbitrage_cycles(
    edges: list, mode: str = "asset", require_rated: bool = True, max_found: int = 10,
) -> list:
    """Up to max_found distinct negative-cost loops (executable gain > 0
    before fees). After each find, its single costliest arc is banned so the
    next search cannot return the same loop; overlapping loops sharing other
    arcs may therefore be missed - a detector, not an enumerator."""
    assert max_found > 0, "max_found must be positive"
    found: list = []
    for graph in build_graphs(edges, mode).values():
        banned: set = set()
        while len(found) < max_found:
            result = _bf_find(graph.nodes, _bf_arcs(edges, graph, require_rated, banned))
            if result is None:
                break
            cycle, _total = result
            found.append(cycle)
            banned.add(max(cycle, key=lambda arc: _arc_weight(edges[arc[0]], arc[1])))
    return found


# ------------------------------------------------------------- utilities
def cycle_legs(edges: list, cycle: Cycle) -> list:
    """[(instrument, venue, d)] - to map a cycle onto per-bar series."""
    assert cycle, "empty cycle"
    return [(edges[idx].instrument, edges[idx].venue, d) for idx, d in cycle]


def cycle_signature(edges: list, cycle: Cycle) -> frozenset:
    """Order- and orientation-independent identity of a cycle."""
    assert cycle, "empty cycle"
    return frozenset((edges[i].instrument, edges[i].venue, edges[i].venue_b) for i, _ in cycle)
