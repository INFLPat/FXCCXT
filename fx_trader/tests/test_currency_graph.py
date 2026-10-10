# version: 261009
"""
tests/test_currency_graph.py

Method: synthetic worlds only (hermetic; no DB, no real outputs). A latent
log-value per currency makes every pair exactly triangle-consistent, so any
residual is an injected fault. Checks: orientation involution; cycle counts
against combinatorial ground truth (K4: 4 triangles, 7 cycles up to length 4;
basis size E - N + C); injected k bps fault found at k bps; metamorphic
invariance (edge order, pair inversion); differential test against the
audit's enumerate_triangles / evaluate_cycle / basis_specs / bar_tier;
verdict logic incl. unknown costs; two-venue projections; Bellman-Ford.

Run: python3 -m tests.test_currency_graph
"""

import math
import random

from tests import oracle_audit_261003 as au  # frozen pre-refactor audit logic (independent oracle)
from currency_graph import (
    CostSpec, CycleCeilingError, EdgeType, basis_edge, bar_tier, canonicalise, check_closed, cycle_basis,
    cycle_legs, cycle_signature, enumerate_cycles, evaluate_cycle, evaluate_quotes, find_arbitrage_cycles,
    invert_edge, quoted_edge, venue_edge,
)
from currency_graph import evaluate as ev_mod

CCY0 = {"USD": 0.0, "GBP": math.log(1.27), "EUR": math.log(1.10), "JPY": -math.log(150.0),
        "CHF": math.log(1.1), "CAD": -math.log(1.35)}
FX_PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
HALF = 0.00005


def _raises(exc_type, call):
    try:
        call()
    except exc_type:
        return True
    return False


def _quotes(pairs, seed=1, ccy0=CCY0, half=HALF):
    """pair -> (bid, ask) from a random latent per currency; consistent mids."""
    rng = random.Random(seed)
    latent = {c: v + rng.gauss(0, 0.01) for c, v in ccy0.items()}
    out = {}
    for pair in pairs:
        b, q = pair.split("_")
        m = math.exp(latent[b] - latent[q])
        out[pair] = (m * (1 - half), m * (1 + half))
    return out


def _edges(quotes, venue="oanda"):
    return [quoted_edge(p, venue, bid, ask) for p, (bid, ask) in quotes.items()]


def _sig_resid(edges, cycles):
    return {cycle_signature(edges, c): abs(evaluate_cycle(edges, c).residual_bps) for c in cycles}


def test_edge_validation_costs_and_spread_known():
    e = quoted_edge("GBP_USD", "oanda", 1.2699, 1.2701)
    assert e.spread_known and abs(e.mid - 1.27) < 1e-12 and e.spread_bps > 0
    assert not quoted_edge("BTC/GBP", "kraken", 40000.0, 40000.0).spread_known, "bid == ask carries no spread"
    assert quoted_edge("EUR_USD", "oanda", 1.1, 1.1, spread_known=True).spread_known
    assert _raises(ValueError, lambda: quoted_edge("GBP_USD", "oanda", 1.3, 1.2))
    assert _raises(ValueError, lambda: quoted_edge("GBP_USD", "oanda", float("nan"), 1.2))
    assert _raises(ValueError, lambda: quoted_edge("GBPUSD", "oanda", 1.0, 1.1))
    assert _raises(ValueError, lambda: CostSpec(taker_bps=-1.0))
    assert quoted_edge("GBP_USD", "oanda", 1.0, 1.1).cost is None, "no fee is ever defaulted"
    assert _raises(ValueError, lambda: venue_edge("USD", "a", "a"))
    print("edge validation assertions passed.")


def test_invert_is_an_involution_and_canonicalise_is_deterministic():
    e = quoted_edge("GBP_USD", "oanda", 1.2699, 1.2701)
    inv = invert_edge(e)
    assert (inv.base, inv.quote) == ("USD", "GBP") and inv.meta["inverted"] is True
    assert abs(inv.bid - 1 / 1.2701) < 1e-15 and abs(inv.ask - 1 / 1.2699) < 1e-15
    back = invert_edge(inv)
    assert (back.base, back.quote, back.bid, back.ask) == ("GBP", "USD", e.bid, e.ask) or (
        abs(back.bid - e.bid) < 1e-12 and abs(back.ask - e.ask) < 1e-12 and back.base == "GBP")
    c1, c2 = canonicalise(e), canonicalise(inv)
    assert (c1.base, c1.quote) == (c2.base, c2.quote) == ("GBP", "USD")
    assert abs(c1.bid - c2.bid) < 1e-12 and abs(c1.ask - c2.ask) < 1e-12
    basis = basis_edge("USDT", "USD", "kraken", venue_b="oanda", basis_bps=7.0)
    bi = invert_edge(basis)
    assert (bi.venue, bi.venue_b) == ("oanda", "kraken") and (bi.base, bi.quote) == ("USD", "USDT")
    ve = venue_edge("USD", "oanda", "kraken")
    assert canonicalise(ve).venue == "kraken", "VENUE edge orients venue < venue_b"
    print("orientation assertions passed.")


def test_cycle_counts_match_combinatorial_ground_truth():
    eight = _edges(_quotes(FX_PAIRS))
    assert len(enumerate_cycles(eight, "asset", 3).cycles) == 3, "8 pairs / 6 currencies: 3 triangles"
    assert len(cycle_basis(eight)) == 8 - 6 + 1
    k4_pairs = ["A_B", "A_C", "A_D", "B_C", "B_D", "C_D"]
    k4 = _edges(_quotes(k4_pairs, ccy0={"A": 0.0, "B": 0.1, "C": 0.2, "D": 0.3}))
    assert len(enumerate_cycles(k4, "asset", 3).cycles) == 4
    assert len(enumerate_cycles(k4, "asset", 4).cycles) == 7, "K4: 4 triangles + 3 four-cycles"
    assert len(cycle_basis(k4)) == 3
    two_components = k4 + _edges(_quotes(["X_Y", "Y_Z", "X_Z"], ccy0={"X": 0.0, "Y": 0.1, "Z": 0.2}))
    assert len(cycle_basis(two_components)) == (9 - 7 + 2), "E - N + C with C = 2"
    for cyc in enumerate_cycles(k4, "asset", 4).cycles + cycle_basis(k4):
        assert check_closed(k4, cyc), f"cycle does not close: {cyc}"
    print("cycle-count assertions passed.")


def test_consistent_world_has_zero_residual_and_basis_cycles_close():
    edges = _edges(_quotes(FX_PAIRS))
    for c in enumerate_cycles(edges, "asset", 3).cycles + cycle_basis(edges):
        assert abs(evaluate_cycle(edges, c).residual_bps) < 1e-6
    print("zero-residual assertion passed.")


def test_injected_fault_found_at_k_bps():
    base = _quotes(FX_PAIRS)
    for k in (5.0, 50.0, 130.8):
        faulty = dict(base)
        bid, ask = faulty["EUR_USD"]
        faulty["EUR_USD"] = (bid * math.exp(k / 1e4), ask * math.exp(k / 1e4))
        edges = _edges(faulty)
        for cyc in enumerate_cycles(edges, "asset", 3).cycles:
            resid = abs(evaluate_cycle(edges, cyc).residual_bps)
            expected = k if "EUR_USD" in {n for n, _v, _d in cycle_legs(edges, cyc)} else 0.0
            assert abs(resid - expected) < 1e-6, (k, cyc, resid)
    print("injected-fault (k bps found at k bps) assertions passed.")


def test_metamorphic_edge_order_and_pair_inversion_invariance():
    edges = _edges(_quotes(FX_PAIRS))
    ref = _sig_resid(edges, enumerate_cycles(edges, "asset", 4).cycles)
    shuffled = list(edges)
    random.Random(3).shuffle(shuffled)
    assert _sig_resid(shuffled, enumerate_cycles(shuffled, "asset", 4).cycles).keys() == ref.keys()
    for sig, r in _sig_resid(shuffled, enumerate_cycles(shuffled, "asset", 4).cycles).items():
        assert abs(r - ref[sig]) < 1e-9
    flipped = [invert_edge(e) if i % 2 == 0 else e for i, e in enumerate(edges)]
    got = _sig_resid(flipped, enumerate_cycles(flipped, "asset", 4).cycles)
    assert got.keys() == ref.keys()
    for sig, r in got.items():
        # arithmetic mid is not exactly inversion-symmetric: gap ~ (half-spread)^2 = ~2.5e-5 bps here
        assert abs(r - ref[sig]) < 1e-3, "pair inversion must leave |residual| unchanged (to second order in spread)"
    a = {e.instrument: (e.base, e.quote, e.bid, e.ask) for e in map(canonicalise, edges)}
    b = {e.instrument: (e.base, e.quote, e.bid, e.ask) for e in map(canonicalise, flipped)}
    for name, (base, quote, bid, ask) in a.items():
        assert (base, quote) == b[name][:2] and abs(bid - b[name][2]) < 1e-12 and abs(ask - b[name][3]) < 1e-12
    print("metamorphic invariance assertions passed.")


def test_differential_against_audit_triangles_and_basis():
    assert (ev_mod.RATIO_CLEAN, ev_mod.RATIO_SUSPECT, ev_mod.ABS_SUSPECT_BPS) == (
        au.RATIO_CLEAN, au.RATIO_SUSPECT, au.ABS_SUSPECT_BPS), "tier edges drifted from the audit"
    crypto = ["BTC/USDT", "BTC/GBP"]
    for seed in range(1, 8):
        q = _quotes(FX_PAIRS, seed=seed)
        gbpusd = (q["GBP_USD"][0] + q["GBP_USD"][1]) / 2
        q["EUR_USD"] = (q["EUR_USD"][0] * math.exp(0.0003 * seed), q["EUR_USD"][1] * math.exp(0.0003 * seed))
        px = 40000.0 + 100 * seed
        quotes_c = {"BTC/USDT": (px, px), "BTC/GBP": (px / gbpusd * math.exp(0.0002 * seed),) * 2}
        edges = _edges(q, "oanda") + [quoted_edge(n, "binance" if "USDT" in n else "kraken", *quotes_c[n]) for n in crypto]
        series = {n: {0: v} for n, v in {**q, **quotes_c}.items()}
        tri = au.enumerate_triangles(FX_PAIRS)
        cycles = enumerate_cycles(edges[:8], "asset", 3).cycles
        by_sig = {cycle_signature(edges, c): c for c in cycles}
        assert len(by_sig) == len(tri) == 3
        for spec in tri:
            mine = evaluate_cycle(edges, by_sig[frozenset((n, "oanda", None) for n, _ in spec)])
            ref = au.evaluate_cycle(spec, series)
            assert abs(abs(mine.residual_bps) - abs(ref["resid"][0])) < 1e-9
            assert abs(mine.spread_bps - ref["spread"][0]) < 1e-9
            assert all(abs(x - y) < 1e-9 for x, y in zip(sorted((mine.gain_fwd_bps, mine.gain_rev_bps)),
                                                         sorted((ref["gain_fwd"][0], ref["gain_rev"][0]))))
            assert bar_tier(abs(mine.residual_bps), mine.spread_bps) == au.bar_tier(abs(ref["resid"][0]), ref["spread"][0])
        allx = edges + [basis_edge("USDT", "USD", "binance", venue_b="oanda")]
        four = [c for c in enumerate_cycles(allx, "asset", 4).cycles
                if {"BTC/USDT", "BTC/GBP", "GBP_USD"} <= {n for n, _v, _d in cycle_legs(allx, c)}]
        assert len(four) == 1
        spec = au.basis_specs(crypto, FX_PAIRS)["BTC via USDT/GBP"]
        mine = evaluate_cycle(allx, four[0])
        ref = au.evaluate_cycle(spec, series)["resid"][0]
        assert abs(abs(mine.residual_bps) - abs(ref)) < 1e-9, "alias BASIS edge must reproduce the audit's basis cycle"
        assert mine.verdict == "VENUE_BLOCKED" and mine.uses_assumed, "three venues: blocked before rating"
        assert evaluate_cycle(allx, four[0], preposition=True).verdict == "UNRATED", "crypto legs have no spread"
    print("differential-vs-audit assertions passed (7 worlds, triangles + basis alias).")


def test_basis_value_shifts_the_cycle_residual_exactly():
    gbpusd = 1.27
    legs = [quoted_edge("BTC/USDT", "binance", 40000.0, 40000.0), quoted_edge("BTC/GBP", "kraken", 40000.0 / gbpusd, 40000.0 / gbpusd),
            quoted_edge("GBP_USD", "oanda", gbpusd * (1 - HALF), gbpusd * (1 + HALF))]
    resid = {}
    for bps in (0.0, 7.0):
        edges = legs + [basis_edge("USDT", "USD", "binance", basis_bps=bps, venue_b="oanda")]
        cyc = enumerate_cycles(edges, "asset", 4).cycles
        assert len(cyc) == 1
        resid[bps] = evaluate_cycle(edges, cyc[0]).residual_bps
    assert abs(resid[0.0]) < 1e-6 and abs(abs(resid[7.0]) - 7.0) < 1e-6
    print("basis-value assertion passed.")


def _two_venue_edges():
    a = quoted_edge("EUR_USD", "oanda", 1.0999, 1.1001)
    b = quoted_edge("EUR_USD", "venueb", 1.1010, 1.1012)
    return [a, b]


def test_parallel_edges_and_three_projections_with_venue_logic():
    edges = _two_venue_edges()
    asset = enumerate_cycles(edges, "asset", 2).cycles
    assert len(asset) == 1
    ev = evaluate_cycle(edges, asset[0])
    expected_gain = (math.log(1.1010) - math.log(1.1001)) * 1e4
    assert abs(max(ev.gain_fwd_bps, ev.gain_rev_bps) - expected_gain) < 1e-6
    assert ev.multi_venue and ev.verdict == "VENUE_BLOCKED"
    assert evaluate_cycle(edges, asset[0], preposition=True).verdict == "GAIN_COST_UNKNOWN"
    assert enumerate_cycles(edges, "per_venue", 4).cycles == [], "per_venue graphs have one edge each"
    assert enumerate_cycles(edges, "asset_venue", 4).cycles == [], "no transfer edges: two disconnected components"
    linked = edges + [venue_edge("EUR", "oanda", "venueb"), venue_edge("USD", "oanda", "venueb")]
    av = enumerate_cycles(linked, "asset_venue", 4).cycles
    assert len(av) == 1 and len(av[0]) == 4
    blocked = evaluate_cycle(linked, av[0])
    assert blocked.verdict == "VENUE_BLOCKED" and not blocked.rated
    pre = evaluate_cycle(linked, av[0], preposition=True)
    assert pre.rated and abs(max(pre.gain_fwd_bps, pre.gain_rev_bps) - expected_gain) < 1e-6
    assert enumerate_cycles(linked, "asset", 4).cycles == asset, "asset projection ignores VENUE edges"
    print("two-venue projection assertions passed.")


def test_verdicts_with_unknown_and_known_costs():
    def two(cost):
        a = quoted_edge("EUR_USD", "oanda", 1.0999, 1.1001, cost=cost)
        b = quoted_edge("EUR_USD", "venueb", 1.1010, 1.1012, cost=cost)
        return [a, b], enumerate_cycles([a, b], "asset", 2).cycles[0]
    edges, cyc = two(None)
    assert evaluate_cycle(edges, cyc, True).verdict == "GAIN_COST_UNKNOWN"
    assert evaluate_cycle(edges, cyc, True).cost_bps is None and evaluate_cycle(edges, cyc, True).net_gain_bps is None
    edges, cyc = two(CostSpec(taker_bps=1.0))
    r = evaluate_cycle(edges, cyc, True)
    assert r.verdict == "EXECUTABLE_AFTER_COST" and abs(r.cost_bps - 2.0) < 1e-12 and r.net_gain_bps > 0
    edges, cyc = two(CostSpec(taker_bps=5.0))
    assert evaluate_cycle(edges, cyc, True).verdict == "COST_EATS_GAIN"
    edges, cyc = two(CostSpec(maker_bps=1.0))
    assert evaluate_cycle(edges, cyc, True).verdict == "GAIN_COST_UNKNOWN", "maker fee alone is not a taker cost"
    flat = _edges(_quotes(FX_PAIRS))
    c = enumerate_cycles(flat, "asset", 3).cycles[0]
    assert evaluate_cycle(flat, c).verdict == "NO_GAIN"
    crypto = [quoted_edge("BTC/USDT", "binance", 40000.0, 40000.0), quoted_edge("BTC/GBP", "binance", 31000.0, 31000.0),
              quoted_edge("GBP_USDT", "binance", 1.29, 1.29)]
    cc = enumerate_cycles(crypto, "asset", 3).cycles[0]
    assert evaluate_cycle(crypto, cc).verdict == "UNRATED"
    print("verdict assertions passed.")


def test_bellman_ford_finds_executable_loops_and_ignores_unrated_arcs():
    base = _quotes(FX_PAIRS, half=0.00005)
    assert find_arbitrage_cycles(_edges(base), "asset") == [], "consistent world with spreads has no executable loop"
    faulty = dict(base)
    faulty["EUR_USD"] = (base["EUR_USD"][0] * math.exp(0.005), base["EUR_USD"][1] * math.exp(0.005))
    edges = _edges(faulty)
    found = find_arbitrage_cycles(edges, "asset")
    assert found and all(check_closed(edges, c) for c in found)
    for c in found:
        legs = [(edges[i].bid, edges[i].ask, d) for i, d in c]
        assert evaluate_quotes(legs)[1] > 40.0, "walked as returned, the loop gains ~50 bps less spreads"
        assert "EUR_USD" in {n for n, _v, _d in cycle_legs(edges, c)}
    crypto = [quoted_edge("BTC/USDT", "x", 100.0, 100.0), quoted_edge("BTC/GBP", "x", 70.0, 70.0), quoted_edge("GBP_USDT", "x", 1.0, 1.0)]
    assert find_arbitrage_cycles(crypto, "asset") == [], "unrated arcs are excluded by default"
    assert find_arbitrage_cycles(crypto, "asset", require_rated=False), "included on request"
    print("Bellman-Ford assertions passed.")


def test_cycle_ceilings_raise_or_truncate_loudly():
    names = [f"{a}_{b}" for i, a in enumerate("ABCDEF") for b in "ABCDEF"[i + 1:]]
    k6 = _edges(_quotes(names, ccy0={c: 0.01 * i for i, c in enumerate("ABCDEF")}))
    assert _raises(CycleCeilingError, lambda: enumerate_cycles(k6, "asset", 3, max_cycles=5))
    cut = enumerate_cycles(k6, "asset", 3, max_cycles=5, allow_truncate=True)
    assert len(cut.cycles) == 5 and cut.truncated
    assert len(enumerate_cycles(k6, "asset", 3).cycles) == 20 and not enumerate_cycles(k6, "asset", 3).truncated
    assert _raises(ValueError, lambda: enumerate_cycles(k6, "asset", 99))
    assert _raises(ValueError, lambda: enumerate_cycles(k6, "bogus", 3))
    print("ceiling assertions passed.")


if __name__ == "__main__":
    test_edge_validation_costs_and_spread_known()
    test_invert_is_an_involution_and_canonicalise_is_deterministic()
    test_cycle_counts_match_combinatorial_ground_truth()
    test_consistent_world_has_zero_residual_and_basis_cycles_close()
    test_injected_fault_found_at_k_bps()
    test_metamorphic_edge_order_and_pair_inversion_invariance()
    test_differential_against_audit_triangles_and_basis()
    test_basis_value_shifts_the_cycle_residual_exactly()
    test_parallel_edges_and_three_projections_with_venue_logic()
    test_verdicts_with_unknown_and_known_costs()
    test_bellman_ford_finds_executable_loops_and_ignores_unrated_arcs()
    test_cycle_ceilings_raise_or_truncate_loudly()
    print("\nAll currency_graph tests passed.")
