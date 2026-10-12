# version: 261011
"""
tests/test_strengths.py

Method: synthetic worlds only (hermetic; no DB, no real outputs). A hand-solved
3-currency triangle fixes the exact numbers (strengths, hat 2/3, LOO +-3); the
8-pair FX topology (6 currencies, USD_CAD a bridge) is used for consistency,
fast-vs-oracle agreement at 1e-9 over random weighted worlds, leave-one-
currency-out and views. Differential: in a triangle, every pair's |LOO| equals
the |cycle residual| of currency_graph.evaluate_quotes.

Run: python3 -m tests.test_strengths
"""

import math
import random

from currency_graph.strengths import (
    Obs, SCHEMES, components, fit_zero_sum, from_edges, invert, is_connected, loco, loo_fast,
    loo_oracle, select_component, strength_views, weights_for,
)

FX = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
LAT = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
NODES = sorted(LAT)
TOL = 1e-9


def _raises(exc_type, call):
    try:
        call()
    except exc_type:
        return True
    return False


def _world(seed: int, noise: float = 0.0, pairs=FX) -> list:
    rng = random.Random(seed)
    out = []
    for p in pairs:
        a, b = p.split("_")
        out.append(Obs(a, b, LAT[a] - LAT[b] + rng.gauss(0, noise), rng.uniform(0.5, 3.0), p))
    return out


def _tri(y_ac: float) -> list:
    return [Obs("A", "B", 10.0, 1.0, "AB"), Obs("B", "C", 20.0, 1.0, "BC"), Obs("A", "C", y_ac, 1.0, "AC")]


def test_hand_solved_triangle():
    tn = ["A", "B", "C"]
    f = fit_zero_sum(tn, _tri(30.0), [1.0] * 3)
    want = {"A": 40 / 3, "B": 10 / 3, "C": -50 / 3}
    assert all(abs(f.c[k] - want[k]) < TOL for k in tn), f.c
    assert abs(sum(f.c.values())) < TOL and all(abs(r) < TOL for r in f.resid)
    assert all(abs(h - 2 / 3) < TOL for h in f.hat), f.hat
    g = fit_zero_sum(tn, _tri(33.0), [1.0] * 3)
    assert all(abs(a - b) < TOL for a, b in zip(g.resid, [-1.0, -1.0, 1.0])), g.resid
    fast = loo_fast(g)
    assert all(abs(a - b) < TOL for a, b in zip(fast, [-3.0, -3.0, 3.0])), fast
    orc = loo_oracle(tn, _tri(33.0), [1.0] * 3)
    assert all(abs(a - b) < TOL for a, b in zip(orc, fast)), (orc, fast)
    print("hand-solved triangle assertions passed.")


def test_consistent_world_recovers_latent_up_to_gauge():
    mean = sum(LAT.values()) / len(LAT)
    for scheme in SCHEMES:
        obs = _world(1)
        f = fit_zero_sum(NODES, obs, weights_for(scheme, obs))
        assert all(abs(f.c[k] - (LAT[k] - mean)) < 1e-8 for k in NODES), (scheme, f.c)
        assert all(abs(r) < 1e-8 for r in f.resid) and abs(sum(f.c.values())) < 1e-8
    print("consistent-world recovery assertions passed.")


def test_fast_path_equals_oracle_on_random_weighted_worlds():
    for seed in range(1, 21):
        obs = _world(seed, noise=2.0)
        rng = random.Random(100 + seed)
        w = [rng.uniform(0.2, 5.0) for _ in obs]
        fast, orc = loo_fast(fit_zero_sum(NODES, obs, w)), loo_oracle(NODES, obs, w)
        assert [x is None for x in fast] == [x is None for x in orc], (seed, fast, orc)
        for x, y in zip(fast, orc):
            assert x is None or abs(x - y) < TOL, (seed, x, y)
        bridges = [o.name for o, x in zip(obs, fast) if x is None]
        assert bridges == ["USD_CAD"], bridges
    k4 = ["A_B", "A_C", "A_D", "B_C", "B_D", "C_D"]
    rng = random.Random(7)
    obs = [Obs(p[0], p[2], rng.gauss(0, 5), 1.0, p) for p in k4]
    w = [rng.uniform(0.2, 5.0) for _ in obs]
    fast, orc = loo_fast(fit_zero_sum(list("ABCD"), obs, w)), loo_oracle(list("ABCD"), obs, w)
    assert all(x is not None and abs(x - y) < TOL for x, y in zip(fast, orc))
    print("fast-vs-oracle (20 weighted FX worlds + K4) assertions passed; only USD_CAD is a bridge.")


def test_triangle_loo_equals_engine_cycle_residual():
    from currency_graph import evaluate_quotes
    rng = random.Random(3)
    half = 0.00005
    mids = {"AB": 1.30, "BC": 0.90, "AC": 1.30 * 0.90 * math.exp(0.0007)}   # 7 bps inconsistency on AC
    q = {k: (m * (1 - half), m * (1 + half)) for k, m in mids.items()}
    legs = [(*q["AB"], 1), (*q["BC"], 1), (*q["AC"], -1)]
    resid_bps = evaluate_quotes(legs)[0]
    obs = [Obs(n[0], n[1], math.log((q[n][0] + q[n][1]) / 2) * 1e4, 1.0, n) for n in ("AB", "BC", "AC")]
    obs[0], obs[1], obs[2] = Obs("A", "B", obs[0].y, 1.0, "AB"), Obs("B", "C", obs[1].y, 1.0, "BC"), Obs("A", "C", obs[2].y, 1.0, "AC")
    w = [rng.uniform(0.3, 4.0) for _ in obs]
    loo = loo_fast(fit_zero_sum(["A", "B", "C"], obs, w))
    assert all(abs(abs(x) - abs(resid_bps)) < 1e-6 for x in loo), (loo, resid_bps)
    print(f"triangle |LOO| == |evaluate_quotes residual| ({resid_bps:.3f} bps) for all three pairs.")


def test_inverse_spread_weight_ratio_in_triangle():
    obs = [Obs("A", "B", 10.0, 1.0), Obs("B", "C", 20.0, 1.0), Obs("A", "C", 33.0, 100.0)]
    f = fit_zero_sum(["A", "B", "C"], obs, weights_for("inv_spread", obs))
    assert abs(f.resid[2] / f.resid[0] + 100.0) < 1e-6, f.resid     # residual share is proportional to spread (opposite sign)
    u = fit_zero_sum(["A", "B", "C"], obs, weights_for("unweighted", obs))
    assert abs(u.c["A"] - f.c["A"]) > 0.1, "weighting must change the fit when spreads differ"
    print("1/spread residual-ratio assertion passed.")


def test_weights_for():
    obs = _world(2)
    for scheme in SCHEMES:
        w = weights_for(scheme, obs)
        assert abs(sum(w) / len(w) - 1.0) < 1e-12 and all(x > 0 for x in w)
    assert weights_for("unweighted", [Obs("A", "B", 0.0, 0.0)]) == [1.0]
    assert _raises(ValueError, lambda: weights_for("inv_spread", [Obs("A", "B", 0.0, 0.0)]))
    assert _raises(AssertionError, lambda: weights_for("bogus", obs))
    print("weights_for assertions passed.")


def test_loco():
    obs = _world(1)
    w = [1.0] * len(obs)
    f = fit_zero_sum(NODES, obs, w)
    out = loco(NODES, obs, w, f.c)
    assert out["USD"] is None, "dropping USD isolates CAD"
    for k in ("CAD", "GBP", "EUR", "JPY", "CHF"):
        assert out[k] is not None and all(abs(v) < 1e-8 for v in out[k].values()), (k, out[k])
    noisy = _world(1, noise=3.0)
    fn = fit_zero_sum(NODES, noisy, w)
    shifts = loco(NODES, noisy, w, fn.c)["GBP"]
    assert max(abs(v) for v in shifts.values()) > 1e-6
    print("leave-one-currency-out assertions passed.")


def test_views_share_differences_and_special_cases():
    obs = _world(5, noise=2.0)
    f = fit_zero_sum(NODES, obs, weights_for("inv_spread", obs))
    views = strength_views(f.c, obs, "USD", {"USD": 1.0})
    for name, v in views.items():
        assert v is not None, name
        assert all(abs((v[i] - v[j]) - (f.c[i] - f.c[j])) < 1e-8 for i in NODES for j in NODES), name
    assert all(abs(views["basket_equal"][k] - views["zero_sum"][k]) < 1e-9 for k in NODES)
    assert abs(views["vs_USD"]["USD"]) < 1e-12
    assert all(abs(views["basket_user"][k] - views["vs_USD"][k]) < 1e-9 for k in NODES), "all weight on USD == vs USD"
    flat = [Obs(o.a, o.b, o.y, 1.0, o.name) for o in obs]
    ff = fit_zero_sum(NODES, flat, [1.0] * len(flat))
    v2 = strength_views(ff.c, flat)
    assert all(abs(v2["basket_inv_spread"][k] - v2["zero_sum"][k]) < 1e-9 for k in NODES), "equal spreads: basket == zero-sum"
    assert strength_views(ff.c, flat, "XXX")["vs_XXX"] is None and strength_views(ff.c, flat)["basket_user"] is None
    print("view assertions passed.")


def test_components_and_disconnected_fit():
    obs = [Obs("A", "B", 1.0, 1.0), Obs("B", "C", 1.0, 1.0), Obs("D", "E", 1.0, 1.0)]
    comps = components(list("ABCDEF"), obs)
    assert comps == [["A", "B", "C"], ["D", "E"], ["F"]], comps
    main, sub = select_component(list("ABCDEF"), obs)
    assert main == ["A", "B", "C"] and len(sub) == 2
    assert not is_connected(list("ABCDE"), obs)
    assert _raises(ValueError, lambda: fit_zero_sum(list("ABCDE"), obs, [1.0] * 3))
    assert _raises(ValueError, lambda: invert([[1.0, 1.0], [1.0, 1.0]]))
    inv = invert([[2.0, 0.0], [0.0, 4.0]])
    assert abs(inv[0][0] - 0.5) < 1e-15 and abs(inv[1][1] - 0.25) < 1e-15
    print("components / disconnected / invert assertions passed.")


def test_from_edges_adapter():
    class E:
        base, quote, mid, spread_bps, instrument = "EUR", "USD", 1.1, 1.5, "EUR_USD"
    o = from_edges([E()])[0]
    assert (o.a, o.b, o.s, o.name) == ("EUR", "USD", 1.5, "EUR_USD") and abs(o.y - math.log(1.1) * 1e4) < 1e-9
    print("from_edges adapter assertion passed.")


if __name__ == "__main__":
    test_hand_solved_triangle()
    test_consistent_world_recovers_latent_up_to_gauge()
    test_fast_path_equals_oracle_on_random_weighted_worlds()
    test_triangle_loo_equals_engine_cycle_residual()
    test_inverse_spread_weight_ratio_in_triangle()
    test_weights_for()
    test_loco()
    test_views_share_differences_and_special_cases()
    test_components_and_disconnected_fit()
    test_from_edges_adapter()
    print("\nAll strengths tests passed.")
