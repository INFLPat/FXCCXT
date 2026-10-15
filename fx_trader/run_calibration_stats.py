# version: 261014
"""
run_calibration_stats.py  (fx_trader/run_calibration_stats.py)

Chat 14p4-ii, step a1: READ-ONLY calibration statistics of the real sandbox,
the numbers the synthetic fault worlds (parametric, empirical-residual,
calibrated-parametric) are fitted to and checked against. Everything is
computed in ONE pass and stored so later chats re-slice without re-running:
no threshold, detector, k or scheme is chosen here.

PER FULL BAR (all 8 pairs; 9 degraded bars skipped): unweighted plain-LS fast
LOO per pair, hat-corrected noise z = resid / (spread * sqrt(1 - h)), LOO over
spread and over the pair's ARM spread (detectors._arm_scales), max |LOO|,
detectors A, B (three scales) and C, spreads, volumes, ranges.

GROUPS (each also split by DST cell): all; flat (08-15 UTC); rollover_ny (the
first bar after New York 17:00: 21 UTC in US summer, 22 in winter);
adjacent_ny (the bars either side); utc21_legacy (hour 21 only, the p4db
definition); every UTC hour h00..h23. Per group: max|LOO| percentiles, mean
spread, spread dispersion, pooled LOO and noise-z tail shape (MAD sigma, p99 /
sigma against the Gaussian 2.576, excess kurtosis), per pair spread / |LOO| /
|LOO|/spread / |LOO|/arm spread / volume / volume over typical / range /
zero-range and thin shares, log-spread correlation matrix (class groups), and
REAL FLAG COUNTS on grids: A 3-20 bps; B ratio 0.5-3 on each scale; C
stale-volume {1,2,5} x thin-fraction {0.02,0.05,0.10} with components.

POPULATIONS: 'all', and 'ref' = bars outside the MASK (+/- --mask-pad hours
around every A flag and every B_mean_spread run of >= --mask-min-run bars).
CIRCULAR for A and B (the mask is built from them): use 'all' for flag rates,
'ref' for noise shape. Mask intervals are stored so any chat can re-mask.

CONTIGUITY AND BLOCK SAMPLING (read before building block worlds): the report
lists the contiguous full-bar stretches and the number of valid block starts
for block lengths 1, 3, 6, 12, 24, 48 h, for 'all' and 'ref'. A block must be
hourly-contiguous, so it never straddles a weekend or a degraded bar; see
LIMITATIONS for the consequences.

OPTIONAL --dump-bars: per-bar JSONL (spreads, volumes, ranges, LOO, hat-
corrected z, flags, masked) to reports/calibration_bars_*.jsonl (gitignored,
about 10-15 MB): the empirical world can then be built and re-built from the
file without the 120 MB DB.

NOT covered: crypto, per-pair attribution, any fault truth, choosing anything.
Volume is assumed to be OANDA's tick count (UNVERIFIED). The --chat argument is
the integer chat number (14); the roadmap name of this chat (14p4-ii) is not
used in file names.

Run (from fx_trader/):
  python3 run_calibration_stats.py --chat 14 --tag a1 --dump-bars
Output: reports/calibration_stats_chat<N>_<run date, London>[_<tag>].json
(refuses to overwrite) plus a text summary on stdout.
"""

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from audit_sandbox_alignment import pcts
from currency_graph.detectors import (
    A_BPS, B_KINDS, B_RATIO, C_MIN_OTHER_RANGE_BPS, C_STALE_VOL, C_THIN_FRAC, HourSpreadHistory, LegInfo,
    _arm_scales, detector_a, detector_b, detector_c,
)
from currency_graph.robust import huber_fit
from currency_graph.strengths import BRIDGE_TOL, Obs, loo_fast, select_component
from run_detector_scan import PAIRS, _runs, load_all
from run_monitor import LONDON, count, item, monitored_run, note
from run_residual_investigations import dst_cell
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import stored_ts_to_epoch

BPS = 1e4
HOUR = 3600
MAX_BARS = 200_000
MIN_GROUP = 30
MAX_MASK_HOURS = 2_000
A_GRID = (3.0, 5.0, 7.5, 10.0, 15.0, 20.0)
B_GRID = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
C_STALE_GRID = (1, 2, 5)
C_THIN_GRID = (0.02, 0.05, 0.10)
BLOCK_LENGTHS = (1, 3, 6, 12, 24, 48)
LOO_PCTS = (50, 90, 99, 99.9, 100)
GAUSS_P99 = 2.576
LIMITATIONS = [
    "Real bars contain the faults themselves: the 'ref' population excludes A flags and long B_mean runs (+/- pad), which is CIRCULAR for A and B calibration; flag rates come from 'all'.",
    "Noise z assumes noise sd proportional to spread; it is shown per hour and per pair so a failure of that assumption is visible, not hidden.",
    "Volume is assumed to be OANDA's tick count (UNVERIFIED); typical volume is the full-history median per pair and UTC hour (mild look-ahead).",
    "BLOCKS: a block must be hourly-contiguous, so it never straddles a weekend or a degraded bar. Bars right after a gap (Sunday opens: two of the 17 fault bars) can only be block STARTS and bars right before a gap only block ENDS, so long blocks under-represent them relative to real frequency.",
    "BLOCKS: the 9 degraded bars are absent (not full bars), which also splits stretches; the longest possible block is the longest stretch (an FX week is about 120 bars), so 48 h blocks cannot reach every week boundary.",
    "BLOCKS: overlapping blocks reuse bars, so effective sample size is below the trial count; confidence intervals must resample blocks, not trials.",
    "BLOCKS: sampling only from 'ref' removes the known JPY runs and their +/- pad, so interaction between injected and real persistent offsets is untested; a block's hour-of-day mix depends on its start.",
    "rollover_ny follows New York 17:00 (21 UTC US summer, 22 UTC winter); utc21_legacy is kept only for comparison with the p4db experiment.",
]


# ------------------------------------------------------------ pure helpers
def bar_classes(hour: int, cell: str) -> list:
    assert 0 <= hour < 24, "hour must be 0..23"
    assert isinstance(cell, str) and cell, "cell required"
    us_winter = cell in ("both_winter", "uk_summer_us_winter")
    offset = (hour - (22 if us_winter else 21)) % 24
    out = ["all"]
    if 8 <= hour <= 15:
        out.append("flat")
    if offset == 0:
        out.append("rollover_ny")
    if offset in (1, 23):
        out.append("adjacent_ny")
    if hour == 21:
        out.append("utc21_legacy")
    return out


def mad_sigma(values) -> float:
    vals = list(values)
    assert vals, "mad_sigma needs values"
    med = statistics.median(vals)
    return 1.4826 * statistics.median([abs(v - med) for v in vals])


def excess_kurtosis(values: list):
    n = len(values)
    assert n >= 4, "need at least 4 values"
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    if var == 0:
        return None
    return sum((v - mean) ** 4 for v in values) / n / var ** 2 - 3.0


def tail_stats(values: list) -> dict:
    n = len(values)
    assert n >= 0, "n must be non-negative"
    if n < MIN_GROUP:
        return {"n": n}
    sigma = mad_sigma(values)
    p99 = pcts([abs(v) for v in values], (99,))["99"]
    return {"n": n, "mad_sigma": sigma, "abs_p99": p99, "p99_over_mad_sigma": (p99 / sigma if sigma > 0 else None),
            "gaussian_reference": GAUSS_P99, "excess_kurtosis": excess_kurtosis(values)}


def _p(values: list, points=LOO_PCTS):
    assert all(0 <= p <= 100 for p in points), "bad percentile"
    return pcts(values, points) if values else None


def stretch_lengths(epochs: list) -> list:
    """Lengths of maximal hourly-contiguous stretches of sorted epochs."""
    assert epochs == sorted(epochs), "epochs must be sorted"
    assert len(epochs) <= MAX_BARS, "bar ceiling exceeded"
    out, cur, prev = [], 0, None
    for e in epochs:
        if prev is not None and e - prev == HOUR:
            cur += 1
        else:
            if cur:
                out.append(cur)
            cur = 1
        prev = e
    if cur:
        out.append(cur)
    return out


def block_starts(lengths: list, block: int) -> int:
    assert block >= 1, "block length must be >= 1"
    assert all(x >= 1 for x in lengths), "stretch lengths must be >= 1"
    return sum(max(0, x - block + 1) for x in lengths)


def contiguity(rows: list) -> dict:
    epochs = [r["epoch"] for r in rows]
    lengths = stretch_lengths(epochs)
    if not lengths:
        return {"n_bars": 0}
    return {"n_bars": len(epochs), "n_stretches": len(lengths), "length_p50": statistics.median(lengths),
            "length_max": max(lengths), "valid_block_starts": {str(b): block_starts(lengths, b) for b in BLOCK_LENGTHS}}


def masked_epochs(intervals: list, pad: int) -> set:
    """intervals: [(first_epoch, last_epoch)] -> every hourly epoch within +/- pad hours."""
    assert pad >= 0, "pad must be non-negative"
    out: set = set()
    for a, b in intervals:
        lo, hi = a - pad * HOUR, b + pad * HOUR
        assert (hi - lo) // HOUR <= MAX_MASK_HOURS, "mask interval ceiling exceeded"
        out.update(range(lo, hi + 1, HOUR))
    return out


# ------------------------------------------------------------ detector C
def c_prep(legs: list) -> list:
    """Per leg: (volume, zero-range-while-others-move, typical volume)."""
    assert legs, "no legs"
    out = []
    for i, leg in enumerate(legs):
        others = [x.range_bps for j, x in enumerate(legs) if j != i and x.range_bps is not None]
        med = statistics.median(others) if others else 0.0
        zero = leg.range_bps is not None and leg.range_bps == 0 and med >= C_MIN_OTHER_RANGE_BPS
        out.append((leg.volume, zero, leg.typ_volume))
    return out


def c_components(prep: list, stale_vol: float, thin_frac: float) -> tuple:
    """(stale by volume, stale by zero range, thin): detector_c's rule with parameters."""
    assert stale_vol >= 0 and thin_frac > 0, "bad C parameters"
    by_vol = by_zero = thin = False
    for vol, zero, typ in prep:
        sv = vol is not None and vol <= stale_vol
        if sv or zero:
            by_vol, by_zero = by_vol or sv, by_zero or zero
        elif vol is not None and typ and vol < thin_frac * typ:
            thin = True
    return by_vol, by_zero, thin


def c_key(stale_vol: float, thin_frac: float) -> str:
    return f"s{stale_vol:g}_t{thin_frac:g}"


# ------------------------------------------------------------ per-bar row
def compute_row(t: str, quotes: dict, med, typ_vol: dict) -> dict:
    assert set(quotes) == set(PAIRS), "a full bar needs every pair"
    assert c_key(C_STALE_VOL, C_THIN_FRAC) in {c_key(s, f) for s in C_STALE_GRID for f in C_THIN_GRID}, "C defaults must be on the grid"
    obs = [Obs(p.split("_")[0], p.split("_")[1], math.log((quotes[p][0] + quotes[p][1]) / 2) * BPS,
               (math.log(quotes[p][1]) - math.log(quotes[p][0])) * BPS, p) for p in PAIRS]
    main, sub = select_component(sorted({c for o in obs for c in (o.a, o.b)}), obs)
    assert len(sub) == len(PAIRS), "a full bar must fit as one component"
    fit = huber_fit(main, sub, [1.0] * len(sub), math.inf).fit
    loo, scales = loo_fast(fit), _arm_scales(sub)
    legs = [LegInfo(p, quotes[p][4], (quotes[p][2] - quotes[p][3]) / ((quotes[p][0] + quotes[p][1]) / 2) * BPS, typ_vol.get((p, t[11:13]))) for p in PAIRS]
    prep = c_prep(legs)
    grid = {c_key(s, f): c_components(prep, s, f) for s in C_STALE_GRID for f in C_THIN_GRID}
    assert any(grid[c_key(C_STALE_VOL, C_THIN_FRAC)]) == detector_c(legs)["flag"], f"{t}: C grid default disagrees with detector_c"
    spreads = [o.s for o in sub]
    epoch = stored_ts_to_epoch(t)
    return {
        "t": t, "epoch": epoch, "hour": int(t[11:13]), "cell": dst_cell(epoch), "spread": spreads,
        "vol": [q[4] for q in (quotes[p] for p in PAIRS)], "rng": [lg.range_bps for lg in legs], "typ": [lg.typ_volume for lg in legs],
        "loo": loo, "z": [None if 1 - h < BRIDGE_TOL else r / (o.s * math.sqrt(1 - h)) for r, h, o in zip(fit.resid, fit.hat, sub)],
        "loo_over_s": [None if v is None else abs(v) / o.s for v, o in zip(loo, sub)],
        "loo_over_arm": [None if v is None else abs(v) / sc for v, sc in zip(loo, scales)],
        "max_abs": detector_a(sub, loo)["max_abs_bps"], "mean_spread": sum(spreads) / len(spreads),
        "dispersion": max(spreads) / min(spreads),
        "b": {k: detector_b(sub, loo, k, hour_median=med)["max_ratio"] for k in B_KINDS}, "c": grid,
    }


def compute_rows(bars: dict, typ_vol: dict, hooks: dict | None = None) -> tuple:
    assert bars, "no bars"
    assert len(bars) <= MAX_BARS, "bar ceiling exceeded"
    hist, rows, skipped = HourSpreadHistory(), [], 0
    for t in sorted(bars):
        if len(bars[t]) < len(PAIRS):
            skipped += 1
            continue
        row = compute_row(t, bars[t], hist.median_before(int(t[11:13])), typ_vol)
        hist.push(row["hour"], row["mean_spread"])
        rows.append(row)
        if hooks and hooks.get("count"):
            hooks["count"]("bars")
    return rows, skipped


# ------------------------------------------------------------ summaries
def flag_counts(rows: list) -> dict:
    assert rows, "no rows"
    out = {"A": {f"{t:g}": sum(r["max_abs"] > t for r in rows) for t in A_GRID}, "B": {}, "B_defined": {}, "C": {}}
    for k in B_KINDS:
        vals = [r["b"][k] for r in rows if r["b"][k] is not None]
        out["B_defined"][k] = len(vals)
        out["B"][k] = {f"{x:g}": sum(v > x for v in vals) for x in B_GRID}
    for key in rows[0]["c"]:
        comps = [r["c"][key] for r in rows]
        out["C"][key] = {"any": sum(any(c) for c in comps), "stale_volume": sum(c[0] for c in comps),
                         "stale_zero_range": sum(c[1] for c in comps), "thin": sum(c[2] for c in comps)}
    return out


def pair_stats(rows: list, idx: int) -> dict:
    assert rows and 0 <= idx < len(PAIRS), "bad rows or pair index"
    n = len(rows)
    vols = [r["vol"][idx] for r in rows if r["vol"][idx] is not None]
    ratio = [r["vol"][idx] / r["typ"][idx] for r in rows if r["vol"][idx] is not None and r["typ"][idx]]
    rng = [r["rng"][idx] for r in rows]
    col = lambda key: [r[key][idx] for r in rows if r[key][idx] is not None]
    zs = col("z")
    return {
        "spread_bps": _p([r["spread"][idx] for r in rows], (10, 50, 90, 99)), "abs_loo_bps": _p([abs(v) for v in col("loo")], (50, 99, 99.9)),
        "abs_loo_over_spread": _p(col("loo_over_s"), (50, 99, 99.9)), "abs_loo_over_arm_spread": _p(col("loo_over_arm"), (50, 99, 99.9)),
        "noise_ratio_mad": mad_sigma(zs) if len(zs) >= MIN_GROUP else None,
        "volume": _p(vols, (1, 5, 50)), "volume_over_typical": _p(ratio, (1, 5, 50)), "range_bps": _p(rng, (50, 99)),
        "zero_range_share": sum(x == 0 for x in rng) / n, "volume_le_2_share": sum(v <= 2 for v in vols) / n,
        "volume_lt_5pct_typical_share": sum(x < 0.05 for x in ratio) / n,
    }


def spread_corr(rows: list) -> dict:
    assert rows, "no rows"
    cols = [[math.log(max(r["spread"][i], 1e-9)) for r in rows] for i in range(len(PAIRS))]
    matrix, off = {}, []
    for i, a in enumerate(PAIRS):
        matrix[a] = {}
        for j, b in enumerate(PAIRS):
            try:
                c = 1.0 if i == j else statistics.correlation(cols[i], cols[j])
            except statistics.StatisticsError:
                c = None
            matrix[a][b] = c
            if c is not None and i < j:
                off.append(c)
    return {"matrix": matrix, "mean_offdiag": (sum(off) / len(off) if off else None)}


def summarise_group(rows: list, with_corr: bool) -> dict:
    n = len(rows)
    assert n >= 0, "n must be non-negative"
    assert isinstance(with_corr, bool), "with_corr must be a bool"
    if n < MIN_GROUP:
        return {"n": n, "note": f"fewer than {MIN_GROUP} bars"}
    out = {
        "n": n, "max_abs_loo_bps": _p([r["max_abs"] for r in rows]), "mean_spread_bps": _p([r["mean_spread"] for r in rows], (50, 90, 99)),
        "spread_dispersion": _p([r["dispersion"] for r in rows], (50, 99)),
        "loo_tail": tail_stats([v for r in rows for v in r["loo"] if v is not None]),
        "noise_z": tail_stats([v for r in rows for v in r["z"] if v is not None]),
        "flag_counts": flag_counts(rows), "per_pair": {p: pair_stats(rows, i) for i, p in enumerate(PAIRS)},
    }
    if with_corr:
        out["log_spread_corr"] = spread_corr(rows)
    return out


def group_rows(rows: list) -> dict:
    assert rows, "no rows"
    groups = defaultdict(list)
    for r in rows:
        classes = bar_classes(r["hour"], r["cell"])
        for c in classes:
            groups[c].append(r)
        groups[f"h{r['hour']:02d}"].append(r)
        for c in ("rollover_ny", "adjacent_ny", "flat"):
            if c in classes:
                groups[f"{r['cell']}|{c}"].append(r)
    return groups


def summarise_population(rows: list) -> dict:
    assert rows, "empty population"
    return {name: summarise_group(g, with_corr=not re.fullmatch(r"h\d\d", name)) for name, g in sorted(group_rows(rows).items())}


# ------------------------------------------------------------ mask + report
def mask_intervals(rows: list, min_run: int) -> list:
    assert min_run >= 1, "min_run must be >= 1"
    assert rows, "no rows"
    stamps = [r["t"] for r in rows if r["b"]["mean_spread"] is not None and r["b"]["mean_spread"] > B_RATIO]
    out = [{"source": "B_mean_spread_run", "start": x["start"], "end": x["end"], "bars": x["bars"]} for x in _runs(stamps) if x["bars"] >= min_run]
    return out + [{"source": "A_flag", "start": r["t"], "end": r["t"], "bars": 1} for r in rows if r["max_abs"] > A_BPS]


def build_report(cfg: dict, rows: list, skipped: int) -> dict:
    assert rows, "no rows"
    intervals = mask_intervals(rows, cfg["mask_min_run"])
    masked = masked_epochs([(stored_ts_to_epoch(i["start"]), stored_ts_to_epoch(i["end"])) for i in intervals], cfg["mask_pad"])
    for r in rows:
        r["masked"] = r["epoch"] in masked
    ref = [r for r in rows if not r["masked"]]
    assert ref, "the mask removed every bar"
    return {
        "schema": 1, "tool": "run_calibration_stats", "chat": cfg["chat"], "sandbox_db": cfg["sandbox_path"],
        "n_full_bars": len(rows), "n_skipped_degraded": skipped, "n_ref_bars": len(ref),
        "mask": {"pad_hours": cfg["mask_pad"], "min_run_bars": cfg["mask_min_run"], "rule": "A flag; B_mean_spread run of >= min_run bars; both +/- pad hours",
                 "n_masked_bars": len(rows) - len(ref), "intervals": intervals},
        "grids": {"A_bps": list(A_GRID), "B_ratio": list(B_GRID), "C_stale_volume": list(C_STALE_GRID), "C_thin_fraction": list(C_THIN_GRID),
                  "block_lengths_hours": list(BLOCK_LENGTHS), "detector_defaults": {"A_bps": A_BPS, "B_ratio": B_RATIO, "C_stale_volume": C_STALE_VOL, "C_thin_fraction": C_THIN_FRAC}},
        "limitations": LIMITATIONS, "pairs": list(PAIRS),
        "contiguity": {"all": contiguity(rows), "ref": contiguity(ref)},
        "populations": {"all": summarise_population(rows), "ref": summarise_population(ref)},
    }


def dump_bars(path: str, rows: list) -> None:
    assert rows, "no rows"
    assert not Path(path).exists(), f"bar dump already exists: {path}"
    rnd = lambda xs: [None if x is None else round(x, 4) for x in xs]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            rec = {"t": r["t"], "hour": r["hour"], "cell": r["cell"], "masked": r["masked"], "spread": rnd(r["spread"]), "vol": r["vol"],
                   "rng": rnd(r["rng"]), "typ": r["typ"], "loo": rnd(r["loo"]), "z": rnd(r["z"]), "max_abs": round(r["max_abs"], 4),
                   "b": {k: (None if v is None else round(v, 4)) for k, v in r["b"].items()},
                   "c_default": any(r["c"][c_key(C_STALE_VOL, C_THIN_FRAC)])}
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def render_text(rep: dict) -> str:
    assert rep["populations"], "no populations"
    f = lambda x, d=2: "n/a" if x is None else f"{x:.{d}f}"
    lines = [f"Calibration stats | chat {rep['chat']} | {rep['sandbox_db']} | full bars {rep['n_full_bars']} (degraded skipped {rep['n_skipped_degraded']}), ref {rep['n_ref_bars']}, masked {rep['mask']['n_masked_bars']}",
             f"mask: {len(rep['mask']['intervals'])} intervals, pad {rep['mask']['pad_hours']} h, min B_mean run {rep['mask']['min_run_bars']} bars"]
    for pop in ("all", "ref"):
        lines.append(f"\n=== population {pop}")
        lines.append(f"{'group':<26}{'n':>6} {'maxLOO p50':>10}{'p99':>7} {'spread p50':>10} {'noise sd':>8} {'tail p99/s':>10} {'kurt':>6} {'A>10':>5} {'C dflt':>6} {'spr corr':>8}")
        for name in ("all", "flat", "rollover_ny", "adjacent_ny", "utc21_legacy"):
            g = rep["populations"][pop].get(name)
            if not g or "note" in g:
                continue
            fc = g["flag_counts"]
            lines.append(f"{name:<26}{g['n']:>6} {f(g['max_abs_loo_bps']['50']):>10}{f(g['max_abs_loo_bps']['99']):>7} {f(g['mean_spread_bps']['50']):>10} "
                         f"{f(g['noise_z'].get('mad_sigma'), 3):>8} {f(g['loo_tail'].get('p99_over_mad_sigma')):>10} {f(g['loo_tail'].get('excess_kurtosis'), 1):>6} "
                         f"{fc['A']['10']:>5} {fc['C'][c_key(C_STALE_VOL, C_THIN_FRAC)]['any']:>6} {f(g['log_spread_corr']['mean_offdiag']):>8}")
    lines.append("\nA flags at 10 bps by UTC hour (all): " + " ".join(f"{h:02d}:{rep['populations']['all'][f'h{h:02d}']['flag_counts']['A']['10']}" for h in range(24) if f"h{h:02d}" in rep["populations"]["all"] and "flag_counts" in rep["populations"]["all"][f"h{h:02d}"]))
    for pop in ("all", "ref"):
        c = rep["contiguity"][pop]
        lines.append(f"contiguity {pop}: bars {c.get('n_bars')}, stretches {c.get('n_stretches')}, median {c.get('length_p50')}, max {c.get('length_max')}, valid block starts {c.get('valid_block_starts')}")
    lines.append("LIMITATIONS: " + " | ".join(rep["limitations"]))
    return "\n".join(lines)


# ------------------------------------------------------------------ cli
def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Read-only calibration statistics of the sandbox (chat 14p4-ii).")
    p.add_argument("--chat", type=int, required=True, help="integer chat number (14), used in the output name")
    p.add_argument("--sandbox-db", default=None)
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--mask-pad", type=int, default=6, help="hours masked either side of each flagged interval")
    p.add_argument("--mask-min-run", type=int, default=6, help="smallest B_mean_spread run (bars) that is masked")
    p.add_argument("--dump-bars", action="store_true", help="also write per-bar JSONL (10-15 MB, gitignored)")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert 0 <= args.mask_pad <= 72, "--mask-pad must be 0..72"
    assert args.mask_min_run >= 1, "--mask-min-run must be >= 1"
    sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    stem = f"chat{args.chat}_{run_date}{suffix}"
    out_path = str(Path(args.out_dir) / f"calibration_stats_{stem}.json")
    bars_path = str(Path(args.out_dir) / f"calibration_bars_{stem}.jsonl") if args.dump_bars else None
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    assert not (bars_path and Path(bars_path).exists()), f"output already exists: {bars_path} - pick a different --tag"
    return {"chat": args.chat, "sandbox_path": sandbox, "out_path": out_path, "bars_path": bars_path, "tag": args.tag,
            "mask_pad": args.mask_pad, "mask_min_run": args.mask_min_run}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_calibration_stats", chat=args.chat, tag=args.tag, sandbox_db=cfg["sandbox_path"], out_path=cfg["out_path"],
                       mask_pad=cfg["mask_pad"], mask_min_run=cfg["mask_min_run"], dump_bars=bool(cfg["bars_path"])):
        note("sandbox_db", cfg["sandbox_path"])
        con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
        try:
            bars, typ = load_all(con)
        finally:
            con.close()
        t0 = time.perf_counter()
        rows, skipped = compute_rows(bars, typ, {"count": count})
        item("per_bar_rows", time.perf_counter() - t0, bars=len(rows))
        t0 = time.perf_counter()
        report = build_report(cfg, rows, skipped)
        item("summaries", time.perf_counter() - t0)
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        if cfg["bars_path"]:
            dump_bars(cfg["bars_path"], rows)
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}" + (f"\nBar dump: {cfg['bars_path']}" if cfg["bars_path"] else ""))


if __name__ == "__main__":
    main()
