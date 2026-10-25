# version: 261025
"""
tests/test_run_runlevel_experiment.py

Method: run_runlevel_experiment.py on a SYNTHETIC base (primary arrays built in memory, with gaps, masked
stretches and a planted JPY run) for the numpy parts, plus one integration test that reuses the temp
14-day DB of test_run_inv4_attribution through the a1 dump (hermetic: temp files only).
Checks: stretch geometry / context buckets / minimum-spacing start rules hand values; start pools satisfy the
clip-and-mask geometry (run inside a ref stretch, 24 h spacing, prefix-stable allocation); trial windows mark
only in-stretch bars available; gap-policy parsing and layouts (reset / carry / bar-index, ref vs all);
injection: a large flat offset is detected in the 48+ bucket and never in the paired clean windows, class and
bucket splits sum to the totals, hysteresis cover / tail are supersets and ordered, checkpoints are prefix
stable, stretch-clustered CIs are ordered, trial table spacing; real pass: shapes, hysteresis supersets, the
planted run is seen by all_* policies and not by mask_* policies; per-bar dump round trip; R3 reference flag
timing; config rejections; (integration) a tiny end-to-end run of all sections on the synthetic DB.
14p4-vi: step_plateau shapes, the rz floor sweep, bank tiers (CI columns are a subset of the bank), the threshold-free
series dump (stats equal a direct iter_stats, apply_flags reproduces the real-pass flag counts: no rerun), alternative
run extents (detector extent equals the legacy real-run table; return-to-zero / fraction-of-peak hand values; user JSON).

Run: python3 -m tests.test_run_runlevel_experiment
"""

import json
import tempfile
from pathlib import Path

import numpy as np

import run_runlevel_experiment as x
from currency_graph import runlevel as rl
from time_policy import stored_ts_to_epoch

HOUR = 3600


def _synthetic(seed=0, hours=1700, plant=True):
    """Primary arrays like load_dump + build_base would produce (no w needed), then finish_base."""
    rng = np.random.default_rng(seed)
    t0 = stored_ts_to_epoch("2023-04-04T00:00:00.000000000Z")
    hrs = [h for h in range(hours) if not (400 <= h < 404 or 900 <= h < 930)]
    N = len(hrs)
    epoch = np.array([t0 + h * HOUR for h in hrs], np.int64)
    hour = ((epoch // HOUR) % 24).astype(int)
    masked = np.zeros(N, bool)
    s1 = stored_ts_to_epoch("2023-04-05T00:00:00.000000000Z")
    e1 = stored_ts_to_epoch("2023-04-05T19:00:00.000000000Z")
    run = (epoch >= s1) & (epoch <= e1)
    masked |= run
    masked[200:210] = True
    masked[1000:1008] = True
    pr = rng.normal(0, 0.8, size=(4, N))
    if plant:
        pr[3][run] += 10.0
    ps = 2.0 + np.abs(rng.normal(0, 0.3, size=(4, N)))
    contiguous = np.r_[False, np.diff(epoch) == HOUR]
    acl = np.cumsum(~contiguous) - 1
    rcl = np.full(N, -1)
    sr, prev = -1, None
    for i in range(N):
        if masked[i]:
            continue
        if not (prev is not None and prev == i - 1 and contiguous[i]):
            sr += 1
        rcl[i] = sr
        prev = i
    base = {"N": N, "pr": pr, "ps": ps, "ms": ps.mean(0), "epoch": epoch, "hour": hour, "cls": hour % 4,
            "cell": np.array(["x"] * N), "masked": masked, "acl": acl, "rcl": rcl}
    return x.finish_base(base)


def _tmp(name):
    return str(Path(tempfile.mkdtemp()) / name)


def _inj_cfg(base, **over):
    d = Path(tempfile.mkdtemp())
    cfg = {"inj_fills": ["full"], "detail": "headline", "hyst": [0.75, 0.5], "paths": ["arm:JPY"], "signs": [1], "mags": [15.0],
           "shapes": ["flat"], "durs": [6, 12], "stride": 24.0, "stride_class": {}, "seed": 5, "boot": 20, "checkpoints": [8, 16],
           "out_paths": {"inj_ckpt": str(d / "ckpt{k}.npz"), "inj": str(d / "inj.npz")}}
    return cfg | over


def _col(meta_inj, sid):
    return meta_inj["columns"].index(sid)


def test_geometry_buckets_and_spacing_hand_values():
    rcl = np.array([-1, 0, 0, 0, -1, 1, 1])
    p, rem = x.stretch_geometry(rcl)
    assert list(p) == [-1, 0, 1, 2, -1, 0, 1] and list(rem) == [-1, 3, 2, 1, -1, 2, 1], (p, rem)
    assert list(x.context_bucket([0, 5, 6, 11, 12, 23, 24, 47, 48, 100])) == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    ep = np.arange(0, 100) * HOUR
    assert x.spaced_rows(range(100), ep, 24) == [0, 24, 48, 72, 96]
    assert x.spaced_rows([0, 10, 30, 33, 60], ep, 24) == [0, 30, 60]
    assert x.spaced_rows([], ep, 24) == []
    print("stretch geometry, buckets and spacing hand values passed.")


def test_shapes_and_deviation():
    assert np.allclose(x.shape_vec("flat", 4), 1.0)
    assert np.allclose(x.shape_vec("ramp", 4), [0.25, 0.5, 0.75, 1.0])
    assert np.allclose(x.shape_vec("decay", 4), [1.0, 0.75, 0.5, 0.25])
    assert x.shape_vec("pulse", 5).max() <= 1.0 and x.shape_vec("pulse", 5).min() > 0
    assert abs(x.shape_vec("expdecay", 6)[0] - 1.0) < 1e-12 and x.shape_vec("expdecay", 6)[-1] < 0.1
    sp = x.shape_vec("sp3_p2", 8, 6.0)
    assert np.allclose(sp, [1, 1, 1, 1 / 3, 1 / 3, 1 / 3, 1 / 3, 1 / 3]), sp
    assert np.allclose(x.shape_vec("sp3_p0", 5, 4.0), [1, 1, 1, 0, 0]) and np.allclose(x.shape_vec("sp3_p3.5", 5, 2.0), 1.0), "plateau capped at the magnitude"
    assert np.allclose(x.shape_vec("sp3_p3", 2, 6.0), [1, 1]), "a duration shorter than the step is all step"
    try:
        x.shape_vec("sp3_p2", 5)
        raise RuntimeError("expected rejection without a magnitude")
    except AssertionError:
        pass
    assert x.SHAPES == x.SHAPES_BASE + x.SHAPES_SP and all(x.SP_RE.fullmatch(n) for n in x.SHAPES_SP)
    try:
        x.shape_vec("nope", 3)
        raise RuntimeError("expected rejection")
    except ValueError:
        pass
    rng = np.random.default_rng(1)
    PR = rng.normal(size=(4, 3, 5))
    for k in range(4):
        assert np.allclose(x.deviation(PR, k), PR[k] - np.median(PR, axis=0))
    print("injection shapes and path deviation passed.")


def test_finish_base_invariants():
    b = _synthetic()
    assert b["d"].shape == (4, b["N"]) and np.allclose(b["d"], b["pr"] - np.median(b["pr"], axis=0))
    ok = b["rcl"] >= 0
    assert np.all((b["p"] >= 0) == ok) and np.all((b["rem"] >= 1) == ok)
    for c in np.unique(b["rcl"][ok]):
        m = b["rcl"] == c
        assert len(set((b["p"][m] + b["rem"][m]).tolist())) == 1, "p + rem is constant inside a stretch"
        assert b["p"][m][0] == 0 and b["rem"][m][-1] == 1
    assert b["cl"].max() == b["n_cl"] - 1 and np.all(b["cl"][~ok] == -1)
    first = np.where(b["hour"] == 7)[0][:10]
    assert np.all(np.isnan(b["hour_scale"][:, first])), "same-hour scale needs 10 previous same-hour bars"
    assert np.isfinite(b["hour_scale"][:, np.where(b["hour"] == 7)[0][15]]).all()
    print("finish_base invariants passed.")


def test_start_pools_satisfy_geometry_spacing_and_prefix():
    b = _synthetic()
    for D in (1, 6, 24, 72):
        pools = x.ordered_starts(b, D, 24.0, {}, 11)
        again = x.ordered_starts(b, D, 24.0, {}, 11)
        other = x.ordered_starts(b, D, 24.0, {}, 12)
        assert pools == again, "seeded order must be deterministic"
        assert any(pools[c] != other[c] for c in pools if len(pools[c]) > 2), "the seed must change the order"
        for ci, c in enumerate(x.CLASSES):
            rows = np.array(pools[c], int)
            if len(rows) == 0:
                continue
            assert np.all(b["cls"][rows] == ci)
            assert np.all(b["p"][rows] >= 0) and np.all(b["rem"][rows] >= D), "the run must lie inside one ref stretch"
            e = np.sort(b["epoch"][rows])
            assert np.all(np.diff(e) >= 24 * HOUR), "minimum spacing within the class"
    pools = x.ordered_starts(b, 6, 24.0, {}, 11)
    r8, c8 = x.chunk_starts(pools, 0, 8)
    r16, c16 = x.chunk_starts(pools, 0, 16)
    r8b, _ = x.chunk_starts(pools, 8, 16)
    assert list(r16) == list(r8) + list(r8b) or sorted(r16) == sorted(list(r8) + list(r8b)), "prefix-stable allocation"
    assert len(r8) == 8 and set(c8.tolist()) == {0, 1, 2, 3}
    try:
        x.chunk_starts(pools, 16, 8)
        raise RuntimeError("expected rejection")
    except AssertionError:
        pass
    pc = x.ordered_starts(b, 6, 24.0, {"flat": 48.0}, 11)
    e = np.sort(b["epoch"][np.array(pc["flat"], int)])
    assert np.all(np.diff(e) >= 48 * HOUR), "per-class spacing override"
    print("start pools: geometry, spacing, determinism, prefix property passed.")


def test_trial_window_availability():
    b = _synthetic()
    D = 6
    for i in (int(np.flatnonzero((b["p"] == 2) & (b["rem"] >= D + 10))[0]), int(np.flatnonzero((b["p"] >= 60) & (b["rem"] == D + 3))[0])):
        I, av = x.trial_window(b, np.array([i]), D)
        k = np.arange(len(av[0])) - x.PRE
        want = (k >= -b["p"][i]) & (k < b["rem"][i])
        assert np.array_equal(av[0], want)
        assert I.shape == (1, x.PRE + D + x.POST)
        rcl = b["rcl"][np.clip(i + k, 0, b["N"] - 1)]
        assert np.all(rcl[av[0]] == b["rcl"][i]), "available bars are in the same stretch"
    print("trial windows: only in-stretch bars are available.")


def test_policy_parsing_and_layouts():
    assert x.parse_policy("mask_reset") == ("ref", 0) and x.parse_policy("all_chron") == ("all", 0)
    assert x.parse_policy("mask_carry2") == ("ref", 2) and x.parse_policy("all_carry12") == ("all", 12)
    assert x.parse_policy("mask_barindex") == ("ref", None) and x.parse_policy("all_barindex") == ("all", None)
    for bad in ("", "mask", "mask_carry", "masked_reset", "all_carry-1", "mask_reset2"):
        try:
            x.parse_policy(bad)
            raise RuntimeError(f"expected rejection of {bad!r}")
        except AssertionError:
            pass
    b = _synthetic()
    pos, M, segs = x.policy_layout(b, "all_chron")
    gaps = int((np.diff(b["epoch"]) != HOUR).sum())
    assert len(segs) == gaps + 1 and np.all(pos >= 0)
    pos, M, segs = x.policy_layout(b, "mask_reset")
    assert np.all(pos[b["masked"]] == -1) and np.all(pos[~b["masked"]] >= 0)
    assert len(segs) == b["n_cl"], "mask_reset segments = ref stretches"
    n_a = len(x.policy_layout(b, "all_carry3")[2])
    n_b = len(x.policy_layout(b, "all_carry4")[2])
    assert n_a == gaps + 1 - 0 and n_b == gaps, "the 4-bar hole is bridged only when carry >= 4; the 30-bar hole never"
    pos, M, segs = x.policy_layout(b, "mask_barindex")
    assert len(segs) == 1 and segs[0][1] == int((~b["masked"]).sum()) and M == segs[0][1]
    sc = x.get_policy(b, "mask_reset")["scales"]
    assert set(sc) == {"path", "roll", "bar", "hour", "rzraw"} | set(rl.RZ_NAMES) and all(v.shape == (4, b["N"]) for v in sc.values())
    assert np.isnan(sc["roll"][:, np.flatnonzero(b["p"] == 0)[:3]]).all(), "roll scale needs 12 bars"
    for fl in rl.RZ_FLOORS:
        nm = rl.rz_name(fl)
        assert np.nanmin(sc[nm]) >= fl - 1e-12, f"robust-z floor {fl}"
        assert np.array_equal(np.isnan(sc[nm]), np.isnan(sc["rzraw"])) and np.allclose(sc[nm], np.maximum(sc["rzraw"], fl), equal_nan=True)
    assert np.all((sc["rz0.5"] >= sc["rz0.25"]) | np.isnan(sc["rz0.5"]))
    assert x.get_policy(b, "mask_reset") is x.get_policy(b, "mask_reset"), "layouts are cached"
    print("gap policies: parsing, layouts, bridging and scales passed.")


def _check_injection(b):
    cfg = _inj_cfg(b)
    meta = x.run_injection(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["inj"]))
    nD = 2
    ids = meta["columns"]
    hc, cc = out["hcols"], out["ci_cols"]
    assert meta["n_cells"] == 2 and out["det"].shape == (2, meta["n_cols"]) and len(hc) == meta["n_headline_cols"] == 819
    assert len(cc) == meta["n_ci_cols"] == 234 and set(cc.tolist()) <= set(hc.tolist()), "CI columns are a subset of the bank"
    assert any(i.startswith("hl|W12|full|trailing|raw|rz|") for i in ids) and any("|abs|1.25" in i for i in ids)
    assert not any("|rz0.5|" in i for i in ids), "the bank carries the legacy rz floor only (totals for the other floors are in the full layout)"
    sid_hit = "median|W6|full|trailing|raw|abs|3"
    sid_lo = "median|W6|full|trailing|raw|abs|10"
    c3, c10 = ids.index(sid_hit), ids.index(sid_lo)
    h3 = int(np.flatnonzero(hc == c3)[0])
    k3 = int(np.flatnonzero(cc == c3)[0])
    ntr, ntb = out["n_trials"], out["n_trials_bucket"]
    assert ntr.min() >= 8, ntr
    for di in range(nD):
        assert out["hb_det"][4, di, h3] == ntb[4, di], "48+ bucket: every 15 bps run (flat) is detected at W6 / 3 bps"
        assert out["det"][di, c3] >= 0.7 * ntr[di]
        assert out["c_det"][di, c3] == 0 and out["c_det"][di, c10] == 0, "clean windows: no flag (noise 0.8 bps)"
    for m in x.METRICS:
        assert np.array_equal(out[f"h_{m}"].sum(0), out[m][:, hc]), f"class split must sum to the total ({m})"
        assert np.array_equal(out[f"hb_{m}"].sum(0), out[m][:, hc]), f"bucket split must sum to the total ({m})"
        assert np.array_equal(out[f"hc_{m}"].sum(0), out[f"c_{m}"][:, hc])
    assert np.array_equal(out["n_trials_class"].sum(0), ntr) and np.array_equal(ntb.sum(0), ntr) and np.array_equal(out["n_trials_stretch"].sum(1), ntr)
    for kind in ("cover", "tail"):
        plain = out[kind][:, hc]
        assert np.all(out[f"hy75_{kind}"] >= plain) and np.all(out[f"hy50_{kind}"] >= out[f"hy75_{kind}"]), f"latch supersets ({kind})"
        assert np.all(out[f"chy75_{kind}"] >= out[f"c_{kind}"][:, hc])
    assert np.any(out["hy50_cover"] > out["cover"][:, hc]) or np.any(out["hy50_tail"] > out["tail"][:, hc]), "hysteresis must change something at 15 bps"
    assert out["ci_det_lo"].shape == (2, 234) and np.all(out["ci_det_lo"] <= out["ci_det_hi"] + 1e-6)
    ok = ~np.isnan(out["ci_lat_lo"])
    assert ok.any() and np.all(out["ci_lat_lo"][ok] <= out["ci_lat_hi"][ok] + 1e-6)
    assert out["ci_det_hi"][0, k3] <= 1.0 + 1e-6
    # trial table: spacing within class and duration
    rows, cls, di_ = out["trial_row"], out["trial_cls"], out["trial_di"]
    for di in range(nD):
        for ci in range(4):
            e = np.sort(b["epoch"][rows[(di_ == di) & (cls == ci)]])
            assert np.all(np.diff(e) >= 24 * HOUR)
    assert np.all(b["rem"][rows] >= np.array([6, 12])[di_]) and np.all(b["p"][rows] >= 0)
    assert np.array_equal(out["tail_valid"].astype(int) >= 0, np.ones(nD, bool))
    return out


def test_injection_totals_splits_hysteresis_ci():
    b = _synthetic()
    _check_injection(b)
    print("injection: detection, clean windows, splits, hysteresis supersets, CIs, spacing passed.")


def test_injection_prefix_property_and_stronger_offset_detects_more():
    b = _synthetic()
    c1 = _inj_cfg(b, checkpoints=[8, 16])
    c2 = _inj_cfg(b, checkpoints=[16])
    x.run_injection(b, c1, {})
    x.run_injection(b, c2, {})
    o1, o2 = dict(np.load(c1["out_paths"]["inj"])), dict(np.load(c2["out_paths"]["inj"]))
    for k in ("det", "lat", "cover", "tail", "h_det", "hb_cover", "hy75_cover", "n_trials", "ci_det_lo"):
        assert np.allclose(o1[k], o2[k], equal_nan=True), f"checkpointed run differs from single run ({k})"
    ck = dict(np.load(c1["out_paths"]["inj_ckpt"].format(k=8)))
    assert ck["n_trials"].sum() < o1["n_trials"].sum() and np.all(ck["a_det"] <= o1["det"][:, o1["hcols"]])
    weak = _inj_cfg(b, mags=[2.0])
    x.run_injection(b, weak, {})
    ow = dict(np.load(weak["out_paths"]["inj"]))
    assert ow["det"][:, o1["hcols"]].sum() <= o1["det"][:, o1["hcols"]].sum(), "2 bps is detected no more often than 15 bps"
    print("injection: prefix property and magnitude ordering passed.")


def test_injection_full_layout_runs_and_signs_mirror():
    b = _synthetic()
    cfg = _inj_cfg(b, detail="full", signs=[1, -1], durs=[6], checkpoints=[4], boot=0)
    meta = x.run_injection(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["inj"]))
    assert meta["n_cols"] == 3746 and out["det"].shape == (2, 3746) and "ci_det_lo" not in out
    ids = meta["columns"]
    c = ids.index("median|W6|full|trailing|raw|abs|3")
    assert out["det"][0, c] == out["det"][1, c], "|statistic| is sign-symmetric for the median at 15 bps"
    assert all(f"median|W6|full|trailing|raw|{nm}|4" in ids for nm in rl.RZ_NAMES), "all four rz floors are in the full layout"
    for nm in rl.RZ_NAMES[1:]:
        assert out["det"][0, ids.index(f"median|W6|full|trailing|raw|{nm}|2")] <= out["det"][0, ids.index("median|W6|full|trailing|raw|rz|2")], "a higher floor flags no more"
    print("injection: full layout (98 specs, 3746 columns) and sign symmetry passed.")


def _real_cfg(**over):
    d = Path(tempfile.mkdtemp())
    cfg = {"detail": "headline", "policies": ["mask_reset", "all_chron", "mask_carry2", "mask_barindex"], "hyst": [0.75, 0.5], "blocks_per_length": 40,
           "boot": 5, "seed": 3, "out_paths": {"real": str(d / "real.npz")}}
    return cfg | over


def test_real_pass_shapes_hysteresis_policies_and_planted_run():
    b = _synthetic()
    cfg = _real_cfg()
    meta = x.real_pass(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["real"]))
    nPol, nH, nc = 4, 3, meta["n_cols"]
    assert nc == 819 and out["flagbars"].shape == (nPol, nH, 4, nc) and out["runs_total"].shape == (nPol, nH, 4, 4, nc)
    assert out["blk_runs"].shape == (nPol, nH, 7, 4, 4, nc) and out["blk_ci_lo"].shape == (nPol, nH, 7, 4, 4, 234)
    assert out["real_runs"].shape == (nPol, nH, 6, 4, nc, 5)
    assert meta["hyst_variants"] == [1.0, 0.75, 0.5]
    assert np.all(out["flagbars"][:, 1] >= out["flagbars"][:, 0]) and np.all(out["flagbars"][:, 2] >= out["flagbars"][:, 1]), "latch supersets"
    assert np.all(out["runs_total"][:, :, 0] >= out["runs_total"][:, :, 1]) and np.all(out["runs_total"][:, :, 1] >= out["runs_total"][:, :, 3]), "wider merge gap, fewer runs"
    assert np.all(out["runs_total"][:, :, 0] <= out["flagbars"])
    assert meta["segments"]["mask_barindex"]["n_segments"] == 1 and meta["segments"]["mask_carry2"]["carry"] == 2
    assert meta["segments"]["all_chron"]["population"] == "all" and meta["segments"]["mask_reset"]["run_bars_present"][0] == 0
    ids = meta["columns"]
    c = ids.index("median|W6|full|trailing|raw|abs|3")
    ri = 0                                                   # run1, planted at +10 bps on the JPY path
    pa, pm = meta["policies"].index("all_chron"), meta["policies"].index("mask_reset")
    nv_all, nv_mask = out["real_runs"][pa, 0, ri, 3, c, 0], out["real_runs"][pm, 0, ri, 3, c, 0]
    assert nv_all == 20 and nv_mask == 0, (nv_all, nv_mask)
    lat = out["real_runs"][pa, 0, ri, 3, c, 2]
    assert 2.0 <= lat <= 5.0, f"W6 median needs 4 offset bars: first flag at bar 3 (got {lat})"
    assert out["real_runs"][pa, 0, ri, 3, c, 1] >= 14
    assert out["real_runs"][pa, 0, ri, 0, c, 1] == 0, "the other paths are not flagged"
    assert out["real_runs"][pa, 2, ri, 3, c, 1] >= out["real_runs"][pa, 0, ri, 3, c, 1]
    assert np.all(np.isnan(out["blk_ci_lo"][pm]) | (out["blk_ci_lo"][pm] <= out["blk_ci_hi"][pm] + 1e-6))
    print("real pass: shapes, latch supersets, merge-gap order, policies and the planted run passed.")


def test_step_plateau_injection_orders_against_flat():
    b = _synthetic()
    cfg = _inj_cfg(b, shapes=["flat", "sp3_p0", "sp3_p3"], mags=[15.0], durs=[12], checkpoints=[8], boot=0)
    meta = x.run_injection(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["inj"]))
    ax = meta["cells_axes"]
    assert ax["shapes"] == ["flat", "sp3_p0", "sp3_p3"] and meta["n_cells"] == 3
    c = meta["columns"].index("median|W6|full|trailing|raw|abs|3")
    flat, p0, p3 = (out["cover"][i, c] for i in range(3))
    assert flat >= p3 >= p0 >= 0, (flat, p3, p0)
    assert out["det"][2, c] >= out["det"][1, c] > 0 and flat > p0
    print("step_plateau cells: cover orders flat >= plateau 3 >= pure step.")


def _tiny_extent_base():
    t0 = stored_ts_to_epoch("2023-04-04T00:00:00.000000000Z")
    N = 120
    d = np.zeros((4, N))
    d[3, 23] = 1.2
    d[3, 24:27] = 6.0
    d[3, 27:41] = 3.0
    d[3, 41:] = 0.5
    return {"epoch": t0 + np.arange(N, dtype=np.int64) * HOUR, "d": d}, t0


def test_duplicate_step_plateau_cells_equal_a_direct_evaluation():
    b = _synthetic()
    kw = dict(shapes=["flat", "sp3_p3", "sp3_p0"], mags=[2.0, 15.0], durs=[3, 12], signs=[1], checkpoints=[8], boot=5)
    c1, c2 = _inj_cfg(b, dedupe=True, **kw), _inj_cfg(b, dedupe=False, **kw)
    m1, m2 = x.run_injection(b, c1, {}), x.run_injection(b, c2, {})
    o1, o2 = dict(np.load(c1["out_paths"]["inj"])), dict(np.load(c2["out_paths"]["inj"]))
    assert m1["duplicate_cells"]["n"] > 0 and m2["duplicate_cells"]["n"] == 0
    for k in o1:
        if k.startswith("trial_"):
            continue
        assert np.allclose(o1[k], o2[k], equal_nan=True), f"deduplicated run differs from the direct one ({k})"
    ck1, ck2 = dict(np.load(c1["out_paths"]["inj_ckpt"].format(k=8))), dict(np.load(c2["out_paths"]["inj_ckpt"].format(k=8)))
    assert np.array_equal(ck1["a_det"], ck2["a_det"])
    print("duplicate step_plateau cells: copying the flat cell equals a direct evaluation (all arrays, checkpoint).")


def test_run_extents_hand_values_and_user_json():
    b, t0 = _tiny_extent_base()
    ep = lambda i: int(t0 + i * HOUR)
    ext = x.run_extents(b)
    assert list(ext) == ["detector"] + [f"rtz_x{X:g}_n{N}" for X in x.EXT_X for N in x.EXT_N] + [f"peak_f{F:g}_n3" for F in x.EXT_FRAC]
    assert ext["detector"][0][:2] == (ep(24), ep(43)) and not ext["detector"][0][2]
    assert ext["rtz_x1_n3"][0] == (ep(23), ep(40), False), "excursion extends one bar back (1.2 >= 1); quiet block starts at bar 41"
    assert ext["rtz_x2_n1"][0] == (ep(24), ep(40), False)
    assert ext["rtz_x1.5_n6"][0][:2] == (ep(24), ep(40))
    assert ext["peak_f0.5_n3"][0] == (ep(24), ep(40), False) and ext["peak_f0.25_n3"][0] == (ep(24), ep(40), False)
    for name, rows in ext.items():
        if name != "detector":
            assert all(r[2] for r in rows[1:]), f"{name}: runs outside the data are censored"
            assert all(r[0] <= r[1] for r in rows)
    ext = x.run_extents(b, {"run1": {"start": "2023-04-05T02:00:00.000000000Z", "end": "2023-04-05T10:00:00.000000000Z"}})
    assert ext["user"][0][:2] == (ep(26), ep(34)) and ext["user"][1][:2] == ext["detector"][1][:2], "unlisted runs keep the detector span"
    try:
        x.run_extents(b, {"run9": {"start": "2023-04-05T02:00:00.000000000Z", "end": "2023-04-05T10:00:00.000000000Z"}})
        raise RuntimeError("expected rejection of an unknown run")
    except AssertionError:
        pass
    b["d"][3, 24:41] = 0.1                                   # never above X: the excursion is empty -> end clamps to the start
    ext = x.run_extents(b)
    assert all(r[0] <= r[1] for r in ext["rtz_x1_n1"])
    print("run extents: return-to-zero / fraction-of-peak hand values, censoring, user JSON passed.")


def test_series_dump_reproduces_real_pass_flags_without_rerun():
    b = _synthetic()
    d = Path(tempfile.mkdtemp())
    scfg = {"series_policies": ["all_chron", "mask_reset"], "series_fills": ["full", "min3"], "series_w": [6, 12], "out_paths": {"series": str(d / "s.npz")}}
    smeta = x.run_series(b, scfg, {})
    z = dict(np.load(scfg["out_paths"]["series"]))
    specs = rl.series_specs(("full", "min3"), (6, 12))
    ids = [sp.sid for sp in specs]
    assert smeta["n_specs"] == len(specs) == len(ids) and list(z["spec_ids"]) == ids and smeta["spec_ids"] == ids
    st = z["stat_all_chron"]
    assert st.shape == (len(specs), 4, b["N"]) and st.dtype == np.float32 and list(z["rz_floors"]) == list(rl.RZ_FLOORS)
    pd = x.get_policy(b, "all_chron")
    Xs = {"raw": x.pack(b["d"], pd["pos"], pd["M"]), "norm": x.pack(b["d"] / np.maximum(b["ps"], 1e-9), pd["pos"], pd["M"])}
    direct = dict(rl.iter_stats(Xs, specs, 0, pd["M"], True))
    for i in (0, 7, ids.index("hl|W12|full|trailing|raw"), ids.index("huber|W6|min3|centred|norm"), ids.index("cusum|K1|-|trailing|raw")):
        assert np.allclose(x.unpack(direct[i], pd["pos"]), st[i], atol=1e-4, equal_nan=True), ids[i]
    assert np.all(np.isnan(z["stat_mask_reset"][:, :, b["masked"]])), "mask_reset series have no value on masked bars"
    lad = smeta["ladder"]
    assert lad["rz"]["rungs"] == list(rl.RZ_GRID) and len(lad["rz"]["rung_bps_at_median_by_path"]) == 4 and lad["abs"]["scale"] == 1.0
    assert set(smeta["profiles"]) == {"MASK", "ALERT", "CORRECT"} and smeta["profiles"]["CORRECT"]["gate"] == "MASK"
    # thresholds applied OFFLINE give the real pass's flag counts
    rcfg = _real_cfg(policies=["all_chron", "mask_reset"], out_paths={"real": str(d / "r.npz")})
    rmeta = x.real_pass(b, rcfg, {})
    r = dict(np.load(rcfg["out_paths"]["real"]))
    cols = [("median|W12|full|trailing|raw|abs|2", "abs", 2.0), ("median|W12|full|trailing|raw|abs|1.25", "abs", 1.25),
            ("median|W6|full|trailing|raw|rz|4", "rz", 4.0), ("median|W12|full|trailing|raw|path|1", "path", 1.0)]
    for pi, pol in enumerate(rmeta["policies"]):
        stp = z[f"stat_{pol}"]
        scale = {"abs": None, "path": z["scale_path"], "rz": np.maximum(z[f"scale_rzraw_{pol}"], 0.1)}
        for cid, sc, thr in cols:
            si, ci = ids.index(cid.rsplit("|", 2)[0]), rmeta["columns"].index(cid)
            flags = rl.apply_flags(stp[si], scale[sc], thr, sc)
            assert np.array_equal(flags.sum(1), r["flagbars"][pi, 0, :, ci]), (pol, cid, flags.sum(1), r["flagbars"][pi, 0, :, ci])
    print("series dump: equals a direct computation, masked bars empty, thresholds applied offline reproduce the real-pass flag counts.")


def test_real_ext_detector_extent_equals_legacy_table_and_alternatives_exist():
    b = _synthetic()
    cfg = _real_cfg(policies=["mask_reset", "all_chron"], user_extents={"run1": {"start": "2023-04-05T02:00:00.000000000Z", "end": "2023-04-05T10:00:00.000000000Z"}})
    meta = x.real_pass(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["real"]))
    names = meta["extents"]["definitions"]
    pa = meta["policies"].index("all_chron")
    assert names[0] == "detector" and "user" in names and out["real_ext"].shape == (len(names), 6, meta["n_cols"], 5)
    assert np.array_equal(out["real_ext"][0], out["real_runs"][pa, 0, :, 3]), "detector extent == the legacy JPY real-run table"
    c = meta["columns"].index("median|W6|full|trailing|raw|abs|3")
    ui = names.index("user")
    assert out["real_ext"][ui, 0, c, 0] == 9 and out["real_ext"][ui, 0, c, 1] <= 9, "user extent: 9 bars, fewer flagged than the full run"
    assert out["real_ext"][names.index("rtz_x1_n3"), 0, c, 0] >= 1
    assert meta["extents"]["spans"]["detector"][0][2] is False
    print("real_ext: detector extent equals the legacy table; user and return-to-zero extents are evaluated.")


def test_per_bar_dump_roundtrip():
    b = _synthetic()
    path = _tmp("bars.npz")
    x.write_bars(path, b, ["mask_reset", "all_chron", "mask_carry2"])
    z = x.load_bars_npz(path)
    assert int(z["schema"][0]) == 3 and list(z["rz_floors"]) == list(rl.RZ_FLOORS) and list(z["paths"]) == list(x.PATHS) and z["triangle_resid"].shape == (3, b["N"])
    assert np.allclose(z["d"], b["d"]) and np.array_equal(z["pos_in_stretch"], b["p"]) and np.array_equal(z["ctx_bucket"], b["bkt"])
    for pol in ("mask_reset", "all_chron", "mask_carry2"):
        assert f"roll_{pol}" in z and f"rz_{pol}" in z and f"rzraw_{pol}" in z
        assert np.allclose(z[f"rz_{pol}"], np.maximum(z[f"rzraw_{pol}"], 0.1), equal_nan=True)
    try:
        x.write_bars(path, b, [])
        raise RuntimeError("expected refusal to overwrite")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("per-bar dump round trip passed.")


def test_real_run_check_and_r3_reference_timing():
    b = _synthetic()
    chk = x.real_run_check(b)
    assert chk[0]["n_rows"] == 20 and chk[0]["n_masked"] == 20 and chk[2]["n_rows"] == 0 and chk[2]["median_d_jpy_bps"] is None
    assert abs(chk[0]["median_d_jpy_bps"] - 10.0) < 1.0
    i = int(np.flatnonzero((b["p"] >= 60) & (b["rem"] >= 40))[5])
    D = 24
    flags, est = x.r3_series(b, i, D, 10.0 * np.ones(D))
    assert not flags[:5].any() and flags[9:].all(), flags
    assert abs(est[-1] - 10.0) < 1.0
    flags3, _ = x.r3_series(b, i, 3, 10.0 * np.ones(3))
    assert not flags3.any(), "R3 cannot fire for a 3-bar run (a trailing W=12 median needs >= 6 offset bars)"
    print("real-run check and R3 reference timing passed.")


def _args(bars, *extra):
    return x.parse_args(["--chat", "14", "--bars", bars, "--out-dir", tempfile.mkdtemp(), *extra])


def test_config_rejections_and_output_names():
    bars = _tmp("b.jsonl")
    Path(bars).write_text("{}\n")
    cfg = x.build_config(_args(bars, "--tag", "T1", "--policies", "mask_reset,all_carry3", "--hyst-exit", "0.8,0.4", "--starts-per-cell", "20"), "261023")
    assert cfg["checkpoints"] == [20] and cfg["policies"] == ["mask_reset", "all_carry3"] and cfg["hyst"] == [0.8, 0.4]
    assert cfg["out_paths"]["meta"].endswith("runlevel_meta_chat14_261023_T1.json")
    cfg = x.build_config(_args(bars, "--starts-per-cell", "600"), "261023")
    assert cfg["checkpoints"] == [250, 500, 600]
    bad = [("--policies", "mask_reset,mask_reset"), ("--policies", "wat"), ("--hyst-exit", "1.0"), ("--hyst-exit", "0.5,0.5"), ("--sections", "inj,zzz"),
           ("--tag", "a b"), ("--checkpoints", "10,5"), ("--checkpoints", "2000"), ("--inj-fills", "half"), ("--stride", "0"),
           ("--stride-class", "nope=5"), ("--tier2-cap-mb", "0"), ("--starts-per-cell", "2"), ("--paths", "arm:XXX"), ("--durs", ""),
           ("--shapes", "sp3"), ("--shapes", "wobble"), ("--series-policies", "wat"), ("--series-w", "7"), ("--series-fills", "zzz"),
           ("--run-extents", "/nonexistent.json"), ("--mags", "0")]
    for flag, val in bad:
        try:
            x.build_config(_args(bars, flag, val), "261023")
            raise RuntimeError(f"expected rejection of {flag} {val}")
        except (AssertionError, SystemExit):
            pass
    try:
        x.build_config(_args(bars), "26102")
        raise RuntimeError("expected rejection of the date")
    except AssertionError:
        pass
    try:
        x.build_config(_args("/nonexistent.jsonl"), "261023")
        raise RuntimeError("expected rejection of the missing dump")
    except AssertionError as exc:
        assert "not found" in str(exc)
    cfg = x.build_config(_args(bars, "--tag", "T2"), "261023")
    Path(cfg["out_paths"]["meta"]).write_text("{}")
    try:
        x.build_config(argparse_same(bars, cfg, "--tag", "T2"), "261023")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejections and output-name assertions passed.")


def argparse_same(bars, cfg, *extra):
    return x.parse_args(["--chat", "14", "--bars", bars, "--out-dir", str(Path(cfg["out_paths"]["meta"]).parent), *extra])


def test_integration_all_sections_on_synthetic_db():
    import sqlite3

    import run_calibration_stats as cs
    import run_world_experiment as w
    from tests.test_run_inv4_attribution import _db

    con = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
    try:
        bars, typ = cs.load_all(con)
    finally:
        con.close()
    rows, skipped = cs.compute_rows(bars, typ)
    cs.build_report({"chat": 14, "sandbox_path": "x.db", "mask_pad": 2, "mask_min_run": 1}, rows, skipped)
    dump = _tmp("bars.jsonl")
    cs.dump_bars(dump, rows)
    d = tempfile.mkdtemp()
    args = x.parse_args(["--chat", "14", "--bars", dump, "--out-dir", d, "--tag", "it", "--starts-per-cell", "8", "--checkpoints", "8", "--durs", "6",
                         "--mags", "9", "--shapes", "flat", "--paths", "arm:JPY", "--signs", "1", "--blocks-per-length", "30", "--boot", "5",
                         "--detail", "headline", "--tier2-starts", "4", "--t2-legs", "GBP_JPY", "--t2-mags", "9", "--t2-shapes", "flat", "--t2-durs", "6",
                         "--policies", "mask_reset,all_chron,mask_carry2"])
    cfg = x.build_config(args, "261023")
    meta = x.run_all(cfg, {})
    for key in ("meta",):
        assert not Path(cfg["out_paths"][key]).exists(), "run_all does not write the meta (main does)"
    for key in ("bars", "series", "real", "inj", "tier2"):
        assert Path(cfg["out_paths"][key]).exists(), key
    assert meta["schema"] == 3 and "series" in meta and meta["series"]["n_specs"] == 256 and meta["n_rows"] == len(rows) and "real" in meta and "inj" in meta and "tier2" in meta
    assert meta["tier2"]["records"] >= 0 and len(meta["real_run_check"]) == 6
    for line in Path(cfg["out_paths"]["tier2"]).read_text().splitlines()[:3]:
        rec = json.loads(line)
        assert len(rec["err_mean"]) == len(w.METHODS) == len(rec["err_mean_r3"]) and rec["bucket"] in x.BUCKET_LABELS
    text = x.render_text(meta)
    assert "Run-level experiment" in text and "LIMITATIONS" in text
    z = x.load_bars_npz(cfg["out_paths"]["bars"])
    assert z["d"].shape[1] == len(rows)
    print("integration: bars, real, inj and tier2 ran end to end on the synthetic DB dump.")


if __name__ == "__main__":
    test_geometry_buckets_and_spacing_hand_values()
    test_shapes_and_deviation()
    test_finish_base_invariants()
    test_start_pools_satisfy_geometry_spacing_and_prefix()
    test_trial_window_availability()
    test_policy_parsing_and_layouts()
    test_injection_totals_splits_hysteresis_ci()
    test_injection_prefix_property_and_stronger_offset_detects_more()
    test_injection_full_layout_runs_and_signs_mirror()
    test_real_pass_shapes_hysteresis_policies_and_planted_run()
    test_step_plateau_injection_orders_against_flat()
    test_duplicate_step_plateau_cells_equal_a_direct_evaluation()
    test_run_extents_hand_values_and_user_json()
    test_series_dump_reproduces_real_pass_flags_without_rerun()
    test_real_ext_detector_extent_equals_legacy_table_and_alternatives_exist()
    test_per_bar_dump_roundtrip()
    test_real_run_check_and_r3_reference_timing()
    test_config_rejections_and_output_names()
    test_integration_all_sections_on_synthetic_db()
    print("\nAll run_runlevel_experiment tests passed.")
