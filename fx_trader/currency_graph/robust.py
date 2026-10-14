# version: 261013
"""
currency_graph/robust.py

Robust strengths and fault attribution (chat 14p4). Standard library only;
imports only currency_graph.strengths. Evidence/decisions: CONTEXT_HANDOFF 4i.

1. HUBER IRLS (huber_fit). Plain least squares SMEARS a fault onto innocent
   pairs (4h.3); a Huber M-estimator bounds each pair's influence. Weights
   are base weights (a strengths.SCHEMES scheme) times psi, where
   u = resid / sigma and psi = 1 if |u| <= k else k / |u|. Iterated to
   convergence on the strengths. The returned fit's residuals are the RAW
   residuals under the robust fit: a faulty pair keeps a large residual (that
   is the attribution signal) instead of the smear spreading it around.
   k is a PARAMETER LIST, not one choice (user decision 261012, in the spirit
   of CONFIDENCE_SIZING_DESIGN 5: compute several, let data pick per job):
   HUBER_KS = 1.0, 1.345, 2.0, 3.0 and inf (inf = plain least squares).
   Two noise-scale axes (SCALE_AXES), also run in parallel:
     "spread": sigma_i = noise_ratio * s_i (the pair's own spread)
     "mad":    one sigma for the bar = max(1.4826 * median|resid| over
               non-bridge pairs, MAD_FLOOR_BPS), RE-ESTIMATED every
               iteration. Holding it at the initial plain-fit value fails:
               least squares spreads a fault over many residuals, so that
               MAD is inflated (MEASURED on a 60 bps fault: sigma 8.9 bps).
               Cost: geometric convergence (~0.75 per iteration, MEASURED);
               hence MAX_ITER 200 and CONV_TOL 1e-7 bps
   NOISE_RATIO (0.25 = the audit's provisional 'clean' ratio edge) and
   MAD_FLOOR_BPS are UNVALIDATED starting values; the synthetic experiment
   (run_fault_experiment.py) is how they and k get chosen per job.

2. HUB/ARM MEDIAN-OF-PATHS (path_attribution). The two highest-degree
   currencies are the ANCHORS; the pair joining them is the HUB; every other
   currency quoted against both anchors forms an ARM (two legs in series).
   Each path (hub; each arm) gives an independent estimate of c_A - c_B. The
   median of the path estimates is robust to one bad path. A path is a
   candidate when |estimate - median| / (summed spread of its legs) exceeds
   RATIO_THRESH (1.0 = the audit's suspect edge; provisional). Verdicts:
   clean / hub / arm:<ccy> (exactly one candidate), ambiguous (two or more:
   not identifiable), insufficient (< 3 paths). The two legs of an arm are in
   series and CANNOT be separated; the verdict names the arm. Currencies
   quoted against only one anchor (CAD) carry no path.
"""

import math
import statistics
from collections import Counter, namedtuple

from currency_graph.strengths import fit_zero_sum

HUBER_KS = (1.0, 1.345, 2.0, 3.0, math.inf)
SCALE_AXES = ("spread", "mad")
NOISE_RATIO = 0.25
MAD_FLOOR_BPS = 0.1
MAX_ITER = 200
CONV_TOL = 1e-7
RATIO_THRESH = 1.0
MIN_PATHS = 3
BRIDGE_HAT = 1.0 - 1e-9

RobustFit = namedtuple("RobustFit", "fit k scale iterations converged psi")
HubArms = namedtuple("HubArms", "anchors hub arms")   # hub: obs index or None; arms: {ccy: (i1, s1, i2, s2)}
PathResult = namedtuple("PathResult", "median_bps paths verdict culprit margin")


def _sigmas(fit, obs: list, scale: str, noise_ratio: float) -> list:
    assert scale in SCALE_AXES, f"scale must be one of {SCALE_AXES}"
    assert noise_ratio > 0, "noise_ratio must be positive"
    if scale == "spread":
        if any(not o.s > 0 for o in obs):
            raise ValueError("spread scale needs positive spreads")
        return [noise_ratio * o.s for o in obs]
    pool = [abs(r) for r, h in zip(fit.resid, fit.hat) if h < BRIDGE_HAT]
    sigma = max(1.4826 * statistics.median(pool), MAD_FLOOR_BPS) if pool else MAD_FLOOR_BPS
    return [sigma] * len(obs)


def _psi(resid: list, sigmas: list, k: float) -> list:
    assert len(resid) == len(sigmas), "resid/sigma mismatch"
    assert k > 0, "k must be positive"
    return [1.0 if abs(r) <= k * s else k * s / abs(r) for r, s in zip(resid, sigmas)]


def huber_fit(nodes: list, obs: list, w: list, k: float, scale: str = "spread", noise_ratio: float = NOISE_RATIO) -> RobustFit:
    """Huber IRLS strengths (zero-sum gauge). k = inf returns the plain fit."""
    assert k > 0, "k must be positive"
    assert scale in SCALE_AXES, f"scale must be one of {SCALE_AXES}"
    fit = fit_zero_sum(nodes, obs, w)
    if math.isinf(k):
        return RobustFit(fit, k, scale, 0, True, [1.0] * len(obs))
    psi = [1.0] * len(obs)
    for it in range(1, MAX_ITER + 1):
        psi = _psi(fit.resid, _sigmas(fit, obs, scale, noise_ratio), k)
        new = fit_zero_sum(nodes, obs, [wi * p for wi, p in zip(w, psi)])
        delta = max(abs(new.c[n] - fit.c[n]) for n in nodes)
        fit = new
        if delta < CONV_TOL:
            return RobustFit(fit, k, scale, it, True, psi)
    return RobustFit(fit, k, scale, MAX_ITER, False, psi)


def hub_arms(obs: list) -> HubArms:
    """Anchors, hub observation index and arms, from the pair structure."""
    assert obs, "no observations"
    degree: Counter = Counter()
    index: dict = {}
    for i, o in enumerate(obs):
        key = frozenset((o.a, o.b))
        assert len(key) == 2 and key not in index, f"self-loop or duplicate pair {o.name or (o.a, o.b)}"
        index[key] = i
        degree[o.a] += 1
        degree[o.b] += 1
    assert len(degree) >= 3, "need at least 3 currencies"
    ranked = sorted(degree, key=lambda n: (-degree[n], n))
    anchor_a, anchor_b = ranked[0], ranked[1]
    arms = {}
    for x in sorted(degree):
        i1, i2 = index.get(frozenset((x, anchor_a))), index.get(frozenset((x, anchor_b)))
        if x in (anchor_a, anchor_b) or i1 is None or i2 is None:
            continue
        s1 = 1 if obs[i1].a == anchor_a else -1     # y1 = c_A - c_X when a == A
        s2 = 1 if obs[i2].a == x else -1            # y2 = c_X - c_B when a == X
        arms[x] = (i1, s1, i2, s2)
    return HubArms((anchor_a, anchor_b), index.get(frozenset((anchor_a, anchor_b))), arms)


def group_of(ha: HubArms, obs: list) -> dict:
    """pair name -> 'hub' | 'arm:<ccy>' | 'other' (e.g. the CAD pendant)."""
    assert obs, "no observations"
    out = {o.name: "other" for o in obs}
    if ha.hub is not None:
        out[obs[ha.hub].name] = "hub"
    for x, (i1, _s1, i2, _s2) in ha.arms.items():
        out[obs[i1].name] = out[obs[i2].name] = f"arm:{x}"
    return out


def _path_list(obs: list, ha: HubArms) -> list:
    """[(label, estimate of c_A - c_B, summed leg spread)]."""
    assert obs, "no observations"
    paths = []
    if ha.hub is not None:
        o = obs[ha.hub]
        paths.append(("hub", (1 if o.a == ha.anchors[0] else -1) * o.y, o.s))
    for x, (i1, s1, i2, s2) in ha.arms.items():
        paths.append((f"arm:{x}", s1 * obs[i1].y + s2 * obs[i2].y, obs[i1].s + obs[i2].s))
    return paths


def path_attribution(obs: list, ratio_thresh: float = RATIO_THRESH) -> PathResult:
    assert obs and ratio_thresh > 0, "obs and a positive threshold required"
    paths = _path_list(obs, hub_arms(obs))
    if len(paths) < MIN_PATHS:
        return PathResult(None, [], "insufficient", None, None)
    med = statistics.median(p[1] for p in paths)
    rows = [{"label": lab, "estimate": est, "dev": est - med, "ratio": abs(est - med) / max(sp, 1e-12)} for lab, est, sp in paths]
    cands = sorted((r for r in rows if r["ratio"] > ratio_thresh), key=lambda r: -r["ratio"])
    if not cands:
        return PathResult(med, rows, "clean", None, None)
    margin = None if len(cands) == 1 else cands[0]["ratio"] / cands[1]["ratio"]
    if len(cands) == 1:
        return PathResult(med, rows, cands[0]["label"], cands[0]["label"], None)
    return PathResult(med, rows, "ambiguous", None, margin)
