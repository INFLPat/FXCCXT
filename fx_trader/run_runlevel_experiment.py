# version: 261023
"""
run_runlevel_experiment.py  (fx_trader/run_runlevel_experiment.py)

Chat 14p4-iv: RUN-LEVEL VIEW for persistent arm offsets. REPORT ONLY: no window,
ratio, threshold, statistic, detector, k, scheme, hysteresis or gap policy is chosen
(CONTEXT_HANDOFF 4j.6, 4l). Read CONTEXT_HANDOFF 4i.11, 4j.5, 4k.8, 4k.15 and 4l (esp. 4l.8).
numpy engine: currency_graph/runlevel.py (stdlib oracle in its tests).

INPUT: the a1 bar dump (run_calibration_stats.py --dump-bars). No DB needed.
PATHS: hub and the three arms (CHF, EUR, JPY). Per bar the path estimate pr_X is
built from the dump's unweighted-LS residuals (the fitted common part cancels),
d_X = pr_X - median of the 4 paths (= robust.path_attribution's 'dev'), path
spread = summed spread of the path's legs. The three triangle residuals are
pr_X - pr_hub.

SECTIONS (--sections, comma list, default all):
  bars   PER-BAR DUMP runlevel_bars_<stem>.npz - raw series (pr, ps, d, triangle
         residuals, scales per gap policy, stretch geometry, masks, stretch ids).
         EXISTS SO NO LATER CHAT RECOMPUTES IT: re-upload it instead (load_bars_npz()).
  real   real chronology under the gap policies in --policies (default mask_reset and
         all_chron; extras: mask_carryN / all_carryN = gaps of up to N missing bars are
         bridged by NaN placeholders, mask_barindex / all_barindex = gaps ignored,
         DIAGNOSTIC ONLY), ALL statistics x windows x fills x causality x scales x
         thresholds, each with the plain flag and the hysteresis latches (--hyst-exit):
         flag-bar counts, run counts for RUN_GAP 1/3/6/12, block false-run rates (blocks
         1/3/6/12/24/48 h + mixed, never across gaps except the barindex diagnostics;
         CONTEXT_HANDOFF 4j.5), segment-bootstrap CIs for the headline columns, and the
         six real JPY runs UNTUNED (onset latency, bars covered, flags before/after) + a
         check of the runs against the mask and of the dump route against 4i.11.
  inj    INJECTED persistent offsets (R1: added to the path residual, then the median of
         paths is recomputed), real ref chronology, paired CLEAN windows (same starts, no
         injection). Cells = path x sign x magnitude x shape x duration. GEOMETRY (261023):
         the run (D bars) must lie inside one ref stretch; the 48 bars before and 48 after
         are used as far as the stretch provides them (NaN outside), so the fill rules
         matter and stretches (max 120 bars) can host 48 / 72 h runs. Starts are at least
         SPACING hours apart within a start-hour class (--stride, default 24), seeded order,
         checkpoints (prefix property). Strata: start-hour class and pre-context bucket
         (0-5 / 6-11 / 12-23 / 24-47 / 48+ bars; the short buckets are post-gap-like onsets,
         stored, NOT analysed: 14p4-v item 5), stored separately for the headline columns.
         Totals for all columns; hysteresis cover / tail for the headline columns;
         stretch-clustered bootstrap CIs (detection rate, mean latency) for the headline
         columns at the final checkpoint.
  tier2  R2 leg-level injection (one leg of the JPY arm, both legs) into the
         reconstructed bars with the full 27-method Huber grid, and R3: once a
         fixed reference run flag fires (trailing median W=12, |.|/path spread >= 1.0)
         the offset estimate is subtracted from the arm's first leg and the bar refitted.
         Same start geometry as inj. Capped JSONL.

Outputs (refuse to overwrite; all gitignored, local): reports/runlevel_<kind>_<stem>...
 stem = chat<N>_<run date, London>[_<tag>]. Axes of every array are in the meta JSON.
 Several processes may run side by side with different --tag (e.g. one --paths each).

Run (from fx_trader/; each command is ONE line):
  smoke: python3 run_runlevel_experiment.py --chat 14 --tag smoke --bars reports/calibration_bars_chat14_261015_a1.jsonl --starts-per-cell 12 --checkpoints 12 --durs 6,24 --mags 4 --shapes flat --paths arm:JPY --signs 1 --blocks-per-length 500 --detail headline --tier2-starts 4 --t2-legs GBP_JPY --t2-mags 9 --t2-shapes flat --t2-durs 6
  real:  python3 run_runlevel_experiment.py --chat 14 --tag r2real --bars reports/calibration_bars_chat14_261015_a1.jsonl --sections bars,real
  extra gap policies: python3 run_runlevel_experiment.py --chat 14 --tag r2pol --bars reports/calibration_bars_chat14_261015_a1.jsonl --sections real --policies mask_carry2,all_carry2,mask_barindex,all_barindex
  injection, one path per terminal: python3 run_runlevel_experiment.py --chat 14 --tag r2injJPY --bars reports/calibration_bars_chat14_261015_a1.jsonl --sections inj --paths arm:JPY
"""

import argparse
import itertools
import json
import random
import re
import statistics
import sys
import time
import warnings
import zlib
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import run_world_experiment as w
from currency_graph import runlevel as rl
from run_monitor import LONDON, count, item, monitored_run, note
from time_policy import stored_ts_to_epoch

HOUR = 3600
PRE, TAIL, POST = 48, 24, 48
RUN_GAPS = (1, 3, 6, 12)
PATHS = ("hub", "arm:CHF", "arm:EUR", "arm:JPY")
JPY = 3
CLASSES = w.CLASSES
POLICIES_DEFAULT = ("mask_reset", "all_chron")
POLICY_RE = re.compile(r"(mask_reset|all_chron|(mask|all)_carry(\d+)|(mask|all)_barindex)")
SHAPES = ("flat", "ramp", "decay", "pulse", "expdecay")
MAGS = (2.0, 3.0, 4.0, 6.0, 9.0, 15.0)
DURS = (1, 3, 6, 12, 24, 48, 72)
METRICS = ("det", "lat", "cover", "tail")
SECTIONS_ALL = ("bars", "real", "inj", "tier2")
BUCKET_EDGES = (6, 12, 24, 48)
BUCKET_LABELS = ("0-5", "6-11", "12-23", "24-47", "48+")
SPACING_H = 24
R3_RATIO = 1.0
R3_SPEC = rl.make_spec("median", 12, "full", "trailing", "raw")
T2_TG = {"arm:JPY"}
T2_EXCL = {"JPY"}
MAX_ROWS = 200_000
# RESEARCH_NOTES 17.3 (bar open times, UTC) and CONTEXT_HANDOFF 4i.11 JPY-path deviations (bps).
REAL_RUNS = (
    ("run1", "2023-04-05T00:00:00.000000000Z", "2023-04-05T19:00:00.000000000Z", 5.6),
    ("run2", "2023-04-27T22:00:00.000000000Z", "2023-04-28T19:00:00.000000000Z", 8.8),
    ("run3", "2024-03-26T22:00:00.000000000Z", "2024-03-27T20:00:00.000000000Z", 6.6),
    ("run4", "2024-12-24T02:00:00.000000000Z", "2024-12-24T17:00:00.000000000Z", 4.6),
    ("run5", "2025-04-15T23:00:00.000000000Z", "2025-04-16T18:00:00.000000000Z", 4.3),
    ("run6", "2025-12-24T08:00:00.000000000Z", "2025-12-24T08:00:00.000000000Z", 3.6),
)
LIMITATIONS = [
    "REPORT ONLY: nothing is chosen. Every number is conditional on the world that produced it (empirical bars: only the cycle space of real residuals).",
    "Injection geometry: the run must lie inside one ref stretch; context before / after is used as far as the stretch provides it, so short-context buckets are post-gap-like onsets (stored, not analysed: 14p4-v item 5). Starts are >= 24 h apart within a class but windows of long runs overlap: use the stretch-clustered CIs, not binomial ones.",
    "Injection scales are computed on the CLEAN base series (injection-independent): robust z is therefore optimistic (a live MAD would include the offset).",
    "Injection stores hysteresis cover / tail for the HEADLINE columns only; first-flag time (det, lat) is identical under hysteresis by construction.",
    "Real runs: their start and end were themselves defined by detector B_arm (gap <= 3 merge), so onset latency is relative to a detector-defined start (circular). n = 6, no CIs. They are fully masked: ref-population policies see none of their bars.",
    "The 'ref' chronology is contaminated by unknown real runs outside the mask; false-run rates are upper bounds on clean-world false runs.",
    "Block results cite CONTEXT_HANDOFF 4j.5 (no blocks across gaps; bars next to gaps under-represented; overlapping blocks need block-aware CIs; ref excludes the masked runs). The *_barindex policies ignore gaps BY DESIGN (diagnostic only) and violate 4j.5.",
    "Per-class (start-hour) totals and block CIs are stored for the HEADLINE columns only; centred statistics are HINDSIGHT; EWMA/CUSUM are trailing only.",
    "Tier 2: R3 reference rule (W=12 trailing median, ratio 1.0) and correction leg (the arm's first leg) are fixed, not chosen; empirical-world innocent-currency error floor is about 0 by construction (4k.7).",
    "Volume is assumed to be OANDA's tick count (UNVERIFIED).",
]


# ------------------------------------------------------------ small helpers
def shape_vec(name: str, D: int):
    assert D >= 1, "duration must be >= 1"
    k = np.arange(D, dtype=float)
    if name == "flat":
        return np.ones(D)
    if name == "ramp":
        return (k + 1.0) / D
    if name == "decay":
        return (D - k) / D
    if name == "pulse":
        return np.sin(np.pi * (k + 0.5) / D)
    if name == "expdecay":
        return np.exp(-3.0 * k / D)
    raise ValueError(f"unknown shape {name}")


def deviation(PR, x: int):
    """PR (4, n, L) path estimates -> d of path x = PR[x] - median of the 4 paths."""
    assert PR.shape[0] == 4, "four paths expected"
    s = np.sort(PR, axis=0)
    return PR[x] - 0.5 * (s[1] + s[2])


def pack(arr, pos, M: int):
    arr = np.asarray(arr, float)
    out = np.full((arr.shape[0], M), np.nan)
    m = pos >= 0
    out[:, pos[m]] = arr[:, m]
    return out


def unpack(arr, pos):
    out = np.full((arr.shape[0], len(pos)), np.nan)
    m = pos >= 0
    out[:, m] = arr[:, pos[m]]
    return out


def one_hot(idx, K: int):
    idx = np.asarray(idx, int)
    oh = np.zeros((len(idx), K), np.float32)
    oh[np.arange(len(idx)), idx] = 1.0
    return oh


def _ri(x, dtype=np.int32):
    return np.rint(x).astype(dtype)


# ---------------------------------------------------------- gap policies
def parse_policy(name: str) -> tuple:
    """-> (population 'ref' | 'all', carry int | None)."""
    m = POLICY_RE.fullmatch(name)
    assert m is not None, (f"unknown gap policy {name!r}: use mask_reset, all_chron, mask_carry<N>, all_carry<N>, "
                           "mask_barindex or all_barindex")
    if name == "mask_reset":
        return "ref", 0
    if name == "all_chron":
        return "all", 0
    if m.group(2):
        return ("ref" if m.group(2) == "mask" else "all"), int(m.group(3))
    return ("ref" if m.group(4) == "mask" else "all"), None


def policy_layout(base: dict, name: str) -> tuple:
    pop, carry = parse_policy(name)
    keep = ~base["masked"] if pop == "ref" else np.ones(base["N"], bool)
    return rl.pack_layout(keep, base["epoch"], carry, HOUR)


def compute_scales(base: dict, pos, M: int) -> dict:
    ps_p, d_p = pack(base["ps"], pos, M), pack(base["d"], pos, M)
    roll = unpack(rl.rolling_median(ps_p, 24, 12), pos)
    rz = unpack(np.maximum(1.4826 * rl.rolling_mad(d_p, 120, 30), 0.1), pos)
    return {"path": base["ps"], "roll": roll, "bar": np.tile(base["ms"], (4, 1)), "hour": base["hour_scale"], "rz": rz}


def get_policy(base: dict, name: str) -> dict:
    cache = base.setdefault("_pol", {})
    if name not in cache:
        pos, M, segs = policy_layout(base, name)
        cache[name] = {"pos": pos, "M": M, "segs": segs, "scales": compute_scales(base, pos, M)}
    return cache[name]


# ------------------------------------------------------------------ base
def hour_scale(base: dict):
    """Same-hour trailing median of path spread over the previous 60 same-hour bars
    (never the bar itself; at least 10), over all full bars (as the detectors)."""
    N = base["N"]
    out = np.full((4, N), np.nan)
    hist: dict = {}
    for i in range(N):
        h = int(base["hour"][i])
        for x in range(4):
            dq = hist.setdefault((x, h), deque(maxlen=60))
            if len(dq) >= 10:
                out[x, i] = statistics.median(dq)
            dq.append(float(base["ps"][x, i]))
    return out


def stretch_geometry(rcl):
    """Per row of a chronological table: bars before it inside its ref stretch (p, -1 if masked)
    and bars from it to the stretch end inclusive (rem, -1 if masked). Stretch = maximal run of
    equal consecutive ids >= 0."""
    rcl = np.asarray(rcl)
    N = len(rcl)
    p = np.full(N, -1, np.int64)
    rem = np.full(N, -1, np.int64)
    if N == 0:
        return p, rem
    chg = np.ones(N, bool)
    chg[1:] = rcl[1:] != rcl[:-1]
    starts = np.flatnonzero(chg)
    ends = np.append(starts[1:], N)
    for s, e in zip(starts, ends):
        if rcl[s] >= 0:
            n = int(e - s)
            p[s:e] = np.arange(n)
            rem[s:e] = np.arange(n, 0, -1)
    return p, rem


def context_bucket(p):
    return np.searchsorted(np.asarray(BUCKET_EDGES), np.asarray(p), side="right")


def finish_base(b: dict) -> dict:
    """Derived fields from the primary arrays (pr, ps, ms, epoch, hour, cls, masked, rcl, acl)."""
    b["d"] = b["pr"] - np.median(b["pr"], axis=0)
    b["hour_scale"] = hour_scale(b)
    b["p"], b["rem"] = stretch_geometry(b["rcl"])
    b["bkt"] = context_bucket(np.maximum(b["p"], 0))
    uniq = np.unique(b["rcl"][b["rcl"] >= 0])
    b["n_cl"] = int(len(uniq))
    b["cl"] = np.where(b["rcl"] >= 0, np.searchsorted(uniq, b["rcl"]), -1)
    b["_pol"] = {}
    return b


def build_base(rows: list) -> dict:
    assert rows and len(rows) <= MAX_ROWS, "bad row count"
    N = len(rows)
    arms, hub = w.HA8.arms, w.HA8.hub
    assert tuple(["hub"] + [f"arm:{x}" for x in sorted(arms)]) == PATHS, "path order changed"
    hub_sign = 1.0 if w.TOPO[hub].a == w.HA8.anchors[0] else -1.0
    res = np.array([r["res"] for r in rows], float)
    spr = np.array([r["spread"] for r in rows], float)
    pr = np.zeros((4, N))
    ps = np.zeros((4, N))
    pr[0], ps[0] = hub_sign * res[:, hub], spr[:, hub]
    for j, x in enumerate(sorted(arms), 1):
        i1, s1, i2, s2 = arms[x]
        pr[j] = s1 * res[:, i1] + s2 * res[:, i2]
        ps[j] = spr[:, i1] + spr[:, i2]
    base = {
        "N": N, "pr": pr, "ps": ps,
        "ms": np.array([r["ms"] for r in rows], float),
        "epoch": np.array([r["epoch"] for r in rows], np.int64),
        "hour": np.array([r["hour"] for r in rows], int),
        "cls": np.array([CLASSES.index(r["cls"]) for r in rows], int),
        "cell": np.array([r["cell"] for r in rows]),
        "masked": np.array([bool(r["masked"]) for r in rows]),
        "acl": np.array([r["aclu"] for r in rows], int),
        "rcl": np.array([-1 if r["rclu"] is None else r["rclu"] for r in rows], int),
    }
    return finish_base(base)


def write_bars(path: str, base: dict, policies) -> None:
    assert not Path(path).exists(), f"output already exists: {path}"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    arrays = {
        "schema": np.array([2]), "bar_seconds": np.array([HOUR]), "paths": np.array(PATHS),
        "triangle_labels": np.array([f"{p}-hub" for p in PATHS[1:]]),
        "epoch": base["epoch"], "hour": base["hour"], "class_code": base["cls"], "classes": np.array(CLASSES),
        "dst_cell": base["cell"], "masked": base["masked"], "aclu": base["acl"], "rclu": base["rcl"],
        "pos_in_stretch": base["p"], "bars_to_stretch_end": base["rem"], "ctx_bucket": base["bkt"],
        "bucket_labels": np.array(BUCKET_LABELS),
        "pr": base["pr"], "ps": base["ps"], "d": base["d"], "triangle_resid": base["pr"][1:] - base["pr"][0],
        "mean_spread": base["ms"], "hour_scale": base["hour_scale"],
    }
    for pol in sorted(set(policies) | set(POLICIES_DEFAULT)):
        sc = get_policy(base, pol)["scales"]
        arrays[f"roll_{pol}"] = sc["roll"]
        arrays[f"rz_{pol}"] = sc["rz"]
    np.savez_compressed(path, **arrays)


def load_bars_npz(path: str) -> dict:
    """Reload the per-bar dump (no recomputation). Keys as written by write_bars."""
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ------------------------------------------------------------------ starts
def spaced_rows(rows, epoch, gap_h: float) -> list:
    """Greedy chronological selection keeping starts at least gap_h hours apart."""
    keep, last, gap = [], None, gap_h * HOUR
    for r in rows:
        e = int(epoch[r])
        if last is None or e - last >= gap:
            keep.append(int(r))
            last = e
    return keep


def ordered_starts(base: dict, D: int, spacing_h: float, spacing_class: dict, seed: int) -> dict:
    """Per class: eligible starts (a D-bar run inside one ref stretch), thinned to a minimum
    spacing in hours, in a seeded order (prefix-stable)."""
    assert spacing_h > 0, "spacing must be positive"
    el = np.flatnonzero((base["p"] >= 0) & (base["rem"] >= D))
    out = {}
    for ci, c in enumerate(CLASSES):
        rows_c = el[base["cls"][el] == ci]
        lst = spaced_rows(rows_c, base["epoch"], spacing_class.get(c, spacing_h))
        random.Random(f"{seed}|starts|{D}|{c}").shuffle(lst)
        out[c] = lst
    return out


def chunk_starts(pools: dict, k_prev: int, k_now: int) -> tuple:
    """Prefix-stable allocation: k_now // 4 starts per class (capped by the pool)."""
    assert 0 <= k_prev <= k_now, "checkpoints must increase"
    per = len(CLASSES)
    rows, cls = [], []
    for ci, c in enumerate(CLASSES):
        a, b = min(k_prev // per, len(pools[c])), min(k_now // per, len(pools[c]))
        rows += pools[c][a:b]
        cls += [ci] * (b - a)
    return np.array(rows, int), np.array(cls, int)


def trial_window(base: dict, rows, D: int) -> tuple:
    """Row indices (n, L) and availability (n, L) of the trial windows; offset k runs
    -PRE .. D+POST-1 relative to the onset; a bar is available iff it lies in the same ref stretch."""
    rows = np.asarray(rows, int)
    L = PRE + D + POST
    k = np.arange(L) - PRE
    I = rows[:, None] + k[None, :]
    p = base["p"][rows][:, None]
    rem = base["rem"][rows][:, None]
    avail = (k[None, :] >= -p) & (k[None, :] < rem)
    return np.clip(I, 0, base["N"] - 1), avail


def headline_starts(groups) -> tuple:
    hs, h = {}, 0
    for gi, g in enumerate(groups):
        if g.headline:
            hs[gi] = h
            h += len(g.thr)
    return hs, h


def headline_cols(groups):
    return np.array([c for g in groups if g.headline for c in range(g.col0, g.col0 + len(g.thr))], int)


# --------------------------------------------------------------- injection
class InjAcc:
    """Accumulators of the injection section: one row per injected cell, one per clean (path, duration)."""

    def __init__(self, nc, ncl, ncols, nh, ncls, nb, n_cl, hy, use_ci):
        z = lambda *s: np.zeros(s, np.int32)
        self.A = {m: z(nc, ncols) for m in METRICS}
        self.C = {m: z(ncl, ncols) for m in METRICS}
        self.H = {m: z(ncls, nc, nh) for m in METRICS}
        self.HB = {m: z(nb, nc, nh) for m in METRICS}
        self.HCc = {m: z(ncls, ncl, nh) for m in METRICS}
        self.HCb = {m: z(nb, ncl, nh) for m in METRICS}
        self.HY = {(v, k): z(nc, nh) for v in hy for k in ("cover", "tail")}
        self.HYC = {(v, k): z(ncl, nh) for v in hy for k in ("cover", "tail")}
        self.SD = np.zeros((nc, n_cl, nh), np.uint8) if use_ci else None
        self.SL = np.zeros((nc, n_cl, nh), np.int16) if use_ci else None

    def cell(self, c: int) -> dict:
        return {"A": [self.A[m][c] for m in METRICS], "H": [self.H[m][:, c] for m in METRICS],
                "HB": [self.HB[m][:, c] for m in METRICS], "HY": {k: v[c] for k, v in self.HY.items()},
                "SD": None if self.SD is None else self.SD[c], "SL": None if self.SL is None else self.SL[c]}

    def clean(self, r: int) -> dict:
        return {"A": [self.C[m][r] for m in METRICS], "H": [self.HCc[m][:, r] for m in METRICS],
                "HB": [self.HCb[m][:, r] for m in METRICS], "HY": {k: v[r] for k, v in self.HYC.items()}, "SD": None, "SL": None}


def eval_inj(stats, groups, thr, scales, D, oh, acc, hstart, hy) -> None:
    """Accumulate det / first-flag index / cover / tail for every column of one cell (or clean row);
    headline columns also by class, by context bucket, by stretch cluster, and under hysteresis."""
    for gi, g in enumerate(groups):
        R = rl.ratio_array(stats[g.spec], g.scale, scales.get(g.scale))
        FL = R[None] >= thr[gi][:, None, None]
        inrun = FL[..., :D]
        det = inrun.any(-1)
        first = np.where(det, inrun.argmax(-1), 0)
        vals = (det, first, inrun.sum(-1), FL[..., D:].sum(-1))
        k, c0 = len(g.thr), g.col0
        for m in range(4):
            acc["A"][m][c0:c0 + k] += vals[m].sum(1)
        h0 = hstart.get(gi, -1)
        if h0 < 0:
            continue
        for m in range(4):
            v = vals[m].astype(np.float32).T
            acc["H"][m][:, h0:h0 + k] += _ri(oh["cls"].T @ v)
            acc["HB"][m][:, h0:h0 + k] += _ri(oh["bkt"].T @ v)
        if acc["SD"] is not None:
            acc["SD"][:, h0:h0 + k] += _ri(oh["clu"].T @ vals[0].astype(np.float32).T, np.uint8)
            acc["SL"][:, h0:h0 + k] += _ri(oh["clu"].T @ vals[1].astype(np.float32).T, np.int16)
        for v in hy:
            FLh = rl.latch(FL, R[None] >= (thr[gi] * v)[:, None, None])
            acc["HY"][(v, "cover")][h0:h0 + k] += FLh[..., :D].sum((-1, -2))
            acc["HY"][(v, "tail")][h0:h0 + k] += FLh[..., D:].sum((-1, -2))


def injection_ci(SD, SL, ntcl, nD: int, boot: int, seed: int) -> dict:
    """Stretch-clustered bootstrap (resample stretches with replacement, per duration) of the
    detection rate and of the mean first-flag index among detected trials, per cell and headline column."""
    nc, n_cl, nh = SD.shape
    rng = np.random.default_rng([seed, 31337])
    out = {k: np.full((nc, nh), np.nan, np.float32) for k in ("det_lo", "det_hi", "lat_lo", "lat_hi")}
    for di in range(nD):
        n_c = np.asarray(ntcl[di])
        act = np.flatnonzero(n_c > 0)
        if len(act) == 0:
            continue
        idx = act[rng.integers(0, len(act), size=(boot, len(act)))]
        den = n_c[idx].sum(1).astype(np.float64)
        for c in range(di, nc, nD):
            d = SD[c].astype(np.float32)
            l = SL[c].astype(np.float32)
            num_d = d[idx].sum(1)
            num_l = l[idx].sum(1)
            rate = num_d / den[:, None]
            lat = np.where(num_d > 0, num_l / np.maximum(num_d, 1), np.nan)
            out["det_lo"][c], out["det_hi"][c] = np.percentile(rate, [2.5, 97.5], axis=0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                out["lat_lo"][c], out["lat_hi"][c] = np.nanpercentile(lat, [2.5, 97.5], axis=0)
    return out


def run_injection(base: dict, cfg: dict, hooks: dict) -> dict:
    fills = tuple(cfg["inj_fills"])
    specs, groups, ncols = rl.build_layout(fills, cfg["detail"] == "headline")
    thr = [np.asarray(g.thr, float) for g in groups]
    hstart, nh = headline_starts(groups)
    assert nh > 0, "the layout has no headline columns (needs fill 'full')"
    hcols = headline_cols(groups)
    hy = tuple(cfg["hyst"])
    paths, signs, mags, shapes, durs = cfg["paths"], cfg["signs"], cfg["mags"], cfg["shapes"], cfg["durs"]
    pidx = [PATHS.index(p) for p in paths]
    nP, nS, nM, nH, nD = len(paths), len(signs), len(mags), len(shapes), len(durs)
    nc, ncl = nP * nS * nM * nH * nD, nP * nD
    ncls, nb, n_cl = len(CLASSES), len(BUCKET_LABELS), base["n_cl"]
    use_ci = cfg["boot"] > 0
    acc = InjAcc(nc, ncl, ncols, nh, ncls, nb, n_cl, hy, use_ci)
    ntr = np.zeros(nD, np.int64)
    ntc, ntb = np.zeros((ncls, nD), np.int64), np.zeros((nb, nD), np.int64)
    ntcl = np.zeros((nD, n_cl), np.int64)
    tl, tlc, tlb = np.zeros(nD), np.zeros((ncls, nD)), np.zeros((nb, nD))
    log = {k: [] for k in ("di", "row", "cls", "bkt", "clu", "tail_valid", "ckpt")}
    pools = {D: ordered_starts(base, D, cfg["stride"], cfg["stride_class"], cfg["seed"]) for D in durs}
    pool_sizes = {str(D): {c: len(v) for c, v in pools[D].items()} for D in durs}
    sc_all = get_policy(base, "mask_reset")["scales"]
    k_prev = 0
    for k_now in cfg["checkpoints"]:
        t0 = time.perf_counter()
        for di, D in enumerate(durs):
            rows, cls = chunk_starts(pools[D], k_prev, k_now)
            n = len(rows)
            if n == 0:
                continue
            bkt, clu = base["bkt"][rows], base["cl"][rows]
            tail_valid = np.clip(base["rem"][rows] - D, 0, TAIL)
            oh = {"cls": one_hot(cls, ncls), "bkt": one_hot(bkt, nb), "clu": one_hot(clu, n_cl)}
            ntr[di] += n
            ntc[:, di] += np.bincount(cls, minlength=ncls)
            ntb[:, di] += np.bincount(bkt, minlength=nb)
            ntcl[di] += np.bincount(clu, minlength=n_cl)
            tl[di] += tail_valid.sum()
            tlc[:, di] += np.bincount(cls, weights=tail_valid, minlength=ncls)
            tlb[:, di] += np.bincount(bkt, weights=tail_valid, minlength=nb)
            for key, val in (("di", np.full(n, di)), ("row", rows), ("cls", cls), ("bkt", bkt), ("clu", clu),
                             ("tail_valid", tail_valid), ("ckpt", np.full(n, k_now))):
                log[key].append(val)
            E = D + TAIL
            I, avail = trial_window(base, rows, D)
            PRB = np.where(avail[None], base["pr"][:, I], np.nan)
            Ie, avE = I[:, PRE:PRE + E], avail[:, PRE:PRE + E]
            shapes_v = {s: shape_vec(s, D) for s in shapes}
            for pi, xi in enumerate(pidx):
                PSX = np.where(avail, np.maximum(base["ps"][xi][I], 1e-9), np.nan)
                scales = {name: np.where(avE, sc_all[name][xi][Ie], np.nan) for name in ("path", "roll", "bar", "hour", "rz")}
                d0 = deviation(PRB, xi)
                stats = [None] * len(specs)
                for si_, a in rl.iter_stats({"raw": d0, "norm": d0 / PSX}, specs, PRE, PRE + E, False):
                    stats[si_] = a
                eval_inj(stats, groups, thr, scales, D, oh, acc.clean(pi * nD + di), hstart, hy)
                for si, sg in enumerate(signs):
                    for mi, mag in enumerate(mags):
                        for shi, shp in enumerate(shapes):
                            PR = PRB.copy()
                            PR[xi][:, PRE:PRE + D] += sg * mag * shapes_v[shp][None, :]
                            d = deviation(PR, xi)
                            stats = [None] * len(specs)
                            for si_, a in rl.iter_stats({"raw": d, "norm": d / PSX}, specs, PRE, PRE + E, False):
                                stats[si_] = a
                            cidx = ((((pi * nS + si) * nM + mi) * nH + shi) * nD + di)
                            eval_inj(stats, groups, thr, scales, D, oh, acc.cell(cidx), hstart, hy)
            if hooks.get("count"):
                hooks["count"]("injection_trials", n * (1 + nP * nS * nM * nH))
        if hooks.get("item"):
            hooks["item"](f"inj/checkpoint{k_now}", time.perf_counter() - t0, starts=int(k_now))
        np.savez_compressed(cfg["out_paths"]["inj_ckpt"].format(k=k_now), n_trials=ntr.copy(), n_trials_class=ntc.copy(),
                            n_trials_bucket=ntb.copy(), hcols=hcols, **{f"a_{m}": acc.A[m][:, hcols] for m in METRICS})
        k_prev = k_now
    out = {"n_trials": ntr, "n_trials_class": ntc, "n_trials_bucket": ntb, "n_trials_stretch": ntcl,
           "tail_valid": tl, "tail_valid_class": tlc, "tail_valid_bucket": tlb, "hcols": hcols}
    for m in METRICS:
        out[m] = acc.A[m].astype(np.uint32)
        out[f"c_{m}"] = acc.C[m].astype(np.uint32)
        out[f"h_{m}"] = acc.H[m].astype(np.uint32)
        out[f"hb_{m}"] = acc.HB[m].astype(np.uint32)
        out[f"hc_{m}"] = acc.HCc[m].astype(np.uint32)
        out[f"hcb_{m}"] = acc.HCb[m].astype(np.uint32)
    for (v, kind), arr in acc.HY.items():
        out[f"hy{int(round(v * 100))}_{kind}"] = arr.astype(np.uint32)
    for (v, kind), arr in acc.HYC.items():
        out[f"chy{int(round(v * 100))}_{kind}"] = arr.astype(np.uint32)
    if use_ci:
        out.update({f"ci_{k}": v for k, v in injection_ci(acc.SD, acc.SL, ntcl, nD, cfg["boot"], cfg["seed"]).items()})
    for key, vals in log.items():
        out[f"trial_{key}"] = np.concatenate(vals) if vals else np.zeros(0, int)
    np.savez_compressed(cfg["out_paths"]["inj"], **out)
    return {"columns": rl.column_ids(specs, groups), "n_cols": ncols, "n_headline_cols": nh, "n_cells": nc, "pool_sizes": pool_sizes,
            "fills": list(fills), "hyst_exit": list(hy), "bucket_labels": list(BUCKET_LABELS), "classes": list(CLASSES),
            "cells_axes": {"order": "path,sign,mag,shape,dur (C order); cell = ((((p*S+s)*M+m)*H+h)*D+d)", "paths": list(paths),
                           "signs": list(signs), "mags": list(mags), "shapes": list(shapes), "durs": list(durs)},
            "clean_axes": "row = path_index*len(durs)+dur_index", "checkpoints": list(cfg["checkpoints"]),
            "metrics": {"det": "trials with a flag during the run", "lat": "sum of first-flag index (bars from onset) over detected trials",
                        "cover": "sum of flagged bars inside the run", "tail": f"sum of flagged bars in the {TAIL} bars after the run (see tail_valid for the denominator)"},
            "arrays": {"det/lat/cover/tail": "[cell, column] totals (all columns)", "c_*": "same for the paired clean windows [path*dur row, column]",
                       "h_*": "[class, cell, headline column]", "hb_*": "[context bucket, cell, headline column]",
                       "hc_* / hcb_*": "clean windows by class / bucket [.., path*dur row, headline column]",
                       "hy75_cover etc.": "cover / tail under hysteresis exit 0.75 (50 = 0.5) [cell, headline column]; chy*: clean windows",
                       "ci_det_lo/hi, ci_lat_lo/hi": "[cell, headline column] stretch-clustered bootstrap 95% CI (final checkpoint) of the detection rate and of the mean first-flag index among detected trials",
                       "n_trials*": "[dur] / [class, dur] / [bucket, dur] / [dur, stretch cluster]; tail_valid*: available tail bars (denominator of tail)",
                       "trial_*": "per-trial table: duration index, bar row, class, bucket, stretch cluster, tail_valid, checkpoint at which it entered",
                       "hcols": "column indices of the headline columns"}}


# -------------------------------------------------------------------- real
def seg_bootstrap_ci(runs, segidx, nseg: int, boot: int, rng) -> tuple:
    """runs (k, P, B), segidx (B,) -> 95% CI of runs per block, resampling SEGMENTS."""
    k, P, B = runs.shape
    if B == 0 or nseg == 0:
        return np.full((k, P), np.nan), np.full((k, P), np.nan)
    rows = runs.reshape(-1, B).astype(float)
    onehot = np.zeros((B, nseg))
    onehot[np.arange(B), segidx] = 1.0
    segsum = rows @ onehot
    segcnt = onehot.sum(0)
    idx = rng.integers(0, nseg, size=(boot, nseg))
    num = segsum[:, idx].sum(-1)
    den = np.maximum(segcnt[idx].sum(-1), 1.0)
    ratio = num / den
    lo, hi = np.percentile(ratio, [2.5, 97.5], axis=1)
    return lo.reshape(k, P), hi.reshape(k, P)


def run_windows(base: dict, pos) -> list:
    ep = base["epoch"]
    out = []
    for label, s, e, _x in REAL_RUNS:
        s_ep, e_ep = stored_ts_to_epoch(s), stored_ts_to_epoch(e)
        sel = {"run": (ep >= s_ep) & (ep <= e_ep), "pre": (ep >= s_ep - PRE * HOUR) & (ep < s_ep),
               "post": (ep > e_ep) & (ep <= e_ep + PRE * HOUR)}
        d = {k: pos[np.where(v)[0]][pos[np.where(v)[0]] >= 0] for k, v in sel.items()}
        rr = np.where(sel["run"])[0]
        d["ep_run"] = ep[rr][pos[rr] >= 0]
        d["s_ep"] = s_ep
        out.append(d)
    return out


def real_pass(base: dict, cfg: dict, hooks: dict) -> dict:
    specs, groups, ncols = rl.build_layout(rl.FILLS, cfg["detail"] == "headline")
    by_spec = defaultdict(list)
    for gi, g in enumerate(groups):
        by_spec[g.spec].append(gi)
    hstart, nh = headline_starts(groups)
    pols = cfg["policies"]
    hyst = [1.0] + list(cfg["hyst"])
    nPol, nHy, nl, ng, nr = len(pols), len(hyst), len(rl.BLOCK_LABELS), len(RUN_GAPS), len(REAL_RUNS)
    out = {
        "flagbars": np.zeros((nPol, nHy, 4, ncols), np.int32), "runs_total": np.zeros((nPol, nHy, ng, 4, ncols), np.int32),
        "blk_runs": np.zeros((nPol, nHy, nl, ng, 4, ncols), np.float32), "blk_share": np.zeros((nPol, nHy, nl, ng, 4, ncols), np.float32),
        "blk_ci_lo": np.full((nPol, nHy, nl, ng, 4, max(nh, 1)), np.nan, np.float32),
        "blk_ci_hi": np.full((nPol, nHy, nl, ng, 4, max(nh, 1)), np.nan, np.float32),
        "real_runs": np.zeros((nPol, nHy, nr, 4, ncols, 5), np.float32), "hcols": headline_cols(groups),
    }
    seginfo = {}
    for pi, pol in enumerate(pols):
        t0 = time.perf_counter()
        pd = get_policy(base, pol)
        pos, M, segs = pd["pos"], pd["M"], pd["segs"]
        Xraw = pack(base["d"], pos, M)
        Xnorm = pack(base["d"] / np.maximum(base["ps"], 1e-9), pos, M)
        SCp = {k: pack(v, pos, M) for k, v in pd["scales"].items()}
        crc = zlib.crc32(pol.encode())
        rng = np.random.default_rng([cfg["seed"], crc])
        blocks = [rl.draw_blocks(segs, None if lab == "mixed" else int(lab), cfg["blocks_per_length"], rng) for lab in rl.BLOCK_LABELS]
        block_sets = [(b[0], b[1]) for b in blocks]
        rw = run_windows(base, pos)
        popn, carry = parse_policy(pol)
        seginfo[pol] = {"population": popn, "carry": carry, "n_segments": len(segs), "longest_segment": max((n for _s, n in segs), default=0),
                        "packed_len": int(M), "blocks_drawn": {lab: int(len(b[0])) for lab, b in zip(rl.BLOCK_LABELS, blocks)},
                        "run_bars_present": [int(len(d["run"])) for d in rw]}
        ci_rng = np.random.default_rng([cfg["seed"], crc, 99])
        for si, S in rl.iter_stats({"raw": Xraw, "norm": Xnorm}, specs, 0, M, True):
            for gi in by_spec[si]:
                g = groups[gi]
                R = rl.ratio_array(S, g.scale, SCp.get(g.scale))
                thr = np.asarray(g.thr, float)
                k, c0 = len(g.thr), g.col0
                ent = R[None] >= thr[:, None, None]
                for hi_, h in enumerate(hyst):
                    F = ent if h == 1.0 else rl.latch(ent, R[None] >= (thr * h)[:, None, None])
                    out["flagbars"][pi, hi_, :, c0:c0 + k] = F.sum(-1).T
                    totals, runs = rl.block_runs(F, RUN_GAPS, block_sets)
                    for gj, gap in enumerate(RUN_GAPS):
                        out["runs_total"][pi, hi_, gj, :, c0:c0 + k] = totals[gap].T
                        for li in range(nl):
                            r = runs[gap][li]
                            if r.shape[-1] == 0:
                                continue
                            out["blk_runs"][pi, hi_, li, gj, :, c0:c0 + k] = r.mean(-1).T
                            out["blk_share"][pi, hi_, li, gj, :, c0:c0 + k] = (r > 0).mean(-1).T
                            h0 = hstart.get(gi, -1)
                            if h0 >= 0:
                                lo, hi = seg_bootstrap_ci(r, blocks[li][2], len(segs), cfg["boot"], ci_rng)
                                out["blk_ci_lo"][pi, hi_, li, gj, :, h0:h0 + k] = lo.T
                                out["blk_ci_hi"][pi, hi_, li, gj, :, h0:h0 + k] = hi.T
                    for ri, d in enumerate(rw):
                        nv = len(d["run"])
                        out["real_runs"][pi, hi_, ri, :, c0:c0 + k, 0] = nv
                        if nv == 0:
                            continue
                        sub = F[:, :, d["run"]]
                        det = sub.any(-1)
                        first = sub.argmax(-1)
                        out["real_runs"][pi, hi_, ri, :, c0:c0 + k, 1] = sub.sum(-1).T
                        out["real_runs"][pi, hi_, ri, :, c0:c0 + k, 2] = np.where(det, (d["ep_run"][first] - d["s_ep"]) / HOUR, -1.0).T
                        for slot, key in ((3, "pre"), (4, "post")):
                            if len(d[key]):
                                out["real_runs"][pi, hi_, ri, :, c0:c0 + k, slot] = F[:, :, d[key]].sum(-1).T
        if hooks.get("item"):
            hooks["item"](f"real/{pol}", time.perf_counter() - t0, packed=int(M))
    np.savez_compressed(cfg["out_paths"]["real"], **out)
    return {"columns": rl.column_ids(specs, groups), "n_cols": ncols, "n_headline_cols": nh, "policies": list(pols), "hyst_variants": hyst,
            "segments": seginfo,
            "axes": {"flagbars": "[policy, hyst, path, col]", "runs_total": "[policy, hyst, run_gap, path, col]",
                     "blk_runs": "[policy, hyst, block_label, run_gap, path, col] mean runs per block",
                     "blk_share": "same axes: share of blocks with >= 1 run",
                     "blk_ci_lo/hi": "[policy, hyst, block_label, run_gap, path, headline_col] segment-bootstrap 95% CI of blk_runs",
                     "real_runs": "[policy, hyst, run, path, col, (n_valid_bars, flagged_in_run, first_flag_latency_hours or -1, flagged_in_prev_48h, flagged_in_next_48h)]",
                     "hyst": "hyst_variants[i]: 1.0 = plain flag; 0.75 / 0.5 = latch that stays ON while the ratio >= that fraction of the entry threshold",
                     "population": "mask_* policies use the ref population (masked bars removed), all_* the full population"},
            "run_gaps": list(RUN_GAPS), "block_labels": list(rl.BLOCK_LABELS), "paths": list(PATHS), "runs": [r[0] for r in REAL_RUNS]}


def real_run_check(base: dict) -> list:
    """Are the six real runs inside the mask, and does the dump route reproduce the 4i.11 JPY deviations?"""
    out = []
    ep = base["epoch"]
    for label, s, e, expected in REAL_RUNS:
        sel = (ep >= stored_ts_to_epoch(s)) & (ep <= stored_ts_to_epoch(e))
        n = int(sel.sum())
        out.append({"run": label, "start": s, "end": e, "n_rows": n, "n_masked": int(base["masked"][sel].sum()),
                    "median_d_jpy_bps": float(np.median(base["d"][JPY][sel])) if n else None,
                    "expected_4i11_dev_bps": expected})
        out[-1]["dump_route_ok"] = None if not n else abs(out[-1]["median_d_jpy_bps"] - expected) <= 0.15
    return out


# ------------------------------------------------------------------ tier 2
def r3_series(base: dict, i: int, D: int, p_t):
    """Reference run flag for R3 along the D run bars (JPY path): trailing median W=12 of d,
    flagged when |.| / path spread >= R3_RATIO. Bars before the stretch start are NaN (fill 'full')."""
    I, avail = trial_window(base, np.array([i]), D)
    PR = np.where(avail[None], base["pr"][:, I], np.nan)
    PR[JPY, :, PRE:PRE + D] += p_t[None, :]
    dd = deviation(PR, JPY)
    S = None
    for _i, a in rl.iter_stats({"raw": dd}, [R3_SPEC], PRE, PRE + D, False):
        S = a
    ps = np.maximum(base["ps"][JPY][I[0, PRE:PRE + D]], 1e-9)
    with np.errstate(invalid="ignore"):
        flags = (np.abs(S[0]) / ps) >= R3_RATIO
    return flags, S[0]


def run_tier2(rows: list, base: dict, cfg: dict, writer, hooks: dict) -> dict:
    i1, s1, i2, s2 = w.HA8.arms["JPY"]
    leg_info = {w.PAIRS[i1]: (i1, s1), w.PAIRS[i2]: (i2, s2)}
    pools_by_d: dict = {}
    n_rec = 0
    for leg, mag, shp, D in itertools.product(cfg["t2_legs"], cfg["t2_mags"], cfg["t2_shapes"], cfg["t2_durs"]):
        t0 = time.perf_counter()
        li, ls = leg_info[leg]
        if D not in pools_by_d:
            pools_by_d[D] = ordered_starts(base, D, cfg["stride"], cfg["stride_class"], cfg["seed"])
        starts, cls = chunk_starts(pools_by_d[D], 0, cfg["tier2_starts"])
        p_t = mag * shape_vec(shp, D)
        for i, ci in zip(starts, cls):
            i = int(i)
            flags, est = r3_series(base, i, D, p_t)
            rng = random.Random(f"{cfg['seed']}|t2|{leg}|{mag}|{shp}|{D}|{i}")
            eu, ec, hu, hc, gap = [], [], [], [], []
            for k in range(D):
                bar = w.emp_bar(rows[i + k], rng)
                obs = list(bar["obs"])
                obs[li] = obs[li]._replace(y=obs[li].y + p_t[k] * ls)
                b_u = dict(bar, obs=obs)
                u = w.tier2(b_u, w.analyse(b_u), T2_TG, T2_EXCL)
                if flags[k]:
                    obs2 = list(obs)
                    obs2[i1] = obs2[i1]._replace(y=obs2[i1].y - s1 * est[k])
                    b_c = dict(bar, obs=obs2)
                    c = w.tier2(b_c, w.analyse(b_c), T2_TG, T2_EXCL)
                else:
                    c = u
                eu.append(u["err"])
                ec.append(c["err"])
                hu.append([int(ch) for ch in u["h1"]])
                hc.append([int(ch) for ch in c["h1"]])
                gap.append(u["gap"])
            eu, ec, hu, hc = np.array(eu), np.array(ec), np.array(hu), np.array(hc)
            rec = {"leg": leg, "mag": mag, "shape": shp, "dur": D, "start": int(base["epoch"][i]), "cls": CLASSES[int(ci)],
                   "ctx_bars": int(base["p"][i]), "bucket": BUCKET_LABELS[int(base["bkt"][i])],
                   "r3_flag_bars": int(flags.sum()), "err_mean": np.round(eu.mean(0), 3).tolist(), "err_max": np.round(eu.max(0), 3).tolist(),
                   "top1": np.round(hu.mean(0), 3).tolist(), "err_mean_r3": np.round(ec.mean(0), 3).tolist(),
                   "err_max_r3": np.round(ec.max(0), 3).tolist(), "top1_r3": np.round(hc.mean(0), 3).tolist(),
                   "gap_max": np.round(np.array(gap).max(0), 3).tolist()}
            writer.write(rec)
            n_rec += 1
        if hooks.get("item"):
            hooks["item"](f"tier2/{leg}|{mag:g}|{shp}|{D}", time.perf_counter() - t0, trials=int(len(starts)))
    return {"records": n_rec, "methods": [m[0] for m in w.METHODS], "r3_rule": f"trailing median W=12 on d_JPY, |.|/path spread >= {R3_RATIO}; "
            "correction subtracts the estimate from the arm's first leg and refits; uncorrected bars reuse the uncorrected fit",
            "fields": "per trial: mean/max innocent-currency error and top-1 arm hit per method over the run bars, uncorrected and _r3"}


# -------------------------------------------------------------------- main
def run_all(cfg: dict, hooks: dict | None = None) -> dict:
    hooks = hooks or {}
    t_start = time.perf_counter()
    rows = w.load_dump(cfg["bars_path"])
    w.setup_ctx(rows, 10, cfg["seed"])
    base = build_base(rows)
    meta = {"schema": 2, "tool": "run_runlevel_experiment", "chat": cfg["chat"], "limitations": LIMITATIONS,
            "config": {k: v for k, v in cfg.items() if k not in ("out_paths",)}, "n_rows": base["N"], "n_ref": int((~base["masked"]).sum()),
            "constants": {"PRE": PRE, "TAIL": TAIL, "POST": POST, "SEP": rl.SEP, "bucket_edges": list(BUCKET_EDGES),
                          "bucket_labels": list(BUCKET_LABELS), "n_ref_stretches": base["n_cl"]},
            "real_run_check": real_run_check(base), "paths": list(PATHS), "files": cfg["out_paths"]}
    sections = cfg["sections"]
    if "bars" in sections:
        write_bars(cfg["out_paths"]["bars"], base, cfg["policies"])
    if "real" in sections:
        meta["real"] = real_pass(base, cfg, hooks)
    if "inj" in sections:
        meta["inj"] = run_injection(base, cfg, hooks)
    if "tier2" in sections:
        writer = w.T2Writer(cfg["out_paths"]["tier2"], int(cfg["tier2_cap_mb"] * 1e6))
        try:
            meta["tier2"] = run_tier2(rows, base, cfg, writer, hooks)
        finally:
            writer.close()
        meta["tier2"].update({"bytes": writer.bytes, "dropped_after_cap": writer.dropped})
    meta["runtime_s"] = time.perf_counter() - t_start
    return meta


def render_text(meta: dict) -> str:
    lines = [f"Run-level experiment | chat {meta['chat']} | rows {meta['n_rows']} (ref {meta['n_ref']}) | runtime {meta['runtime_s']:.1f}s"]
    for r in meta["real_run_check"]:
        med = "n/a" if r["median_d_jpy_bps"] is None else f"{r['median_d_jpy_bps']:+.2f}"
        lines.append(f"  {r['run']}: rows {r['n_rows']}, masked {r['n_masked']}, median d_JPY {med} (4i.11: {r['expected_4i11_dev_bps']:+.1f})"
                     + ("" if r.get("dump_route_ok") in (None, True) else "  <-- MISMATCH: dump route differs from 4i.11, do not trust this run"))
    for key in ("real", "inj", "tier2"):
        if key in meta:
            lines.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in meta[key].items() if isinstance(v, (int, float, str))))
    if "inj" in meta:
        lines.append("inj start pools per duration: " + json.dumps(meta["inj"]["pool_sizes"]))
    lines.append("files: " + json.dumps(meta["files"]))
    lines.append("LIMITATIONS: " + " | ".join(meta["limitations"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------- cli
def _csv(text: str, cast, allowed=None) -> list:
    vals = [cast(x.strip()) for x in text.split(",") if x.strip()]
    assert vals, "empty list"
    assert allowed is None or all(v in allowed for v in vals), f"values must be among {list(allowed)}"
    return vals


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Run-level view for persistent arm offsets (14p4-iv), report only.")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--bars", required=True, help="a1 bar dump JSONL")
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--sections", default=",".join(SECTIONS_ALL))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--stride", type=float, default=float(SPACING_H), help="minimum hours between injection starts within a start-hour class")
    p.add_argument("--stride-class", action="append", default=[], help="CLASS=HOURS override of the spacing, repeatable")
    p.add_argument("--starts-per-cell", type=int, default=1000)
    p.add_argument("--checkpoints", default=None, help="comma list; default 250,500,... up to starts-per-cell")
    p.add_argument("--blocks-per-length", type=int, default=5000)
    p.add_argument("--boot", type=int, default=200, help="bootstrap resamples (block CIs and injection stretch-clustered CIs); 0 = no injection CIs")
    p.add_argument("--detail", choices=("full", "headline"), default="full")
    p.add_argument("--policies", default=",".join(POLICIES_DEFAULT), help="gap policies for the real pass: mask_reset, all_chron, mask_carryN, all_carryN, mask_barindex, all_barindex")
    p.add_argument("--hyst-exit", default=",".join(f"{h:g}" for h in rl.HYST_EXIT), help="hysteresis exit fractions of the entry threshold (the plain flag is always included)")
    p.add_argument("--inj-fills", default=",".join(rl.FILLS), help="window fills computed in injection (must include full)")
    p.add_argument("--paths", default=",".join(PATHS))
    p.add_argument("--signs", default="1,-1")
    p.add_argument("--mags", default=",".join(f"{m:g}" for m in MAGS))
    p.add_argument("--shapes", default=",".join(SHAPES))
    p.add_argument("--durs", default=",".join(str(d) for d in DURS))
    p.add_argument("--tier2-starts", type=int, default=40)
    p.add_argument("--tier2-cap-mb", type=float, default=20.0)
    p.add_argument("--t2-legs", default="GBP_JPY,USD_JPY")
    p.add_argument("--t2-mags", default="4,9")
    p.add_argument("--t2-shapes", default="flat,ramp")
    p.add_argument("--t2-durs", default="6,24,72")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert Path(args.bars).exists(), f"bar dump not found: {args.bars}"
    sections = _csv(args.sections, str, SECTIONS_ALL)
    assert args.stride > 0 and args.starts_per_cell >= len(CLASSES), "stride > 0 and starts-per-cell >= 4 required"
    assert args.blocks_per_length >= 1 and args.boot >= 0 and args.tier2_starts >= len(CLASSES), "bad block/boot/tier2 counts"
    assert 0 < args.tier2_cap_mb <= 500, "--tier2-cap-mb must be in (0, 500]"
    sc = {}
    for item_ in args.stride_class:
        k, _s, v = item_.partition("=")
        assert k in CLASSES and v.replace(".", "", 1).isdigit() and float(v) > 0, f"--stride-class must be CLASS=HOURS with CLASS in {CLASSES}"
        sc[k] = float(v)
    if args.checkpoints:
        cks = _csv(args.checkpoints, int)
    else:
        cks = list(range(250, args.starts_per_cell, 250)) + [args.starts_per_cell]
    assert cks == sorted(set(cks)) and cks[-1] <= args.starts_per_cell and cks[0] >= 1, "checkpoints must increase and end <= starts-per-cell"
    spc = args.starts_per_cell if cks[-1] == args.starts_per_cell else cks[-1]
    policies = _csv(args.policies, str)
    assert len(set(policies)) == len(policies), "--policies must not repeat a policy"
    for pol in policies:
        parse_policy(pol)
    hyst = _csv(args.hyst_exit, float)
    assert all(0 < h < 1 for h in hyst) and len(set(hyst)) == len(hyst), "--hyst-exit values must be distinct fractions in (0, 1)"
    inj_fills = _csv(args.inj_fills, str, rl.FILLS)
    assert "full" in inj_fills, "--inj-fills must include 'full' (the headline columns use it)"
    cfg_lists = {
        "paths": _csv(args.paths, str, PATHS), "signs": _csv(args.signs, int, (1, -1)), "mags": _csv(args.mags, float),
        "shapes": _csv(args.shapes, str, SHAPES), "durs": _csv(args.durs, int),
        "t2_legs": _csv(args.t2_legs, str, ("GBP_JPY", "USD_JPY")), "t2_mags": _csv(args.t2_mags, float),
        "t2_shapes": _csv(args.t2_shapes, str, SHAPES), "t2_durs": _csv(args.t2_durs, int),
    }
    stem = f"chat{args.chat}_{run_date}" + (f"_{args.tag}" if args.tag else "")
    d = Path(args.out_dir)
    paths = {"meta": str(d / f"runlevel_meta_{stem}.json"), "bars": str(d / f"runlevel_bars_{stem}.npz"),
             "real": str(d / f"runlevel_real_{stem}.npz"), "inj": str(d / f"runlevel_inj_{stem}.npz"),
             "inj_ckpt": str(d / f"runlevel_inj_{stem}_ckpt{{k}}.npz"), "tier2": str(d / f"runlevel_tier2_{stem}.jsonl")}
    for key, pth in paths.items():
        if key == "inj_ckpt":
            for k in cks:
                assert not Path(pth.format(k=k)).exists(), f"output already exists: {pth.format(k=k)} - pick a different --tag"
        else:
            assert not Path(pth).exists(), f"output already exists: {pth} - pick a different --tag"
    return {"chat": args.chat, "bars_path": args.bars, "tag": args.tag, "sections": sections, "seed": args.seed, "stride": args.stride,
            "stride_class": sc, "starts_per_cell": spc, "checkpoints": cks, "blocks_per_length": args.blocks_per_length, "boot": args.boot,
            "detail": args.detail, "policies": policies, "hyst": hyst, "inj_fills": inj_fills, "tier2_starts": args.tier2_starts,
            "tier2_cap_mb": args.tier2_cap_mb, "out_paths": paths, **cfg_lists}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    with monitored_run("run_runlevel_experiment", chat=args.chat, tag=args.tag, sections=",".join(cfg["sections"]), bars=cfg["bars_path"]):
        note("bars", cfg["bars_path"])
        meta = run_all(cfg, {"item": item, "count": count})
        Path(cfg["out_paths"]["meta"]).write_text(json.dumps(meta, indent=1, default=str))
        print(render_text(meta))
        print(f"\nWritten: {cfg['out_paths']['meta']} and the files listed above")


if __name__ == "__main__":
    main()
