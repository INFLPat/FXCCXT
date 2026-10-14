# version: 261013
"""
tests/test_fault_experiment.py

Method: the model-spread world only (hermetic; no DB, no real outputs, temp
dirs for config checks). Checks: method grid size; inject() semantics for
stale / wide / missing; innocent_error is gauge-free (a common shift of the
innocents is invisible); same seed gives an identical report; a stale 100 bps
fault is attributed far better by Huber and by hub/arm than by plain least
squares is NOT assumed - only that every rate is a valid probability and the
clean cell has no attribution; sanity: error rises with fault size for plain
least squares; config rejections.

Run: python3 -m tests.test_fault_experiment
"""

import json
import random
import tempfile
from pathlib import Path

import run_fault_experiment as fe

CFG = {"chat": 14, "seed": 5, "trials": 12}


def test_method_grid_and_inject_semantics():
    assert len(fe.method_specs()) == 3 * (1 + 4 * 2) and fe.method_specs()[0][0] == "unweighted|LS"
    rng = random.Random(1)
    obs, vol, rg, _lat = fe.make_bar(rng, fe.ModelSpreads(), "flat")
    i = fe.PAIRS.index("USD_CHF")
    o2, v2, r2 = fe.inject(rng, obs, vol, rg, "stale", 40.0, i)
    assert abs(abs(o2[i].y - obs[i].y) - 40.0) < 1e-9 and o2[i].s == obs[i].s and v2[i] == 1 and r2[i] == 0.0
    o3, v3, _ = fe.inject(rng, obs, vol, rg, "wide", 10.0, i)
    assert abs(o3[i].s - 10.0 * obs[i].s) < 1e-9 and v3[i] <= vol[i]
    o4, v4, r4 = fe.inject(rng, obs, vol, rg, "missing", 0.0, i)
    assert len(o4) == len(v4) == len(r4) == 7 and "USD_CHF" not in [o.name for o in o4]
    assert len(obs) == 8, "inject must not mutate its input"
    print("method grid and inject semantics assertions passed.")


def test_innocent_error_is_gauge_free():
    nodes = ["A", "B", "C", "D"]
    truth = {"A": 1.0, "B": 2.0, "C": 3.0, "D": -6.0}
    shifted = {k: v + 5.0 for k, v in truth.items()}
    shifted["D"] -= 40.0                                  # the excluded currency may be wildly wrong
    assert fe.innocent_error(shifted, truth, nodes, {"D"}) < 1e-12
    off = dict(shifted)
    off["B"] += 3.0
    assert abs(fe.innocent_error(off, truth, nodes, {"D"}) - 2.0) < 1e-9, "3 bps on one of three innocents, minus the re-centring (2/3 x 3)"
    print("innocent_error gauge-free assertions passed.")


def test_experiment_is_deterministic_and_well_formed():
    cells = [("clean", 0.0, "flat"), ("stale", 40.0, "flat"), ("wide", 10.0, "rollover"), ("missing", 0.0, "flat")]
    a = fe.run_experiment(CFG, fe.ModelSpreads(), cells=cells)
    b = fe.run_experiment(CFG, fe.ModelSpreads(), cells=cells)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), "same seed must reproduce exactly"
    clean, stale = a["cells"]["clean:0|flat"], a["cells"]["stale:40|flat"]
    assert clean["attribution_top1"] is None and clean["hub_arm_verdicts"] is None and a["cells"]["missing:0|flat"]["detector_flag_rate"] is None
    for c in a["cells"].values():
        for table in (c["attribution_top1"], c["detector_flag_rate"], c["hub_arm_verdicts"]):
            assert table is None or all(0.0 <= v <= 1.0 for v in table.values())
    ls = "unweighted|LS"
    assert stale["err_innocent_bps"][ls]["p50"] > clean["err_innocent_bps"][ls]["p50"], "a 40 bps fault must raise plain-LS error"
    assert sum(stale["hub_arm_verdicts"].values()) > 0.999
    assert "cell" in fe.render_text(a)
    print("determinism / well-formed / error-rises-with-fault assertions passed.")


def test_config_rejections_and_output_name():
    out = tempfile.mkdtemp()

    def args(*extra):
        return fe.parse_args(["--chat", "14", "--out-dir", out, *extra])

    cfg = fe.build_config(args("--tag", "T1", "--trials", "5"), "000101")
    assert Path(cfg["out_path"]).name == "fault_experiment_chat14_000101_T1.json" and cfg["sandbox_path"] is None
    for extra, expected in ((("--tag", "bad tag!"), "tag"), (("--trials", "0"), "trials"), (("--trials", "99999"), "trials"),
                            (("--spreads", "db", "--sandbox-db", "/nonexistent/x.db"), "not found")):
        try:
            fe.build_config(args(*extra), "000101")
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        fe.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("config rejection assertions passed.")


if __name__ == "__main__":
    test_method_grid_and_inject_semantics()
    test_innocent_error_is_gauge_free()
    test_experiment_is_deterministic_and_well_formed()
    test_config_rejections_and_output_name()
    print("\nAll fault_experiment tests passed.")
