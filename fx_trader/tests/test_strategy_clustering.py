"""
tests/test_strategy_clustering.py

Hand-verified tests for both cross-strategy correlation mechanisms in
backtest/strategy_clustering.py - see that module's docstring and
SUBTASK_CROSS_STRATEGY_CORRELATION.md for the full design.

Run: python -m tests.test_strategy_clustering
"""

from backtest.strategy_clustering import (
    ClusterResult,
    cluster_strategies,
    collapse_to_cluster_votes,
    pairwise_discount_weights,
)


def _matrix(pairs: dict[tuple, float], names: list[str]) -> dict[str, dict[str, float]]:
    """Builds a full symmetric correlation matrix (diagonal=1.0) from a
    sparse dict of unordered pairs - unlisted pairs default to 0.0."""
    m = {a: {b: (1.0 if a == b else 0.0) for b in names} for a in names}
    for (a, b), corr in pairs.items():
        m[a][b] = corr
        m[b][a] = corr
    return m


def test_cluster_strategies_simple_two_groups():
    names = ["A", "B", "C", "D"]
    matrix = _matrix({("A", "B"): 0.7, ("C", "D"): 0.6, ("A", "C"): 0.1}, names)
    result = cluster_strategies(matrix, threshold=0.5)
    clusters_as_sets = {frozenset(c) for c in result.clusters}
    assert clusters_as_sets == {frozenset({"A", "B"}), frozenset({"C", "D"})}, result.clusters
    assert result.cluster_of["A"] == result.cluster_of["B"]
    assert result.cluster_of["C"] == result.cluster_of["D"]
    assert result.cluster_of["A"] != result.cluster_of["C"]
    print("Simple two-cluster assertion passed.")


def test_cluster_strategies_transitive_single_linkage():
    names = ["A", "B", "C"]
    # A-C alone is BELOW threshold, but A-B and B-C are both above -
    # single-linkage must still merge all three via B.
    matrix = _matrix({("A", "B"): 0.6, ("B", "C"): 0.6, ("A", "C"): 0.2}, names)
    result = cluster_strategies(matrix, threshold=0.5)
    assert len(result.clusters) == 1
    assert set(result.clusters[0]) == {"A", "B", "C"}
    print("Transitive single-linkage assertion passed.")


def test_cluster_strategies_no_correlation_all_singletons():
    names = ["A", "B", "C"]
    matrix = _matrix({}, names)  # everything defaults to 0.0
    result = cluster_strategies(matrix, threshold=0.5)
    assert len(result.clusters) == 3
    assert all(len(c) == 1 for c in result.clusters)
    print("All-singleton (no correlation) assertion passed.")


def test_collapse_to_cluster_votes_hand_computed():
    # Cluster {A, B}: stances +1, -1; weights 0.6, 0.2.
    # weighted_stance = (1*0.6 + -1*0.2) / 0.8 = 0.4/0.8 = 0.5
    # cluster_weight = mean(0.6, 0.2) = 0.4
    clustering = ClusterResult(clusters=[["A", "B"], ["C"]])
    stances = {"A": 1.0, "B": -1.0, "C": 1.0}
    weights = {"A": 0.6, "B": 0.2, "C": 0.9}
    cluster_stances, cluster_weights = collapse_to_cluster_votes(stances, weights, clustering)

    assert abs(cluster_stances[0] - 0.5) < 1e-9, f"expected 0.5, got {cluster_stances[0]}"
    assert abs(cluster_weights[0] - 0.4) < 1e-9, f"expected 0.4 (mean, not sum), got {cluster_weights[0]}"
    assert abs(cluster_stances[1] - 1.0) < 1e-9, "singleton cluster must pass its stance through unchanged"
    assert abs(cluster_weights[1] - 0.9) < 1e-9, "singleton cluster must pass its weight through unchanged"
    print(f"Cluster 0 (A,B): stance={cluster_stances[0]} weight={cluster_weights[0]} (expected 0.5, 0.4)")
    print("Hand-computed collapse assertion passed.")


def test_pairwise_discount_hand_computed():
    # A=1.0 (highest, unchanged), B=0.8 (discounted by its corr with A
    # only, since A is the only higher-priority strategy at B's turn),
    # C=0.3 (discounted by its MAX corr with {A, B}).
    names = ["A", "B", "C"]
    matrix = _matrix({("A", "B"): 0.7, ("A", "C"): 0.1, ("B", "C"): 0.6}, names)
    weights = {"A": 1.0, "B": 0.8, "C": 0.3}
    discounted = pairwise_discount_weights(weights, matrix)

    assert abs(discounted["A"] - 1.0) < 1e-9, "highest-weight strategy must keep its full weight"
    assert abs(discounted["B"] - 0.8 * (1 - 0.7)) < 1e-9, f"expected {0.8*0.3}, got {discounted['B']}"
    expected_c = 0.3 * (1 - max(0.1, 0.6))
    assert abs(discounted["C"] - expected_c) < 1e-9, f"expected {expected_c}, got {discounted['C']}"
    print(f"discounted={discounted} (A unchanged, B=0.24, C={expected_c:.3f})")
    print("Hand-computed pairwise-discount assertion passed.")


def test_pairwise_discount_no_correlation_leaves_weights_unchanged():
    names = ["A", "B", "C"]
    matrix = _matrix({}, names)
    weights = {"A": 1.0, "B": 0.5, "C": 0.2}
    discounted = pairwise_discount_weights(weights, matrix)
    for name in names:
        assert abs(discounted[name] - weights[name]) < 1e-9, (
            f"zero correlation must leave weights untouched - regression check for the no-op case"
        )
    print("No-correlation regression-safety assertion passed.")


def test_real_data_rsi_confluence_actually_cluster_macd_does_not():
    """Uses the ACTUAL average correlations measured this session
    (CONTEXT_HANDOFF.md Section 4d / CONFIDENCE_SIZING_DESIGN.md Section
    4.2) - not synthetic numbers - to confirm the mechanism solves the
    real problem it was built for: RSI and Confluence (0.65 avg) cluster
    together at the chosen threshold; MACD (0.08-0.27 avg with everyone)
    does not cluster with anything."""
    names = ["SmaCrossoverStrategy", "RsiStrategy", "MacdStrategy", "RsiMacdConfluenceStrategy", "BollingerBandsStrategy"]
    matrix = _matrix({
        ("SmaCrossoverStrategy", "BollingerBandsStrategy"): -0.59,
        ("RsiStrategy", "BollingerBandsStrategy"): 0.39,
        ("RsiStrategy", "RsiMacdConfluenceStrategy"): 0.65,
        ("SmaCrossoverStrategy", "RsiStrategy"): -0.27,
        ("SmaCrossoverStrategy", "MacdStrategy"): 0.08,
        ("MacdStrategy", "RsiStrategy"): 0.22,
        ("MacdStrategy", "RsiMacdConfluenceStrategy"): 0.27,
        ("MacdStrategy", "BollingerBandsStrategy"): 0.08,
    }, names)

    result = cluster_strategies(matrix, threshold=0.5)
    assert result.cluster_of["RsiStrategy"] == result.cluster_of["RsiMacdConfluenceStrategy"], (
        "RSI and Confluence (0.65 avg correlation) must cluster together at threshold=0.5"
    )
    assert result.cluster_of["MacdStrategy"] != result.cluster_of["RsiStrategy"], (
        "MACD (0.22 with RSI, well under threshold) must NOT be pulled into the RSI/Confluence cluster"
    )
    print(f"clusters={result.clusters}")
    print("Real-measured-correlation clustering assertion passed.")


if __name__ == "__main__":
    test_cluster_strategies_simple_two_groups()
    test_cluster_strategies_transitive_single_linkage()
    test_cluster_strategies_no_correlation_all_singletons()
    test_collapse_to_cluster_votes_hand_computed()
    test_pairwise_discount_hand_computed()
    test_pairwise_discount_no_correlation_leaves_weights_unchanged()
    test_real_data_rsi_confluence_actually_cluster_macd_does_not()
    print("\nAll strategy_clustering tests passed.")
