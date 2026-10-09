# version: 261008
"""
tests/test_residual_investigations.py

Method: tiny synthetic FX worlds (one triangle EUR_USD / EUR_GBP / GBP_USD on a
random walk, so any residual is injected) in temp SQLite files; hermetic. Each
investigation is checked against a world where the answer is KNOWN: noise
inversely proportional to volume (INV-2 must find a strongly negative rank
correlation; a volume-independent control must not); noise proportional to
range; noise at a New-York-anchored vs a London-anchored hour (INV-3 must
classify the mismatch cell correctly in each); a constant injected bias
(INV-5's CI must exclude 0; a no-bias control's must contain it). The
designation table is checked against an INDEPENDENT hand computation from raw
mids. Pure helpers (bootstrap, deciles, decimals, floor) have direct tests.

Run: python3 -m tests.test_residual_investigations
"""

import math
import random
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import run_residual_investigations as ri
from data.store import Candle, FxStore
from time_policy import epoch_to_stored_ts

UTC = timezone.utc
HOUR = 3600
PAIRS = ["EUR_USD", "EUR_GBP", "GBP_USD"]
INSTRUMENTS = [(p, "H1", None, 6048) for p in PAIRS]
LATENT0 = {"USD": 0.0, "GBP": math.log(1.27), "EUR": math.log(1.10)}
HALF = 0.00005


def _ep(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp())


def make_world(start: int, n_hours: int, sd_fn, seed: int = 3, bias_bps: float = 0.0) -> str:
    """sd_fn(epoch, volume, range_bps) -> noise std (bps) added to EUR_USD's mid.
    Every leg shares the bar's volume and range so those columns are the only
    thing the investigation can latch onto."""
    rng = random.Random(seed)
    state = dict(LATENT0)
    prev_mid = {}
    candles = []
    for k in range(n_hours):
        t = start + k * HOUR
        state = {c: v + rng.gauss(0, 0.0003) for c, v in state.items()}
        volume = rng.randint(10, 500)
        range_bps = rng.uniform(2.0, 20.0)
        noise = rng.gauss(0, sd_fn(t, volume, range_bps)) + bias_bps
        for pair in PAIRS:
            b, q = pair.split("_")
            mid = math.exp(state[b] - state[q]) * (math.exp(noise / 1e4) if pair == "EUR_USD" else 1.0)
            o = prev_mid.get(pair, mid)
            hi, lo = max(o, mid) * math.exp(range_bps / 2e4), min(o, mid) * math.exp(-range_bps / 2e4)
            prev_mid[pair] = mid
            candles.append(Candle(
                instrument=pair, granularity="H1", timestamp=epoch_to_stored_ts(t),
                bid_open=o * (1 - HALF), bid_high=hi * (1 - HALF), bid_low=lo * (1 - HALF), bid_close=mid * (1 - HALF),
                ask_open=o * (1 + HALF), ask_high=hi * (1 + HALF), ask_low=lo * (1 + HALF), ask_close=mid * (1 + HALF),
                volume=volume))
    path = tempfile.mktemp(suffix=".db")
    FxStore(database_url=f"sqlite:///{path}").upsert_candles(candles)
    return path


def _report(path: str, n_boot: int = 200) -> dict:
    cfg = {"sandbox_path": path, "chat": 14, "n_boot": n_boot, "seed": 42}
    return ri.run_investigations(cfg, INSTRUMENTS)


def _col(report: dict, name: str, key: str = "spearman_vs_abs_resid"):
    return report["triangles"][0]["inv2"]["columns"][name][key]


def test_triangle_discovery_and_labels():
    path = make_world(_ep(2024, 1, 1), 300, lambda t, v, r: 1.0)
    rep = _report(path)
    assert len(rep["triangles"]) == 1 and rep["triangles"][0]["n"] == 300
    labels = rep["triangles"][0]["cycle"].split()
    assert sorted(x[1:] for x in labels) == sorted(PAIRS), labels
    print("triangle discovery assertions passed.")


def test_inv2_finds_thin_bar_effect_and_control_does_not():
    start = _ep(2023, 1, 2)
    thin = _report(make_world(start, 4000, lambda t, v, r: 0.2 + 3000.0 / v))
    assert _col(thin, "volume_min") < -0.5, _col(thin, "volume_min")
    control = _report(make_world(start, 4000, lambda t, v, r: 1.0))
    assert abs(_col(control, "volume_min")) < 0.1, _col(control, "volume_min")
    for name in ri.VARIANTS:
        assert abs(_col(control, name)) < 0.1, (name, _col(control, name))
    rng_world = _report(make_world(start, 4000, lambda t, v, r: r ** 3 / 100))
    assert _col(rng_world, "R2_max") > 0.5 and _col(rng_world, "R3_max") > 0.5, "range-proportional noise must show up in range columns"
    assert _col(rng_world, "volume_min") is not None and abs(_col(rng_world, "volume_min")) < 0.1
    print(f"INV-2: thin-bar rho {_col(thin, 'volume_min'):+.2f}, control {_col(control, 'volume_min'):+.2f}, "
          f"range world R2_max rho {_col(rng_world, 'R2_max'):+.2f}")


def test_parkinson_max_is_rank_identical_to_r2_max_but_sum_is_not():
    rep = _report(make_world(_ep(2023, 1, 2), 2500, lambda t, v, r: r ** 3 / 100))
    cols = rep["triangles"][0]["inv2"]["columns"]
    assert cols["R6_max"]["spearman_vs_abs_resid"] == cols["R2_max"]["spearman_vs_abs_resid"]
    assert cols["R6_max"]["decile_p99_abs_resid"] == cols["R2_max"]["decile_p99_abs_resid"]
    assert cols["R6_sum"]["spearman_vs_abs_resid"] != cols["R2_sum"]["spearman_vs_abs_resid"]
    print("R6_max == R2_max (rank identity) and R6_sum != R2_sum assertions passed.")


def test_range_definitions_on_hand_computed_bar():
    bars = {100: (1.0, 1.1, 0.9, 1.05, 1.0, 1.1, 0.9, 1.05, 7), 200: (1.05, 1.2, 1.0, 1.1, 1.05, 1.2, 1.0, 1.1, None)}
    rows = ri.derive_leg_rows(bars)
    r2 = math.log(1.1 / 0.9) * 1e4
    assert abs(rows[100][0] - r2) < 1e-9 and abs(rows[100][1] - r2) < 1e-9, "zero spread: R3 == R2"
    assert abs(rows[100][2] - abs(math.log(1.05 / 1.0)) * 1e4) < 1e-9
    assert abs(rows[100][3] - r2) < 1e-9, "first bar has no previous close: R5 falls back to R2"
    assert abs(rows[100][4] - r2 ** 2 / (4 * math.log(2))) < 1e-6
    assert abs(rows[200][3] - math.log(1.2 / 1.0) * 1e4) < 1e-9, "prev close 1.05 sits inside [1.0, 1.2]"
    gap = ri.derive_leg_rows({1: (1, 1, 1, 1, 1, 1, 1, 1, 1), 2: (2, 2.1, 2, 2.05, 2, 2.1, 2, 2.05, 1)})
    assert abs(gap[2][3] - math.log(2.1 / 1.0) * 1e4) < 1e-9, "true range must include the gap from the previous close"
    assert rows[200][5] is None and rows[100][5] == 7
    print("range-definition hand-computation assertions passed.")


def test_inv3_classifies_ny_and_london_anchoring_correctly():
    start, n = _ep(2024, 2, 20), 60 * 24
    ny = _report(make_world(start, n, lambda t, v, r: 25.0 if ((t // HOUR) % 24 == (21 if ri.dst_cell(t) in ("both_summer", "uk_winter_us_summer") else 22)) else 0.3))
    v = ny["triangles"][0]["inv3"]["mismatch_test"]
    assert (v["winter_peak_hour"], v["summer_peak_hour"]) == (22, 21), v
    assert v["verdict"].startswith("US-anchored"), v
    uk = _report(make_world(start, n, lambda t, v_, r: 25.0 if ((t // HOUR) % 24 == (16 if ri.dst_cell(t) == "both_summer" else 17)) else 0.3))
    v = uk["triangles"][0]["inv3"]["mismatch_test"]
    assert (v["winter_peak_hour"], v["summer_peak_hour"]) == (17, 16), v
    assert v["verdict"].startswith("UK-anchored"), v
    flat = _report(make_world(start, n, lambda t, v_, r: 25.0 if (t // HOUR) % 24 == 12 else 0.3))
    assert "cannot separate" in flat["triangles"][0]["inv3"]["mismatch_test"]["verdict"]
    cells = ny["triangles"][0]["inv3"]["cell_summary"]
    assert "uk_summer_us_winter" not in cells, "UK-summer/US-winter never occurs"
    print("INV-3 NY / UK / no-shift classification assertions passed.")


def test_dst_cells_at_known_dates():
    assert ri.dst_cell(_ep(2024, 3, 5, 12)) == "both_winter"
    assert ri.dst_cell(_ep(2024, 3, 20, 12)) == "uk_winter_us_summer", "US switched 10 Mar, UK on 31 Mar"
    assert ri.dst_cell(_ep(2024, 4, 5, 12)) == "both_summer"
    assert ri.dst_cell(_ep(2024, 10, 30, 12)) == "uk_winter_us_summer", "UK switched 27 Oct, US on 3 Nov"
    assert ri.dst_cell(_ep(2024, 11, 10, 12)) == "both_winter"
    print("DST-cell date assertions passed.")


def test_inv5_bias_detected_and_control_contains_zero():
    start = _ep(2023, 1, 2)
    biased = _report(make_world(start, 3000, lambda t, v, r: 1.0, bias_bps=3.0), n_boot=300)
    s = biased["triangles"][0]["inv5"]["signed_residual_in_walk_orientation"]
    assert not s["mean_ci_contains_0"] and not s["median_ci_contains_0"], s
    control = _report(make_world(start, 3000, lambda t, v, r: 1.0, bias_bps=0.0), n_boot=300)
    c = control["triangles"][0]["inv5"]["signed_residual_in_walk_orientation"]
    assert c["mean_ci_contains_0"], c
    print(f"INV-5: biased mean {s['mean_bps']:+.2f} (CI excludes 0); control mean {c['mean_bps']:+.3f} (CI contains 0).")


def test_designation_table_matches_independent_hand_computation():
    path = make_world(_ep(2023, 1, 2), 1500, lambda t, v, r: 2.0, bias_bps=1.5)
    rep = _report(path, n_boot=50)
    table = {row["direct"]: row["mean_bps"] for row in rep["triangles"][0]["inv5"]["designation_table"]}
    con = __import__("sqlite3").connect(path)
    mids = {}
    for p in PAIRS:
        for ts, bid, ask in con.execute("SELECT timestamp, bid_close, ask_close FROM candles WHERE instrument=?", (p,)):
            mids.setdefault(ts, {})[p] = math.log((bid + ask) / 2)
    con.close()
    rows = [m for m in mids.values() if len(m) == 3]
    expected = {
        "EUR_USD": [(m["EUR_GBP"] + m["GBP_USD"]) - m["EUR_USD"] for m in rows],
        "EUR_GBP": [(m["EUR_USD"] - m["GBP_USD"]) - m["EUR_GBP"] for m in rows],
        "GBP_USD": [(m["EUR_USD"] - m["EUR_GBP"]) - m["GBP_USD"] for m in rows],
    }
    for name, vals in expected.items():
        want = statistics.fmean(vals) * 1e4
        assert abs(table[name] - want) < 1e-9, (name, table[name], want)
    print("designation table matches independent computation (all three designations).")


def test_consistent_sign_combination_counter():
    def tri(*means):
        return {"designation_table": [{"mean_bps": m} for m in means]}
    assert ri.consistent_sign_combinations([tri(1, 1, 1), tri(1, 1, 1), tri(1, 1, 1)]) == {"combinations": 27, "all_same_sign": 27}
    assert ri.consistent_sign_combinations([tri(1, 1, 1), tri(-1, -1, -1), tri(1, 1, 1)])["all_same_sign"] == 0
    assert ri.consistent_sign_combinations([tri(1, -1, 1), tri(1, -1, 1), tri(1, -1, 1)])["all_same_sign"] == 2 * 2 * 2 + 1 * 1 * 1
    print("sign-combination counter assertions passed.")


def test_pure_helpers():
    boot = ri.day_block_bootstrap([5.0] * 40, ["d%d" % (i // 4) for i in range(40)], 50, 1)
    assert set(boot["mean"]) == {5.0} and set(boot["median"]) == {5.0}
    again = ri.day_block_bootstrap([float(i) for i in range(40)], ["d%d" % (i // 4) for i in range(40)], 50, 7)
    assert again == ri.day_block_bootstrap([float(i) for i in range(40)], ["d%d" % (i // 4) for i in range(40)], 50, 7), "seeded"
    assert ri.observed_decimals([1.23456, 1.1]) == 5 and ri.observed_decimals([0.5, 1.25]) == 2 and ri.observed_decimals([3.0]) == 0
    assert ri.observed_decimals([1 / 3]) is None
    floor = ri.floor_std_bps([5, 5, 5], [1.0, 1.0, 1.0])
    assert abs(floor - 1e4 * math.sqrt(3 * (1e-5) ** 2 / 24)) < 1e-12
    groups = ri.decile_groups(list(range(100)))
    assert [len(g) for g in groups] == [10] * 10 and groups[0] == list(range(10)) and groups[9][-1] == 99
    w = ri.winsorised_mean([0.0] * 98 + [1000.0, -1000.0])
    assert abs(w) < 20, "winsorising must clip the two extremes"
    for bad in (lambda: ri.day_block_bootstrap([], [], 5, 1), lambda: ri.day_block_bootstrap([1.0], ["d"], 0, 1)):
        try:
            bad()
            raise RuntimeError("expected a rejection")
        except AssertionError:
            pass
    print("pure-helper assertions passed.")


def test_config_rejections_and_output_name():
    path = make_world(_ep(2024, 1, 1), 300, lambda t, v, r: 1.0)
    out = tempfile.mkdtemp()

    def args(*extra):
        return ri.parse_args(["--chat", "14", "--sandbox-db", path, "--out-dir", out, *extra])

    cfg = ri.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["out_path"]).name == "residual_inv_240101to240113_chat14_000101_T1.json", cfg["out_path"]
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--bootstrap", "0"), "bootstrap"), (("--bootstrap", "999999"), "bootstrap")):
        try:
            ri.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    try:
        ri.build_config(ri.parse_args(["--chat", "14", "--sandbox-db", "/nonexistent/x.db"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        ri.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config / output-name assertions passed.")


def test_render_text_and_json_roundtrip():
    import json
    rep = _report(make_world(_ep(2024, 2, 20), 1200, lambda t, v, r: 1.0), n_boot=30)
    text = ri.render_text(rep)
    assert "INV-2 volume" in text and "INV-3" in text and "INV-5" in text and "LIMITATIONS" in text
    assert json.loads(json.dumps(rep))["tool"] == "run_residual_investigations"
    print("render / JSON assertions passed.")


if __name__ == "__main__":
    test_triangle_discovery_and_labels()
    test_inv2_finds_thin_bar_effect_and_control_does_not()
    test_parkinson_max_is_rank_identical_to_r2_max_but_sum_is_not()
    test_range_definitions_on_hand_computed_bar()
    test_inv3_classifies_ny_and_london_anchoring_correctly()
    test_dst_cells_at_known_dates()
    test_inv5_bias_detected_and_control_contains_zero()
    test_designation_table_matches_independent_hand_computation()
    test_consistent_sign_combination_counter()
    test_pure_helpers()
    test_config_rejections_and_output_name()
    test_render_text_and_json_roundtrip()
    print("\nAll residual_investigations tests passed.")
