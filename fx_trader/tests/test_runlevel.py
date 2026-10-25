# version: 261023
"""
tests/test_runlevel.py

Method: currency_graph/runlevel.py (numpy engine) against stdlib oracles. Checks: every windowed statistic
(median / mean / trim20 / signpers) equals ref_stat over the same window to 1e-12 for trailing and centred
windows, all fills, with NaNs; a statistic is NaN where its own bar is missing (all fills); EWMA / CUSUM hand
values; the hysteresis latch equals a plain Python state machine on random inputs; block run counts equal a
Python re-implementation of the run rule (count_runs) on random flags; drawn blocks never cross a segment;
pack_layout hand values for carry 0 / g / None; layout sizes (86 / 1874 columns for one fill, 296 / 6344 for
four, 162 headline columns); threshold monotonicity of flags.

Run: python3 -m tests.test_runlevel
"""

import math

import numpy as np

from currency_graph import runlevel as rl


def _series(rng, n, T, nan_p=0.15):
    X = rng.normal(0, 3, size=(n, T))
    X[rng.random((n, T)) < nan_p] = np.nan
    return X


def test_windowed_stats_match_oracle():
    rng = np.random.default_rng(1)
    X = _series(rng, 3, 90)
    for caus in rl.CAUSALITY:
        for W in (3, 6, 12, 24):
            back, fwd = rl.window_bounds(W, caus)
            nominal = back + fwd + 1
            V = rl._win_view(X, back, fwd, 0, X.shape[1], True)
            o = rl.order_stats(V)
            sg = rl.sign_stat(V)
            for r in range(X.shape[0]):
                for t in range(X.shape[1]):
                    vals = [x for x in V[r, t] if not math.isnan(x)]
                    for kind in ("median", "mean", "trim20"):
                        exp = rl.ref_stat(vals, kind)
                        got = o[kind][r, t]
                        assert (math.isnan(exp) and math.isnan(got)) or abs(exp - got) < 1e-12, (caus, W, kind, r, t)
                    exp = rl.ref_stat(vals, "signpers")
                    got = sg["sign"][r, t]
                    assert (math.isnan(exp) and math.isnan(got)) or abs(exp - got) < 1e-12, (caus, W, "signpers")
            for fill in rl.FILLS:
                f = rl.apply_fill(o["median"], o["cnt"], nominal, fill)
                need = rl.min_count(nominal, fill)
                assert np.array_equal(np.isnan(f), (o["cnt"] < need) | np.isnan(o["median"])), (caus, W, fill)
    print("windowed statistics match the stdlib oracle (trailing + centred, all fills).")


def test_iter_stats_gates_missing_bars_and_pads():
    rng = np.random.default_rng(2)
    X = _series(rng, 4, 70, 0.3)
    specs = rl.build_specs(rl.FILLS)
    got = {}
    for i, a in rl.iter_stats({"raw": X, "norm": X / 2.0}, specs, 0, X.shape[1], True):
        got[i] = a
    assert len(got) == len(specs), "every spec must be yielded once"
    miss = np.isnan(X)
    for i, a in got.items():
        assert a.shape == X.shape
        assert np.all(np.isnan(a[miss])), f"statistic {specs[i].sid} is defined on a missing bar"
    sp = rl.make_spec("median", 6, "full", "trailing", "raw")
    i = specs.index(sp)
    t = 40
    vals = [x for x in X[1, t - 5:t + 1] if not math.isnan(x)]
    if not miss[1, t] and len(vals) == 6:
        assert abs(got[i][1, t] - rl.ref_stat(vals, "median")) < 1e-12
    print("statistics are NaN on missing bars for every spec / fill.")


def test_unpadded_equals_padded_inside():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(2, 120))
    specs = [rl.make_spec("mean", 12, "full", "trailing", "raw"), rl.make_spec("median", 12, "half", "centred", "raw"),
             rl.make_spec("ewma", 6, "-", "trailing", "raw"), rl.make_spec("cusum", 1.0, "-", "trailing", "raw")]
    full = dict(rl.iter_stats({"raw": X}, specs, 0, 120, True))
    part = dict(rl.iter_stats({"raw": X}, specs, 48, 100, False))
    for i in (0, 1):
        assert np.allclose(full[i][:, 48:100], part[i], equal_nan=True), specs[i].sid
    print("unpadded windows equal padded windows where context exists.")


def test_ewma_and_cusum_hand_values():
    X = np.array([[1.0, 1.0, 1.0, 1.0, 5.0]])
    e = rl.ewma(X, 1.0)                         # a = 0.5
    assert np.isnan(e[0, 0]) and np.isnan(e[0, 1]) and abs(e[0, 2] - 1.0) < 1e-12
    assert abs(e[0, 4] - 3.0) < 1e-12
    c = rl.cusum(np.array([[2.0, 2.0, -1.0, np.nan, 2.0]]), 0.5)
    assert abs(c[0, 0] - 1.5) < 1e-12 and abs(c[0, 1] - 3.0) < 1e-12 and abs(c[0, 2] - 1.5) < 1e-12
    assert math.isnan(c[0, 3]) and abs(c[0, 4] - 1.5) < 1e-12, "NaN resets the sums"
    n = rl.cusum(np.array([[-2.0, -2.0]]), 0.5)
    assert abs(n[0, 1] + 3.0) < 1e-12, "the negative side is returned signed"
    print("EWMA / CUSUM hand values passed.")


def _latch_loop(ent, ext):
    out = np.zeros(len(ent), bool)
    on = False
    for t in range(len(ent)):
        if not ext[t]:
            on = False
        elif ent[t]:
            on = True
        out[t] = on and ext[t]
    return out


def test_latch_matches_state_machine():
    rng = np.random.default_rng(4)
    for _ in range(300):
        T = int(rng.integers(1, 40))
        ext = rng.random(T) < 0.6
        ent = (rng.random(T) < 0.3) & ext
        assert np.array_equal(rl.latch(ent, ext), _latch_loop(ent, ext)), (ent, ext)
    ent = rng.random((3, 2, 50)) < 0.2
    ext = ent | (rng.random((3, 2, 50)) < 0.5)
    L = rl.latch(ent, ext)
    for i in range(3):
        for j in range(2):
            assert np.array_equal(L[i, j], _latch_loop(ent[i, j], ext[i, j]))
    assert np.all(L >= ent), "the latch must contain every entering bar"
    assert rl.latch(np.zeros((2, 0), bool), np.zeros((2, 0), bool)).shape == (2, 0)
    print("hysteresis latch equals the state machine (300 random + batched).")


def _runs_loop(F, s, length, gap):
    runs, prev = 0, None
    for j in range(s, s + length):
        if F[j]:
            runs += prev is None or j - prev > gap
            prev = j
    return runs


def test_block_runs_match_run_rule():
    rng = np.random.default_rng(5)
    M = 400
    F = rng.random((2, 3, M)) < 0.08
    starts = rng.integers(0, M - 48, size=60)
    lens = rng.choice(rl.BLOCK_LENGTHS, size=60)
    lens = np.minimum(lens, M - starts)
    totals, runs = rl.block_runs(F, (1, 3, 6, 12), [(starts, lens)])
    for g in (1, 3, 6, 12):
        for a in range(2):
            for b in range(3):
                assert totals[g][a, b] == _runs_loop(F[a, b], 0, M, g)
                for k in range(len(starts)):
                    assert runs[g][0][a, b, k] == _runs_loop(F[a, b], int(starts[k]), int(lens[k]), g), (g, a, b, k)
    print("block run counts equal the count_runs rule for gaps 1/3/6/12.")


def test_draw_blocks_stay_inside_segments():
    segs = [(0, 30), (80, 5), (100, 120), (300, 2)]
    rng = np.random.default_rng(6)
    for lab in (1, 3, 6, 12, 24, 48, None):
        S, L, G = rl.draw_blocks(segs, lab, 400, rng)
        assert len(S) == 400
        for s, l, g in zip(S, L, G):
            a, n = segs[g]
            assert a <= s and s + l <= a + n, (lab, s, l, segs[g])
    S, L, G = rl.draw_blocks([(0, 5)], 24, 10, rng)
    assert len(S) == 0, "no segment is long enough: nothing is drawn"
    print("drawn blocks never cross a segment.")


def test_pack_layout_hand_values():
    step = 3600
    ep = np.array([0, 1, 2, 5, 6, 8, 9, 10]) * step
    keep = np.ones(8, bool)
    pos, M, segs = rl.pack_layout(keep, ep, 0, step, sep=10)
    assert list(pos) == [0, 1, 2, 13, 14, 25, 26, 27] and M == 28 and segs == [(0, 3), (13, 2), (25, 3)], (pos, M, segs)
    pos, M, segs = rl.pack_layout(keep, ep, 2, step, sep=10)       # gap of 2 missing bars (3,4) bridged; gap of 1 (7) bridged
    assert list(pos) == [0, 1, 2, 5, 6, 8, 9, 10] and M == 11 and segs == [(0, 11)], (pos, M, segs)
    pos, M, segs = rl.pack_layout(keep, ep, 1, step, sep=10)       # (3,4) = 2 missing > 1: new segment; (7) bridged
    assert list(pos) == [0, 1, 2, 13, 14, 16, 17, 18] and segs == [(0, 3), (13, 6)], (pos, segs)
    pos, M, segs = rl.pack_layout(keep, ep, None, step, sep=10)
    assert list(pos) == list(range(8)) and M == 8 and segs == [(0, 8)]
    keep2 = keep.copy()
    keep2[[1, 2]] = False
    pos, M, segs = rl.pack_layout(keep2, ep, 0, step, sep=10)
    assert list(pos[:3]) == [0, -1, -1] and segs[0] == (0, 1)
    pos, M, segs = rl.pack_layout(np.zeros(3, bool), ep[:3], 0, step)
    assert M == 0 and segs == [] and list(pos) == [-1, -1, -1]
    try:
        rl.pack_layout(keep, ep[::-1].copy(), 0, step)
        raise RuntimeError("expected rejection of non-increasing epochs")
    except AssertionError:
        pass
    print("pack_layout hand values (reset / carry / bar-index / masked) passed.")


def test_carry_window_keeps_real_time_length():
    step = 3600
    ep = np.array([0, 1, 2, 3, 5, 6]) * step           # one missing bar (4)
    X = np.array([[1.0, 2.0, 3.0, 4.0, 6.0, 7.0]])
    pos, M, _ = rl.pack_layout(np.ones(6, bool), ep, 1, step, sep=10)
    P = np.full((1, M), np.nan)
    P[:, pos] = X
    s = dict(rl.iter_stats({"raw": P}, [rl.make_spec("mean", 3, "full", "trailing", "raw"),
                                        rl.make_spec("mean", 3, "half", "trailing", "raw")], 0, M, True))
    assert list(pos) == [0, 1, 2, 3, 5, 6] and M == 7
    assert abs(s[0][0, 3] - 3.0) < 1e-12, "window 1..3 is complete"
    assert np.isnan(s[0][0, 5]) and np.isnan(s[0][0, 6]), "full fill: windows over the bridged bar are incomplete"
    assert np.isnan(s[1][0, 4]), "half fill: still NaN on the bridged (missing) bar itself"
    assert abs(s[1][0, 5] - 5.0) < 1e-12 and abs(s[1][0, 6] - 6.5) < 1e-12, "half fill: 2 of 3 bars suffice"
    print("carry bridges keep the window in real time.")


def test_layout_sizes_and_threshold_monotone():
    sp1, g1, n1 = rl.build_layout(("full",))
    assert (len(sp1), n1) == (86, 1874), (len(sp1), n1)
    sp4, g4, n4 = rl.build_layout(rl.FILLS)
    assert (len(sp4), n4) == (296, 6344)
    sph, gh, nh = rl.build_layout(rl.FILLS, True)
    assert sum(len(g.thr) for g in gh) == 162 and all(g.headline for g in gh)
    ids = rl.column_ids(sp4, g4)
    assert len(ids) == n4 == len(set(ids)), "column ids must be unique"
    rng = np.random.default_rng(7)
    S = rng.normal(0, 4, size=(2, 80))
    R = rl.ratio_array(S, "abs", None)
    F = R[None] >= np.asarray(rl.ABS_GRID)[:, None, None]
    assert np.all(F[1:].sum() <= F[:-1].sum()) and all(F[i + 1].sum() <= F[i].sum() for i in range(len(F) - 1))
    print("layout sizes 86/1874, 296/6344, 162 headline; column ids unique; flags monotone in the threshold.")


if __name__ == "__main__":
    test_windowed_stats_match_oracle()
    test_iter_stats_gates_missing_bars_and_pads()
    test_unpadded_equals_padded_inside()
    test_ewma_and_cusum_hand_values()
    test_latch_matches_state_machine()
    test_block_runs_match_run_rule()
    test_draw_blocks_stay_inside_segments()
    test_pack_layout_hand_values()
    test_carry_window_keeps_real_time_length()
    test_layout_sizes_and_threshold_monotone()
    print("\nAll runlevel tests passed.")
