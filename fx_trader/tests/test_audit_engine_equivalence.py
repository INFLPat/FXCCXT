# version: 261009
"""
tests/test_audit_engine_equivalence.py

Method: the refactored audit (cycle discovery and per-bar numbers from
currency_graph) is compared EXACTLY - same labels, same leg order and
orientation, same list order, bit-identical floats - with the frozen
pre-refactor logic in tests/oracle_audit_261003.py. Inputs: several FX pair
sets (including inverted pair names and a shuffled input order), crypto sets
with one and with several quote currencies, random quote series, and the
synthetic world of test_audit_sandbox_alignment (whole cycles/basis sections,
compared as JSON). A non-canonical alias shape must fall back, not crash.
Hermetic: no real outputs, temp files only.

Run: python3 -m tests.test_audit_engine_equivalence
"""

import json
import math
import random
import sqlite3

import audit_sandbox_alignment as au
from tests import oracle_audit_261003 as oracle
from tests.test_audit_sandbox_alignment import FX_PAIRS, INSTRUMENTS, _world

FX_SETS = {
    "eight pairs / six currencies": FX_PAIRS,
    "K4 on A-D": ["A_B", "A_C", "A_D", "B_C", "B_D", "C_D"],
    "inverted names": ["EUR_USD", "GBP_EUR", "USD_GBP", "USD_JPY", "JPY_GBP"],
    "no triangle": ["EUR_USD", "USD_CAD"],
}
CRYPTO_SETS = {
    "four bases, USDT and GBP": ["BTC/USDT", "BTC/GBP", "ETH/USDT", "ETH/GBP", "LTC/USDT", "LTC/GBP", "XRP/USDT", "XRP/GBP"],
    "one base, three quotes": ["BTC/USDT", "BTC/GBP", "BTC/EUR"],
    "USDT only (no link)": ["BTC/USDT", "ETH/USDT"],
}


def _random_series(names: list, seed: int, bars: int = 40) -> dict:
    rng = random.Random(seed)
    out = {}
    for name in names:
        price = rng.uniform(0.5, 2.0) if "/" not in name else rng.uniform(100.0, 40000.0)
        series = {}
        for k in range(bars):
            price *= math.exp(rng.gauss(0, 0.002))
            half = price * (rng.uniform(1e-5, 3e-5) if "/" not in name else 0.0)
            series[k * 3600] = (price - half, price + half)
        out[name] = series
    return out


def _oracle_triangles(fx: list) -> list:
    return [(oracle.cycle_label(s), s) for s in oracle.enumerate_triangles(fx)]


def _oracle_basis(crypto: list, fx: list) -> list:
    return [(f"{k}: {oracle.cycle_label(s)}", s) for k, s in oracle.basis_specs(crypto, fx).items()]


def test_triangle_labels_specs_and_order_match_oracle():
    for name, fx in FX_SETS.items():
        shuffled = list(fx)
        random.Random(4).shuffle(shuffled)
        want = _oracle_triangles(fx)
        for given in (fx, shuffled):
            got = au.discover_cycles(sorted(given), [], max_len=3)["triangle"]
            assert got == want, (name, got, want)
    print("triangle discovery == oracle (labels, leg order, orientation, list order) for", list(FX_SETS))


def test_basis_labels_specs_and_order_match_oracle():
    for fx_name, fx in (("eight pairs", FX_PAIRS), ("with EUR link", FX_PAIRS + ["EUR_JPY"])):
        for name, crypto in CRYPTO_SETS.items():
            want = _oracle_basis(crypto, fx)
            got = au.discover_cycles(sorted(fx), sorted(crypto))["basis"]
            assert got == want, (fx_name, name, got, want)
    multi = au.discover_cycles(sorted(FX_PAIRS), sorted(CRYPTO_SETS["one base, three quotes"]))["basis"]
    assert [label.split(":")[0] for label, _s in multi] == ["BTC via USDT/EUR", "BTC via USDT/GBP"], multi
    print("basis discovery == oracle, including a base with several non-proxy quotes.")


def test_per_bar_numbers_are_bit_identical():
    fx, crypto = sorted(FX_PAIRS), sorted(CRYPTO_SETS["four bases, USDT and GBP"])
    for seed in range(1, 6):
        series = _random_series(fx + crypto, seed)
        for label, spec in _oracle_triangles(fx) + _oracle_basis(crypto, fx):
            assert au.evaluate_spec(spec, series) == oracle.evaluate_cycle(spec, series), (seed, label)
    print("evaluate_spec == oracle.evaluate_cycle exactly on 5 random worlds (triangles and basis cycles).")


def test_report_sections_identical_on_synthetic_world():
    path = _world()
    report = au.run_audit({"checks": ("cycles", "basis"), "display_tz": "Europe/London", "sandbox_path": path, "chat": 14,
                           "write_manifest": None, "verify_manifest": None}, INSTRUMENTS)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        series = {n: au.load_series(con, n, g) for n, g, _c, _p in INSTRUMENTS}
    finally:
        con.close()
    fx = sorted(n for n in series if not au.is_crypto(n))
    crypto = sorted(n for n in series if au.is_crypto(n))
    old_cycles = {"n_cycles": len(oracle.enumerate_triangles(fx)),
                  "cycles": [au.summarize_cycle(label, oracle.evaluate_cycle(s, series), "Europe/London") for label, s in _oracle_triangles(fx)]}
    old_basis = {"n_cycles": len(oracle.basis_specs(crypto, fx)),
                 "cycles": [au.summarize_cycle(label, oracle.evaluate_cycle(s, series), "Europe/London", rated=False) for label, s in _oracle_basis(crypto, fx)]}
    assert json.dumps(report["cycles"]) == json.dumps(old_cycles), "cycles section differs from the oracle composition"
    assert json.dumps(report["basis"]) == json.dumps(old_basis), "basis section differs from the oracle composition"
    assert report["cycles"]["n_cycles"] == 3 and report["basis"]["n_cycles"] == 2
    print("whole cycles/basis report sections are JSON-identical to the oracle composition.")


def test_noncanonical_alias_shape_falls_back_instead_of_crashing():
    found = au.discover_cycles([], ["BTC/USDT", "BTC/USD"])
    assert len(found["basis"]) == 1 and found["basis"][0][0].startswith("basis: "), found["basis"]
    assert len(found["basis"][0][1]) == 2, "alias leg is skipped: two quoted legs remain"
    print("three-leg alias cycle falls back to engine orientation with a 'basis:' label.")


def test_oriented_spec_rejects_a_path_that_does_not_fit():
    edges = au.build_topology(["A_B", "B_C", "A_C"], [])
    cycle = au.enumerate_cycles(edges, "asset", 3).cycles[0]
    try:
        au.oriented_spec(edges, cycle, ["A", "B", "Z"])
        raise AssertionError("expected a ValueError for a path that does not follow the cycle")
    except ValueError as exc:
        assert "no leg joins" in str(exc)
    print("oriented_spec rejection assertion passed.")


if __name__ == "__main__":
    test_triangle_labels_specs_and_order_match_oracle()
    test_basis_labels_specs_and_order_match_oracle()
    test_per_bar_numbers_are_bit_identical()
    test_report_sections_identical_on_synthetic_world()
    test_noncanonical_alias_shape_falls_back_instead_of_crashing()
    test_oriented_spec_rejects_a_path_that_does_not_fit()
    print("\nAll audit/engine equivalence tests passed.")
