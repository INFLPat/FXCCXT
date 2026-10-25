# version: 261023
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
timing; config rejections; (integration) a tiny end-to-end run of all four sections on the synthetic DB.

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
    assert set(sc) == {"path", "roll", "bar", "hour", "rz"} and all(v.shape == (4, b["N"]) or v.shape == b["ps"].shape for v in sc.values())
    assert np.isnan(sc["roll"][:, np.flatnonzero(b["p"] == 0)[:3]]).all(), "roll scale needs 12 bars"
    assert np.nanmin(sc["rz"]) >= 0.1 - 1e-12, "robust-z floor"
    assert x.get_policy(b, "mask_reset") is x.get_policy(b, "mask_reset"), "layouts are cached"
    print("gap policies: parsing, layouts, bridging and scales passed.")


def _check_injection(b):
    cfg = _inj_cfg(b)
    meta = x.run_injection(b, cfg, {})
    out = dict(np.load(cfg["out_paths"]["inj"]))
    nD = 2
    ids = meta["columns"]
    hc = out["hcols"]
    assert meta["n_cells"] == 2 and out["det"].shape == (2, meta["n_cols"]) and len(hc) == meta["n_headline_cols"] == 162
    sid_hit = "median|W6|full|trailing|raw|abs|3"
    sid_lo = "median|W6|full|trailing|raw|abs|10"
    c3, c10 = ids.index(sid_hit), ids.index(sid_lo)
    h3 = int(np.flatnonzero(hc == c3)[0])
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
    assert np.all(out["ci_det_lo"] <= out["ci_det_hi"] + 1e-6)
    ok = ~np.isnan(out["ci_lat_lo"])
    assert ok.any() and np.all(out["ci_lat_lo"][ok] <= out["ci_lat_hi"][ok] + 1e-6)
    assert out["ci_det_hi"][0, h3] <= 1.0 + 1e-6
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
    assert meta["n_cols"] == 1874 and out["det"].shape == (2, 1874) and "ci_det_lo" not in out
    ids = meta["columns"]
    c = ids.index("median|W6|full|trailing|raw|abs|3")
    assert out["det"][0, c] == out["det"][1, c], "|statistic| is sign-symmetric for the median at 15 bps"
    print("injection: full layout (86 specs) and sign symmetry passed.")


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
    assert nc == 162 and out["flagbars"].shape == (nPol, nH, 4, nc) and out["runs_total"].shape == (nPol, nH, 4, 4, nc)
    assert out["blk_runs"].shape == (nPol, nH, 7, 4, 4, nc) and out["blk_ci_lo"].shape == (nPol, nH, 7, 4, 4, 162)
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


def test_per_bar_dump_roundtrip():
    b = _synthetic()
    path = _tmp("bars.npz")
    x.write_bars(path, b, ["mask_reset", "all_chron", "mask_carry2"])
    z = x.load_bars_npz(path)
    assert int(z["schema"][0]) == 2 and list(z["paths"]) == list(x.PATHS) and z["triangle_resid"].shape == (3, b["N"])
    assert np.allclose(z["d"], b["d"]) and np.array_equal(z["pos_in_stretch"], b["p"]) and np.array_equal(z["ctx_bucket"], b["bkt"])
    for pol in ("mask_reset", "all_chron", "mask_carry2"):
        assert f"roll_{pol}" in z and f"rz_{pol}" in z
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
           ("--stride-class", "nope=5"), ("--tier2-cap-mb", "0"), ("--starts-per-cell", "2"), ("--paths", "arm:XXX"), ("--durs", "")]
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
    for key in ("bars", "real", "inj", "tier2"):
        assert Path(cfg["out_paths"][key]).exists(), key
    assert meta["schema"] == 2 and meta["n_rows"] == len(rows) and "real" in meta and "inj" in meta and "tier2" in meta
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
    test_per_bar_dump_roundtrip()
    test_real_run_check_and_r3_reference_timing()
    test_config_rejections_and_output_names()
    test_integration_all_sections_on_synthetic_db()
    print("\nAll run_runlevel_experiment tests passed.")
