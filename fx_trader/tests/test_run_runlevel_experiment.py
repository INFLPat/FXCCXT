# version: 261021
"""
tests/test_run_runlevel_experiment.py

Method: reuses the temp 14-day synthetic world of test_run_inv4_attribution through
the a1-style dump of test_run_world_experiment (hermetic: temp files only, no real
outputs). Checks: shape / deviation / packing hand values; the path deviation built
from the dump equals robust.path_attribution's 'dev' on reconstructed bars; start
eligibility (inside one ref stretch, right class, prefix-stable checkpoints); a
tiny end-to-end run (all four sections, headline detail) that detects a 15 bps
injected offset in every trial while its paired clean windows are never flagged,
is deterministic, and writes the per-bar dump and Tier 2 JSONL; config rejections.

Run: python3 -m tests.test_run_runlevel_experiment
"""

import json
import random
import tempfile
from pathlib import Path

import numpy as np

import run_runlevel_experiment as rr
import run_world_experiment as w
from tests.test_run_world_experiment import _dump

_CACHE = {}


def _rows() -> list:
    if "rows" not in _CACHE:
        rows = w.load_dump(_dump())
        w.setup_ctx(rows, 10, 7)
        _CACHE["rows"] = rows
    return _CACHE["rows"]


def _args(out: str, *extra):
    return rr.parse_args(["--chat", "14", "--bars", _dump(), "--out-dir", out, "--stride", "1", "--starts-per-cell", "8",
                          "--checkpoints", "4,8", "--paths", "arm:JPY", "--signs", "1", "--mags", "15", "--shapes", "flat",
                          "--durs", "6", "--blocks-per-length", "40", "--boot", "10", "--detail", "headline",
                          "--tier2-starts", "4", "--t2-legs", "GBP_JPY", "--t2-mags", "9", "--t2-shapes", "flat",
                          "--t2-durs", "3", *extra])


def test_shapes_deviation_and_packing_hand_values():
    assert np.allclose(rr.shape_vec("ramp", 4), [0.25, 0.5, 0.75, 1.0]) and np.allclose(rr.shape_vec("decay", 4), [1.0, 0.75, 0.5, 0.25])
    assert np.allclose(rr.shape_vec("flat", 3), 1.0)
    for name in rr.SHAPES:
        assert np.allclose(rr.shape_vec(name, 1), 1.0), name
    pulse = rr.shape_vec("pulse", 6)
    assert np.allclose(pulse, pulse[::-1]) and pulse.max() <= 1.0
    ex = rr.shape_vec("expdecay", 5)
    assert ex[0] == 1.0 and np.all(np.diff(ex) < 0)

    PR = np.zeros((4, 1, 1))
    PR[0, 0, 0] = 8.0
    assert abs(rr.deviation(PR, 0)[0, 0] - 8.0) < 1e-12, "median of [0,0,0,8] is 0"
    PR = np.array([10.0, 2.0, 4.0, 6.0]).reshape(4, 1, 1)
    assert abs(rr.deviation(PR, 0)[0, 0] - 5.0) < 1e-12, "injected path also moves the median: sorted [2,4,6,10] -> 5"
    assert abs(rr.deviation(PR, 3)[0, 0] - 1.0) < 1e-12

    pos, M, segs = rr.pack_index(np.array([0, 0, 0, -1, 1, 1, 2]))
    assert list(pos) == [0, 1, 2, -1, 53, 54, 105] and M == 106 and segs == [(0, 3), (53, 2), (105, 1)], (pos, M, segs)
    arr = np.arange(14.0).reshape(2, 7)
    back = rr.unpack(rr.pack(arr, pos, M), pos)
    assert np.array_equal(back[:, [0, 1, 2, 4, 5, 6]], arr[:, [0, 1, 2, 4, 5, 6]]) and np.isnan(back[:, 3]).all()
    print("shape / deviation / packing hand-value assertions passed.")


def test_path_deviation_equals_robust_path_attribution():
    rows = _rows()
    base = rr.build_base(rows)
    assert base["pr"].shape == (4, len(rows)) and base["d"].shape == (4, len(rows))
    for i in (60, 150, 250):
        bar = w.emp_bar(rows[i], random.Random(1))
        pa = w.path_attribution(bar["obs"])
        assert len(pa.paths) == 4
        for p in pa.paths:
            j = rr.PATHS.index(p["label"])
            assert abs(p["dev"] - base["d"][j, i]) < 1e-6, (i, p["label"], p["dev"], base["d"][j, i])
    tri = base["pr"][1:] - base["pr"][0]
    assert np.allclose(tri, base["d"][1:] - base["d"][0])
    print("dump-route path deviation == robust.path_attribution dev (3 bars x 4 paths).")


def test_start_eligibility_and_prefix_stability():
    base = rr.build_base(_rows())
    pools = rr.ordered_starts(base, 6, 1, {}, 5)
    assert set(pools) == set(rr.CLASSES) and all(len(v) > 2 for v in pools.values())
    for c, lst in pools.items():
        for i in lst:
            assert i - rr.PRE >= 0 and base["rcl"][i - rr.PRE] >= 0
            assert base["rcl"][i - rr.PRE] == base["rcl"][i + 6 + rr.POST - 1], "window must sit inside one ref stretch"
            assert not base["masked"][i] and rr.CLASSES[base["cls"][i]] == c
    a, _ = rr.chunk_starts(pools, 0, 4)
    ab, _ = rr.chunk_starts(pools, 0, 8)
    b, _ = rr.chunk_starts(pools, 4, 8)
    assert set(a.tolist()) <= set(ab.tolist()) and not (set(a.tolist()) & set(b.tolist())) and len(ab) == 8
    assert set(rr.ordered_starts(base, 6, 1, {}, 5)["flat"]) == set(pools["flat"]), "deterministic for a seed"
    sparse = rr.ordered_starts(base, 6, 1, {"flat": 10}, 5)
    assert len(sparse["flat"]) < len(pools["flat"]), "per-class stride override must thin that class only"
    print("start eligibility / prefix-stability assertions passed.")


def test_config_names_and_rejections():
    out = tempfile.mkdtemp()
    cfg = rr.build_config(_args(out, "--tag", "T1"), "000101")
    assert Path(cfg["out_paths"]["meta"]).name == "runlevel_meta_chat14_000101_T1.json"
    assert Path(cfg["out_paths"]["bars"]).name == "runlevel_bars_chat14_000101_T1.npz" and cfg["checkpoints"] == [4, 8]
    assert Path(cfg["out_paths"]["inj_ckpt"].format(k=4)).name == "runlevel_inj_chat14_000101_T1_ckpt4.npz"
    cases = [(("--tag", "bad tag!"), "tag"), (("--stride", "0"), "stride"), (("--sections", "nonsense"), "must be among"),
             (("--checkpoints", "5,3"), "checkpoints"), (("--stride-class", "foo=3"), "stride-class"), (("--durs", ""), "empty")]
    for extra, expected in cases:
        try:
            rr.build_config(_args(out, *extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), (extra, str(exc))
    try:
        rr.build_config(rr.parse_args(["--chat", "14", "--bars", "/nonexistent.jsonl"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_paths"]["real"]).write_text("x")
    try:
        rr.build_config(_args(out, "--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    default = rr.build_config(rr.parse_args(["--chat", "14", "--bars", _dump(), "--out-dir", out]), "000102")
    assert default["checkpoints"] == [250, 500, 750, 1000] and default["stride"] == 24 and len(default["mags"]) == 6
    print("config / output-name assertions passed.")


def test_end_to_end_small_run_detects_injection_and_is_deterministic():
    out = tempfile.mkdtemp()
    cfg = rr.build_config(_args(out, "--tag", "T1"), "000101")
    meta = rr.run_all(cfg)
    assert [r["n_rows"] for r in meta["real_run_check"]] == [0] * 6, "synthetic dates contain none of the six real runs"
    for key in ("bars", "real", "inj", "tier2"):
        assert Path(cfg["out_paths"][key]).exists(), key
    assert Path(cfg["out_paths"]["inj_ckpt"].format(k=4)).exists() and Path(cfg["out_paths"]["inj_ckpt"].format(k=8)).exists()

    dump = rr.load_bars_npz(cfg["out_paths"]["bars"])
    n = len(_rows())
    assert dump["pr"].shape == (4, n) and dump["triangle_resid"].shape == (3, n) and list(dump["paths"]) == list(rr.PATHS)

    cols = meta["inj"]["columns"]
    col = cols.index("median|W6|full|trailing|raw|abs|5")
    inj = np.load(cfg["out_paths"]["inj"])
    assert int(inj["n_trials"][0]) == 8, inj["n_trials"]
    assert int(inj["det"][0, col]) == 8, "a flat 15 bps offset must be flagged in every trial"
    assert int(inj["c_det"][0, col]) == 0, "paired clean windows must not be flagged"
    assert 8 * 2 <= int(inj["lat"][0, col]) <= 8 * 3, "W=6 trailing median (even window) flags at bar 2 or 3 of the run"
    assert 8 * 3 <= int(inj["cover"][0, col]) <= 8 * 4, "flagged from bar 2 or 3 to the end of a 6-bar run"
    assert inj["h_det"].shape == (4, 1, meta["inj"]["n_headline_cols"]) and int(inj["n_trials_class"].sum()) == 8

    real = np.load(cfg["out_paths"]["real"])
    ncols = meta["real"]["n_cols"]
    assert real["flagbars"].shape == (2, 4, ncols) and real["blk_runs"].shape[:4] == (2, 7, 4, 4)

    lines = Path(cfg["out_paths"]["tier2"]).read_text().splitlines()
    assert len(lines) == 4
    rec = json.loads(lines[0])
    assert len(rec["err_mean"]) == len(w.METHODS) == len(rec["top1_r3"]) and rec["leg"] == "GBP_JPY" and rec["dur"] == 3

    cfg2 = rr.build_config(_args(out, "--tag", "T2", "--sections", "inj"), "000101")
    rr.run_all(cfg2)
    inj2 = np.load(cfg2["out_paths"]["inj"])
    for k in ("det", "lat", "cover", "tail", "c_det"):
        assert np.array_equal(inj[k], inj2[k]), f"{k}: same seed must reproduce"
    print("end-to-end assertions passed (15 bps flat offset: 8/8 detected, 0/8 clean, deterministic).")


if __name__ == "__main__":
    test_shapes_deviation_and_packing_hand_values()
    test_path_deviation_equals_robust_path_attribution()
    test_start_eligibility_and_prefix_stability()
    test_config_names_and_rejections()
    test_end_to_end_small_run_detects_injection_and_is_deterministic()
    print("\nAll run_runlevel_experiment tests passed.")
