# version: 261016
"""
tests/test_run_world_experiment.py

Method: reuses the temp 14-day synthetic DB of test_run_inv4_attribution; the a1-style bar dump is
produced by run_calibration_stats on it (hermetic: temp files only, no real outputs). Checks:
apply_fault equals run_fault_experiment.inject under the same rng; the parametric clean|flat rates
reproduce run_fault_experiment's first cell EXACTLY (ModelSpreads); the empirical reconstruction
reproduces the dump's max|LOO| to 2e-3 bps (the 4 dp rounding); two-fault path-sign relation
(add = 2E, cancel = 0 on the arm path); histogram/accumulator and block-run hand values; a tiny
scenario run is deterministic, flags a 100 bps stale fault always and keeps the Tier 2 JSONL under
its cap; the calibrated world produces analysable bars; config rejections.

Run: python3 -m tests.test_run_world_experiment
"""

import json
import math
import random
import sqlite3
import tempfile
from pathlib import Path

import run_calibration_stats as cs
import run_fault_experiment as fe
import run_world_experiment as w
from tests.test_run_inv4_attribution import _db

_CACHE = {}


def _dump() -> str:
    if "p" not in _CACHE:
        con = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
        try:
            bars, typ = cs.load_all(con)
        finally:
            con.close()
        rows, skipped = cs.compute_rows(bars, typ)
        cs.build_report({"chat": 14, "sandbox_path": "x.db", "mask_pad": 2, "mask_min_run": 1}, rows, skipped)
        path = str(Path(tempfile.mkdtemp()) / "bars.jsonl")
        cs.dump_bars(path, rows)
        _CACHE["p"], _CACHE["rows"] = path, rows
    return _CACHE["p"]


def _rows() -> list:
    rows = w.load_dump(_dump())
    w.setup_ctx(rows, 10, 7)
    return rows


def _cfg(**over) -> dict:
    cfg = {"seed": 3, "stride": {"core": 12, "family": 12, "double": 12}, "sim_trials": 20, "family_sim": 10, "adj_trials": 10,
           "blocks_per_length": 50, "boot": 10, "cal_sims": 60}
    return cfg | over


def test_apply_fault_equals_fe_inject():
    rows = _rows()
    bar = w.emp_bar(rows[40], random.Random(1))
    for kind, mag, f in (("stale", 40.0, w.fault_of("stale", {"E": 40.0, "sig": "s1"})), ("wide", 10.0, w.fault_of("wide", {"m": 10.0, "ratio": 0.5}))):
        r1, r2 = random.Random(9), random.Random(9)
        o2, v2, g2 = fe.inject(r1, bar["obs"], bar["vol"], bar["rng"], kind, mag, 6)
        o, v, g = list(bar["obs"]), list(bar["vol"]), list(bar["rng"])
        w.apply_fault(r2, o, v, g, f, 6, None)
        assert o == o2 and v == v2 and g == g2, kind
    print("apply_fault == fe.inject (stale, wide) assertion passed.")


def test_parametric_clean_flat_reproduces_fault_experiment_exactly():
    _rows()
    mine = w.parametric_clean_rates(fe.ModelSpreads(), 5, 40, "flat")
    ref = fe.run_experiment({"chat": 14, "seed": 5, "trials": 40}, fe.ModelSpreads(), cells=[("clean", 0.0, "flat")])
    rates = ref["cells"]["clean:0|flat"]["detector_flag_rate"]
    assert {k: mine[k] for k in mine} == {k: rates[k] for k in mine}, (mine, rates)
    print("parametric clean|flat rates identical to run_fault_experiment's first cell:", mine)


def test_empirical_reconstruction_matches_dump():
    rows = _rows()
    worst = 0.0
    for r in rows[::7]:
        st = w.analyse(w.emp_bar(r, random.Random(2)))
        worst = max(worst, abs(st["A"] - r["max_abs"]))
        for k in w.B_KINDS:
            if r["b"][k] is not None and st["B"][k] is not None:
                assert abs(st["B"][k] - r["b"][k]) < 5e-3 * max(1.0, r["b"][k]), (k, st["B"][k], r["b"][k])
    assert worst < 2e-3, worst
    print(f"empirical reconstruction max |A - dump max_abs| = {worst:.5f} bps (< 2e-3).")


def test_double_fault_path_sign_relation():
    rows = _rows()
    for place, want in (("same_arm_add", 20.0), ("same_arm_cancel", 0.0)):
        for seed in range(20):
            bar = w.emp_bar(rows[10], random.Random(0))
            base = {o.name: o.y for o in bar["obs"]}
            spec = {"type": "double", "place": place, "a": w.fault_of("stale", {"E": 10.0, "sig": "s1"}), "b": w.fault_of("stale", {"E": 10.0, "sig": "s1"})}
            nb, faults, tg, ex = w.inject_spec(random.Random(seed), bar, spec)
            moved = [o.name for o, b0 in zip(nb["obs"], base.values()) if abs(o.y - base[o.name]) > 1e-9]
            assert len(moved) == 2 and len(tg) == 1 and len(ex) == 1, (moved, tg)
            x = next(iter(tg)).split(":")[1]
            i1, s1, i2, s2 = w.HA8.arms[x]
            err = s1 * (nb["obs"][i1].y - base[w.PAIRS[i1]]) + s2 * (nb["obs"][i2].y - base[w.PAIRS[i2]])
            assert abs(abs(err) - want) < 1e-9, (place, err)
    nb, _f, tg, _ex = w.inject_spec(random.Random(1), w.emp_bar(rows[10], random.Random(0)), {"type": "double", "place": "hub_arm", "a": w.fault_of("stale", {"E": 5.0, "sig": "s1"}), "b": w.fault_of("stale", {"E": 5.0, "sig": "s1"})})
    assert "hub" in tg and len(tg) == 2
    print("two-fault path-sign relation assertion passed (add 2E, cancel 0, hub+arm).")


def test_hist_bins_and_accumulator_hand_values():
    assert w.hbin(1e-9) == 0 and w.hbin(1e-3) == 0 and w.hbin(1e9) == w.HBINS - 1 and w.hbin(1.0) == w.HBINS // 2
    acc = w.Acc(hist=True)
    st = {"A": 12.0, "B": {k: 1.2 for k in w.B_KINDS}, "C": {ck: (True, False, False) for ck, _s, _f in w.CKEYS}}
    acc.add(st, (1, 0), ("hit", "raw:arm:CHF"))
    acc.add({**st, "A": 1.0, "B": {k: None for k in w.B_KINDS}}, None, None)
    out = acc.out()
    assert out["n"] == 2 and out["A"][w.A_GRID.index(10.0)] == 1 and out["Bn"]["B_mean_spread" if False else w.B_KINDS[0]] == 1
    assert out["att"] == [1, 1, 0] and out["verdict"] == {"hit": 1, "raw:arm:CHF": 1}
    assert out["C"][w.C_DEFAULT_KEY] == [2, 2, 0, 0] and sum(c for _b, c in out["hist"]["A"]) == 2
    print("histogram / accumulator hand-value assertions passed.")


def test_block_helpers():
    flags = [[False] * 5 for _ in range(12)]
    for j in (2, 3, 4, 8):
        flags[j][0] = True
    assert w.count_runs(flags, 0, 12, 0) == 2 and w.count_runs(flags, 0, 5, 0) == 1 and w.count_runs(flags, 5, 3, 0) == 0
    flags[6][0] = True                      # gap 2 between 4 and 6, then 6 -> 8: all joined (gap <= RUN_GAP)
    assert w.count_runs(flags, 0, 12, 0) == 1
    rows = [{"epoch": i * 3600 + (7200 if i >= 6 else 0), "masked": i == 3} for i in range(10)]
    assert w.segments(rows, "all") == [(0, 6), (6, 4)], w.segments(rows, "all")
    assert w.segments(rows, "ref") == [(0, 3), (4, 2), (6, 4)], w.segments(rows, "ref")
    print("block run / segment hand-value assertions passed.")


def test_scenario_run_is_deterministic_and_flags_big_stale_fault():
    rows = _rows()
    world, cfg = w.EmpWorld(rows), _cfg()
    out = tempfile.mkdtemp()
    sc = dict(w.core_scenarios())
    results = []
    for _ in range(2):
        t2 = w.T2Writer(str(Path(out) / f"t{len(results)}.jsonl"), 60_000)
        results.append(w.run_scenario(world, "stale100s1", sc["stale100s1"], "core", cfg, t2, 2))
        t2.close()
    assert json.dumps(results[0], sort_keys=True) == json.dumps(results[1], sort_keys=True), "same seed must reproduce"
    cell = results[0]
    allg = cell["groups"]["all"]
    assert allg["A"][w.A_GRID.index(10.0)] == allg["n"] > 0, "a 100 bps stale fault must always exceed 10 bps"
    assert allg["att"][0] == allg["n"] and cell["ci"]["A"]["method"] == "cluster_bootstrap_ref_stretches"
    t2c = w.T2Writer(str(Path(out) / "c.jsonl"), 1000)
    clean = w.run_scenario(world, "clean", sc["clean"], "core", cfg, t2c, 0)
    t2c.close()
    assert clean["groups"]["all"]["att"] == [0, 0, 0] and clean["groups"]["all"]["A"][w.A_GRID.index(10.0)] <= 1
    lines = Path(out, "t0.jsonl").read_text().splitlines()
    assert 0 < len(lines) <= 8 and len(json.loads(lines[0])["err"]) == len(w.METHODS)
    t2 = w.T2Writer(str(Path(out) / "cap.jsonl"), 100)
    assert t2.write({"x": "y" * 50}) and not t2.write({"x": "y" * 60}) and t2.dropped == 1
    t2.close()
    print("scenario determinism / detection / Tier 2 cap assertions passed.")


def test_double_adjacent_and_blocks_run_on_synthetic_dump():
    rows = _rows()
    world, cfg = w.EmpWorld(rows), _cfg(adj_trials=8)
    sid, spec = w.double_scenarios()[0]
    t2d = w.T2Writer(str(Path(tempfile.mkdtemp()) / "d.jsonl"), 100_000)
    cell = w.run_scenario(world, sid, spec, "double", cfg, t2d, 1)
    t2d.close()
    assert cell["groups"]["all"]["att"][0] == cell["n"] and json.loads(json.dumps(cell))
    asid, aspec = w.adj_scenarios()[0]
    adj = w.run_adjacent(world, asid, aspec, cfg)
    assert adj["n"] == 8 and all(v[0] == 8 for v in adj["detectors"].values())
    flags = w.clean_flags(rows, 3)
    b = w.block_stats(rows, flags, "ref", cfg)
    assert b["by_length"]["1"]["n_blocks"] == 50 and set(b["by_length"]["1"]["runs_per_block"]) == set(w.DET_NAMES)
    print("double / adjacent / block-stats smoke assertions passed.")


def test_calibrated_world_bars_are_analysable():
    rows = _rows()
    ref = [r for r in rows if not r["masked"]]
    meta = {}
    cfg = _cfg(worlds=["cal_t_prop", "cal_mix_abs"])
    worlds = w.build_worlds(cfg, rows, meta)
    assert [x.name for x in worlds] == ["cal_t_prop", "cal_mix_abs"] and "__all__" in meta["calibration"]
    for world in worlds:
        rng = random.Random(4)
        for i in range(25):
            bar = world.bar(rng, i, "core", cfg)
            st = w.analyse(bar)
            assert len(bar["obs"]) == 8 and st["A"] >= 0 and 0 <= bar["dec"] <= 9
    tbl = meta["calibration"]["__all__"]["table"]
    assert {e["family"] for e in tbl} == {"t", "mix"} and {e["variant"] for e in tbl} == {"prop", "abs"} and len(tbl) == 2 * (len(w.DF_GRID) + len(w.MIX_GRID))
    assert len(ref) > 0
    print("calibrated-world bars analysable; full calibration table stored.")


def test_config_rejections_and_output_names():
    path = _dump()
    out = tempfile.mkdtemp()

    def args(*extra):
        return w.parse_args(["--chat", "14", "--bars", path, "--out-dir", out, "--worlds", "empirical,cal_t_prop", *extra])

    cfg = w.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["out_paths"]["meta"]).name == "world_experiment_chat14_000101_T1_meta.json" and Path(cfg["out_paths"]["empirical"]).name.endswith("_empirical.json")
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--stride", "0"), "stride"), (("--sets", "nonsense"), "sets"), (("--jsonl-cap-mb", "0"), "jsonl-cap-mb")):
        try:
            w.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    try:
        w.build_config(w.parse_args(["--chat", "14", "--bars", path, "--worlds", "parametric_db", "--sandbox-db", "/nonexistent/x.db"]), "000101")
        raise RuntimeError("expected rejection")
    except AssertionError as exc:
        assert "not found" in str(exc)
    Path(cfg["out_paths"]["meta"]).write_text("{}")
    try:
        w.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejection / output-name assertions passed.")


if __name__ == "__main__":
    test_apply_fault_equals_fe_inject()
    test_parametric_clean_flat_reproduces_fault_experiment_exactly()
    test_empirical_reconstruction_matches_dump()
    test_double_fault_path_sign_relation()
    test_hist_bins_and_accumulator_hand_values()
    test_block_helpers()
    test_scenario_run_is_deterministic_and_flags_big_stale_fault()
    test_double_adjacent_and_blocks_run_on_synthetic_dump()
    test_calibrated_world_bars_are_analysable()
    test_config_rejections_and_output_names()
    print("\nAll run_world_experiment tests passed.")
