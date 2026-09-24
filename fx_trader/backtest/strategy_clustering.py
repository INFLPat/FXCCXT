"""
backtest/strategy_clustering.py

Cross-strategy correlation handling. CONFIDENCE_SIZING_DESIGN.md Section
1's "don't double-allocate correlated instruments" principle extended to
strategies on the same instrument - a real defect found this session (see
CONTEXT_HANDOFF.md Section 4d, SUBTASK_CROSS_STRATEGY_CORRELATION.md for
the full reasoning): RsiStrategy vs RsiMacdConfluenceStrategy correlate
at +0.65 average (up to +0.82 on LTC/GBP) because Confluence literally
embeds RSI's own signal as half its logic. Naively summing both
strategies' weighted votes in Section 2.2's raw_score formula
double-counts one edge as two independent confirmations.

TWO MECHANISMS, both built and compared on real data (see the sub-task
doc for the full comparison and which to ship first):

1. CLUSTER/GROUP (`cluster_strategies` + `collapse_to_cluster_votes`):
   single-linkage clustering on a strategy correlation matrix, computed
   once per instrument from the historical validation run - not live/
   rolling. Two strategies join the same cluster if their pairwise
   |correlation| meets or exceeds CORRELATION_CLUSTER_THRESHOLD,
   transitively. Every cluster member collapses into ONE effective
   (stance, weight) pair before Section 2.2's formula ever sees them -
   coarser, cheaper, loses resolution when correlated members actually
   disagree with each other.

2. PAIRWISE DISCOUNT (`pairwise_discount_weights`): every strategy keeps
   its own individual vote (own stance, own term in the sum) - only its
   WEIGHT is discounted by how much a higher-trust, already-counted
   strategy already correlates with it. Finer-grained, preserves
   disagreement signal within a correlated group, costs one extra O(n^2)
   pass, no clustering/threshold-membership logic needed.

Both computed STATICALLY (once per instrument, from the historical
validation run) - not live/rolling. Dynamic/rolling correlation is
explicitly deferred to a future session (see sub-task doc's future-work
section) - it turns the correlation lookback window itself into a new,
unvalidated hyperparameter (Section 2.4's own warning), and live
recomputation is real new infrastructure, not a formula change.

GENERALITY: both are systemic fixes, not one-off patches for the RSI/
Confluence pair specifically - they discover correlated strategies from
data, so either would also catch a future 6th/7th strategy that happened
to derive from an existing one, without hardcoding that case.
"""

from dataclasses import dataclass, field

CORRELATION_CLUSTER_THRESHOLD = 0.5  # a stated, deliberate starting value - NOT yet validated via sensitivity analysis (see sub-task doc's risk register)


@dataclass
class ClusterResult:
    clusters: list[list[str]]           # each inner list = strategy names in one cluster
    cluster_of: dict[str, int] = field(default_factory=dict)  # strategy name -> index into `clusters`

    def __post_init__(self):
        if not self.cluster_of:
            self.cluster_of = {name: idx for idx, cluster in enumerate(self.clusters) for name in cluster}


def cluster_strategies(
    correlation_matrix: dict[str, dict[str, float]], threshold: float = CORRELATION_CLUSTER_THRESHOLD,
) -> ClusterResult:
    """Single-linkage clustering on a strategy correlation matrix (same
    shape as backtest.portfolio.PortfolioMetricsResult.correlation_matrix,
    just keyed by strategy name instead of instrument name)."""
    assert correlation_matrix, "cluster_strategies requires a non-empty correlation matrix"
    assert 0 <= threshold <= 1, "threshold must be in [0, 1] - correlation matrices are already symmetric/normalized"
    names = list(correlation_matrix.keys())
    assert all(name in correlation_matrix[name] for name in names), "correlation_matrix must be square/complete"

    parent = {name: name for name in names}

    def find(name: str) -> str:
        root = name
        while parent[root] != root:
            root = parent[root]
        while parent[name] != root:
            parent[name], name = root, parent[name]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if abs(correlation_matrix[a][b]) >= threshold:
                union(a, b)

    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)

    return ClusterResult(clusters=list(groups.values()))


def collapse_to_cluster_votes(
    stances: dict[str, float], weights: dict[str, float], clustering: ClusterResult,
) -> tuple[dict[int, float], dict[int, float]]:
    """Given each strategy's current shadow stance (Section 2.1) and
    weight (Section 4), collapses every cluster into ONE effective
    (stance, weight) pair, indexed by cluster id - ready to feed into
    Section 2.2's raw_score formula unchanged, one term per cluster
    instead of one term per strategy. Returns (cluster_stances,
    cluster_weights), both keyed by the same cluster index."""
    assert stances.keys() == weights.keys(), "stances and weights must cover the exact same strategies"
    assert set(stances.keys()) == set(clustering.cluster_of.keys()), (
        "stances must cover every strategy the clustering was built from"
    )

    cluster_stances: dict[int, float] = {}
    cluster_weights: dict[int, float] = {}
    for cluster_idx, members in enumerate(clustering.clusters):
        member_weights = [weights[m] for m in members]
        total_weight = sum(member_weights)
        weighted_stance = (
            sum(stances[m] * weights[m] for m in members) / total_weight if total_weight > 0 else 0.0
        )
        cluster_stances[cluster_idx] = weighted_stance
        cluster_weights[cluster_idx] = total_weight / len(members)  # MEAN, not sum - see module docstring
    return cluster_stances, cluster_weights


def pairwise_discount_weights(
    weights: dict[str, float], correlation_matrix: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Alternative to cluster_strategies/collapse_to_cluster_votes - see
    module docstring for the comparison. Greedy sequential discount:
    strategies are processed in descending weight order (highest-trust
    first). Each subsequent strategy's weight is discounted by
    (1 - max |correlation| with any already-processed, higher-weight
    strategy) - the highest-weight member of a correlated group keeps its
    full weight; strategies whose information is already well-represented
    by something more trusted get progressively discounted, WITHOUT
    merging their individual stance into anyone else's - every strategy
    still contributes its own separate term to Section 2.2's raw_score
    formula, only its weight changes."""
    assert weights.keys() == correlation_matrix.keys(), "weights and correlation_matrix must cover the same strategies"
    assert all(name in correlation_matrix[name] for name in weights), "correlation_matrix must be square/complete"
    ordered = sorted(weights.keys(), key=lambda name: weights[name], reverse=True)

    discounted: dict[str, float] = {}
    for i, name in enumerate(ordered):
        higher_priority = ordered[:i]
        if not higher_priority:
            discounted[name] = weights[name]
            continue
        max_corr = max(abs(correlation_matrix[name][other]) for other in higher_priority)
        discounted[name] = weights[name] * (1 - max_corr)
    return discounted
