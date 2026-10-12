# version: 261012
"""
tests/test_run_strengths_by_hour.py

Method: a synthetic dump is produced by run_strengths.process_bar on 30
synthetic hourly bars (8-pair FX topology; bar 5 has 60 bps injected on
GBP_CHF so exactly that bar is a fault), written to a temp JSONL, then
summarised by run_by_hour (hermetic; no DB, no real outputs). Checks bar
grouping, fault flag, hour totals, threshold counts, spread columns and
config rejections.

Run: python3 -m tests.test_run_strengths_by_hour
"""

import random
import tempfile
from pathlib import Path

import run_strengths as rs
import run_strengths_by_hour as bh
from currency_graph.strengths import Obs, SCHEMES

FX = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
LAT = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
T0 = 1_700_000_000 - (1_700_000_000 % 3600)     # on the hour, Nov 2023 (both winter)


def _world(n: int = 30, fault_bar: int = 5) -> dict:
    rng = random.Random(4)
    bars = {}
    for i in range(n):
        row = []
        for p in FX:
            a, b = p.split("_")
            y = LAT[a] - LAT[b] + rng.gauss(0, 1.0) + (60.0 if (i == fault_bar and p == "GBP_CHF") else 0.0)
            row.append(Obs(a, b, y, rng.uniform(0.5, 3.0), p))
        bars[T0 + 3600 * i] = row
    return bars


def _dump(bars: dict) -> str:
    path = tempfile.mktemp(suffix=".jsonl")
    ctx = {"nodes": sorted(LAT), "n_pairs": 8, "oracle": False, "ref": "USD", "user_weights": None}
    accs = {s: rs.SchemeAcc(s) for s in SCHEMES}
    with open(path, "w", encoding="utf-8") as f:
        for t in sorted(bars):
            for acc in accs.values():
                rs.process_bar(t, bars[t], acc, ctx, f)
    return path


def test_summary_on_synthetic_dump():
    bars = _world()
    cfg = {"dump_path": _dump(bars), "fault_bps": 10.0, "chat": 14}
    rep = bh.run_by_hour(cfg, bars)
    assert rep["n_bars"] == 30 and rep["n_fault_bars"] == 1 and rep["n_degraded_bars"] == 0
    assert sum(h["n"] for h in rep["by_hour"].values()) == 30
    fault_hour = str(((T0 + 3600 * 5) // 3600) % 24)
    assert rep["by_hour"][fault_hour]["n_fault"] == 1
    assert rep["fault_days_top"][0]["fault_bars"] == 1
    c = rep["bars_above_threshold_bps"]
    assert c["3"] >= c["5"] >= c["10"] >= c["20"] >= c["50"] >= 1, c
    assert set(rep["by_cell_hour"]) == {"both_winter"}
    assert rep["spread_columns"] and rep["spearman_dispersion_vs_max_diff_non_fault"].keys() == {"inv_spread", "inv_spread2"}
    assert rep["by_hour"][fault_hour]["fault_max_diff_inv_spread_bps"] is not None
    assert "UTC h" in bh.render_text(rep)
    print("by-hour summary assertions passed.")


def test_no_spread_mode_and_incomplete_bar_rejected():
    bars = _world()
    path = _dump(bars)
    rep = bh.run_by_hour({"dump_path": path, "fault_bps": 10.0, "chat": 14}, None)
    assert rep["spread_columns"] is False and all(v is None for v in rep["spearman_dispersion_vs_max_diff_non_fault"].values())
    lines = Path(path).read_text().splitlines()
    cut = tempfile.mktemp(suffix=".jsonl")
    Path(cut).write_text("\n".join(lines[:-1]) + "\n")     # last bar loses its inv_spread2 record
    try:
        bh.run_by_hour({"dump_path": cut, "fault_bps": 10.0, "chat": 14}, None)
        raise RuntimeError("expected rejection of an incomplete bar")
    except AssertionError as exc:
        assert "every scheme" in str(exc)
    print("no-spread / incomplete-bar assertions passed.")


def test_config_rejections():
    path = _dump(_world(6, 2))
    out = tempfile.mkdtemp()

    def args(*extra):
        return bh.parse_args(["--chat", "14", "--full-dump", path, "--no-spread", "--out-dir", out, *extra])

    cfg = bh.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["out_path"]).name == "strengths_by_hour_chat14_000101_T1.json" and cfg["sandbox_path"] is None
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--fault-bps", "0"), "fault-bps")):
        try:
            bh.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    try:
        bh.build_config(bh.parse_args(["--chat", "14", "--full-dump", "/nonexistent.jsonl"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        bh.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejection assertions passed.")


if __name__ == "__main__":
    test_summary_on_synthetic_dump()
    test_no_spread_mode_and_incomplete_bar_rejected()
    test_config_rejections()
    print("\nAll run_strengths_by_hour tests passed.")
