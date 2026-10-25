# version: 261025b
"""
tests/test_run_runlevel_report.py

Method: builds the artefacts of run_runlevel_experiment.py on the SYNTHETIC base of test_run_runlevel_experiment
(real pass with the default and extra gap policies, injection for one path, a hand-made Tier 2 JSONL), writes them
under the real file-name convention into a temp dir, and runs run_runlevel_report on them. Checks: every section
appears; table cells escape the pipes in column ids; the planted run is detected in the real-runs table; injection
rates are in [0, 1] and monotone in magnitude for the 15 bps vs 2 bps rows of a threshold column; a non-headline or
unknown column is rejected; the report is deterministic; an existing output is never overwritten; Tier 2 numbers
are the hand-computed means. Hermetic: temp files only, no real outputs.

Run: python3 -m tests.test_run_runlevel_report
"""

import json
import tempfile
from pathlib import Path

import numpy as np

import run_runlevel_experiment as x
import run_runlevel_report as rr
from tests.test_run_runlevel_experiment import _inj_cfg, _real_cfg, _synthetic

STEM = "chat1_000000"


def _artefacts():
    b = _synthetic()
    d = Path(tempfile.mkdtemp())
    base_meta = {"n_rows": b["N"], "n_ref": int((~b["masked"]).sum()), "constants": {"n_ref_stretches": b["n_cl"]}, "real_run_check": x.real_run_check(b)}
    rc = _real_cfg(policies=["mask_reset", "all_chron"], out_paths={"real": str(d / f"runlevel_real_{STEM}_re.npz")})
    meta = base_meta | {"real": x.real_pass(b, rc, {})}
    (d / f"runlevel_meta_{STEM}_re.json").write_text(json.dumps(meta))
    pc = _real_cfg(policies=["mask_reset", "all_chron", "all_carry3", "all_barindex"], out_paths={"real": str(d / f"runlevel_real_{STEM}_po.npz")})
    (d / f"runlevel_meta_{STEM}_po.json").write_text(json.dumps(base_meta | {"real": x.real_pass(b, pc, {})}))
    ic = _inj_cfg(b, signs=[1, -1], mags=[2.0, 2.5, 3.0, 4.0, 6.0, 9.0, 15.0], shapes=["flat", "ramp", "sp3_p3"], durs=[12, 24, 72], checkpoints=[8], paths=["arm:JPY"])
    ic["out_paths"] = {"inj_ckpt": str(d / "ck{k}.npz"), "inj": str(d / f"runlevel_inj_{STEM}_ij.npz")}
    (d / f"runlevel_meta_{STEM}_ij.json").write_text(json.dumps(base_meta | {"inj": x.run_injection(b, ic, {})}))
    methods = ["unweighted|LS", "unweighted|H1.0|spread", "unweighted|H1.0|mad"] + [f"m{i}" for i in range(21)] + ["inv_spread|LS", "inv_spread|H1.0|spread", "inv_spread2|H1.0|spread"]
    methods = [m for m in methods if m not in ("inv_spread|LS", "inv_spread|H1.0|spread", "inv_spread2|H1.0|spread")] + ["inv_spread|LS", "inv_spread|H1.0|spread", "inv_spread2|H1.0|spread"]
    n = len(methods)
    recs = [{"leg": "GBP_JPY", "mag": 9.0, "shape": "flat", "dur": 6, "start": 1, "cls": "flat", "ctx_bars": 60, "bucket": "48+", "r3_flag_bars": 2,
             "err_mean": [1.0] * n, "err_mean_r3": [0.5] * n, "err_max": [1.0] * n, "top1": [1.0] * n, "err_max_r3": [1.0] * n, "top1_r3": [0.5] * n, "gap_max": [0, 0]}
            for _ in range(4)]
    (d / f"runlevel_tier2_{STEM}_t2.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    (d / f"runlevel_meta_{STEM}_t2.json").write_text(json.dumps(base_meta | {"tier2": {"methods": methods}}))
    return d


def _args(d, *extra):
    return rr.parse_args(["--chat", "1", "--date", "000000", "--dir", str(d), "--real-tag", "re", "--inj-tags", "ij", "--t2-tag", "t2", "--pol-tag", "po",
                          "--out", str(d / "out.md"), *extra])


def test_report_sections_and_values():
    d = _artefacts()
    text = rr.build_report(_args(d))
    for head in ("## Real chronology", "### Block false-run rate", "### Hysteresis on the false-run side", "### The six real JPY runs", "## Extra gap policies",
                 "## Injection", "### Detection rate, flat, + sign", "### Borderline offset", "### Hysteresis on the run side", "## Tier 2"):
        assert head in text, head
    assert "median\\|W6\\|full\\|trailing\\|raw\\|abs\\|3" in text, "pipes in column ids must be escaped inside tables"
    assert "REPORT ONLY" in text and "recommend" in text
    row = [ln for ln in text.splitlines() if ln.startswith("| median\\|W6\\|full\\|trailing\\|raw\\|abs\\|3 |") and "h " in ln][0]
    assert "0h" in row or "1h" in row or "2h" in row, "the planted 10 bps run is detected within a few hours"
    det = [ln for ln in text.splitlines() if ln.startswith("| JPY | 15 |")][0].split("|")
    low = [ln for ln in text.splitlines() if ln.startswith("| JPY | 2 |")][0].split("|")
    assert all(0.0 <= float(v) <= 1.0 for v in det[3:-1] if v.strip() not in ("n/a",)), det
    assert float(det[4]) >= float(low[4]), "15 bps is detected at least as often as 2 bps"
    t2 = [ln for ln in text.splitlines() if ln.startswith("| 9 | flat | 6 | 4 |")][0]
    assert "1.00->0.50" in t2 and "0.50" in t2, t2
    assert rr.build_report(_args(d)) == text, "deterministic"
    print("report: all sections present, pipes escaped, planted run detected, tier 2 means hand-checked.")


def test_survey_lookup():
    d = _artefacts()
    a = _args(d)
    text = rr.build_survey(a)
    lines = text.strip().splitlines()
    meta, _z = rr.load_pair(d, "real", "chat1_000000", "re")
    assert len(lines) == len(meta["real"]["columns"]) + 1, "one row per column plus the header"
    n = lines[0].count(",")
    assert all(ln.count(",") == n for ln in lines), "ragged csv (column ids contain no commas)"
    row = [ln for ln in lines if ln.startswith("median|W6|full|trailing|raw|abs|3,")][0].split(",")
    assert int(row[3]) >= 1, "the planted run is detected by the W6 / abs 3 column"
    head = lines[0].split(",")
    assert "real_ext_detector_detected_of_6" in head and "tail_JPY_4bps_D24" in head and "det_JPY_step6_plat3_D24" in head and "det_JPY_2.5bps_D24" in head
    g = lambda name: row[head.index(name)]
    assert int(g("real_ext_detector_detected_of_6")) == int(row[3]), "the detector extent is the legacy real-run table"
    assert 0.0 <= float(g("det_JPY_step6_plat3_D24")) <= float(g("det_JPY_4bps_D24")) + 1e-9 and float(g("det_JPY_2.5bps_D24")) <= float(g("det_JPY_3bps_D24")) + 1e-9
    assert rr.build_survey(a) == text, "deterministic"
    print("survey: one row per column, consistent fields, planted run found.")


def test_report_rejections():
    d = _artefacts()
    for extra in (("--cols", "median|W6|full|trailing|raw|abs|99"), ("--cols", "median|W6|full|trailing|raw|bar|1")):
        try:
            rr.build_report(_args(d, *extra))
            raise RuntimeError("expected rejection of a missing / non-headline column")
        except AssertionError:
            pass
    try:
        rr.build_report(rr.parse_args(["--chat", "1", "--date", "000000", "--dir", str(d), "--real-tag", "nope", "--out", str(d / "o2.md")]))
        raise RuntimeError("expected rejection of a missing artefact")
    except AssertionError as exc:
        assert "missing artefact" in str(exc)
    (d / "out.md").write_text("x")
    try:
        rr.main(["--chat", "1", "--date", "000000", "--dir", str(d), "--real-tag", "re", "--out", str(d / "out.md")])
        raise RuntimeError("expected refusal to overwrite")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    try:
        rr.main(["--chat", "1", "--date", "00", "--real-tag", "re", "--out", str(d / "o3.md")])
        raise RuntimeError("expected rejection of the date")
    except AssertionError:
        pass
    rr.main(["--chat", "1", "--date", "000000", "--dir", str(d), "--real-tag", "re", "--inj-tags", "ij", "--out", str(d / "ok.md")])
    assert (d / "ok.md").exists() and "## Tier 2" not in (d / "ok.md").read_text()
    print("report rejections, overwrite refusal and optional sections passed.")


if __name__ == "__main__":
    test_report_sections_and_values()
    test_survey_lookup()
    test_report_rejections()
    print("\nAll run_runlevel_report tests passed.")
