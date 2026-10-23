# version: 261021
"""
run_runlevel_experiment.py  (fx_trader/run_runlevel_experiment.py)

Chat 14p4-iv: RUN-LEVEL VIEW for persistent arm offsets. REPORT ONLY: no window,
ratio, threshold, statistic, detector, k, scheme or gap policy is chosen
(CONTEXT_HANDOFF 4j.6, 4l). Read CONTEXT_HANDOFF 4i.11, 4j.5, 4k.8, 4k.15 and 4l.
numpy engine: currency_graph/runlevel.py (stdlib oracle in its tests).

INPUT: the a1 bar dump (run_calibration_stats.py --dump-bars). No DB needed.
PATHS: hub and the three arms (CHF, EUR, JPY). Per bar the path estimate pr_X is
built from the dump's unweighted-LS residuals (the fitted common part cancels),
d_X = pr_X - median of the 4 paths (= robust.path_attribution's 'dev'), path
spread = summed spread of the path's legs. The three triangle residuals are
pr_X - pr_hub.

SECTIONS (--sections, comma list, default all):
  bars   PER-BAR DUMP runlevel_bars_<stem>.npz - raw series (pr, ps, d, triangle
         residuals, scales for both gap policies, masks, stretch ids). EXISTS SO
         NO LATER CHAT RECOMPUTES IT: re-upload it instead (load_bars_npz()).
  real   real chronology, both gap policies (mask_reset = windows reset at ref
         stretch / mask boundaries; all_chron = windows may include masked bars,
         reset only at gaps), ALL statistics x windows x fills x causality x
         scales x thresholds: flag-bar counts, run counts for RUN_GAP 1/3/6/12,
         block false-run rates (blocks 1/3/6/12/24/48 h + mixed, never across
         gaps; CONTEXT_HANDOFF 4j.5 binds), segment-bootstrap CIs for the
         headline columns, and the six real JPY runs UNTUNED (onset latency,
         bars covered, flags before/after) + a check of the runs against the mask
         and of the dump route against the 4i.11 deviations.
  inj    INJECTED persistent offsets (R1: added to the path residual, then the
         median of paths is recomputed), real ref chronology, paired CLEAN windows
         (same starts, no injection). Cells = path x sign x magnitude x shape x
         duration. Starts: per class every STRIDE-th eligible bar, seeded order,
         checkpoints (prefix property). Totals for all columns; per start-hour
         class for the headline columns. Fill axis is DEGENERATE here (full
         PRE context), it is exercised in 'real'.
  tier2  R2 leg-level injection (one leg of the JPY arm, both legs) into the
         reconstructed bars with the full 27-method Huber grid, and R3: once a
         fixed reference run flag fires (trailing median W=12, |.|/path spread
         >= 1.0) the offset estimate is subtracted from the arm's first leg and
         the bar refitted. Capped JSONL.

Outputs (refuse to overwrite; all gitignored, local): reports/runlevel_<kind>_<stem>...
 stem = chat<N>_<run date, London>[_<tag>]. Axes of every array are in the meta JSON.

Run (from fx_trader/), smoke first:
  python3 run_runlevel_experiment.py --chat 14 --tag smoke --bars reports/calibration_bars_chat14_261015_a1.jsonl \
      --starts-per-cell 10 --checkpoints 10 --durs 6,24 --mags 4 --shapes flat --paths arm:JPY --signs 1 --blocks-per-length 500
  full:  python3 run_runlevel_experiment.py --chat 14 --tag r1 --bars reports/calibration_bars_chat14_261015_a1.jsonl
"""

import argparse
import itertools
import json
import math
import random
import re
import statistics
import sys
import time
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
POLICIES = ("mask_reset", "all_chron")
SHAPES = ("flat", "ramp", "decay", "pulse", "expdecay")
MAGS = (2.0, 3.0, 4.0, 6.0, 9.0, 15.0)
DURS = (1, 3, 6, 12, 24, 48, 72)
METRICS = ("det", "lat", "cover", "tail")
SECTIONS_ALL = ("bars", "real", "inj", "tier2")
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
    "Injection: the FILL axis is degenerate (every trial has PRE=48 bars of full context); fills are exercised only in the real-chronology pass. Post-gap onsets are not injected (14p4-v).",
    "Injection scales are computed on the CLEAN base series (injection-independent): robust z is therefore optimistic (a live MAD would include the offset).",
    "Real runs: their start and end were themselves defined by detector B_arm (gap <= 3 merge), so onset latency is relative to a detector-defined start (circular). n = 6, no CIs.",
    "The 'ref' chronology is contaminated by unknown real runs outside the mask; false-run rates are upper bounds on clean-world false runs.",
    "Block results cite CONTEXT_HANDOFF 4j.5 (no blocks across gaps; bars next to gaps under-represented; overlapping blocks need block-aware CIs; ref excludes the masked runs).",
    "Per-class (start-hour) totals and block CIs are stored for the HEADLINE columns only; centred statistics are HINDSIGHT; EWMA/CUSUM are trailing only.",
    "Hysteresis, carry-across-gap windows and a post-gap injection stratum are NOT built (ledger CONTEXT_HANDOFF 4l.6).",
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


def pack_index(seg) -> tuple:
    """seg: int array per row (-1 = excluded), chronological. Returns (pos, M, segs):
    pos[i] = packed index (or -1), segments separated by rl.SEP NaN bars."""
    N = len(seg)
    pos = np.full(N, -1, int)
    p, prev, segs = 0, -1, []
    for i in range(N):
        s = int(seg[i])
        if s < 0:
            prev = -1
            continue
        if s != prev:
            if p > 0:
                p += rl.SEP
            segs.append([p, 0])
        pos[i] = p
        p += 1
        prev = s
        segs[-1][1] += 1
    return pos, p, [tuple(x) for x in segs]


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


# ------------------------------------------------------------------ base
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
        "N": N, "pr": pr, "ps": ps, "d": pr - np.median(pr, axis=0),
        "ms": np.array([r["ms"] for r in rows], float),
        "epoch": np.array([r["epoch"] for r in rows], np.int64),
        "hour": np.array([r["hour"] for r in rows], int),
        "cls": np.array([CLASSES.index(r["cls"]) for r in rows], int),
        "cell": np.array([r["cell"] for r in rows]),
        "masked": np.array([bool(r["masked"]) for r in rows]),
        "acl": np.array([r["aclu"] for r in rows], int),
        "rcl": np.array([-1 if r["rclu"] is None else r["rclu"] for r in rows], int),
    }
    base["hour_scale"] = hour_scale(base)
    base["sc"] = {pol: compute_scales(base, base["rcl"] if pol == "mask_reset" else base["acl"]) for pol in POLICIES}
    return base


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


def compute_scales(base: dict, seg) -> dict:
    pos, M, _ = pack_index(seg)
    ps_p, d_p = pack(base["ps"], pos, M), pack(base["d"], pos, M)
    roll = unpack(rl.rolling_median(ps_p, 24, 12), pos)
    rz = unpack(np.maximum(1.4826 * rl.rolling_mad(d_p, 120, 30), 0.1), pos)
    return {"path": base["ps"], "roll": roll, "bar": np.tile(base["ms"], (4, 1)), "hour": base["hour_scale"], "rz": rz}


def write_bars(path: str, base: dict) -> None:
    assert not Path(path).exists(), f"output already exists: {path}"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    arrays = {
        "schema": np.array([1]), "bar_seconds": np.array([HOUR]), "paths": np.array(PATHS),
        "triangle_labels": np.array([f"{p}-hub" for p in PATHS[1:]]),
        "epoch": base["epoch"], "hour": base["hour"], "class_code": base["cls"], "classes": np.array(CLASSES),
        "dst_cell": base["cell"], "masked": base["masked"], "aclu": base["acl"], "rclu": base["rcl"],
        "pr": base["pr"], "ps": base["ps"], "d": base["d"], "triangle_resid": base["pr"][1:] - base["pr"][0],
        "mean_spread": base["ms"], "hour_scale": base["hour_scale"],
    }
    for pol in POLICIES:
        arrays[f"roll_{pol}"] = base["sc"][pol]["roll"]
        arrays[f"rz_{pol}"] = base["sc"][pol]["rz"]
    np.savez_compressed(path, **arrays)


def load_bars_npz(path: str) -> dict:
    """Reload the per-bar dump (no recomputation). Keys as written by write_bars."""
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ------------------------------------------------------------------ starts
def eligible_rows(rcl, D: int):
    N = len(rcl)
    i = np.arange(N)
    lo, hi = i - PRE, i + D + POST - 1
    ok = (lo >= 0) & (hi < N)
    idx = i[ok]
    good = (rcl[lo[idx]] >= 0) & (rcl[lo[idx]] == rcl[hi[idx]])
    return idx[good]


def ordered_starts(base: dict, D: int, stride: int, stride_class: dict, seed: int) -> dict:
    assert stride >= 1, "stride must be >= 1"
    el = eligible_rows(base["rcl"], D)
    out = {}
    for ci, c in enumerate(CLASSES):
        rows_c = el[base["cls"][el] == ci][:: stride_class.get(c, stride)]
        lst = [int(x) for x in rows_c]
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


def headline_starts(groups) -> tuple:
    hs, h = {}, 0
    for gi, g in enumerate(groups):
        if g.headline:
            hs[gi] = h
            h += len(g.thr)
    return hs, h


# --------------------------------------------------------------- injection
def eval_inj(stats, groups, thr, scales, D, cls_idx, rowsA, rowsH, hstart) -> None:
    """Accumulate det / latency sum / cover / tail for every column of one cell."""
    for gi, g in enumerate(groups):
        R = rl.ratio_array(stats[g.spec], g.scale, scales.get(g.scale))
        FL = R[None] >= thr[gi][:, None, None]
        inrun = FL[..., :D]
        det = inrun.any(-1)
        first = np.where(det, inrun.argmax(-1), 0)
        cover = inrun.sum(-1)
        tail = FL[..., D:].sum(-1)
        k, c0 = len(g.thr), g.col0
        vals = (det, first, cover, tail)
        for m in range(4):
            rowsA[m][c0:c0 + k] += vals[m].sum(1)
        h0 = hstart.get(gi, -1)
        if h0 >= 0:
            for ci in range(len(CLASSES)):
                sel = cls_idx == ci
                if sel.any():
                    for m in range(4):
                        rowsH[m][ci, h0:h0 + k] += vals[m][:, sel].sum(1)


def run_injection(base: dict, cfg: dict, hooks: dict) -> dict:
    specs, groups, ncols = rl.build_layout(("full",), cfg["detail"] == "headline")
    thr = [np.asarray(g.thr, float) for g in groups]
    hstart, nh = headline_starts(groups)
    paths, signs, mags, shapes, durs = cfg["paths"], cfg["signs"], cfg["mags"], cfg["shapes"], cfg["durs"]
    pidx = [PATHS.index(p) for p in paths]
    nc, ncl = len(paths) * len(signs) * len(mags) * len(shapes) * len(durs), len(paths) * len(durs)
    A = {m: np.zeros((nc, ncols), np.int64) for m in METRICS}
    C = {m: np.zeros((ncl, ncols), np.int64) for m in METRICS}
    H = {m: np.zeros((len(CLASSES), nc, max(nh, 1)), np.int64) for m in METRICS}
    HC = {m: np.zeros((len(CLASSES), ncl, max(nh, 1)), np.int64) for m in METRICS}
    ntr = np.zeros(len(durs), np.int64)
    ntc = np.zeros((len(CLASSES), len(durs)), np.int64)
    pools = {D: ordered_starts(base, D, cfg["stride"], cfg["stride_class"], cfg["seed"]) for D in durs}
    pool_sizes = {str(D): {c: len(v) for c, v in pools[D].items()} for D in durs}
    sc_all = base["sc"]["mask_reset"]
    k_prev = 0
    for k_now in cfg["checkpoints"]:
        t0 = time.perf_counter()
        for di, D in enumerate(durs):
            rows, cls = chunk_starts(pools[D], k_prev, k_now)
            n = len(rows)
            if n == 0:
                continue
            E, L = D + TAIL, PRE + D + POST
            I = rows[:, None] + (np.arange(L) - PRE)[None, :]
            PRB = base["pr"][:, I]
            ntr[di] += n
            ntc[:, di] += np.bincount(cls, minlength=len(CLASSES))
            Ie = I[:, PRE:PRE + E]
            shapes_v = {s: shape_vec(s, D) for s in shapes}
            for pi, xi in enumerate(pidx):
                PSX = np.maximum(base["ps"][xi][I], 1e-9)
                scales = {name: sc_all[name][xi][Ie] for name in ("path", "roll", "bar", "hour", "rz")}
                d0 = deviation(PRB, xi)
                stats = [None] * len(specs)
                for si_, a in rl.iter_stats({"raw": d0, "norm": d0 / PSX}, specs, PRE, PRE + E, False):
                    stats[si_] = a
                crow = pi * len(durs) + di
                eval_inj(stats, groups, thr, scales, D, cls, [C[m][crow] for m in METRICS],
                         [HC[m][:, crow] for m in METRICS], hstart)
                for si, sg in enumerate(signs):
                    for mi, mag in enumerate(mags):
                        for shi, shp in enumerate(shapes):
                            PR = PRB.copy()
                            PR[xi][:, PRE:PRE + D] += sg * mag * shapes_v[shp][None, :]
                            d = deviation(PR, xi)
                            stats = [None] * len(specs)
                            for si_, a in rl.iter_stats({"raw": d, "norm": d / PSX}, specs, PRE, PRE + E, False):
                                stats[si_] = a
                            cidx = ((((pi * len(signs) + si) * len(mags) + mi) * len(shapes) + shi) * len(durs) + di)
                            eval_inj(stats, groups, thr, scales, D, cls, [A[m][cidx] for m in METRICS],
                                     [H[m][:, cidx] for m in METRICS], hstart)
            if hooks.get("count"):
                hooks["count"]("injection_trials", n * (1 + len(pidx) * len(signs) * len(mags) * len(shapes)))
        if hooks.get("item"):
            hooks["item"](f"inj/checkpoint{k_now}", time.perf_counter() - t0, starts=int(k_now))
        hpath = cfg["out_paths"]["inj_ckpt"].format(k=k_now)
        np.savez_compressed(hpath, n_trials=ntr.copy(), n_trials_class=ntc.copy(),
                            **{f"h_{m}": H[m].astype(np.uint32) for m in METRICS},
                            **{f"hc_{m}": HC[m].astype(np.uint32) for m in METRICS})
        k_prev = k_now
    out = {"n_trials": ntr, "n_trials_class": ntc}
    for m in METRICS:
        out[m] = A[m].astype(np.uint32)
        out[f"c_{m}"] = C[m].astype(np.uint32)
        out[f"h_{m}"] = H[m].astype(np.uint32)
        out[f"hc_{m}"] = HC[m].astype(np.uint32)
    out["hcols"] = np.array([c for gi, g in enumerate(groups) if g.headline for c in range(g.col0, g.col0 + len(g.thr))], int)
    np.savez_compressed(cfg["out_paths"]["inj"], **out)
    return {"columns": rl.column_ids(specs, groups), "n_cols": ncols, "n_headline_cols": nh, "n_cells": nc, "pool_sizes": pool_sizes,
            "cells_axes": {"order": "path,sign,mag,shape,dur (C order); cell = ((((p*S+s)*M+m)*H+h)*D+d)", "paths": list(paths),
                           "signs": list(signs), "mags": list(mags), "shapes": list(shapes), "durs": list(durs)},
            "clean_axes": "row = path_index*len(durs)+dur_index", "checkpoints": list(cfg["checkpoints"]),
            "metrics": {"det": "trials with a flag during the run", "lat": "sum of first-flag index (bars from onset) over detected trials",
                        "cover": "sum of flagged bars inside the run", "tail": f"sum of flagged bars in the {TAIL} bars after the run"},
            "n_trials_axes": "n_trials[dur_index]; n_trials_class[class, dur_index]"}


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


def real_pass(base: dict, cfg: dict, hooks: dict) -> tuple:
    specs, groups, ncols = rl.build_layout(rl.FILLS, cfg["detail"] == "headline")
    by_spec = defaultdict(list)
    for gi, g in enumerate(groups):
        by_spec[g.spec].append(gi)
    hstart, nh = headline_starts(groups)
    nl, ng, nr = len(rl.BLOCK_LABELS), len(RUN_GAPS), len(REAL_RUNS)
    out = {
        "flagbars": np.zeros((2, 4, ncols), np.int32), "runs_total": np.zeros((2, ng, 4, ncols), np.int32),
        "blk_runs": np.zeros((2, nl, ng, 4, ncols), np.float32), "blk_share": np.zeros((2, nl, ng, 4, ncols), np.float32),
        "blk_ci_lo": np.full((2, nl, ng, 4, max(nh, 1)), np.nan, np.float32),
        "blk_ci_hi": np.full((2, nl, ng, 4, max(nh, 1)), np.nan, np.float32),
        "real_runs": np.zeros((2, nr, 4, ncols, 5), np.float32),
        "hcols": np.array([c for g in groups if g.headline for c in range(g.col0, g.col0 + len(g.thr))], int),
    }
    seginfo = {}
    for pi, pol in enumerate(POLICIES):
        t0 = time.perf_counter()
        pos, M, segs = pack_index(base["rcl"] if pol == "mask_reset" else base["acl"])
        Xraw = pack(base["d"], pos, M)
        Xnorm = pack(base["d"] / np.maximum(base["ps"], 1e-9), pos, M)
        SCp = {k: pack(v, pos, M) for k, v in base["sc"][pol].items()}
        rng = np.random.default_rng([cfg["seed"], pi])
        blocks = [rl.draw_blocks(segs, None if lab == "mixed" else int(lab), cfg["blocks_per_length"], rng) for lab in rl.BLOCK_LABELS]
        block_sets = [(b[0], b[1]) for b in blocks]
        rw = run_windows(base, pos)
        seginfo[pol] = {"n_segments": len(segs), "longest_segment": max((n for _s, n in segs), default=0), "packed_len": int(M),
                        "blocks_drawn": {lab: int(len(b[0])) for lab, b in zip(rl.BLOCK_LABELS, blocks)}}
        ci_rng = np.random.default_rng([cfg["seed"], pi, 99])
        for si, S in rl.iter_stats({"raw": Xraw, "norm": Xnorm}, specs, 0, M, True):
            for gi in by_spec[si]:
                g = groups[gi]
                R = rl.ratio_array(S, g.scale, SCp.get(g.scale))
                thr = np.asarray(g.thr, float)
                F = R[None] >= thr[:, None, None]
                k, c0 = len(g.thr), g.col0
                out["flagbars"][pi, :, c0:c0 + k] = F.sum(-1).T
                totals, runs = rl.block_runs(F, RUN_GAPS, block_sets)
                for gj, gap in enumerate(RUN_GAPS):
                    out["runs_total"][pi, gj, :, c0:c0 + k] = totals[gap].T
                    for li in range(nl):
                        r = runs[gap][li]
                        if r.shape[-1] == 0:
                            continue
                        out["blk_runs"][pi, li, gj, :, c0:c0 + k] = r.mean(-1).T
                        out["blk_share"][pi, li, gj, :, c0:c0 + k] = (r > 0).mean(-1).T
                        h0 = hstart.get(gi, -1)
                        if h0 >= 0:
                            lo, hi = seg_bootstrap_ci(r, blocks[li][2], len(segs), cfg["boot"], ci_rng)
                            out["blk_ci_lo"][pi, li, gj, :, h0:h0 + k] = lo.T
                            out["blk_ci_hi"][pi, li, gj, :, h0:h0 + k] = hi.T
                for ri, d in enumerate(rw):
                    nv = len(d["run"])
                    out["real_runs"][pi, ri, :, c0:c0 + k, 0] = nv
                    if nv == 0:
                        continue
                    sub = F[:, :, d["run"]]
                    det = sub.any(-1)
                    first = sub.argmax(-1)
                    out["real_runs"][pi, ri, :, c0:c0 + k, 1] = sub.sum(-1).T
                    out["real_runs"][pi, ri, :, c0:c0 + k, 2] = np.where(det, (d["ep_run"][first] - d["s_ep"]) / HOUR, -1.0).T
                    for slot, key in ((3, "pre"), (4, "post")):
                        if len(d[key]):
                            out["real_runs"][pi, ri, :, c0:c0 + k, slot] = F[:, :, d[key]].sum(-1).T
        if hooks.get("item"):
            hooks["item"](f"real/{pol}", time.perf_counter() - t0, packed=int(M))
    np.savez_compressed(cfg["out_paths"]["real"], **out)
    return {"columns": rl.column_ids(specs, groups), "n_cols": ncols, "n_headline_cols": nh, "policies": list(POLICIES), "segments": seginfo,
            "axes": {"flagbars": "[policy, path, col]", "runs_total": "[policy, run_gap, path, col]",
                     "blk_runs": "[policy, block_label, run_gap, path, col] mean runs per block", "blk_share": "same axes: share of blocks with >= 1 run",
                     "blk_ci_lo/hi": "[policy, block_label, run_gap, path, headline_col] segment-bootstrap 95% CI of blk_runs",
                     "real_runs": "[policy, run, path, col, (n_valid_bars, flagged_in_run, first_flag_latency_hours or -1, flagged_in_prev_48h, flagged_in_next_48h)]",
                     "population": "mask_reset = ref population, all_chron = all population"},
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
    return out


# ------------------------------------------------------------------ tier 2
def r3_series(base: dict, i: int, D: int, p_t):
    """Reference run flag for R3 along the D run bars (JPY path): trailing median W=12 of d,
    flagged when |.| / path spread >= R3_RATIO. Returns (flags (D,), offset estimate (D,))."""
    I = np.arange(i - PRE, i + D)
    PR = base["pr"][:, I][:, None, :].copy()
    PR[JPY, 0, PRE:] += p_t
    dd = deviation(PR, JPY)
    S = None
    for _i, a in rl.iter_stats({"raw": dd}, [R3_SPEC], PRE, PRE + D, False):
        S = a
    ps = np.maximum(base["ps"][JPY][I[PRE:]], 1e-9)
    return (np.abs(S[0]) / ps) >= R3_RATIO, S[0]


def run_tier2(rows: list, base: dict, cfg: dict, writer, hooks: dict) -> dict:
    i1, s1, i2, s2 = w.HA8.arms["JPY"]
    leg_info = {w.PAIRS[i1]: (i1, s1), w.PAIRS[i2]: (i2, s2)}
    n_rec = 0
    for leg, mag, shp, D in itertools.product(cfg["t2_legs"], cfg["t2_mags"], cfg["t2_shapes"], cfg["t2_durs"]):
        t0 = time.perf_counter()
        li, ls = leg_info[leg]
        pools = ordered_starts(base, D, cfg["stride"], cfg["stride_class"], cfg["seed"])
        starts, cls = chunk_starts(pools, 0, cfg["tier2_starts"])
        shape = shape_vec(shp, D)
        p_t = mag * shape
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
    meta = {"schema": 1, "tool": "run_runlevel_experiment", "chat": cfg["chat"], "limitations": LIMITATIONS,
            "config": {k: v for k, v in cfg.items() if k not in ("out_paths",)}, "n_rows": base["N"], "n_ref": int((~base["masked"]).sum()),
            "constants": {"PRE": PRE, "TAIL": TAIL, "POST": POST, "SEP": rl.SEP}, "real_run_check": real_run_check(base),
            "paths": list(PATHS), "files": cfg["out_paths"]}
    sections = cfg["sections"]
    if "bars" in sections:
        write_bars(cfg["out_paths"]["bars"], base)
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
        lines.append(f"  {r['run']}: rows {r['n_rows']}, masked {r['n_masked']}, median d_JPY {med} (4i.11: {r['expected_4i11_dev_bps']:+.1f})")
    for key in ("real", "inj", "tier2"):
        if key in meta:
            lines.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in meta[key].items() if isinstance(v, (int, float, str))))
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
    p.add_argument("--stride", type=int, default=24, help="every Nth eligible bar per class forms the start pool")
    p.add_argument("--stride-class", action="append", default=[], help="CLASS=N override, repeatable")
    p.add_argument("--starts-per-cell", type=int, default=1000)
    p.add_argument("--checkpoints", default=None, help="comma list; default 250,500,... up to starts-per-cell")
    p.add_argument("--blocks-per-length", type=int, default=5000)
    p.add_argument("--boot", type=int, default=200)
    p.add_argument("--detail", choices=("full", "headline"), default="full")
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
    assert args.stride >= 1 and args.starts_per_cell >= len(CLASSES), "stride >= 1 and starts-per-cell >= 4 required"
    assert args.blocks_per_length >= 1 and args.boot >= 1 and args.tier2_starts >= len(CLASSES), "bad block/boot/tier2 counts"
    assert 0 < args.tier2_cap_mb <= 500, "--tier2-cap-mb must be in (0, 500]"
    sc = {}
    for item_ in args.stride_class:
        k, _s, v = item_.partition("=")
        assert k in CLASSES and v.isdigit() and int(v) >= 1, f"--stride-class must be CLASS=N with CLASS in {CLASSES}"
        sc[k] = int(v)
    if args.checkpoints:
        cks = _csv(args.checkpoints, int)
    else:
        cks = list(range(250, args.starts_per_cell, 250)) + [args.starts_per_cell]
    assert cks == sorted(set(cks)) and cks[-1] <= args.starts_per_cell and cks[0] >= 1, "checkpoints must increase and end <= starts-per-cell"
    spc = args.starts_per_cell if cks[-1] == args.starts_per_cell else cks[-1]
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
            "detail": args.detail, "tier2_starts": args.tier2_starts, "tier2_cap_mb": args.tier2_cap_mb, "out_paths": paths, **cfg_lists}


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
