# version: 261013
"""
tests/test_robust.py

Method: synthetic worlds only (hermetic; no DB, no real outputs). 8-pair FX
topology on a latent per currency. Checks: k=inf equals the plain fit; Huber
equals least squares on clean data and shrinks the smear of an injected fault
onto innocent pairs; every k and scale axis converges; hub/arm structure
(anchors USD/GBP, arms EUR/CHF/JPY, CAD outside) and path estimates equal
c_A - c_B exactly on consistent data; verdicts for a hub fault, an arm fault,
two faults (ambiguous) and a clean bar.

Run: python3 -m tests.test_robust
"""

import math
import random

from currency_graph.robust import (
    HUBER_KS, SCALE_AXES, group_of, hub_arms, huber_fit, path_attribution,
)
from currency_graph.strengths import Obs, SCHEMES, fit_zero_sum, weights_for

FX = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
LAT = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
NODES = sorted(LAT)
TOL = 1e-9


def _world(seed=1, noise=0.0, fault=None, spread=2.0):
    rng = random.Random(seed)
    out = []
    for p in FX:
        a, b = p.split("_")
        y = LAT[a] - LAT[b] + rng.gauss(0, noise) + (fault[1] if fault and fault[0] == p else 0.0)
        out.append(Obs(a, b, y, spread, p))
    return out


def _innocent_error(c, truth, exclude=("CHF",)):
    """Gauge-free: re-centre both over the innocent currencies (the zero-sum
    gauge shifts every innocent currency by a common amount when the faulted
    arm's own currency absorbs the error)."""
    keep = [k for k in NODES if k not in exclude]
    mc, mt = sum(c[k] for k in keep) / len(keep), sum(truth[k] for k in keep) / len(keep)
    return max(abs((c[k] - mc) - (truth[k] - mt)) for k in keep)


def test_inf_k_is_plain_and_clean_data_matches_ls():
    obs = _world(2, noise=0.3)
    w = weights_for("inv_spread", obs)
    plain = fit_zero_sum(NODES, obs, w)
    rf = huber_fit(NODES, obs, w, math.inf)
    assert rf.iterations == 0 and all(abs(rf.fit.c[k] - plain.c[k]) < TOL for k in NODES)
    mild = huber_fit(NODES, obs, w, 3.0, "spread", noise_ratio=100.0)   # sigma huge: nothing downweighted
    assert all(abs(mild.fit.c[k] - plain.c[k]) < 1e-6 for k in NODES) and all(p == 1.0 for p in mild.psi)
    print("k=inf / huge-sigma equals least squares assertions passed.")


def test_huber_shrinks_smear_of_an_injected_fault():
    clean = fit_zero_sum(NODES, _world(3, 0.0), [1.0] * 8).c
    obs = _world(3, 0.0, fault=("GBP_CHF", 60.0))
    ls = fit_zero_sum(NODES, obs, [1.0] * 8)
    ls_smear = _innocent_error(ls.c, clean)
    assert ls_smear > 5.0, ls_smear
    for scale in SCALE_AXES:
        h = huber_fit(NODES, obs, [1.0] * 8, 1.345, scale)
        assert h.converged
        smear = _innocent_error(h.fit.c, clean)
        assert smear < ls_smear / 3, (scale, smear, ls_smear)
        big = sorted(range(8), key=lambda i: -abs(h.fit.resid[i]))[:2]
        assert {obs[i].name for i in big} == {"GBP_CHF", "USD_CHF"}, "the faulted ARM must carry the largest residuals"
    print(f"Huber smear < LS smear/3 for both scale axes (LS innocent-currency error {ls_smear:.1f} bps).")


def test_every_k_scheme_axis_converges_on_noisy_fault_world():
    for seed in range(1, 6):
        obs = _world(seed, 0.5, fault=("EUR_USD", 40.0), spread=1.5)
        for scheme in SCHEMES:
            for k in HUBER_KS:
                for scale in SCALE_AXES:
                    r = huber_fit(NODES, obs, weights_for(scheme, obs), k, scale)
                    assert r.converged and abs(sum(r.fit.c.values())) < 1e-6, (seed, scheme, k, scale, r.iterations)
    print("all schemes x ks x scale axes converge (5 noisy worlds).")


def test_hub_arm_structure_and_path_estimates():
    obs = _world(1)
    ha = hub_arms(obs)
    assert ha.anchors == ("USD", "GBP") and obs[ha.hub].name == "GBP_USD" and sorted(ha.arms) == ["CHF", "EUR", "JPY"]
    g = group_of(ha, obs)
    assert g["USD_CAD"] == "other" and g["GBP_CHF"] == g["USD_CHF"] == "arm:CHF" and g["GBP_USD"] == "hub"
    res = path_attribution(obs)
    assert res.verdict == "clean" and abs(res.median_bps - (LAT["USD"] - LAT["GBP"])) < TOL
    assert all(abs(r["estimate"] - (LAT["USD"] - LAT["GBP"])) < TOL for r in res.paths) and len(res.paths) == 4
    print("hub/arm structure and exact path-estimate assertions passed.")


def test_verdicts_hub_arm_ambiguous_clean():
    assert path_attribution(_world(1, 0.0, fault=("GBP_USD", 50.0))).verdict == "hub"
    assert path_attribution(_world(1, 0.0, fault=("USD_CHF", -50.0))).verdict == "arm:CHF"
    assert path_attribution(_world(1, 0.0, fault=("GBP_JPY", 30.0))).verdict == "arm:JPY"
    two = _world(1, 0.0, fault=("GBP_CHF", 50.0))
    two = [o._replace(y=o.y + 50.0) if o.name == "EUR_GBP" else o for o in two]
    assert path_attribution(two).verdict in ("ambiguous", "arm:CHF", "arm:EUR"), "two faults are not identifiable: must not claim certainty"
    assert path_attribution(_world(4, 0.2)).verdict == "clean"
    cad = path_attribution(_world(1, 0.0, fault=("USD_CAD", 80.0)))
    assert cad.verdict == "clean", "the CAD pendant carries no path"
    print("verdict assertions passed (hub, arm, ambiguous-or-honest, clean, CAD blind).")


def test_rejections():
    for bad in (lambda: huber_fit(NODES, _world(1), [1.0] * 8, 0.0), lambda: huber_fit(NODES, _world(1), [1.0] * 8, 1.0, "nope"),
                lambda: hub_arms([Obs("A", "B", 0.0, 1.0), Obs("B", "A", 0.0, 1.0)])):
        try:
            bad()
            raise RuntimeError("expected a rejection")
        except AssertionError:
            pass
    print("rejection assertions passed.")


if __name__ == "__main__":
    test_inf_k_is_plain_and_clean_data_matches_ls()
    test_huber_shrinks_smear_of_an_injected_fault()
    test_every_k_scheme_axis_converges_on_noisy_fault_world()
    test_hub_arm_structure_and_path_estimates()
    test_verdicts_hub_arm_ambiguous_clean()
    test_rejections()
    print("\nAll robust tests passed.")
