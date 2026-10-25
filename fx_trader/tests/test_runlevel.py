# version: 261025
"""
tests/test_runlevel.py

Method: currency_graph/runlevel.py (numpy engine) against stdlib oracles. Checks: every windowed statistic
(median / mean / trim20 / signpers; 14p4-vi: hl and huber to 1e-10) equals ref_stat over the same window to 1e-12 for trailing and centred
windows, all fills, with NaNs; a statistic is NaN where its own bar is missing (all fills); EWMA / CUSUM hand
values; the hysteresis latch equals a plain Python state machine on random inputs; block run counts equal a
Python re-implementation of the run rule (count_runs) on random flags; drawn blocks never cross a segment;
pack_layout hand values for carry 0 / g / None; layout sizes by formula (14p4-vi dense ladders, hl / huber, four rz
floors, hl / huber trailing only in the layout: 98 specs / 3746 columns for one fill, 344 / 12968 for four, tiers 585 + 234 = 819 bank columns); threshold
monotonicity of flags; HL / Huber hand values and robustness; series specs; profiles select statistics only;
apply_flags equals the in-engine flag and the latch; CORRECT gating; rz scale names.

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
            hl, hu = rl.hl_stat(V), rl.huber_stat(V)
            for r in range(X.shape[0]):
                for t in range(X.shape[1]):
                    vals = [x for x in V[r, t] if not math.isnan(x)]
                    for kind in ("median", "mean", "trim20"):
                        exp = rl.ref_stat(vals, kind)
                        got = o[kind][r, t]
                        assert (math.isnan(exp) and math.isnan(got)) or abs(exp - got) < 1e-12, (caus, W, kind, r, t)
                    for kind, arr in (("hl", hl), ("huber", hu)):
                        exp = rl.ref_stat(vals, kind)
                        got = arr[r, t]
                        assert (math.isnan(exp) and math.isnan(got)) or abs(exp - got) < 1e-10, (caus, W, kind, r, t)
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
    for F, fills in ((1, ("full",)), (4, rl.FILLS)):
        sp, g, n = rl.build_layout(fills)
        n_raw = 36 * F + 8                                  # windowed 3 x 5 W x 2 causalities x F + hl / huber 2 x 3 W x F (trailing only), EWMA 5, CUSUM 3
        per_raw = 4 * len(rl.RATIO_GRID) + len(rl.ABS_GRID) + 4 * len(rl.RZ_GRID)
        assert (len(sp), n) == (2 * n_raw + 10 * F, n_raw * per_raw + n_raw * len(rl.RATIO_GRID) + 10 * F * len(rl.SHARE_GRID)), (F, len(sp), n)
    assert (len(rl.build_layout(("full",))[0]), rl.build_layout(("full",))[2]) == (98, 3746)
    sp4, g4, n4 = rl.build_layout(rl.FILLS)
    assert (len(sp4), n4) == (344, 12968)
    sph, gh, nh = rl.build_layout(rl.FILLS, True)
    assert nh == 819 and all(g.headline in (1, 2) for g in gh)
    assert sum(len(g.thr) for g in gh if g.headline == 2) == 234 and sum(len(g.thr) for g in gh if g.headline == 1) == 585
    assert all(not (g.headline and g.spec is None) for g in gh)
    tiers = {(sph[g.spec].stat, g.headline) for g in gh}
    assert tiers == {("median", 2), ("mean", 2), ("trim20", 1), ("hl", 1), ("huber", 1), ("ewma", 1), ("cusum", 1)}, tiers
    assert all(sph[g.spec].caus == "trailing" and sph[g.spec].fill in ("full", "-") for g in gh), "the bank is trailing only"
    ids = rl.column_ids(sp4, g4)
    assert len(ids) == n4 == len(set(ids)), "column ids must be unique"
    rng = np.random.default_rng(7)
    S = rng.normal(0, 4, size=(2, 80))
    R = rl.ratio_array(S, "abs", None)
    F = R[None] >= np.asarray(rl.ABS_GRID)[:, None, None]
    assert np.all(F[1:].sum() <= F[:-1].sum()) and all(F[i + 1].sum() <= F[i].sum() for i in range(len(F) - 1))
    print("layout sizes (98/3746, 344/12968, 819 bank = 585 + 234); column ids unique; flags monotone in the threshold.")


def test_hl_and_huber_hand_values_and_robustness():
    one = np.array([[[3.0]]])
    assert rl.hl_stat(one)[0, 0] == 3.0 and rl.huber_stat(one)[0, 0] == 3.0
    V = np.array([[[1.0, 2.0, 4.0]]])               # Walsh averages: 1, 1.5, 2.5, 2, 3, 4 -> sorted 1, 1.5, 2, 2.5, 3, 4 -> median 2.25
    assert abs(rl.hl_stat(V)[0, 0] - 2.25) < 1e-12
    assert abs(rl.ref_stat([1.0, 2.0, 4.0], "hl") - 2.25) < 1e-12
    same = np.full((1, 1, 5), 2.0)
    assert rl.huber_stat(same)[0, 0] == 2.0, "MAD = 0 -> the median"
    assert math.isnan(rl.hl_stat(np.full((1, 1, 4), np.nan))[0, 0]) and math.isnan(rl.huber_stat(np.full((1, 1, 4), np.nan))[0, 0])
    clean = np.array([0.1, -0.2, 0.0, 0.3, -0.1, 0.2, 0.05, -0.05, 0.15, -0.15, 0.1, -0.1])
    dirty = clean.copy()
    dirty[0] = 50.0
    for fn in (rl.hl_stat, rl.huber_stat):
        a = fn(clean[None, None, :])[0, 0]
        b = fn(dirty[None, None, :])[0, 0]
        m = dirty.mean()
        assert abs(b - a) < 0.5 and abs(b - a) < abs(m - clean.mean()) / 5, (fn.__name__, a, b)
    big = np.random.default_rng(11).normal(size=(2, 5000, 25))
    assert np.allclose(rl.hl_stat(big)[:, :3], rl.hl_stat(big[:, :3]), equal_nan=True), "chunking must not change values"
    assert np.allclose(rl.huber_stat(big)[:, 4000:], rl.huber_stat(big[:, 4000:]), equal_nan=True)
    print("Hodges-Lehmann / Huber: hand values, MAD = 0, NaN windows, robustness to one outlier, chunk invariance passed.")


def test_series_specs_profiles_flags_and_correct():
    sps = rl.series_specs(rl.FILLS, (6, 12, 24))
    assert len(sps) == 2 * (5 * 3 * 2 * 4 + 5) + 6 and len({sp.sid for sp in sps}) == len(sps)
    assert {sp.stat for sp in sps} == {"median", "mean", "trim20", "hl", "huber", "ewma", "cusum"}
    full = rl.build_layout(rl.FILLS)[0]
    for name in rl.PROFILES:
        ids = rl.profile_spec_ids(name, full)
        assert ids and all("trailing" in i for i in ids), name
    mask = rl.profile_spec_ids("MASK", full)
    assert "hl|W12|full|trailing|raw" in mask and "median|W24|full|trailing|norm" in mask and not any("|W6|" in i for i in mask)
    alert = rl.profile_spec_ids("ALERT", full)
    assert "cusum|K1|-|trailing|raw" in alert and "median|W6|full|trailing|raw" in alert and not any("|W12|" in i for i in alert)
    assert set(rl.profile_spec_ids("CORRECT", full)) <= set(mask), "CORRECT estimates come from the MASK family at W12"
    rng = np.random.default_rng(8)
    S = rng.normal(0, 3, size=(2, 120))
    S[0, 30:50] += 8
    sc = np.abs(rng.normal(2, 0.5, size=(2, 120))) + 0.5
    for scale, arr, thr in (("abs", None, 3.0), ("path", sc, 1.5), ("rz", sc, 2.0), ("rz0.5", sc, 2.0)):
        ref = rl.ratio_array(S, scale, arr) >= thr
        assert np.array_equal(rl.apply_flags(S, arr, thr, scale), ref), scale
        lat = rl.apply_flags(S, arr, thr, scale, 0.5)
        assert np.all(lat >= ref) and np.array_equal(lat, rl.latch(ref, rl.ratio_array(S, scale, arr) >= thr * 0.5))
    for bad in ((0.0, 1.0), (1.0, 0.0), (1.0, 1.5)):
        try:
            rl.apply_flags(S, None, bad[0], "abs", bad[1])
            raise RuntimeError("expected rejection")
        except AssertionError:
            pass
    est = np.array([1.0, np.nan, 3.0, 4.0])
    assert list(rl.gated_estimate(est, np.array([True, True, False, True]))) == [1.0, 0.0, 0.0, 4.0], "gated: NaN or ungated -> no correction"
    assert rl.RZ_NAMES == ("rz", "rz0.25", "rz0.5", "rz1") and rl.rz_name(0.1) == "rz"
    assert set(rl.ARRAY_SCALES) == {"path", "roll", "bar", "hour"} | set(rl.RZ_NAMES)
    try:
        rl.ratio_array(S, "rz1", None)
        raise RuntimeError("expected rejection")
    except AssertionError:
        pass
    print("series specs, profiles (selection only), apply_flags = engine flag / latch, CORRECT gating, rz names passed.")


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
    test_hl_and_huber_hand_values_and_robustness()
    test_series_specs_profiles_flags_and_correct()
    print("\nAll runlevel tests passed.")
