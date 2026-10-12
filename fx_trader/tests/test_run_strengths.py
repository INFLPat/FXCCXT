# version: 261011
"""
tests/test_run_strengths.py

Method: the runner's bar processing is driven with hand-built synthetic bars
(no DB, no real outputs; hermetic): five bars on the 8-pair FX topology -
three full, one missing USD_CAD (CAD isolated), one missing GBP_USD (still
connected). Checks the full/degraded split, fast-vs-oracle agreement, the
bridge-None behaviour, the JSONL record shape, summary shape and config
rejections.

Run: python3 -m tests.test_run_strengths
"""

import io
import json
import random
import tempfile
from pathlib import Path

import run_strengths as rs
from currency_graph.strengths import Obs, SCHEMES

FX = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
LAT = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
NODES = sorted(LAT)


def _bars() -> dict:
    rng = random.Random(9)
    drop = {3: {"USD_CAD"}, 4: {"GBP_USD"}}
    bars = {}
    for t in range(5):
        row = []
        for p in FX:
            if p in drop.get(t, set()):
                continue
            a, b = p.split("_")
            row.append(Obs(a, b, LAT[a] - LAT[b] + rng.gauss(0, 1.0), rng.uniform(0.5, 3.0), p))
        bars[t] = row
    return bars


def _ctx() -> dict:
    return {"nodes": NODES, "n_pairs": 8, "oracle": True, "ref": "USD", "user_weights": {"USD": 1.0}}


def test_pipeline_full_vs_degraded_and_oracle_agreement():
    bars = _bars()
    for scheme in SCHEMES:
        acc, buf = rs.SchemeAcc(scheme), io.StringIO()
        for t in sorted(bars):
            rs.process_bar(t, bars[t], acc, _ctx(), buf)
        assert acc.n_bars[rs.GROUP_FULL] == 3 and acc.n_bars[rs.GROUP_DEG] == 2, acc.n_bars
        assert acc.oracle_none_mismatch == 0 and acc.oracle_max_diff < 1e-9, (scheme, acc.oracle_max_diff)
        assert acc.loo_none[(rs.GROUP_FULL, "USD_CAD")] == 3, "USD_CAD is a bridge on every full bar"
        s = rs.scheme_summary(acc)
        assert s["bars"] == {"total": 5, "B_full": 3, "D_degraded": 2}
        assert s["fast_vs_oracle"]["within_1e-9"] is True
        assert s["loo_abs_bps"]["D_all"]["GBP_JPY"]["n"] == 5 and s["loo_abs_bps"]["B_full"]["USD_CAD"]["n"] == 0
        assert s["loco_max_abs_shift_bps"]["D_all"]["USD"]["none_n"] == 4, "dropping USD isolates CAD on every bar that fits CAD (all but bar 3)"
        lines = [json.loads(x) for x in buf.getvalue().splitlines()]
        assert len(lines) == 5
        assert "CAD" not in lines[3]["nodes"], "bar 3 misses USD_CAD: CAD must be outside the fitted component"
        assert set(lines[0]["views"]) >= {"zero_sum", "vs_USD", "basket_equal", "basket_inv_spread", "basket_user"}
    print("pipeline assertions passed (full/degraded split, oracle agreement, bridge None, JSONL shape).")


def test_scheme_comparison_and_top_events():
    bars = _bars()
    accs = {s: rs.SchemeAcc(s) for s in SCHEMES}
    for t in sorted(bars):
        for acc in accs.values():
            rs.process_bar(t, bars[t], acc, _ctx(), None)
    cmp_ = rs.scheme_comparison(accs)
    assert set(cmp_) == {"inv_spread_vs_unweighted_abs_bps", "inv_spread2_vs_unweighted_abs_bps"}
    assert cmp_["inv_spread_vs_unweighted_abs_bps"]["USD"]["n"] == 3
    top = rs.scheme_summary(accs["unweighted"])["top_loo_events"]
    assert 0 < len(top) <= rs.TOP_K and top == sorted(top, key=lambda e: -e["abs_loo_bps"])
    print("scheme-comparison / top-events assertions passed.")


def test_build_bars_rejects_zero_spread_and_orders_pairs():
    ok = rs.build_bars({"EUR_USD": {1: (1.0999, 1.1001)}, "GBP_USD": {1: (1.2699, 1.2701)}})
    assert [o.name for o in ok[1]] == ["EUR_USD", "GBP_USD"]
    try:
        rs.build_bars({"EUR_USD": {1: (1.1, 1.1)}})
        raise RuntimeError("expected a rejection of a zero-spread bar")
    except ValueError as exc:
        assert "zero spread" in str(exc)
    print("build_bars assertions passed.")


def test_parse_user_weights_and_config_rejections():
    assert rs.parse_user_weights("USD=0.4,EUR=0.6") == {"USD": 0.4, "EUR": 0.6} and rs.parse_user_weights(None) is None
    for bad in ("USD", "USD=-1", "USD=0"):
        try:
            rs.parse_user_weights(bad)
            raise RuntimeError(f"expected rejection of {bad!r}")
        except AssertionError:
            pass
    out = tempfile.mkdtemp()

    def args(*extra):
        return rs.parse_args(["--chat", "14", "--out-dir", out, *extra])

    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--stride", "0"), "stride"),
                            (("--sandbox-db", "/nonexistent/x.db"), "not found")):
        try:
            rs.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    print("user-weights / config rejection assertions passed.")


if __name__ == "__main__":
    test_pipeline_full_vs_degraded_and_oracle_agreement()
    test_scheme_comparison_and_top_events()
    test_build_bars_rejects_zero_spread_and_orders_pairs()
    test_parse_user_weights_and_config_rejections()
    print("\nAll run_strengths tests passed.")
