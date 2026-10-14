# version: 261013
"""
tests/test_run_inv4_attribution.py

Method: a temp SQLite DB (candles schema of data/store.py) holding a
synthetic 14-day hourly world on the 8-pair topology: consistent latent
currencies, small noise, 1.5 bps spreads, volume ~1000; ONE bar has USD_CHF
frozen-style faulted (+40 bps, volume 1). Hermetic: no real DB, no real
outputs. Checks the faulted bar is attributed to the CHF arm by plain least
squares, Huber and hub/arm, that A, B and C flag it, that a clean bar flags
nothing, that least squares carries a measurable smear, and config rejections.

Run: python3 -m tests.test_run_inv4_attribution
"""

import math
import random
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_inv4_attribution as inv4

LAT = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
FAULT_STAMP = "2024-01-14T21:00:00.000000000Z"
CLEAN_STAMP = "2024-01-14T20:00:00.000000000Z"
SCHEMA = """CREATE TABLE candles (instrument TEXT NOT NULL, granularity TEXT NOT NULL, timestamp TEXT NOT NULL,
 bid_open REAL, bid_high REAL, bid_low REAL, bid_close REAL, ask_open REAL, ask_high REAL, ask_low REAL, ask_close REAL, volume INTEGER,
 PRIMARY KEY (instrument, granularity, timestamp))"""


def _db() -> str:
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    rng = random.Random(7)
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for h in range(14 * 24):
        stamp = (t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        lat = {k: v + rng.gauss(0, 5.0) for k, v in LAT.items()}
        for p in inv4.PAIRS:
            a, b = p.split("_")
            y = lat[a] - lat[b] + rng.gauss(0, 0.2)
            vol = int(1000 * math.exp(rng.gauss(0, 0.3)))
            if stamp == FAULT_STAMP and p == "USD_CHF":
                y, vol = y + 40.0, 1
            mid = math.exp(y / 1e4)
            half = 0.5 * 1.5 / 1e4
            lo, hi = mid * (1 - 6e-4), mid * (1 + 6e-4)
            rows.append((p, "H1", stamp, mid, hi * (1 - half), lo * (1 - half), mid * (1 - half), mid, hi * (1 + half), lo * (1 + half), mid * (1 + half), vol))
    con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def test_faulted_bar_is_attributed_and_flagged_clean_bar_is_not():
    path = _db()
    rep = inv4.run_inv4({"chat": 14, "sandbox_path": path, "bars": (FAULT_STAMP, CLEAN_STAMP)})
    bad, clean = rep["bars"]
    u = bad["schemes"]["unweighted"]
    assert u["ls_loo_top"][0] == "arm:CHF", u["ls_loo_top"]
    assert u["huber"]["H1.0|spread"]["top"][0] == "arm:CHF" and u["huber"]["H1.0|mad"]["top"][0] == "arm:CHF"
    assert u["huber"]["H1.0|spread"]["ls_smear_bps"] > 2.0, "plain LS must carry a measurable smear on a 40 bps fault"
    assert bad["hub_arm"]["verdict"] == "arm:CHF" and len(bad["hub_arm"]["paths"]) == 4
    d = bad["detectors"]
    assert d["A"]["flag"] and d["B_mean_spread"]["flag"] and d["B_arm_spread"]["flag"] and d["B_hour_median"]["flag"] is True
    assert d["C"]["stale"] == ["USD_CHF"] and d["hour_median_used"] is not None
    cd = clean["detectors"]
    assert clean["hub_arm"]["verdict"] == "clean" and not cd["A"]["flag"] and not cd["B_mean_spread"]["flag"] and not cd["C"]["flag"]
    print("faulted-bar attribution / detectors, clean-bar silence, LS smear assertions passed.")


def test_trailing_median_excludes_the_bar_itself_and_needs_history():
    path = _db()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert inv4.trailing_hour_median(con, "2024-01-02T05:00:00.000000000Z") is None, "one prior day is not enough history"
        m = inv4.trailing_hour_median(con, FAULT_STAMP)
        assert m is not None and 1.0 < m < 2.0, m
    finally:
        con.close()
    print("trailing-median history assertions passed.")


def test_config_rejections():
    path = _db()
    out = tempfile.mkdtemp()

    def args(*extra):
        return inv4.parse_args(["--chat", "14", "--sandbox-db", path, "--out-dir", out, *extra])

    cfg = inv4.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["out_path"]).name == "inv4_attribution_chat14_000101_T1.json" and len(cfg["bars"]) == 17
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--bars", "2024-01-01 00:00"), "stored-format")):
        try:
            inv4.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    try:
        inv4.build_config(inv4.parse_args(["--chat", "14", "--sandbox-db", "/nonexistent/x.db"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        inv4.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejection assertions passed.")


if __name__ == "__main__":
    test_faulted_bar_is_attributed_and_flagged_clean_bar_is_not()
    test_trailing_median_excludes_the_bar_itself_and_needs_history()
    test_config_rejections()
    print("\nAll run_inv4_attribution tests passed.")
