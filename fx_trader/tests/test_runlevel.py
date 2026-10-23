# version: 261021
"""
tests/test_runlevel.py

Method: hermetic, numpy only, no DB, no real outputs. The numpy statistics are
checked window by window against the stdlib oracle (rl.ref_stat) on random series
with NaN holes; causality by perturbing the future; EWMA / CUSUM against hand
values; layout column counts against hand counts; the vectorised run counter
against a plain-Python implementation of the count_runs rule on random flags and
random blocks; block draws against segment bounds.

Run: python3 -m tests.test_runlevel
"""

import math

import numpy as np

from currency_graph import runlevel as rl


def _same(a, b, tol=1e-12) -> bool:
    return (math.isnan(a) and math.isnan(b)) or abs(a - b) <= tol


def test_bounds_and_min_count():
    assert rl.window_bounds(6, "trailing") == (5, 0) and rl.window_bounds(6, "centred") == (3, 3)
    assert rl.window_bounds(3, "centred") == (1, 1)
    assert [rl.min_count(12, f) for f in rl.FILLS] == [12, 6, 4, 3]
    assert rl.min_count(2, "min3") == 2 and rl.min_count(1, "third") == 1
    for bad in (lambda: rl.window_bounds(6, "x"), lambda: rl.min_count(6, "x")):
        try:
            bad()
            raise RuntimeError("expected a rejection")
        except ValueError:
            pass
    print("bounds / min_count assertions passed.")


def test_order_and_sign_stats_match_stdlib_oracle():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(3, 40))
    X[0, 10:14] = np.nan
    X[2, :5] = np.nan
    X[1, 20] = 0.0
    for W, caus in ((3, "trailing"), (6, "trailing"), (6, "centred"), (12, "trailing"), (5, "centred")):
        back, fwd = rl.window_bounds(W, caus)
        V = rl.windows(X, back, fwd, True)
        assert V.shape == (3, 40, back + fwd + 1)
        o, sg = rl.order_stats(V), rl.sign_stat(V)
        for r in range(3):
            for t in range(40):
                vals = list(V[r, t])
                for name in ("median", "mean", "trim20"):
                    assert _same(o[name][r, t], rl.ref_stat(vals, name)), (W, caus, r, t, name)
                assert _same(sg["sign"][r, t], rl.ref_stat(vals, "signpers")), (W, caus, r, t)
    X2 = np.arange(10.0)[None, :]
    V = rl.windows(X2, 2, 0, True)
    assert list(V[0, 5]) == [3.0, 4.0, 5.0] and np.isnan(V[0, 0, 0]), "trailing window must end at t"
    Vc = rl.windows(X2, 2, 2, True)
    assert list(Vc[0, 5]) == [3.0, 4.0, 5.0, 6.0, 7.0]
    Vu = rl.windows(X2, 2, 0, False)
    assert Vu.shape == (1, 8, 3) and list(Vu[0, 0]) == [0.0, 1.0, 2.0], "unpadded: first window starts at column 0"
    print("order / sign statistics match the stdlib oracle (5 window shapes, NaN holes).")


def test_trim20_hand_value_and_fill():
    v = np.array([[[1.0, 2.0, 3.0, 4.0, 100.0]]])
    o = rl.order_stats(v)
    assert abs(o["trim20"][0, 0] - 3.0) < 1e-12 and abs(o["median"][0, 0] - 3.0) < 1e-12 and abs(o["mean"][0, 0] - 22.0) < 1e-12
    assert rl.ref_stat([1, 2, 3, 4, 100.0], "trim20") == 3.0
    part = np.array([[[np.nan, np.nan, 1.0, 2.0, 3.0]]])
    o = rl.order_stats(part)
    assert int(o["cnt"][0, 0]) == 3
    assert not np.isnan(rl.apply_fill(o["median"], o["cnt"], 5, "half")[0, 0])
    assert np.isnan(rl.apply_fill(o["median"], o["cnt"], 5, "full")[0, 0]), "3 valid bars of 5 fail the full rule"
    assert not np.isnan(rl.apply_fill(o["median"], o["cnt"], 5, "min3")[0, 0])
    print("trim20 / fill hand-value assertions passed.")


def test_trailing_is_causal_and_centred_is_not():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(1, 60))
    Y = X.copy()
    Y[0, 40:] += 50.0
    spec_t = [rl.make_spec("median", 6, "full", "trailing", "raw")]
    spec_c = [rl.make_spec("median", 6, "full", "centred", "raw")]
    a = dict(rl.iter_stats({"raw": X}, spec_t, 0, 60, True))[0]
    b = dict(rl.iter_stats({"raw": Y}, spec_t, 0, 60, True))[0]
    assert np.allclose(a[0, :40], b[0, :40], equal_nan=True), "trailing statistic at t must not see the future"
    c = dict(rl.iter_stats({"raw": X}, spec_c, 0, 60, True))[0]
    d = dict(rl.iter_stats({"raw": Y}, spec_c, 0, 60, True))[0]
    assert not np.allclose(c[0, 35:40], d[0, 35:40]), "centred statistic must see the future"
    print("causality assertions passed.")


def test_ewma_cusum_hand_values():
    e = rl.ewma(np.array([[4.0, 0.0, 0.0]]), 1.0, min_n=1)
    assert np.allclose(e[0], [4.0, 2.0, 1.0])
    e2 = rl.ewma(np.array([[1.0, 1.0, np.nan, 5.0, 5.0, 5.0]]), 1.0, min_n=2)
    assert np.isnan(e2[0, 0]) and e2[0, 1] == 1.0 and np.isnan(e2[0, 2]) and np.isnan(e2[0, 3]) and e2[0, 4] == 5.0, e2
    c = rl.cusum(np.array([[2.0, 2.0, 2.0], [-2.0, -2.0, -2.0]]), 1.0)
    assert np.allclose(c[0], [1.0, 2.0, 3.0]) and np.allclose(c[1], [-1.0, -2.0, -3.0])
    c2 = rl.cusum(np.array([[2.0, np.nan, 2.0]]), 1.0)
    assert c2[0, 0] == 1.0 and np.isnan(c2[0, 1]) and c2[0, 2] == 1.0, "NaN resets the accumulator"
    print("EWMA / CUSUM hand-value assertions passed.")


def test_rolling_median_and_mad():
    x = np.array([[5.0] * 10])
    assert np.allclose(rl.rolling_mad(x, 4, 2)[0, 3:], 0.0)
    m = rl.rolling_median(np.array([[1.0, 2.0, 3.0, 4.0]]), 3, 3)
    assert np.isnan(m[0, 1]) and m[0, 2] == 2.0 and m[0, 3] == 3.0
    print("rolling median / MAD assertions passed.")


def test_layout_counts_and_unique_ids():
    specs, groups, ncols = rl.build_layout(("full",))
    assert len(specs) == 86 and ncols == 1874, (len(specs), ncols)
    specs4, groups4, ncols4 = rl.build_layout(rl.FILLS)
    assert len(specs4) == 296 and ncols4 == 6344, (len(specs4), ncols4)
    for sp, gr, nc in ((specs, groups, ncols), (specs4, groups4, ncols4)):
        ids = rl.column_ids(sp, gr)
        assert len(ids) == nc and len(set(ids)) == nc, "column ids must be unique"
    hs, hg, hn = rl.build_layout(("full",), headline_only=True)
    assert hn == sum(len(g.thr) for g in hg) and all(g.headline for g in hg) and 0 < hn < 300, hn
    print("layout counts passed: injection 86 specs / 1874 columns, real 296 / 6344, headline", hn)


def _ref_runs(f, s, L, g):
    runs, prev = 0, None
    for j in range(s, s + L):
        if f[j]:
            runs += prev is None or j - prev > g
            prev = j
    return runs


def test_block_runs_match_plain_python_rule():
    rng = np.random.default_rng(3)
    M = 300
    F = rng.random((2, 3, M)) < 0.08
    F[0, 0, :] = False
    F[1, 2, 100:140] = True
    starts = rng.integers(0, M - 50, size=60)
    lens = rng.choice([1, 3, 6, 12, 24, 48], size=60)
    starts[:5] = [0, 0, 5, 99, 100]
    lens[:5] = [1, 48, 12, 6, 40]
    gaps = (1, 3, 6, 12)
    totals, runs = rl.block_runs(F, gaps, [(starts, lens)])
    for g in gaps:
        for a in range(2):
            for b in range(3):
                for k in range(len(starts)):
                    assert int(runs[g][0][a, b, k]) == _ref_runs(F[a, b], int(starts[k]), int(lens[k]), g), (g, a, b, k)
                assert int(totals[g][a, b]) == _ref_runs(F[a, b], 0, M, g)
    print("vectorised block_runs == plain-Python run rule (4 gaps, 2x3 series, 60 blocks).")


def test_draw_blocks_stay_inside_segments():
    segs = [(0, 10), (60, 100), (200, 3)]
    rng = np.random.default_rng(4)
    for label in (1, 3, 12, 48, None):
        S, Ls, G = rl.draw_blocks(segs, label, 400, rng)
        assert len(S) == 400
        for s, L, g in zip(S, Ls, G):
            a, n = segs[int(g)]
            assert a <= s and s + L <= a + n, (label, s, L, g)
        if label is not None:
            assert set(Ls.tolist()) == {label}
    S, Ls, G = rl.draw_blocks(segs, 48, 10, rng)
    assert set(G.tolist()) == {1}, "only the 100-bar segment fits a 48-bar block"
    assert len(rl.draw_blocks(segs, 500, 10, rng)[0]) == 0, "no segment fits: empty result, not a crash"
    print("block draw assertions passed.")


def test_ratio_array_and_scales():
    S = np.array([[1.0, -4.0, np.nan]])
    sc = np.array([[2.0, 2.0, 2.0]])
    r = rl.ratio_array(S, "path", sc)
    assert np.allclose(r[0, :2], [0.5, 2.0]) and np.isnan(r[0, 2])
    assert np.allclose(rl.ratio_array(S, "abs", None)[0, :2], [1.0, 4.0])
    bad = rl.ratio_array(S, "path", np.array([[0.0, np.nan, 1.0]]))
    assert np.isnan(bad[0, 0]) and np.isnan(bad[0, 1]), "zero or NaN scale gives NaN, never a flag"
    print("ratio_array assertions passed.")


if __name__ == "__main__":
    test_bounds_and_min_count()
    test_order_and_sign_stats_match_stdlib_oracle()
    test_trim20_hand_value_and_fill()
    test_trailing_is_causal_and_centred_is_not()
    test_ewma_cusum_hand_values()
    test_rolling_median_and_mad()
    test_layout_counts_and_unique_ids()
    test_block_runs_match_plain_python_rule()
    test_draw_blocks_stay_inside_segments()
    test_ratio_array_and_scales()
    print("\nAll runlevel tests passed.")
