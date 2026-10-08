# version: 261008
"""
tests/test_audit_gate.py

Method: the comparer is tested on reports taken from the SYNTHETIC world of
test_audit_sandbox_alignment (temp SQLite; no real outputs, hermetic). Each
perturbation is applied to a deep copy and must produce the stated severity
(test-the-test: a comparer that passes everything would fail these). The
engine report is compared with the current audit's own cycles/basis sections
on the same synthetic world (differential). Orientation handling is proven by
reversing a loop's label AND negating its signed mean (must pass), then by
reversing only one (must fail).

Run: python3 -m tests.test_audit_gate
"""

import copy
import tempfile
from pathlib import Path

import audit_gate as g
import audit_sandbox_alignment as au
from tests.test_audit_sandbox_alignment import INSTRUMENTS, _cfg, _world

_CACHE = {}


def _old() -> dict:
    if "old" not in _CACHE:
        _CACHE["old"] = au.run_audit(_cfg(_world(), ("cycles", "basis")), INSTRUMENTS)
    return _CACHE["old"]


def _fresh_copy() -> dict:
    return copy.deepcopy(_old())


def _cycle_with(report: dict, needle: str, section: str = "cycles") -> dict:
    hits = [c for c in report[section]["cycles"] if needle in c["cycle"]]
    assert len(hits) == 1, (needle, [c["cycle"] for c in report[section]["cycles"]])
    return hits[0]


def _verdict(fresh: dict, sections=None) -> str:
    return g.compare_reports(_old(), fresh, sections=sections).verdict


def _flip_label(label: str) -> str:
    head, sep, body = label.partition(": ")
    if not sep:
        head, body = "", label
    flipped = " ".join(("-" if t[0] == "+" else "+") + t[1:] for t in body.split())
    return f"{head}: {flipped}" if head else flipped


def _real_cycle(report: dict) -> dict:
    """The cycle carrying the injected 50 bps fault, so signed_mean_bps is not ~0."""
    c = _cycle_with(report, "EUR_USD")
    assert abs(c["signed_mean_bps"]) > 1e-6, "test needs a non-zero signed mean to prove sign handling"
    return c


def test_identical_report_and_ignored_keys_pass():
    fresh = _fresh_copy()
    fresh["chat"], fresh["sandbox_db"], fresh["checks"] = 99, "/elsewhere.db", ["cycles"]
    assert _verdict(fresh) == "PASS"
    changed = _fresh_copy()
    changed["display_tz"] = "UTC"
    assert _verdict(changed) == "FAIL", "a non-ignored top-level key must be compared"
    print("identical / ignored-key assertions passed.")


def test_engine_matches_current_audit_on_synthetic_world():
    report, extra = g.build_engine_report(_world(), INSTRUMENTS, "Europe/London")
    enum = extra["enumeration"]
    assert (enum["triangle"], enum["basis"], enum["crypto_only"]) == (3, 2, 1), enum
    result = g.compare_reports(_old(), report, sections=g.ENGINE_SECTIONS)
    assert result.count(g.FAIL) == 0, [d for d in result.diffs if d.severity == g.FAIL]
    for d in result.diffs:
        if d.severity == g.FLIP:
            assert "float-noise" in d.note, f"unexpected non-noise FLIP: {d}"
    assert len(extra["crypto_only_info"]) == 1 and extra["crypto_only_info"][0]["abs_bps_max"] > 99.0, extra
    assert set(result.not_compared) == set(_old()) - set(g.ENGINE_SECTIONS) - {"chat", "sandbox_db", "checks"}
    print(f"engine vs current audit: verdict {result.verdict}, {result.count(g.FLIP)} noise-tie flips, 0 fails.")


def test_orientation_reversal_passes_only_when_both_label_and_sign_move():
    fresh = _fresh_copy()
    c = _real_cycle(fresh)
    c["cycle"] = _flip_label(c["cycle"])
    c["signed_mean_bps"] = -c["signed_mean_bps"]
    result = g.compare_reports(_old(), fresh)
    assert result.verdict == "PASS" and any("orientation sign -1" in d.note for d in result.diffs)

    only_label = _fresh_copy()
    _real_cycle(only_label)["cycle"] = _flip_label(_real_cycle(only_label)["cycle"])
    assert _verdict(only_label) == "FAIL", "a reversed loop whose signed mean was NOT negated is a real mismatch"

    mixed = _fresh_copy()
    m = _real_cycle(mixed)
    first, rest = m["cycle"].split(" ", 1)
    m["cycle"] = ("-" if first[0] == "+" else "+") + first[1:] + " " + rest
    result = g.compare_reports(_old(), mixed)
    assert result.verdict == "FAIL" and any("not the same loop" in d.note for d in result.diffs)
    print("orientation assertions passed.")


def test_unrounded_numbers_tolerance_boundary():
    tiny = _fresh_copy()
    _real_cycle(tiny)["abs_bps"]["99"] += 1e-10
    assert _verdict(tiny) == "PASS"
    big = _fresh_copy()
    _real_cycle(big)["abs_bps"]["99"] += 1e-6
    assert _verdict(big) == "FAIL"
    print("1e-9 tolerance boundary assertions passed.")


def test_rounded_fields_allow_exactly_one_unit_as_flip():
    for field_path, unit in ((("by_year_abs_bps",), 1e-4), (("worst_events", 0, "abs_bps"), 1e-3)):
        near, far = _fresh_copy(), _fresh_copy()
        for rep, delta, expected in ((near, unit, "FLIP"), (far, 2.5 * unit, "FAIL")):
            c = _real_cycle(rep)
            if field_path[0] == "by_year_abs_bps":
                year = sorted(c["by_year_abs_bps"])[0]
                c["by_year_abs_bps"][year]["50"] += delta
            else:
                c["worst_events"][0]["abs_bps"] += delta
            assert _verdict(rep) == expected, (field_path, delta, expected)
    print("rounding-unit FLIP/FAIL assertions passed.")


def test_count_boundary_flips_are_reported_not_silent():
    tier = _fresh_copy()
    c = _real_cycle(tier)
    c["tiers"]["clean"] += 1
    c["tiers"]["noisy"] = c["tiers"].get("noisy", 0) - 1 if "noisy" in c["tiers"] else -0
    c["tiers"] = {k: v for k, v in c["tiers"].items() if v != 0}
    assert _verdict(tier) in ("FLIP", "FAIL")
    assert g.compare_reports(_old(), tier).count(g.FLIP) >= 1, "a +1 tier count must surface as a FLIP, not pass silently"

    share = _fresh_copy()
    c = _cycle_with(share, "GBP_CHF")
    c["exec_violation_share"] = c["exec_violation_share"] + 1.0 / c["n"]
    assert _verdict(share) == "FLIP"
    share2 = _fresh_copy()
    c = _cycle_with(share2, "GBP_CHF")
    c["exec_violation_share"] = c["exec_violation_share"] + 5.0 / c["n"]
    assert _verdict(share2) == "FAIL", "5 bars is beyond a boundary flip"
    print("count-boundary assertions passed.")


def test_membership_order_and_tie_handling_of_top_n_lists():
    swapped = _fresh_copy()
    ev = _real_cycle(swapped)["worst_events"]
    ev[1], ev[2] = ev[2], ev[1]
    assert _verdict(swapped) == "FLIP"

    replaced = _fresh_copy()
    _real_cycle(replaced)["worst_events"][0]["at"]["utc"] = "2000-01-01T00:00:00.000000000Z"
    assert _verdict(replaced) == "FAIL", "a different significant event is a real difference"

    shorter = _fresh_copy()
    _real_cycle(shorter)["worst_events"].pop()
    assert _verdict(shorter) == "FAIL"
    print("top-N list assertions passed.")


def test_missing_extra_reordered_cycles():
    missing = _fresh_copy()
    missing["cycles"]["cycles"].pop()
    result = g.compare_reports(_old(), missing)
    assert result.verdict == "FAIL" and any("missing from fresh" in d.note for d in result.diffs)

    extra = _fresh_copy()
    clone = copy.deepcopy(extra["cycles"]["cycles"][0])
    clone["cycle"] = clone["cycle"] + " +ZZZ_YYY"
    extra["cycles"]["cycles"].append(clone)
    assert _verdict(extra) == "FAIL"

    reordered = _fresh_copy()
    reordered["cycles"]["cycles"].reverse()
    result = g.compare_reports(_old(), reordered)
    assert result.verdict == "PASS" and any("order differs" in d.note for d in result.diffs)
    print("cycle-matching assertions passed.")


def test_basis_nulls_must_be_reproduced():
    fresh = _fresh_copy()
    c = _cycle_with(fresh, "BTC/USDT", "basis")
    c["tiers"] = {"clean": c["n"]}
    assert _verdict(fresh) == "FAIL", "basis cycles are unrated: tiers must stay null"
    print("basis-null assertion passed.")


def test_parse_label():
    assert g.parse_label("BTC via USDT/GBP: -BTC/USDT +BTC/GBP +GBP_USD") == {"BTC/USDT": -1, "BTC/GBP": 1, "GBP_USD": 1}
    assert g.parse_label("+EUR_GBP +GBP_USD -EUR_USD") == {"EUR_GBP": 1, "GBP_USD": 1, "EUR_USD": -1}
    for bad in ("", "EUR_GBP GBP_USD", "+A +A"):
        try:
            g.parse_label(bad)
            raise RuntimeError(f"expected rejection of {bad!r}")
        except (ValueError, AssertionError):
            pass
    print("parse_label assertions passed.")


def test_exit_codes_and_config_rejections():
    mk = lambda v: [{"verdict": v}]
    assert g.exit_code(mk("PASS")) == 0 and g.exit_code(mk("FLIP")) == 3 and g.exit_code(mk("FAIL")) == 2
    assert g.exit_code(mk("PASS") + mk("FLIP") + mk("FAIL")) == 2

    tmp = Path(tempfile.mkdtemp())
    golden = tmp / "golden.json"
    import json
    golden.write_text(json.dumps(_old()))

    def args(*extra):
        return g.parse_args(["--chat", "14", "--golden", str(golden), "--out-dir", str(tmp), *extra])

    cfg = g.build_config(args("--fresh", str(golden), "--tag", "T1"), "000101", instruments=INSTRUMENTS)
    assert Path(cfg["out_path"]).name == "audit_gate_chat14_000101_T1.json"
    cases = [
        (lambda: g.build_config(args(), "000101", INSTRUMENTS), "nothing to do"),
        (lambda: g.build_config(args("--fresh", "/nonexistent.json"), "000101", INSTRUMENTS), "not found"),
        (lambda: g.build_config(args("--engine", "--sandbox-db", "/nonexistent.db"), "000101", INSTRUMENTS), "not found"),
        (lambda: g.build_config(args("--fresh", str(golden), "--tag", "bad tag!"), "000101", INSTRUMENTS), "tag"),
    ]
    for call, expected in cases:
        try:
            call()
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    Path(cfg["out_path"]).write_text("{}")
    try:
        g.build_config(args("--fresh", str(golden), "--tag", "T1"), "000101", INSTRUMENTS)
        raise RuntimeError("expected refusal to reuse an output path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("exit-code / config assertions passed.")


def test_run_gate_end_to_end_on_synthetic_world():
    tmp = Path(tempfile.mkdtemp())
    golden = tmp / "golden.json"
    import json
    golden.write_text(json.dumps(au.run_audit(_cfg(_world()), INSTRUMENTS)))
    args = g.parse_args(["--chat", "14", "--golden", str(golden), "--fresh", str(golden), "--engine",
                         "--sandbox-db", _world(), "--out-dir", str(tmp)])
    cfg = g.build_config(args, "000101", instruments=INSTRUMENTS)
    comparisons = g.run_gate(cfg, cfg["golden"])
    assert [c["verdict"] for c in comparisons][0] == "PASS", comparisons[0]["diffs"][:3]
    assert comparisons[1]["n_fail"] == 0 and g.exit_code(comparisons) in (0, 3)
    assert "engine (currency_graph)" in g.render_text(comparisons)
    print("run_gate end-to-end assertions passed.")


if __name__ == "__main__":
    test_identical_report_and_ignored_keys_pass()
    test_engine_matches_current_audit_on_synthetic_world()
    test_orientation_reversal_passes_only_when_both_label_and_sign_move()
    test_unrounded_numbers_tolerance_boundary()
    test_rounded_fields_allow_exactly_one_unit_as_flip()
    test_count_boundary_flips_are_reported_not_silent()
    test_membership_order_and_tie_handling_of_top_n_lists()
    test_missing_extra_reordered_cycles()
    test_basis_nulls_must_be_reproduced()
    test_parse_label()
    test_exit_codes_and_config_rejections()
    test_run_gate_end_to_end_on_synthetic_world()
    print("\nAll audit_gate tests passed.")
