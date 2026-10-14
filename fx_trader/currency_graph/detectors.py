# version: 261013
"""
currency_graph/detectors.py

Bar-level fault DETECTORS A, B, C (chat 14p4; all kept side by side, user
decision 261012 - no detector is chosen until more venues/sources exist).
Standard library only; imports only currency_graph.robust.hub_arms.
Flags are BAR-level (Strength fault rule, CONTEXT_HANDOFF 4h.4); the named
pair is only the largest contributor and LOO smears, so it is not an
attribution.

A  absolute: max |LOO| (non-bridge pairs) > A_BPS (10 bps, the 14p3
   provisional flag). Hour-confounded: 13 of the 17 flagged real bars sit at
   the 21-22 UTC rollover where spreads are 3-4x wider (CONTEXT_HANDOFF 4i).
B  spread-normalised: max |LOO_i| / scale_i > B_RATIO (1.0, the audit's
   suspect edge; provisional). Three scale definitions, all computed:
     mean_spread  the bar's mean pair spread
     arm_spread   summed spread of the triangle the pair belongs to (its arm
                  legs + hub; for the hub: hub + median arm sum)
     hour_median  trailing median, over the previous B_WINDOW same-hour
                  bars, of the bar's mean pair spread (HourSpreadHistory;
                  the current bar is never in its own history). None until
                  B_MIN_HISTORY bars exist.
C  stale / thin leg: a leg is STALE if volume <= C_STALE_VOL, or its range is
   0 while the other legs' median range >= C_MIN_OTHER_RANGE_BPS; THIN if its
   volume < C_THIN_FRAC x its typical volume. Needed because a frozen quote
   in a quiet market is invisible to LOO (A and B). Volume is assumed to be
   OANDA's per-bar tick count (UNVERIFIED, INV-2).
"""

import statistics
from collections import deque, namedtuple

from currency_graph.robust import group_of, hub_arms

A_BPS = 10.0
B_RATIO = 1.0
B_KINDS = ("mean_spread", "arm_spread", "hour_median")
B_WINDOW = 60
B_MIN_HISTORY = 10
C_STALE_VOL = 2
C_MIN_OTHER_RANGE_BPS = 2.0
C_THIN_FRAC = 0.05
MAX_HOURS = 24

LegInfo = namedtuple("LegInfo", "name volume range_bps typ_volume", defaults=(None,))


class HourSpreadHistory:
    """Trailing same-hour history of the bar mean spread; lookup excludes the current bar."""

    def __init__(self, window: int = B_WINDOW):
        assert window >= B_MIN_HISTORY, "window must be at least B_MIN_HISTORY"
        self.window = window
        self._h = {}

    def median_before(self, hour: int):
        assert 0 <= hour < MAX_HOURS, "hour must be 0..23"
        vals = self._h.get(hour)
        return statistics.median(vals) if vals and len(vals) >= B_MIN_HISTORY else None

    def push(self, hour: int, mean_spread: float) -> None:
        assert 0 <= hour < MAX_HOURS and mean_spread > 0, "bad hour or spread"
        self._h.setdefault(hour, deque(maxlen=self.window)).append(mean_spread)


def _worst(obs: list, loo: list, scales: list) -> tuple:
    assert len(obs) == len(loo) == len(scales), "length mismatch"
    best = (0.0, None, None)     # (ratio, pair, |loo|)
    for o, v, sc in zip(obs, loo, scales):
        if v is not None and abs(v) / sc > best[0]:
            best = (abs(v) / sc, o.name, abs(v))
    return best


def detector_a(obs: list, loo: list, threshold: float = A_BPS) -> dict:
    assert len(obs) == len(loo) and threshold > 0, "bad inputs"
    best = _worst(obs, loo, [1.0] * len(obs))
    return {"flag": best[0] > threshold, "max_abs_bps": best[0], "pair": best[1]}


def _arm_scales(obs: list) -> list:
    ha = hub_arms(obs)
    groups = group_of(ha, obs)
    mean_s = sum(o.s for o in obs) / len(obs)
    hub_s = obs[ha.hub].s if ha.hub is not None else 0.0
    arm_sum = {f"arm:{x}": obs[i1].s + obs[i2].s for x, (i1, _a, i2, _b) in ha.arms.items()}
    typical_arm = statistics.median(arm_sum.values()) if arm_sum else 2 * mean_s
    out = []
    for o in obs:
        g = groups[o.name]
        out.append(hub_s + arm_sum[g] if g in arm_sum else hub_s + typical_arm if g == "hub" else 3 * mean_s)
    return out


def detector_b(obs: list, loo: list, kind: str, hour_median=None, threshold: float = B_RATIO) -> dict:
    assert kind in B_KINDS, f"kind must be one of {B_KINDS}"
    assert len(obs) == len(loo) and threshold > 0, "bad inputs"
    mean_s = sum(o.s for o in obs) / len(obs)
    if kind == "hour_median" and hour_median is None:
        return {"kind": kind, "flag": None, "max_ratio": None, "pair": None, "note": "insufficient same-hour history"}
    scales = {"mean_spread": [mean_s] * len(obs), "hour_median": [hour_median] * len(obs)}.get(kind) or _arm_scales(obs)
    best = _worst(obs, loo, scales)
    return {"kind": kind, "flag": best[0] > threshold, "max_ratio": best[0], "pair": best[1]}


def detector_c(legs: list) -> dict:
    assert legs, "no legs"
    stale, thin = [], []
    for i, leg in enumerate(legs):
        others = [x.range_bps for j, x in enumerate(legs) if j != i and x.range_bps is not None]
        med_other = statistics.median(others) if others else 0.0
        is_stale = (leg.volume is not None and leg.volume <= C_STALE_VOL) or (
            leg.range_bps is not None and leg.range_bps == 0 and med_other >= C_MIN_OTHER_RANGE_BPS)
        if is_stale:
            stale.append(leg.name)
        elif leg.volume is not None and leg.typ_volume and leg.volume < C_THIN_FRAC * leg.typ_volume:
            thin.append(leg.name)
    return {"flag": bool(stale or thin), "stale": stale, "thin": thin}
