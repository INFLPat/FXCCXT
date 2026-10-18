# version: 261016
"""
run_world_experiment.py  (fx_trader/run_world_experiment.py)

Chat 14p4-iii. Fault-injection experiments in several WORLDS, REPORT ONLY: no
detector, threshold, k, scheme, mask pad or min-run is chosen (CONTEXT_HANDOFF
4j.6). Everything is evaluated in one pass and stored so later chats re-slice.
Read CONTEXT_HANDOFF 4i, 4j (esp. 4j.5 block caveats) before interpreting.

WORLDS (--worlds)
  parametric_model  run_fault_experiment's world, unchanged (ModelSpreads)
  parametric_db     the same with DbSpreads (needs --sandbox-db); regression target p4db
  empirical         PRIMARY: each real ref bar (a1 dump) rebuilt as residual = z x s x
                    sqrt(1-h) on synthetic latent levels, then faulted. Detector rates are
                    exact (LOO depends only on the residuals); STRENGTH-ERROR numbers
                    understate real error (only the 3-dof cycle space is real). USD_CAD
                    (bridge) residual is 0 by construction.
  cal_{t,mix}_{prop,abs}  calibrated-parametric, FOUR variants kept side by side: Gaussian-
                    copula spreads (marginals from the dump, per class), noise tails Student-t
                    (df) or per-bar scale mixture (sigma), noise proportional to spread ('prop')
                    or absolute per pair ('abs'). The tail parameter is chosen per class by
                    simulating the residual z through the real hat matrix and matching the
                    pooled p99 / MAD-sigma; the whole table is stored. Volume ratio and range
                    are sampled per pair independently (not correlated with spread: a stated gap).

SCENARIO SETS (--sets)
  core     clean, missing, stale E{5,15,40,100} x signature{s1 normal spread, s2 quiet spread},
           wide m{4,10,25} x error ratio{0.2,0.5,1.0}                      (all worlds)
  family   detector-(c) family: volume{1,2,5,20} x range x{1,2,4} x spread x{2,4,10,25} x
           price error E{0,5,15,40}  (192; heavy worlds)
  double   two simultaneous faults on one bar: 9 specs (stale E 5/15/40/100; wide m10 ratio
           .2/.5/1; m4 .5; m25 .5), all 45 unordered pairs x placements same_arm_add,
           same_arm_cancel, diff_arm, hub_arm (180; heavy worlds). Path sign relation is
           enforced (half-normal x sign for wide errors in two-fault cells).
  adjacent faults at t and t+k (k 1,2,3,6,12) inside one ref stretch; empirical only.
  blocks   FALSE-RUN rates on clean real bars, block lengths 1,3,6,12,24,48 + mixed, for the
           'ref' and 'all' populations, with segment-bootstrap CIs. Empirical only. Per-bar
           flags are cached, so this costs seconds.

STRATA: every trial updates groups all / class:{flat,rollover_ny,adjacent_ny,other} /
sd:{0-9 mean-spread decile of the real ref bars} / hc:{UTC hour|DST cell} (core only).
200 log-spaced histogram bins (1e-3..1e3) of max|LOO| and the three B ratios for all/class/sd
groups; grid flag counts for every group. Flag-rate CIs: cluster bootstrap over ref stretches
(empirical) or Wilson (synthetic). Tier 2 (27-method Huber grid, attribution, scheme gap) runs on a
reservoir sample per (scenario, class) and is written to a capped JSONL.

NOT covered: run-level view (14p4-iv), INV-21/22 (14p4c), numpy (14p5), crypto. Volume is assumed
to be OANDA's tick count (UNVERIFIED). Block results cite CONTEXT_HANDOFF 4j.5.

Run (from fx_trader/):
  python3 run_world_experiment.py --chat 14 --tag w1 --bars reports/calibration_bars_chat14_261015_a1.jsonl \
      [--a1-json reports/calibration_stats_chat14_261015_a1.json] [--sandbox-db data/sandbox_22Q1to26Q1.db] \
      [--regress-p4db reports/fault_experiment_chat14_261014_p4db.json]
Output (refuses to overwrite): reports/world_experiment_chat<N>_<run date>[_<tag>]_{meta,<world>}.json
and reports/world_trials_chat<N>_<run date>[_<tag>].jsonl (gitignored, local).
"""

import argparse
import bisect
import json
import math
import random
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import run_fault_experiment as fe
from currency_graph.detectors import A_BPS, B_KINDS, B_RATIO, HourSpreadHistory, LegInfo, detector_a, detector_b
from currency_graph.robust import group_of, hub_arms, huber_fit, path_attribution
from currency_graph.strengths import Obs, fit_zero_sum, loo_fast, select_component, weights_for
from run_calibration_stats import (
    A_GRID, B_GRID, BLOCK_LENGTHS, C_STALE_GRID, C_THIN_GRID, MIN_GROUP, bar_classes, c_components, c_key, c_prep, mad_sigma,
)
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import stored_ts_to_epoch

HOUR = 3600
PAIRS = fe.PAIRS
NODES = sorted(fe.LAT_BPS)
TOPO = [Obs(p[:3], p[4:], 0.0, 1.0, p) for p in PAIRS]
HA8 = hub_arms(TOPO)
GROUPS8 = group_of(HA8, TOPO)
PIDX = {p: i for i, p in enumerate(PAIRS)}
HAT8 = fit_zero_sum(NODES, TOPO, [1.0] * len(TOPO)).hat
ARMS = sorted(HA8.arms)
HBINS, HLO, HHI = 200, -3.0, 3.0
RUN_GAP = 3
MAX_ROWS = 200_000
MIN_FIT = 200
CKEYS = [(c_key(s, f), s, f) for s in C_STALE_GRID for f in C_THIN_GRID]
C_DEFAULT_KEY = c_key(2, 0.05)
DET_NAMES = ("A", "B_mean_spread", "B_arm_spread", "B_hour_median", "C")
CLASSES = ("flat", "rollover_ny", "adjacent_ny", "other")
DF_GRID = (1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 10.0, math.inf)
MIX_GRID = (0.25, 0.5, 0.75, 1.0, 1.5)
WORLDS_ALL = ("parametric_model", "parametric_db", "empirical", "cal_t_prop", "cal_t_abs", "cal_mix_prop", "cal_mix_abs")
SETS_ALL = ("core", "family", "double", "adjacent", "blocks")
STALE_E = (5.0, 15.0, 40.0, 100.0)
WIDE_M = (4.0, 10.0, 25.0)
WIDE_R = (0.2, 0.5, 1.0)
FAM_VOL, FAM_RNG, FAM_SPM, FAM_E = (1, 2, 5, 20), (1.0, 2.0, 4.0), (2.0, 4.0, 10.0, 25.0), (0.0, 5.0, 15.0, 40.0)
DOUBLE_SPECS = ([("stale", {"E": e, "sig": "s1"}) for e in STALE_E] + [("wide", {"m": 10.0, "ratio": r}) for r in WIDE_R]
                + [("wide", {"m": 4.0, "ratio": 0.5}), ("wide", {"m": 25.0, "ratio": 0.5})])
PLACEMENTS = ("same_arm_add", "same_arm_cancel", "diff_arm", "hub_arm")
ADJ_TYPES = (("stale40", "stale40"), ("stale15", "wide10"), ("wide10", "wide10"))
ADJ_REL = ("same_pair", "same_arm_other_leg", "diff_arm")
ADJ_K = (1, 2, 3, 6, 12)
LIMITATIONS = [
    "REPORT ONLY: nothing is chosen. Every number is conditional on the world that produced it.",
    "Empirical world: strength-error numbers understate real error (only the cycle space is real); USD_CAD residual is 0; latent levels are synthetic.",
    "Calibrated worlds: tail family/parameter fitted by matching pooled p99/MAD-sigma of z through the real hat matrix; volume ratio and range are drawn independently of spread; B_hour scale uses the class median of real hour-medians.",
    "Wide-error ratio and fault signatures cannot be calibrated (no truth labels): they are swept.",
    "Two-fault wide errors are half-normal x sign (to enforce the path-sign relation); single-fault wide errors are symmetric normal as in run_fault_experiment.",
    "Block results cite CONTEXT_HANDOFF 4j.5: no blocks across gaps/degraded bars, bars next to gaps under-represented in long blocks, overlapping blocks need block-aware CIs, the 'ref' population excludes the masked runs (circular for A and B).",
    "Tier 2 (Huber grid) runs on a reservoir sample per (scenario, class), not on every trial.",
    "Volume is assumed to be OANDA's tick count (UNVERIFIED).",
]
CTX: dict = {}


# ------------------------------------------------------------------ helpers
def primary_class(hour: int, cell: str) -> str:
    cl = bar_classes(hour, cell)
    for c in ("rollover_ny", "adjacent_ny", "flat"):
        if c in cl:
            return c
    return "other"


def hbin(v: float) -> int:
    assert v == v, "NaN statistic"
    if v <= 10 ** HLO:
        return 0
    return min(max(int((math.log10(v) - HLO) * HBINS / (HHI - HLO)), 0), HBINS - 1)


def wilson(k: int, n: int) -> list:
    assert 0 <= k <= n, "bad counts"
    if n == 0:
        return [None, None]
    z, p = 1.96, k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, mid - half), min(1.0, mid + half)]


def resid_matrix() -> list:
    """R with resid = R @ eps for the unweighted topology fit."""
    cols = []
    for j in range(len(TOPO)):
        obs = [Obs(o.a, o.b, 1.0 if i == j else 0.0, 1.0, o.name) for i, o in enumerate(TOPO)]
        cols.append(fit_zero_sum(NODES, obs, [1.0] * len(obs)).resid)
    return [[cols[j][i] for j in range(len(cols))] for i in range(len(cols))]


RMAT = resid_matrix()


def cholesky(m: list) -> list:
    n = len(m)
    assert n > 0 and all(len(r) == n for r in m), "square matrix required"
    for jitter in (0.0, 1e-9, 1e-6, 1e-4):
        a = [[m[i][j] + (jitter if i == j else 0.0) for j in range(n)] for i in range(n)]
        low = [[0.0] * n for _ in range(n)]
        ok = True
        for i in range(n):
            for j in range(i + 1):
                s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
                if i == j:
                    if s <= 0:
                        ok = False
                        break
                    low[i][j] = math.sqrt(s)
                else:
                    low[i][j] = s / low[j][j]
            if not ok:
                break
        if ok:
            return low
    raise ValueError("matrix is not positive definite even with jitter")


def rank_groups(names: list, scores: list) -> list:
    assert len(names) == len(scores), "names/scores mismatch"
    best: dict = {}
    for n, v in zip(names, scores):
        if v is not None:
            g = GROUPS8.get(n, "other")
            best[g] = max(best.get(g, 0.0), abs(v))
    return sorted(best, key=lambda g: -best[g])


# ------------------------------------------------------------------ bars
def build_bar(obs: list, vol: list, rg: list, typ: list, med, lat: dict, hour: int, cell: str, epoch, cluster) -> dict:
    assert len(obs) == len(vol) == len(rg) == len(typ), "bar columns must align"
    ms = sum(o.s for o in obs) / len(obs)
    return {"obs": obs, "vol": vol, "rng": rg, "typ": typ, "med": med, "lat": lat, "hour": hour, "cell": cell, "epoch": epoch,
            "cluster": cluster, "ms": ms, "cls": primary_class(hour, cell), "dec": bisect.bisect(CTX["cuts"], ms)}


def load_dump(path: str) -> list:
    """a1 bar dump -> rows with epoch, class, residual vector, hour-median, ref/all stretch ids."""
    assert Path(path).exists(), f"bar dump not found: {path}"
    rows, hist = [], HourSpreadHistory()
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
        r = json.loads(line)
        assert len(rows) < MAX_ROWS, "row ceiling exceeded"
        r["epoch"] = stored_ts_to_epoch(r["t"])
        r["ms"] = sum(r["spread"]) / len(r["spread"])
        r["med"] = hist.median_before(r["hour"])
        hist.push(r["hour"], r["ms"])
        r["cls"] = primary_class(r["hour"], r["cell"])
        r["res"] = [0.0 if z is None else z * s * math.sqrt(1 - HAT8[i]) for i, (z, s) in enumerate(zip(r["z"], r["spread"]))]
        rows.append(r)
    assert rows, "empty bar dump"
    prev_all = prev_ref = None
    sa = sr = -1
    for r in rows:
        contiguous = prev_all is not None and r["epoch"] - prev_all["epoch"] == HOUR
        if not contiguous:
            sa += 1
        r["aclu"] = sa
        if r["masked"]:
            r["rclu"] = None
        else:
            if not (prev_ref is not None and contiguous and prev_all is prev_ref):
                sr += 1
            r["rclu"] = sr
            prev_ref = r
        prev_all = r
    return rows


def setup_ctx(rows: list, boot: int, seed: int) -> None:
    ref = [r for r in rows if not r["masked"]]
    assert len(ref) >= MIN_GROUP, "too few ref bars"
    ms = sorted(r["ms"] for r in ref)
    CTX["cuts"] = [ms[int(len(ms) * q / 10)] for q in range(1, 10)]
    flat = [r for r in ref if r["cls"] == "flat"] or ref
    CTX["quiet"] = {p: statistics.median(r["spread"][i] for r in flat) for i, p in enumerate(PAIRS)}
    CTX["trng"] = {p: max(statistics.median(r["rng"][i] for r in flat), 1.0) for i, p in enumerate(PAIRS)}
    ncl = 1 + max(r["rclu"] for r in ref)
    rng = random.Random(f"{seed}|boot")
    CTX["ncl"] = ncl
    CTX["boot"] = [[rng.randrange(ncl) for _ in range(ncl)] for _ in range(boot)]


def emp_bar(row: dict, rng: random.Random) -> dict:
    lat = {k: v + rng.gauss(0, fe.LEVEL_SD_BPS) for k, v in fe.LAT_BPS.items()}
    obs = [Obs(o.a, o.b, lat[o.a] - lat[o.b] + row["res"][i], row["spread"][i], o.name) for i, o in enumerate(TOPO)]
    return build_bar(obs, list(row["vol"]), list(row["rng"]), list(row["typ"]), row["med"], lat, row["hour"], row["cell"], row["epoch"], row["rclu"])


# ------------------------------------------------------------------ worlds
class ParamWorld:
    synthetic = True

    def __init__(self, name: str, spreads):
        self.name, self.spreads = name, spreads

    def n_trials(self, set_name: str, cfg: dict) -> int:
        return cfg["sim_trials"] if set_name == "core" else cfg["family_sim"]

    def bar(self, rng, i: int, set_name: str, cfg: dict) -> dict:
        profile = fe.PROFILES[i % 2]
        obs, vol, rg, lat = fe.make_bar(rng, self.spreads, profile)
        hour, cell = (21, "both_summer") if profile == "rollover" else (12, "both_winter")
        return build_bar(obs, vol, rg, [fe.TYP_VOLUME] * len(obs), self.spreads.typical_mean(profile), lat, hour, cell, None, None)


class EmpWorld:
    synthetic = False
    name = "empirical"

    def __init__(self, rows: list):
        self.ref = [r for r in rows if not r["masked"]]

    def n_trials(self, set_name: str, cfg: dict) -> int:
        return -(-len(self.ref) // cfg["stride"][set_name])

    def bar(self, rng, i: int, set_name: str, cfg: dict) -> dict:
        return emp_bar(self.ref[(i * cfg["stride"][set_name]) % len(self.ref)], rng)


def _z_pool(rows: list) -> list:
    return [z for r in rows for z in r["z"] if z is not None]


def _sim_z(rng, spreads: list, base_kind: str, sig: list, family: str, param: float, n: int) -> list:
    """Simulated pooled z through the real hat matrix at scale 1."""
    out = []
    for _ in range(n):
        s = spreads[rng.randrange(len(spreads))]
        t = draw_noise(rng, family, param)
        eps = [(s[j] if base_kind == "prop" else sig[j]) * t[j] for j in range(len(s))]
        for i in range(len(s) - 1):
            out.append(sum(RMAT[i][j] * eps[j] for j in range(len(s))) / (s[i] * math.sqrt(1 - HAT8[i])))
    return out


def draw_noise(rng, family: str, param: float) -> list:
    n = len(PAIRS)
    if family == "t":
        if math.isinf(param):
            return [rng.gauss(0, 1) for _ in range(n)]
        return [rng.gauss(0, 1) / math.sqrt(rng.gammavariate(param / 2, 2) / param) for _ in range(n)]
    assert family == "mix", f"unknown tail family {family}"
    m = math.exp(param * rng.gauss(0, 1))
    return [m * rng.gauss(0, 1) for _ in range(n)]


def fit_class(rows: list, rng, sims: int) -> dict:
    """Per-class calibration: copula, marginals, noise tail table for both families x both variants."""
    assert len(rows) >= MIN_GROUP, "class too small to fit"
    n = len(PAIRS)
    srt = [sorted(r["spread"][i] for r in rows) for i in range(n)]
    nd = statistics.NormalDist()
    scores = []
    for i in range(n):
        order = sorted(range(len(rows)), key=lambda k: rows[k]["spread"][i])
        col = [0.0] * len(rows)
        for rank, k in enumerate(order):
            col[k] = nd.inv_cdf((rank + 0.5) / len(rows))
        scores.append(col)
    corr = [[1.0 if i == j else statistics.correlation(scores[i], scores[j]) for j in range(n)] for i in range(n)]
    zs = _z_pool(rows)
    mad = mad_sigma(zs)
    p99 = sorted(abs(z) for z in zs)[int(0.99 * (len(zs) - 1))]
    real_ratio = p99 / mad
    sig_abs = []
    for i in range(n):
        res = [r["res"][i] for r in rows]
        sig_abs.append(mad_sigma(res) / math.sqrt(1 - HAT8[i]) if HAT8[i] < 1 - 1e-9 else 0.0)
    spreads = [r["spread"] for r in rows[:: max(1, len(rows) // 3000)]]
    table, chosen = [], {}
    for family, grid in (("t", DF_GRID), ("mix", MIX_GRID)):
        for variant in ("prop", "abs"):
            best = None
            for param in grid:
                z = _sim_z(rng, spreads, variant, sig_abs, family, param, sims)
                s0 = mad_sigma(z)
                ratio = sorted(abs(v) for v in z)[int(0.99 * (len(z) - 1))] / s0
                entry = {"family": family, "variant": variant, "param": param if not math.isinf(param) else "inf",
                         "sim_p99_over_mad": ratio, "scale": mad / s0}
                table.append(entry)
                dist = abs(math.log(ratio) - math.log(real_ratio))
                if best is None or dist < best[0]:
                    best = (dist, param, mad / s0)
            chosen[f"{family}_{variant}"] = {"param": best[1], "scale": best[2]}
    vr = [[r["vol"][i] / r["typ"][i] for r in rows if r["vol"][i] is not None and r["typ"][i]] or [1.0] for i in range(n)]
    return {"n": len(rows), "sorted_spread": srt, "chol": cholesky(corr), "corr": corr, "real_mad_z": mad, "real_p99_over_mad": real_ratio,
            "sig_abs": sig_abs, "vr": vr, "rg": [[r["rng"][i] for r in rows] for i in range(n)],
            "typ": [statistics.median(r["typ"][i] or fe.TYP_VOLUME for r in rows) for i in range(n)],
            "med": statistics.median([r["med"] for r in rows if r["med"] is not None] or [statistics.median(r["ms"] for r in rows)]),
            "table": table, "chosen": chosen}


class CalWorld:
    synthetic = True

    def __init__(self, family: str, variant: str, ref: list, fits: dict):
        self.name, self.family, self.variant, self.ref, self.fits = f"cal_{family}_{variant}", family, variant, ref, fits
        self.nd = statistics.NormalDist()

    def n_trials(self, set_name: str, cfg: dict) -> int:
        return cfg["sim_trials"] if set_name == "core" else cfg["family_sim"]

    def bar(self, rng, i: int, set_name: str, cfg: dict) -> dict:
        tmpl = self.ref[rng.randrange(len(self.ref))]
        f = self.fits.get(tmpl["cls"]) or self.fits["__all__"]
        n = len(PAIRS)
        g = [rng.gauss(0, 1) for _ in range(n)]
        x = [sum(f["chol"][r][k] * g[k] for k in range(r + 1)) for r in range(n)]
        spread = [f["sorted_spread"][r][min(len(f["sorted_spread"][r]) - 1, int(self.nd.cdf(x[r]) * len(f["sorted_spread"][r])))] for r in range(n)]
        ch = f["chosen"][f"{self.family}_{self.variant}"]
        t = draw_noise(rng, self.family, ch["param"])
        base = spread if self.variant == "prop" else f["sig_abs"]
        lat = {k: v + rng.gauss(0, fe.LEVEL_SD_BPS) for k, v in fe.LAT_BPS.items()}
        obs = [Obs(o.a, o.b, lat[o.a] - lat[o.b] + ch["scale"] * base[j] * t[j], spread[j], o.name) for j, o in enumerate(TOPO)]
        vol = [max(0, int(rng.choice(f["vr"][j]) * f["typ"][j])) for j in range(n)]
        rg = [rng.choice(f["rg"][j]) for j in range(n)]
        return build_bar(obs, vol, rg, list(f["typ"]), f["med"], lat, tmpl["hour"], tmpl["cell"], None, None)


# ------------------------------------------------------------------ faults
def apply_fault(rng, obs: list, vol: list, rg: list, f: dict, idx: int, sign) -> None:
    """In-place fault on leg idx. sign None = random (single-fault semantics identical to fe.inject)."""
    assert 0 <= idx < len(obs), "bad leg index"
    o, kind = obs[idx], f["kind"]
    if kind in ("stale", "famc"):
        sg = rng.choice((-1.0, 1.0)) if sign is None else sign
        if kind == "stale":
            s = min(o.s, CTX["quiet"][o.name]) if f["sig"] == "s2" else o.s
            obs[idx], vol[idx], rg[idx] = o._replace(y=o.y + sg * f["E"], s=s), 1, 0.0
        else:
            obs[idx], vol[idx], rg[idx] = o._replace(y=o.y + sg * f["E"], s=o.s * f["spm"]), f["vol"], f["rngm"] * CTX["trng"][o.name]
        return
    assert kind == "wide", f"unknown fault kind {kind}"
    s = o.s * f["m"]
    err = rng.gauss(0, f["ratio"] * s) if sign is None else sign * abs(rng.gauss(0, f["ratio"] * s))
    obs[idx], vol[idx] = o._replace(y=o.y + err, s=s), max(3, int(vol[idx] * 0.1))


def fault_of(kind: str, p: dict) -> dict:
    return {"kind": kind, **p}


def pick_legs(rng, place: str) -> tuple:
    """(legs, signs) for a two-fault placement; same_arm signs enforce the path-sign relation."""
    assert place in PLACEMENTS, f"unknown placement {place}"
    flip = lambda: rng.choice((-1.0, 1.0))
    if place.startswith("same_arm"):
        x = ARMS[rng.randrange(len(ARMS))]
        i1, s1, i2, s2 = HA8.arms[x]
        u, rel = flip(), (1.0 if place.endswith("add") else -1.0)
        return [i1, i2], [u * s1, rel * u * s2]
    if place == "diff_arm":
        x, y = rng.sample(ARMS, 2)
        return [HA8.arms[x][rng.choice((0, 2))], HA8.arms[y][rng.choice((0, 2))]], [flip(), flip()]
    x = ARMS[rng.randrange(len(ARMS))]
    return [HA8.hub, HA8.arms[x][rng.choice((0, 2))]], [flip(), flip()]


def groups_and_excl(legs: list) -> tuple:
    tg = {GROUPS8[PAIRS[i]] for i in legs}
    return tg, {g.split(":")[1] for g in tg if g.startswith("arm:")}


def inject_spec(rng, bar: dict, spec: dict) -> tuple:
    """-> (new bar, fault descriptions, true groups, excluded currencies)."""
    obs, vol, rg = list(bar["obs"]), list(bar["vol"]), list(bar["rng"])
    t = spec["type"]
    nb = dict(bar)
    if t == "clean":
        return bar, [], set(), set()
    if t == "missing":
        i = PIDX[fe.FAULT_PAIRS[rng.randrange(len(fe.FAULT_PAIRS))]]
        del obs[i], vol[i], rg[i]
        nb.update(obs=obs, vol=vol, rng=rg, typ=bar["typ"][:i] + bar["typ"][i + 1:])
        return nb, [{"pair": PAIRS[i], "kind": "missing"}], set(), set()
    if t in ("single", "family"):
        i = PIDX[fe.FAULT_PAIRS[rng.randrange(len(fe.FAULT_PAIRS))]]
        apply_fault(rng, obs, vol, rg, spec["fault"], i, None)
        nb.update(obs=obs, vol=vol, rng=rg)
        tg, ex = groups_and_excl([i])
        return nb, [{"pair": PAIRS[i], **spec["fault"]}], tg, ex
    assert t == "double", f"unknown spec type {t}"
    legs, signs = pick_legs(rng, spec["place"])
    for leg, sg, f in zip(legs, signs, (spec["a"], spec["b"])):
        apply_fault(rng, obs, vol, rg, f, leg, sg)
    nb.update(obs=obs, vol=vol, rng=rg)
    tg, ex = groups_and_excl(legs)
    return nb, [{"pair": PAIRS[l], **f} for l, f in zip(legs, (spec["a"], spec["b"]))], tg, ex


# --------------------------------------------------------------- scenarios
def fname(f: dict) -> str:
    if f["kind"] == "stale":
        return f"stale{f['E']:g}{f['sig']}"
    if f["kind"] == "wide":
        return f"wide{f['m']:g}r{f['ratio']:g}"
    return f"v{f['vol']}r{f['rngm']:g}m{f['spm']:g}E{f['E']:g}"


def core_scenarios() -> list:
    out = [("clean", {"type": "clean"}), ("missing", {"type": "missing"})]
    for e in STALE_E:
        for sig in ("s1", "s2"):
            f = fault_of("stale", {"E": e, "sig": sig})
            out.append((fname(f), {"type": "single", "fault": f}))
    for m in WIDE_M:
        for r in WIDE_R:
            f = fault_of("wide", {"m": m, "ratio": r})
            out.append((fname(f), {"type": "single", "fault": f}))
    return out


def family_scenarios() -> list:
    out = []
    for v in FAM_VOL:
        for rm in FAM_RNG:
            for sm in FAM_SPM:
                for e in FAM_E:
                    f = fault_of("famc", {"vol": v, "rngm": rm, "spm": sm, "E": e})
                    out.append((fname(f), {"type": "family", "fault": f}))
    return out


def double_scenarios() -> list:
    out, specs = [], [fault_of(k, p) for k, p in DOUBLE_SPECS]
    for i, a in enumerate(specs):
        for b in specs[i:]:
            for place in PLACEMENTS:
                out.append((f"{place}|{fname(a)}+{fname(b)}", {"type": "double", "place": place, "a": a, "b": b}))
    return out


# --------------------------------------------------------------- analysis
def analyse(bar: dict) -> dict:
    obs = bar["obs"]
    main, sub = select_component(NODES, obs)
    assert len(sub) == len(obs), "every leg must belong to the main component"
    fit = huber_fit(main, sub, [1.0] * len(sub), math.inf).fit
    loo = loo_fast(fit)
    a = detector_a(sub, loo)["max_abs_bps"]
    b = {k: detector_b(sub, loo, k, hour_median=bar["med"])["max_ratio"] for k in B_KINDS}
    prep = c_prep([LegInfo(o.name, v, r, t) for o, v, r, t in zip(sub, bar["vol"], bar["rng"], bar["typ"])])
    names = [o.name for o in sub]
    full = len(sub) == len(PAIRS)
    return {"A": a, "B": b, "C": {ck: c_components(prep, s, f) for ck, s, f in CKEYS}, "main": main, "sub": sub, "loo": loo, "names": names,
            "verdict": path_attribution(sub).verdict if full else None, "rank": rank_groups(names, loo)}


def det_flags(st: dict) -> list:
    return [st["A"] > A_BPS] + [st["B"][k] is not None and st["B"][k] > B_RATIO for k in B_KINDS] + [any(st["C"][C_DEFAULT_KEY])]


class Acc:
    def __init__(self, hist: bool):
        self.n, self.A = 0, [0] * len(A_GRID)
        self.B = {k: [0] * len(B_GRID) for k in B_KINDS}
        self.Bn = {k: 0 for k in B_KINDS}
        self.C = {ck: [0, 0, 0, 0] for ck, _s, _f in CKEYS}
        self.hist = {n: Counter() for n in ("A",) + B_KINDS} if hist else None
        self.att, self.verd = [0, 0, 0], Counter()

    def add(self, st: dict, att, cat) -> None:
        self.n += 1
        a = st["A"]
        for j, t in enumerate(A_GRID):
            self.A[j] += a > t
        for k in B_KINDS:
            v = st["B"][k]
            if v is None:
                continue
            self.Bn[k] += 1
            for j, t in enumerate(B_GRID):
                self.B[k][j] += v > t
        for ck, comp in st["C"].items():
            c = self.C[ck]
            c[0] += any(comp)
            c[1] += comp[0]
            c[2] += comp[1]
            c[3] += comp[2]
        if self.hist is not None:
            self.hist["A"][hbin(a)] += 1
            for k in B_KINDS:
                if st["B"][k] is not None:
                    self.hist[k][hbin(st["B"][k])] += 1
        if att is not None:
            self.att[0] += 1
            self.att[1] += att[0]
            self.att[2] += att[1]
        if cat:
            self.verd[cat[0]] += 1
            self.verd[cat[1]] += 1

    def out(self) -> dict:
        d = {"n": self.n, "A": self.A, "B": self.B, "Bn": self.Bn, "C": self.C, "att": self.att, "verdict": dict(self.verd)}
        if self.hist is not None:
            d["hist"] = {k: sorted([b, c] for b, c in v.items()) for k, v in self.hist.items()}
        return d


def verdict_cats(st: dict, tg: set) -> tuple:
    v = st["verdict"]
    if v is None or not tg:
        return None
    base = "clean" if v == "clean" else "ambiguous" if v == "ambiguous" else "insufficient" if v == "insufficient" else ("hit" if v in tg else "wrong")
    return (base, f"raw:{v}")


def group_keys(bar: dict, with_hc: bool) -> list:
    keys = ["all", f"class:{bar['cls']}", f"sd:{bar['dec']}"]
    return keys + ([f"hc:{bar['hour']:02d}|{bar['cell']}"] if with_hc else [])


def cluster_ci(clusters: dict) -> dict:
    """Cluster bootstrap (empirical) over ref stretches for the 5 default-flag rates."""
    ncl = CTX["ncl"]
    n = [clusters.get(i, [0] * 6)[0] for i in range(ncl)]
    f = [[clusters.get(i, [0] * 6)[j + 1] for i in range(ncl)] for j in range(len(DET_NAMES))]
    tot = sum(n)
    out = {}
    for j, name in enumerate(DET_NAMES):
        rates = []
        for idx in CTX["boot"]:
            den = sum(n[i] for i in idx)
            rates.append(sum(f[j][i] for i in idx) / den if den else 0.0)
        rates.sort()
        out[name] = {"rate": sum(f[j]) / tot if tot else None, "ci95": [rates[int(0.025 * (len(rates) - 1))], rates[int(0.975 * (len(rates) - 1))]], "method": "cluster_bootstrap_ref_stretches"}
    return out


class T2Writer:
    def __init__(self, path: str, cap_bytes: int):
        self.f, self.cap, self.bytes, self.lines, self.dropped = open(path, "w", encoding="utf-8"), cap_bytes, 0, 0, 0

    def write(self, rec: dict) -> bool:
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        if self.bytes + len(line) > self.cap:
            self.dropped += 1
            return False
        self.f.write(line)
        self.bytes += len(line)
        self.lines += 1
        return True

    def close(self) -> None:
        self.f.close()


METHODS = fe.method_specs()


def tier2(bar: dict, st: dict, tg: set, excl: set) -> dict:
    main, sub, names = st["main"], st["sub"], st["names"]
    errs, h1, h2, fits = [], [], [], {}
    for name, scheme, k, scale in METHODS:
        r = huber_fit(main, sub, weights_for(scheme, sub), k, scale)
        fits[name] = r.fit
        errs.append(round(fe.innocent_error(r.fit.c, bar["lat"], main, excl), 3))
        rank = rank_groups(names, loo_fast(r.fit) if math.isinf(k) else r.fit.resid)
        h1.append(int(bool(rank) and rank[0] in tg) if tg else 0)
        h2.append(int(bool(tg) and set(rank[:len(tg)]) == tg))
    gap = [round(max(abs(fits[f"{s}|LS"].c[n] - fits["unweighted|LS"].c[n]) for n in main), 3) for s in ("inv_spread", "inv_spread2")]
    return {"err": errs, "h1": "".join(map(str, h1)), "h2": "".join(map(str, h2)), "gap": gap}


def run_scenario(world, sid: str, spec: dict, set_name: str, cfg: dict, t2: T2Writer, cap: int) -> dict:
    rng = random.Random(f"{cfg['seed']}|{world.name}|{sid}")
    n = world.n_trials(set_name, cfg)
    with_hc = set_name == "core"
    groups, clusters, res, seen, dcount = {}, {}, {c: [] for c in CLASSES}, Counter(), [0] * len(DET_NAMES)
    for i in range(n):
        bar = world.bar(rng, i, set_name, cfg)
        fb, faults, tg, excl = inject_spec(rng, bar, spec)
        st = analyse(fb)
        att = None
        if tg:
            rk = st["rank"]
            att = (int(bool(rk) and rk[0] in tg), int(set(rk[:len(tg)]) == tg))
        cat = verdict_cats(st, tg)
        for key in group_keys(fb, with_hc):
            if key not in groups:
                groups[key] = Acc(hist=not key.startswith("hc:") and (set_name == "core" or key == "all" or key.startswith("class:")))
            groups[key].add(st, att, cat)
        dflags = det_flags(st)
        for j, fl in enumerate(dflags):
            dcount[j] += fl
        if fb["cluster"] is not None:
            c = clusters.setdefault(fb["cluster"], [0] * (1 + len(DET_NAMES)))
            c[0] += 1
            for j, fl in enumerate(dflags):
                c[j + 1] += fl
        if cap > 0:
            seen[fb["cls"]] += 1
            pool = res[fb["cls"]]
            if len(pool) < cap:
                pool.append((fb, st, tg, excl, faults))
            else:
                j = rng.randrange(seen[fb["cls"]])
                if j < cap:
                    pool[j] = (fb, st, tg, excl, faults)
    for cls, pool in res.items():
        for fb, st, tg, excl, faults in pool:
            rec = {"w": world.name, "sc": sid, "cls": cls, "hc": f"{fb['hour']:02d}|{fb['cell']}", "ep": fb["epoch"], "clu": fb["cluster"], "f": faults,
                   "tg": sorted(tg), "A": round(st["A"], 3), "B": [None if st["B"][k] is None else round(st["B"][k], 3) for k in B_KINDS]}
            t2.write(rec | tier2(fb, st, tg, excl))
    cell = {"set": set_name, "n": n, "spec": {k: v for k, v in spec.items()}, "groups": {k: a.out() for k, a in sorted(groups.items())}}
    if world.synthetic:
        cell["ci"] = {name: {"rate": dcount[j] / n, "ci95": wilson(dcount[j], n), "method": "wilson_iid_trials"} for j, name in enumerate(DET_NAMES)}
    else:
        cell["ci"] = cluster_ci(clusters)
    return cell


# ------------------------------------------------------------ adjacent
def adj_scenarios() -> list:
    names = {"stale40": fault_of("stale", {"E": 40.0, "sig": "s1"}), "stale15": fault_of("stale", {"E": 15.0, "sig": "s1"}),
             "wide10": fault_of("wide", {"m": 10.0, "ratio": 0.5})}
    return [(f"adj|{rel}|{a}+{b}|k{k}", {"a": names[a], "b": names[b], "rel": rel, "k": k}) for a, b in ADJ_TYPES for rel in ADJ_REL for k in ADJ_K]


def adj_legs(rng, rel: str) -> tuple:
    i = PIDX[fe.FAULT_PAIRS[rng.randrange(len(fe.FAULT_PAIRS))]]
    g = GROUPS8[PAIRS[i]]
    if rel == "same_pair":
        return i, i
    if rel == "same_arm_other_leg":
        if g == "hub":
            return i, i
        i1, _s1, i2, _s2 = HA8.arms[g.split(":")[1]]
        return i, (i2 if i == i1 else i1)
    others = [x for x in ARMS if f"arm:{x}" != g]
    x = others[rng.randrange(len(others))]
    return i, HA8.arms[x][rng.choice((0, 2))]


def run_adjacent(world: EmpWorld, sid: str, spec: dict, cfg: dict) -> dict:
    rng = random.Random(f"{cfg['seed']}|{world.name}|{sid}")
    ref, k = world.ref, spec["k"]
    ok = [i for i in range(len(ref) - k) if ref[i]["rclu"] == ref[i + k]["rclu"] and ref[i + k]["epoch"] - ref[i]["epoch"] == k * HOUR]
    assert ok, "no valid adjacent pairs"
    cnt = [[0, 0, 0, 0, 0] for _ in DET_NAMES]     # n, f1, f2, both, runs under the RUN_GAP rule
    hit, n = [0, 0, 0, 0], 0                       # ls top group hits fault1 / fault2 at bar1, bar2
    for _ in range(cfg["adj_trials"]):
        i = ok[rng.randrange(len(ok))]
        l1, l2 = adj_legs(rng, spec["rel"])
        fl, tgs = [], []
        for row, leg, f in ((ref[i], l1, spec["a"]), (ref[i + k], l2, spec["b"])):
            bar = emp_bar(row, rng)
            obs, vol, rg = list(bar["obs"]), list(bar["vol"]), list(bar["rng"])
            apply_fault(rng, obs, vol, rg, f, leg, None)
            bar.update(obs=obs, vol=vol, rng=rg)
            st = analyse(bar)
            fl.append(det_flags(st))
            tgs.append((GROUPS8[PAIRS[leg]], st["rank"][0] if st["rank"] else None))
        n += 1
        for d in range(len(DET_NAMES)):
            c = cnt[d]
            c[0] += 1
            c[1] += fl[0][d]
            c[2] += fl[1][d]
            both = fl[0][d] and fl[1][d]
            c[3] += both
            c[4] += 1 if (both and k <= RUN_GAP) else int(fl[0][d]) + int(fl[1][d])
        hit[0] += tgs[0][0] == tgs[0][1]
        hit[1] += tgs[1][0] == tgs[1][1]
    return {"set": "adjacent", "n": n, "spec": spec, "detectors": dict(zip(DET_NAMES, cnt)), "ls_top1_hit_bar1_bar2": hit[:2], "valid_pairs": len(ok)}


# -------------------------------------------------------------- blocks
def clean_flags(rows: list, seed: int) -> list:
    rng = random.Random(f"{seed}|cleanflags")
    return [det_flags(analyse(emp_bar(r, rng))) for r in rows]


def segments(rows: list, pop: str) -> list:
    segs, start, prev = [], None, None
    for i, r in enumerate(rows):
        if pop == "ref" and r["masked"]:
            if start is not None:
                segs.append((start, i - start))
            start = prev = None
            continue
        if start is not None and r["epoch"] - prev != HOUR:
            segs.append((start, i - start))
            start = None
        start = i if start is None else start
        prev = r["epoch"]
    if start is not None:
        segs.append((start, len(rows) - start))
    return segs


def count_runs(flags: list, s: int, length: int, d: int) -> int:
    runs, prev = 0, None
    for j in range(s, s + length):
        if flags[j][d]:
            runs += prev is None or j - prev > RUN_GAP
            prev = j
    return runs


def block_stats(rows: list, flags: list, pop: str, cfg: dict) -> dict:
    segs = segments(rows, pop)
    rng = random.Random(f"{cfg['seed']}|blocks|{pop}")
    out = {"n_segments": len(segs), "longest_segment": max((n for _s, n in segs), default=0), "by_length": {}}
    bar_share = [sum(f[d] for f in flags) / len(flags) for d in range(len(DET_NAMES))]
    out["bar_flag_share_all_rows"] = dict(zip(DET_NAMES, bar_share))
    for label in [str(x) for x in BLOCK_LENGTHS] + ["mixed"]:
        cum, tot = {}, 0
        res = {"n_blocks": 0, "runs": [0] * len(DET_NAMES), "blocks_with_run": [0] * len(DET_NAMES), "bars": 0}
        seg_blocks, seg_runs = [0] * len(segs), [[0] * len(segs) for _ in DET_NAMES]
        for _ in range(cfg["blocks_per_length"]):
            length = rng.choice(BLOCK_LENGTHS) if label == "mixed" else int(label)
            cum_l = cum.get(length)
            if cum_l is None:
                acc, cum_l = 0, []
                for _s, n in segs:
                    acc += max(0, n - length + 1)
                    cum_l.append(acc)
                cum[length] = cum_l
            if cum_l[-1] == 0:
                continue
            u = rng.randrange(cum_l[-1])
            si = bisect.bisect_right(cum_l, u)
            s = segs[si][0] + u - (cum_l[si - 1] if si else 0)
            res["n_blocks"] += 1
            res["bars"] += length
            seg_blocks[si] += 1
            for d in range(len(DET_NAMES)):
                r = count_runs(flags, s, length, d)
                res["runs"][d] += r
                res["blocks_with_run"][d] += r > 0
                seg_runs[d][si] += r
        if res["n_blocks"] == 0:
            out["by_length"][label] = {"n_blocks": 0}
            continue
        boot = random.Random(f"{cfg['seed']}|blockboot|{pop}|{label}")
        ci = {}
        for d, name in enumerate(DET_NAMES):
            vals = []
            for _ in range(cfg["boot"]):
                idx = [boot.randrange(len(segs)) for _ in segs]
                den = sum(seg_blocks[i] for i in idx)
                vals.append(sum(seg_runs[d][i] for i in idx) / den if den else 0.0)
            vals.sort()
            ci[name] = [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]
        out["by_length"][label] = {"n_blocks": res["n_blocks"], "mean_block_len": res["bars"] / res["n_blocks"],
                                   "runs_per_block": {n: res["runs"][d] / res["n_blocks"] for d, n in enumerate(DET_NAMES)},
                                   "share_blocks_with_run": {n: res["blocks_with_run"][d] / res["n_blocks"] for d, n in enumerate(DET_NAMES)},
                                   "runs_per_1000_bars": {n: 1000 * res["runs"][d] / res["bars"] for d, n in enumerate(DET_NAMES)},
                                   "runs_per_block_ci95_segment_bootstrap": ci}
    return out


# --------------------------------------------------------------- checks
def a1_crosscheck(rows: list, a1_path: str) -> dict:
    a1 = json.loads(Path(a1_path).read_text())
    ref = [r for r in rows if not r["masked"]]
    out = {"n_full_bars": [len(rows), a1["n_full_bars"]], "n_ref_bars": [len(ref), a1["n_ref_bars"]], "classes": {}}
    for cls in ("flat", "rollover_ny", "adjacent_ny"):
        mine = mad_sigma(_z_pool([r for r in ref if cls in bar_classes(r["hour"], r["cell"])]))
        theirs = a1["populations"]["ref"][cls]["noise_z"]["mad_sigma"]
        a10 = sum(r["max_abs"] > A_BPS for r in rows if cls in bar_classes(r["hour"], r["cell"]))
        out["classes"][cls] = {"noise_z_mad_sigma": [mine, theirs], "A10_all": [a10, a1["populations"]["all"][cls]["flag_counts"]["A"]["10"]]}
    out["consistent"] = (out["n_full_bars"][0] == out["n_full_bars"][1] and out["n_ref_bars"][0] == out["n_ref_bars"][1]
                         and all(abs(c["noise_z_mad_sigma"][0] - c["noise_z_mad_sigma"][1]) < 5e-3 * c["noise_z_mad_sigma"][1] + 1e-4 and c["A10_all"][0] == c["A10_all"][1] for c in out["classes"].values()))
    return out


def parametric_clean_rates(spreads, seed: int, trials: int, profile: str) -> dict:
    """Replays run_fault_experiment's FIRST cell (clean|flat) rng stream exactly; flat only."""
    assert profile == "flat", "only the first cell of run_fault_experiment is replayable without the preceding cells"
    rng, cnt = random.Random(seed), Counter()
    for _ in range(trials):
        obs, vol, rg, lat = fe.make_bar(rng, spreads, profile)
        st = analyse(build_bar(obs, vol, rg, [fe.TYP_VOLUME] * len(obs), spreads.typical_mean(profile), lat, 12, "both_winter", None, None))
        for name, fl in zip(DET_NAMES, det_flags(st)):
            cnt[name] += fl
    return {k: v / trials for k, v in cnt.items()}


def regress_p4db(path: str, sandbox: str) -> dict:
    ref = json.loads(Path(path).read_text())
    cfg = {"chat": ref["chat"], "seed": ref["seed"], "trials": ref["trials"]}
    new = fe.run_experiment(cfg, fe.DbSpreads(sandbox), cells=[("clean", 0.0, "flat")])
    out = {"seed": cfg["seed"], "trials": cfg["trials"], "cells": {}}
    mine = parametric_clean_rates(fe.DbSpreads(sandbox), cfg["seed"], cfg["trials"], "flat")
    key = "clean:0|flat"
    out["cells"][key] = {"world_script_vs_p4db": {k: [mine[k], ref["cells"][key]["detector_flag_rate"][k]] for k in mine},
                         "fault_experiment_rerun_vs_p4db_flat": {k: [new["cells"][key]["detector_flag_rate"][k], ref["cells"][key]["detector_flag_rate"][k]] for k in mine}}
    out["exact"] = all(v[0] == v[1] for c in out["cells"].values() for grp in c.values() for v in grp.values())
    out["note"] = "Only the first cell (clean|flat) shares the rng stream with p4db; later cells need the full 38 s p4db sequence (run_fault_experiment.py itself is unchanged)."
    return out


# ------------------------------------------------------------------ main
def build_worlds(cfg: dict, rows: list, meta: dict) -> list:
    names, ref, worlds = cfg["worlds"], [r for r in rows if not r["masked"]], []
    if "parametric_model" in names:
        worlds.append(ParamWorld("parametric_model", fe.ModelSpreads()))
    if "parametric_db" in names:
        worlds.append(ParamWorld("parametric_db", fe.DbSpreads(cfg["sandbox_path"])))
    if "empirical" in names:
        worlds.append(EmpWorld(rows))
    if any(n.startswith("cal_") for n in names):
        rng = random.Random(f"{cfg['seed']}|calfit")
        fits = {c: fit_class([r for r in ref if r["cls"] == c], rng, cfg["cal_sims"]) for c in CLASSES if sum(r["cls"] == c for r in ref) >= MIN_FIT}
        fits["__all__"] = fit_class(ref, rng, cfg["cal_sims"])
        meta["calibration"] = {c: {k: v for k, v in f.items() if k in ("n", "real_mad_z", "real_p99_over_mad", "sig_abs", "chosen", "table", "corr")} for c, f in fits.items()}
        for n in names:
            if n.startswith("cal_"):
                _p, fam, var = n.split("_")
                worlds.append(CalWorld(fam, var, ref, fits))
    return worlds


def run_all(cfg: dict, hooks: dict | None = None) -> dict:
    hooks = hooks or {}
    t_start = time.perf_counter()
    rows = load_dump(cfg["bars_path"])
    setup_ctx(rows, cfg["boot"], cfg["seed"])
    meta = {"schema": 1, "tool": "run_world_experiment", "chat": cfg["chat"], "limitations": LIMITATIONS, "config": {k: v for k, v in cfg.items() if k not in ("out_paths",)},
            "grids": {"A_bps": list(A_GRID), "B_ratio": list(B_GRID), "C_keys": [ck for ck, _s, _f in CKEYS], "C_fields": ["any", "stale_volume", "stale_zero_range", "thin"],
                      "det_names": list(DET_NAMES), "hist": {"bins": HBINS, "log10_lo": HLO, "log10_hi": HHI, "note": "bin 0 includes <= lo, last bin >= hi"},
                      "methods": [m[0] for m in METHODS], "defaults": {"A_bps": A_BPS, "B_ratio": B_RATIO, "C": C_DEFAULT_KEY}},
            "n_rows": len(rows), "n_ref": sum(not r["masked"] for r in rows), "decile_cuts": CTX["cuts"], "quiet_spread": CTX["quiet"], "typical_range_bps": CTX["trng"], "worlds": {}}
    if cfg["a1_path"]:
        meta["a1_crosscheck"] = a1_crosscheck(rows, cfg["a1_path"])
    if cfg["p4db_path"]:
        meta["p4db_regression"] = regress_p4db(cfg["p4db_path"], cfg["sandbox_path"])
    worlds = build_worlds(cfg, rows, meta)
    t2 = T2Writer(cfg["out_paths"]["trials"], int(cfg["jsonl_cap_mb"] * 1e6))
    sets = {"core": core_scenarios, "family": family_scenarios, "double": double_scenarios}
    try:
        for w in worlds:
            cells = {}
            for set_name, fn in sets.items():
                if set_name not in cfg["sets"] or (set_name != "core" and w.name not in cfg["heavy_worlds"]):
                    continue
                cap = cfg["huber_core"] if set_name == "core" else cfg["huber_heavy"]
                for sid, spec in fn():
                    t0 = time.perf_counter()
                    cells[sid] = run_scenario(w, sid, spec, set_name, cfg, t2, cap)
                    if hooks.get("item"):
                        hooks["item"](f"{w.name}/{sid}", time.perf_counter() - t0, trials=cells[sid]["n"])
                    if hooks.get("count"):
                        hooks["count"]("trials", cells[sid]["n"])
            if w.name == "empirical":
                for sid, spec in (adj_scenarios() if "adjacent" in cfg["sets"] else []):
                    cells[sid] = run_adjacent(w, sid, spec, cfg)
                if "blocks" in cfg["sets"]:
                    flags = clean_flags(rows, cfg["seed"])
                    meta["blocks"] = {pop: block_stats(rows, flags, pop, cfg) for pop in ("ref", "all")}
            Path(cfg["out_paths"][w.name]).write_text(json.dumps({"world": w.name, "cells": cells}, separators=(",", ":")))
            meta["worlds"][w.name] = {"n_cells": len(cells), "file": cfg["out_paths"][w.name]}
    finally:
        t2.close()
    meta["tier2"] = {"lines": t2.lines, "bytes": t2.bytes, "dropped_after_cap": t2.dropped, "file": cfg["out_paths"]["trials"]}
    meta["runtime_s"] = time.perf_counter() - t_start
    return meta


def render_text(meta: dict) -> str:
    lines = [f"World experiment | chat {meta['chat']} | rows {meta['n_rows']} (ref {meta['n_ref']}) | runtime {meta['runtime_s']:.1f}s",
             f"worlds: {meta['worlds']}", f"tier2: {meta['tier2']}"]
    if "a1_crosscheck" in meta:
        lines.append(f"a1 cross-check consistent: {meta['a1_crosscheck']['consistent']}")
    if "p4db_regression" in meta:
        lines.append(f"p4db regression exact: {meta['p4db_regression']['exact']}")
    for pop, b in meta.get("blocks", {}).items():
        lines.append(f"blocks[{pop}] segments {b['n_segments']} longest {b['longest_segment']}")
        for label, r in b["by_length"].items():
            if r["n_blocks"]:
                lines.append(f"  L={label:>5} runs/block " + " ".join(f"{k}={v:.4f}" for k, v in r["runs_per_block"].items()))
    for c, v in meta.get("calibration", {}).items():
        lines.append(f"calibration {c}: n={v['n']} real p99/MAD={v['real_p99_over_mad']:.2f} chosen={v['chosen']}")
    lines.append("LIMITATIONS: " + " | ".join(meta["limitations"]))
    return "\n".join(lines)


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Fault-injection worlds experiment (14p4-iii), report only.")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--bars", required=True, help="a1 bar dump JSONL (run_calibration_stats.py --dump-bars)")
    p.add_argument("--a1-json", default=None, help="a1 stats JSON for a cross-check")
    p.add_argument("--sandbox-db", default=None, help="needed for parametric_db and --regress-p4db")
    p.add_argument("--regress-p4db", default=None)
    p.add_argument("--worlds", default=",".join(WORLDS_ALL))
    p.add_argument("--sets", default=",".join(SETS_ALL))
    p.add_argument("--heavy-worlds", default="empirical,cal_t_prop")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--stride", type=int, default=1, help="core: use every Nth ref bar (empirical)")
    p.add_argument("--family-stride", type=int, default=8)
    p.add_argument("--double-stride", type=int, default=8)
    p.add_argument("--sim-trials", type=int, default=2000, help="trials per core scenario in synthetic worlds")
    p.add_argument("--family-sim", type=int, default=400, help="trials per family/double scenario in synthetic worlds")
    p.add_argument("--huber-core", type=int, default=25, help="Tier 2 reservoir per (scenario, class), core")
    p.add_argument("--huber-heavy", type=int, default=5, help="Tier 2 reservoir per (scenario, class), family/double")
    p.add_argument("--adj-trials", type=int, default=1500)
    p.add_argument("--blocks-per-length", type=int, default=20000)
    p.add_argument("--boot", type=int, default=100)
    p.add_argument("--cal-sims", type=int, default=2000)
    p.add_argument("--jsonl-cap-mb", type=float, default=50.0)
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    worlds, sets, heavy = (x.split(",") for x in (args.worlds, args.sets, args.heavy_worlds))
    assert set(worlds) <= set(WORLDS_ALL), f"--worlds: choose from {WORLDS_ALL}"
    assert set(sets) <= set(SETS_ALL), f"--sets: choose from {SETS_ALL}"
    for name in ("stride", "family_stride", "double_stride", "sim_trials", "family_sim", "adj_trials", "blocks_per_length", "boot", "cal_sims"):
        assert getattr(args, name) >= 1, f"--{name.replace('_', '-')} must be >= 1"
    assert args.huber_core >= 0 and args.huber_heavy >= 0, "Huber caps must be >= 0"
    assert 0 < args.jsonl_cap_mb <= 500, "--jsonl-cap-mb must be in (0, 500]"
    assert Path(args.bars).exists(), f"bar dump not found: {args.bars}"
    assert not args.a1_json or Path(args.a1_json).exists(), f"a1 JSON not found: {args.a1_json}"
    need_db = "parametric_db" in worlds or args.regress_p4db
    sandbox = args.sandbox_db or (sandbox_db_path(GLOBAL_START, discover_sandbox_end()) if need_db else None)
    assert not need_db or Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    assert not args.regress_p4db or Path(args.regress_p4db).exists(), f"p4db JSON not found: {args.regress_p4db}"
    stem = f"chat{args.chat}_{run_date}" + (f"_{args.tag}" if args.tag else "")
    paths = {"meta": str(Path(args.out_dir) / f"world_experiment_{stem}_meta.json"), "trials": str(Path(args.out_dir) / f"world_trials_{stem}.jsonl")}
    paths.update({w: str(Path(args.out_dir) / f"world_experiment_{stem}_{w}.json") for w in worlds})
    for p in paths.values():
        assert not Path(p).exists(), f"output already exists: {p} - pick a different --tag"
    return {"chat": args.chat, "bars_path": args.bars, "a1_path": args.a1_json, "sandbox_path": sandbox, "p4db_path": args.regress_p4db, "worlds": worlds,
            "sets": sets, "heavy_worlds": heavy, "seed": args.seed, "stride": {"core": args.stride, "family": args.family_stride, "double": args.double_stride},
            "sim_trials": args.sim_trials, "family_sim": args.family_sim, "huber_core": args.huber_core, "huber_heavy": args.huber_heavy,
            "adj_trials": args.adj_trials, "blocks_per_length": args.blocks_per_length, "boot": args.boot, "cal_sims": args.cal_sims,
            "jsonl_cap_mb": args.jsonl_cap_mb, "tag": args.tag, "out_paths": paths}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    with monitored_run("run_world_experiment", chat=args.chat, tag=args.tag, worlds=",".join(cfg["worlds"]), sets=",".join(cfg["sets"]), bars=cfg["bars_path"]):
        note("bars", cfg["bars_path"])
        meta = run_all(cfg, {"item": item, "count": count})
        Path(cfg["out_paths"]["meta"]).write_text(json.dumps(meta, indent=1, default=str))
        print(render_text(meta))
        print(f"\nWritten: {cfg['out_paths']['meta']} and per-world files; Tier 2 trials: {cfg['out_paths']['trials']}")


if __name__ == "__main__":
    main()
