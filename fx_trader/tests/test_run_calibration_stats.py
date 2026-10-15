# version: 261014
"""
tests/test_run_calibration_stats.py

Method: reuses the temp 14-day synthetic DB of test_run_inv4_attribution (336
contiguous hourly bars; one bar, 2024-01-14T21:00, has USD_CHF +40 bps with
volume 1; hermetic). Pure helpers are checked on hand-built inputs; the full
pass is checked for the faulted bar's flags, the C-grid default agreeing with
detector_c (asserted per bar inside compute_row), mask and contiguity numbers
worked out by hand, constant-spread correlation handled as None, and config
rejections.

Run: python3 -m tests.test_run_calibration_stats
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import run_calibration_stats as cs
from tests.test_run_inv4_attribution import FAULT_STAMP, _db


def _rows(path: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars, typ = cs.load_all(con)
    finally:
        con.close()
    return cs.compute_rows(bars, typ)


def test_pure_helpers():
    assert cs.bar_classes(21, "both_summer") == ["all", "rollover_ny", "utc21_legacy"]
    assert cs.bar_classes(22, "both_winter") == ["all", "rollover_ny"]
    assert cs.bar_classes(21, "both_winter") == ["all", "adjacent_ny", "utc21_legacy"]
    assert cs.bar_classes(10, "both_winter") == ["all", "flat"]
    assert cs.stretch_lengths([0, 3600, 7200, 14400, 18000]) == [3, 2]
    assert cs.block_starts([3, 2], 2) == 2 + 1 and cs.block_starts([3, 2], 4) == 0 and cs.block_starts([3, 2], 1) == 5
    assert len(cs.masked_epochs([(36000, 36000)], 2)) == 5 and cs.masked_epochs([], 3) == set()
    assert abs(cs.mad_sigma([1.0, 2.0, 3.0, 4.0, 5.0]) - 1.4826 * 1.0) < 1e-12
    assert cs.excess_kurtosis([1.0, 1.0, 1.0, 1.0]) is None
    assert abs(cs.excess_kurtosis([-1.0, 1.0, -1.0, 1.0]) - (-2.0)) < 1e-12
    assert cs.tail_stats([1.0] * 5) == {"n": 5}
    print("pure-helper assertions passed.")


def test_c_components_rule():
    L = cs.LegInfo
    ok = [L(p, 900, 12.0, 1000.0) for p in cs.PAIRS]
    assert cs.c_components(cs.c_prep(ok), 2, 0.05) == (False, False, False)
    stale = list(ok)
    stale[0] = L(cs.PAIRS[0], 1, 12.0, 1000.0)
    assert cs.c_components(cs.c_prep(stale), 2, 0.05)[0] is True and cs.c_components(cs.c_prep(stale), 0, 0.05)[0] is False
    zero = list(ok)
    zero[1] = L(cs.PAIRS[1], 500, 0.0, 1000.0)
    assert cs.c_components(cs.c_prep(zero), 2, 0.05) == (False, True, False)
    thin = list(ok)
    thin[2] = L(cs.PAIRS[2], 24, 12.0, 1000.0)
    assert cs.c_components(cs.c_prep(thin), 2, 0.05) == (False, False, True) and cs.c_components(cs.c_prep(thin), 2, 0.02)[2] is False
    print("C component rule assertions passed.")


def test_full_pass_on_synthetic_world():
    rows, skipped = _rows(_db())
    assert len(rows) == 14 * 24 and skipped == 0
    cfg = {"chat": 14, "sandbox_path": "x.db", "mask_pad": 2, "mask_min_run": 1}
    rep = cs.build_report(cfg, rows, skipped)
    allp = rep["populations"]["all"]
    key = cs.c_key(cs.C_STALE_VOL, cs.C_THIN_FRAC)
    assert allp["all"]["flag_counts"]["A"]["10"] == 1 and allp["h21"]["n"] == 14 and "note" in allp["h21"], "14 bars: under MIN_GROUP, counts withheld"
    assert allp["all"]["flag_counts"]["C"][key]["any"] >= 1 and allp["all"]["flag_counts"]["C"][key]["stale_volume"] >= 1
    assert allp["all"]["flag_counts"]["A"]["3"] >= allp["all"]["flag_counts"]["A"]["10"] >= allp["all"]["flag_counts"]["A"]["20"]
    assert set(allp["all"]["flag_counts"]["B"]) == set(cs.B_KINDS) and allp["flat"]["n"] == 14 * 8
    assert rep["n_ref_bars"] == 336 - 5 and rep["mask"]["n_masked_bars"] == 5, "fault bar +/- 2 h, all inside the data"
    assert rep["contiguity"]["all"]["valid_block_starts"]["24"] == 336 - 23 and rep["contiguity"]["ref"]["n_stretches"] == 1
    assert rep["contiguity"]["ref"]["valid_block_starts"]["48"] == 331 - 47
    corr = allp["all"]["log_spread_corr"]
    assert corr["matrix"][cs.PAIRS[0]][cs.PAIRS[0]] == 1.0 and (corr["mean_offdiag"] is None or -1.0 <= corr["mean_offdiag"] <= 1.0), "near-constant spreads: defined or None, never a crash"
    assert json.loads(json.dumps(rep))["tool"] == "run_calibration_stats" and "Calibration stats" in cs.render_text(rep)
    print("full-pass assertions passed (fault bar flagged by A and C, mask and block-start counts match hand values).")


def test_bar_dump_and_config_rejections():
    rows, _ = _rows(_db())
    cs.build_report({"chat": 14, "sandbox_path": "x.db", "mask_pad": 2, "mask_min_run": 1}, rows, 0)
    out = tempfile.mkdtemp()
    dump = str(Path(out) / "bars.jsonl")
    cs.dump_bars(dump, rows)
    lines = Path(dump).read_text().splitlines()
    first = json.loads(lines[0])
    assert len(lines) == 336 and first["t"].startswith("2024-01-01") and len(first["loo"]) == 8 and first["loo"][-1] is None, "USD_CAD is the bridge"
    assert sum(json.loads(x)["masked"] for x in lines) == 5
    try:
        cs.dump_bars(dump, rows)
        raise RuntimeError("expected refusal to overwrite")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    path = _db()

    def args(*extra):
        return cs.parse_args(["--chat", "14", "--sandbox-db", path, "--out-dir", out, *extra])

    cfg = cs.build_config(args("--tag", "T1", "--dump-bars"), "000101")
    assert Path(cfg["out_path"]).name == "calibration_stats_chat14_000101_T1.json" and Path(cfg["bars_path"]).name == "calibration_bars_chat14_000101_T1.jsonl"
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--mask-pad", "99"), "mask-pad"), (("--mask-min-run", "0"), "mask-min-run")):
        try:
            cs.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        cs.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("bar dump / config rejection assertions passed.")


if __name__ == "__main__":
    test_pure_helpers()
    test_c_components_rule()
    test_full_pass_on_synthetic_world()
    test_bar_dump_and_config_rejections()
    print("\nAll run_calibration_stats tests passed.")
