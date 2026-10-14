# version: 261013
"""
tests/test_run_detector_scan.py

Method: reuses the temp 14-day synthetic DB of test_run_inv4_attribution (one
bar with a +40 bps / volume-1 USD_CHF fault; hermetic). Checks: the scan finds
exactly that bar with A, B (all three) and C, flags nothing else, counts
degraded bars separately (one bar loses a pair), reports hour counts that sum
to the flagged totals, and rejects bad config.

Run: python3 -m tests.test_run_detector_scan
"""

import sqlite3
import tempfile
from pathlib import Path

import run_detector_scan as ds
from tests.test_run_inv4_attribution import FAULT_STAMP, _db


def _scan(path: str) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars, typ = ds.load_all(con)
    finally:
        con.close()
    return ds.scan(bars, typ)


def test_scan_finds_only_the_faulted_bar():
    rep = _scan(_db())
    assert rep["n_full_bars"] == 14 * 24 and rep["n_skipped_degraded"] == 0
    for d in ds.DETECTORS:
        assert rep["flagged"][d] == 1, (d, rep["flagged"])
        assert rep["by_hour"][d][21] == 1 and sum(rep["by_hour"][d]) == 1
    assert rep["overlap"]["A&C"] == 1 and rep["disagreement_with_A"]["B_arm_spread_not_A"] == 0
    assert rep["n_bars_with_hour_history"] > 14 * 24 - 24 * 10 - 1
    print("scan finds exactly the one faulted bar under A, B x3 and C.")


def test_degraded_bar_is_skipped_not_scanned():
    path = _db()
    con = sqlite3.connect(path)
    con.execute("DELETE FROM candles WHERE instrument='EUR_GBP' AND timestamp=?", (FAULT_STAMP.replace("21:00", "05:00"),))
    con.commit()
    con.close()
    rep = _scan(path)
    assert rep["n_skipped_degraded"] == 1 and rep["n_full_bars"] == 14 * 24 - 1
    print("degraded-bar skip assertion passed.")


def test_config_rejections():
    path = _db()
    out = tempfile.mkdtemp()

    def args(*extra):
        return ds.parse_args(["--chat", "14", "--sandbox-db", path, "--out-dir", out, *extra])

    cfg = ds.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["out_path"]).name == "detector_scan_chat14_000101_T1.json"
    for extra, expected in ((("--tag", "bad tag!"), "tag"),):
        try:
            ds.build_config(args(*extra), "000101")
            raise RuntimeError("expected rejection")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    try:
        ds.build_config(ds.parse_args(["--chat", "14", "--sandbox-db", "/nonexistent/x.db"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        ds.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejection assertions passed.")


if __name__ == "__main__":
    test_scan_finds_only_the_faulted_bar()
    test_degraded_bar_is_skipped_not_scanned()
    test_config_rejections()
    print("\nAll run_detector_scan tests passed.")
