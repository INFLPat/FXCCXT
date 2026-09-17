"""
tests/test_portfolio.py

Hand-verified: identical series must correlate at exactly 1.0 and give a
diversification_ratio of exactly 1.0 (no benefit from "diversifying" into
a copy of yourself); a negated series must correlate at exactly -1.0.
A third, distinct-series case is cross-checked against an INDEPENDENTLY
written reference Pearson/variance implementation in this file, same
philosophy as test_metrics_extended.py's distribution-shape checks.

Run: python -m tests.test_portfolio
"""

import math

from backtest.portfolio import compute_portfolio_metrics, returns_from_mid_close
from data.store import Candle


def test_identical_series_correlate_at_one_with_no_diversification_benefit():
    a = [0.02, -0.01, 0.03, 0.00, -0.02, 0.015]
    returns = {"A": a, "B": list(a)}  # B is a copy, not the same object
    result = compute_portfolio_metrics(returns)

    assert abs(result.correlation_matrix["A"]["B"] - 1.0) < 1e-9
    assert abs(result.correlation_matrix["A"]["A"] - 1.0) < 1e-9
    assert abs(result.diversification_ratio - 1.0) < 1e-9, (
        f"identical series should give diversification_ratio == 1.0 exactly, got {result.diversification_ratio}"
    )
    print(f"corr(A,B)={result.correlation_matrix['A']['B']}, diversification_ratio={result.diversification_ratio}")
    print("Identical-series assertions passed.")


def test_negated_series_correlate_at_minus_one():
    a = [0.02, -0.01, 0.03, 0.00, -0.02, 0.015]
    c = [-x for x in a]
    returns = {"A": a, "C": c}
    result = compute_portfolio_metrics(returns)

    assert abs(result.correlation_matrix["A"]["C"] - (-1.0)) < 1e-9
    pair = result.least_correlated_pair()
    assert pair is not None and abs(pair[2] - (-1.0)) < 1e-9
    assert abs(result.equal_weight_volatility_pct - 0.0) < 1e-9
    assert result.equal_weight_sharpe is None, "Sharpe is undefined when portfolio volatility is exactly 0"
    print(f"corr(A,C)={result.correlation_matrix['A']['C']}, portfolio_vol={result.equal_weight_volatility_pct}%")
    print("Negated-series assertions passed.")


def _independent_pearson(x, y):
    n = len(x)
    mx = sum(v for v in x) / n
    my = sum(v for v in y) / n
    num = 0.0
    for i in range(n):
        num += (x[i] - mx) * (y[i] - my)
    denom_x = math.fsum((v - mx) ** 2 for v in x)
    denom_y = math.fsum((v - my) ** 2 for v in y)
    return num / math.sqrt(denom_x * denom_y)


def test_distinct_series_against_independent_reference():
    a = [0.02, -0.01, 0.03, 0.00, -0.02, 0.015, 0.01]
    b = [-0.01, 0.02, -0.015, 0.01, 0.005, -0.02, 0.03]
    result = compute_portfolio_metrics({"A": a, "B": b}, periods_per_year=252)

    expected_corr = _independent_pearson(a, b)
    assert abs(result.correlation_matrix["A"]["B"] - expected_corr) < 1e-9, (
        f"expected {expected_corr}, got {result.correlation_matrix['A']['B']}"
    )

    n = len(a)
    port = [(a[i] + b[i]) / 2 for i in range(n)]
    mean_p = sum(port) / n
    var_p = sum((p - mean_p) ** 2 for p in port) / (n - 1)
    expected_vol = math.sqrt(var_p)
    assert abs(result.equal_weight_volatility_pct / 100 - expected_vol) < 1e-9

    var_a = sum((v - sum(a) / n) ** 2 for v in a) / (n - 1)
    var_b = sum((v - sum(b) / n) ** 2 for v in b) / (n - 1)
    expected_weighted_avg_vol = 0.5 * math.sqrt(var_a) + 0.5 * math.sqrt(var_b)
    expected_div_ratio = expected_weighted_avg_vol / expected_vol
    assert abs(result.diversification_ratio - expected_div_ratio) < 1e-9, (
        f"expected {expected_div_ratio}, got {result.diversification_ratio}"
    )
    print(f"corr={result.correlation_matrix['A']['B']:.4f} (expected {expected_corr:.4f}), "
          f"diversification_ratio={result.diversification_ratio:.4f} (expected {expected_div_ratio:.4f})")
    print("Distinct-series independent-reference assertions passed.")


def test_mismatched_length_raises():
    try:
        compute_portfolio_metrics({"A": [0.01, 0.02, 0.03], "B": [0.01, 0.02]})
        raise AssertionError("expected an AssertionError for mismatched-length series")
    except AssertionError as e:
        assert "SAME number" in str(e)
    print("Mismatched-length assertion passed.")


def _make_candle(price: float) -> Candle:
    return Candle(
        instrument="TEST", granularity="H1", timestamp="t", bid_open=price, bid_high=price,
        bid_low=price, bid_close=price, ask_open=price, ask_high=price, ask_low=price, ask_close=price,
    )


def test_returns_from_mid_close():
    candles = [_make_candle(p) for p in [100.0, 110.0, 99.0, 99.0 * 1.5]]
    returns = returns_from_mid_close(candles)
    assert len(returns) == 3
    assert abs(returns[0] - 0.10) < 1e-9
    assert abs(returns[1] - (99.0 - 110.0) / 110.0) < 1e-9
    assert abs(returns[2] - 0.5) < 1e-9
    print(f"returns_from_mid_close: {returns}")
    print("returns_from_mid_close assertion passed.")


if __name__ == "__main__":
    test_identical_series_correlate_at_one_with_no_diversification_benefit()
    test_negated_series_correlate_at_minus_one()
    test_distinct_series_against_independent_reference()
    test_mismatched_length_raises()
    test_returns_from_mid_close()
    print("\nAll portfolio tests passed.")
