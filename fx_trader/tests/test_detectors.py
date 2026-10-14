# version: 261013
"""
tests/test_detectors.py

Method: hand-built bars (hermetic; no DB, no real outputs). A: threshold on
the largest |LOO|, bridges (None) ignored. B: the same LOO flagged under one
spread scale and not under a 10x wider one (the rollover case), all three
scale kinds, hour-median history with and without enough bars, and the
no-lookahead property of HourSpreadHistory. C: stale by volume, stale by
zero range while others move, thin by typical volume, clean bar.

Run: python3 -m tests.test_detectors
"""

from currency_graph.detectors import (
    A_BPS, B_KINDS, HourSpreadHistory, LegInfo, detector_a, detector_b, detector_c,
)
from currency_graph.strengths import Obs

FX = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]


def _obs(spread=2.0, wide=None):
    out = []
    for p in FX:
        a, b = p.split("_")
        s = spread * (wide[1] if wide and wide[0] == p else 1.0)
        out.append(Obs(a, b, 0.0, s, p))
    return out


def _loo(**vals):
    return [vals.get(p) for p in FX]


def test_detector_a_threshold_and_bridge():
    obs = _obs()
    assert detector_a(obs, _loo(USD_CHF=12.0, GBP_CHF=12.0))["flag"] is True
    r = detector_a(obs, _loo(USD_CHF=-A_BPS, USD_CAD=None))
    assert r["flag"] is False and r["max_abs_bps"] == A_BPS, "strictly greater than the threshold"
    assert detector_a(obs, [None] * 8)["pair"] is None
    print("detector A assertions passed.")


def test_detector_b_scale_changes_the_verdict():
    loo = _loo(USD_CHF=12.0, GBP_CHF=12.0)
    narrow, wide = _obs(2.0), _obs(2.0, ("GBP_CHF", 10.0))
    narrow = [o._replace(s=2.0) for o in narrow]
    assert detector_a(narrow, loo)["flag"] and detector_a(wide, loo)["flag"], "A cannot tell them apart"
    assert detector_b(narrow, loo, "mean_spread")["flag"] is True       # 12 / 2 = 6
    big = [o._replace(s=20.0) for o in narrow]
    assert detector_b(big, loo, "mean_spread")["flag"] is False         # 12 / 20 = 0.6
    arm_n, arm_w = detector_b(narrow, loo, "arm_spread"), detector_b(big, loo, "arm_spread")
    assert arm_n["flag"] and not arm_w["flag"] and arm_n["max_ratio"] > arm_w["max_ratio"]
    assert abs(arm_n["max_ratio"] - 12.0 / 6.0) < 1e-9, "arm scale = two arm legs + hub = 3 x 2.0"
    assert detector_b(narrow, loo, "hour_median", hour_median=2.0)["flag"] is True
    assert detector_b(narrow, loo, "hour_median", hour_median=20.0)["flag"] is False
    assert detector_b(narrow, loo, "hour_median")["flag"] is None
    assert set(B_KINDS) == {"mean_spread", "arm_spread", "hour_median"}
    print("detector B assertions passed (A flags both, B separates them).")


def test_hour_history_no_lookahead_and_min_history():
    h = HourSpreadHistory(window=12)
    assert h.median_before(21) is None
    for i in range(10):
        h.push(21, 5.0 + i)
    assert abs(h.median_before(21) - 9.5) < 1e-12 and h.median_before(3) is None
    before = h.median_before(21)
    h.push(21, 1000.0)
    assert h.median_before(21) != before, "a lookup sees only what was pushed earlier: the push changes the NEXT lookup"
    for i in range(30):
        h.push(21, 7.0)
    assert h.median_before(21) == 7.0, "window of 12 forgets the old values"
    for bad in (lambda: h.push(24, 1.0), lambda: h.push(3, 0.0), lambda: HourSpreadHistory(window=2)):
        try:
            bad()
            raise RuntimeError("expected a rejection")
        except AssertionError:
            pass
    print("hour-history assertions passed.")


def test_detector_c_cases():
    ok = [LegInfo(p, 900, 12.0, 1000.0) for p in FX]
    assert detector_c(ok) == {"flag": False, "stale": [], "thin": []}
    vol = list(ok)
    vol[4] = LegInfo("EUR_USD", 1, 12.0, 1000.0)
    assert detector_c(vol)["stale"] == ["EUR_USD"]
    zero = list(ok)
    zero[6] = LegInfo("USD_CHF", 500, 0.0, 1000.0)
    assert detector_c(zero)["stale"] == ["USD_CHF"], "zero range while others move"
    quiet = [LegInfo(p, 900, 0.0, 1000.0) for p in FX]
    assert detector_c(quiet)["flag"] is False, "zero range everywhere is a quiet market, not a stale leg"
    thin = list(ok)
    thin[3] = LegInfo("GBP_CHF", 24, 12.0, 3000.0)
    r = detector_c(thin)
    assert r["thin"] == ["GBP_CHF"] and r["stale"] == [] and r["flag"] is True
    unknown = [LegInfo(p, None, None, None) for p in FX]
    assert detector_c(unknown)["flag"] is False
    print("detector C assertions passed.")


if __name__ == "__main__":
    test_detector_a_threshold_and_bridge()
    test_detector_b_scale_changes_the_verdict()
    test_hour_history_no_lookahead_and_min_history()
    test_detector_c_cases()
    print("\nAll detectors tests passed.")
