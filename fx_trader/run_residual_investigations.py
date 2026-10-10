# version: 261009
"""
run_residual_investigations.py  (fx_trader/run_residual_investigations.py)

Sandbox-only investigations of the FX triangle residuals (RESEARCH_NOTES
Section 10; ROADMAP chat 14p2). Read-only on the sandbox DB. Triangles are
discovered with currency_graph (as audit_gate.py does); per-bar residual,
spread and tier use currency_graph.evaluate_quotes and the audit's bar_tier.

INV-2  Are the residual tails thin-bar effects? Per triangle: Spearman
       (ranked) correlation of |residual| and of the cost ratio with
         volume_min = the smallest per-leg bar volume (OANDA tick count - to be
                      confirmed; the script reports nulls/zeros), and
         five bar-range definitions x two aggregations over the 3 legs:
           R2 ln(mid_high/mid_low)            R3 ln(ask_high/bid_low) (includes spread)
           R4 |ln(mid_close/mid_open)| (body) R5 true range incl. the gap from the
           previous bar's mid close           R6 Parkinson variance, R2^2/(4 ln 2)
         (max = widest leg, sum = all legs). ALL are reported side by side.
         PRIMARY, named before the first run (261008): R2_max. Eleven columns x
         three triangles is a multiple-comparisons surface: do not present the
         best-looking column as a finding. R6_max is a monotone transform of
         R2_max, so its rank correlations are IDENTICAL by construction (R6_sum
         is not). Decile tables give |residual| p50/p99 and the 'suspect' share.
INV-3  Which clock anchors the hour concentration? |residual| by UTC hour in
       three DST cells (UK, US): both winter, both summer, and UK-winter/
       US-summer (the ~4 weeks a year the clocks disagree: 2nd Sunday March to
       last Sunday March, last Sunday October to 1st Sunday November). The
       fourth combination (UK summer/US winter) never occurs. A New-York-
       anchored hour moves with the US clock; a London-anchored one with the
       UK clock; winter-vs-summer alone cannot separate them (both shift), only
       the mismatch cell can. LOW POWER BY CONSTRUCTION: n per hour in that
       cell is small; the verdict compares means with standard errors and says
       'indeterminate' unless the difference exceeds 2 combined SE.
INV-5  Is the small signed mean a bias or noise? Per triangle: mean, median,
       mean excluding 'suspect' bars, 1%-winsorised mean, a DAY-BLOCK bootstrap
       95% CI (UTC days resampled with replacement) for mean and median, the
       regression of the signed residual on the summed leg spread, and the
       3 x 3 designation table: for each leg called 'direct', the mean of
       (synthetic - direct) in that leg's as-quoted orientation. It equals
       -d_j x (cycle residual), so the sign pattern is a labelling artefact; the
       table also counts how many of the 27 one-designation-per-triangle
       choices give an all-same-sign pattern. Quantisation floor: decimal
       precision of the stored closes is MEASURED, then the std of the cycle
       residual from independent uniform rounding of bid and ask is computed
       (an assumption - hypothesis only); rounding noise has mean zero, so it
       cannot by itself produce a bias.

NOT covered: leg attribution by leave-one-out (14p3/14p4), the event calendar
for stress days (17p1), any second source (16p1). Results are about bar-close
bid/ask from one broker, not tradeable prices. Nothing here concerns costs.

Run (from fx_trader/):
  python3 run_residual_investigations.py --chat 14 --tag p2c --sandbox-db data/sandbox_22Q1to26Q1.db
Output: reports/residual_inv_<first>to<last>_chat<N>_<run date, London>[_<tag>].json
(refuses to overwrite) plus a text summary on stdout.
"""

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import audit_sandbox_alignment as au
from currency_graph import enumerate_cycles, evaluate_quotes
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import parse_ts, stored_ts_to_epoch

BPS = 1e4
LN2 = math.log(2.0)
PRIMARY = "R2_max"
RANGE_KINDS = ("R2", "R3", "R4", "R5", "R6")
AGGS = ("max", "sum")
VARIANTS = tuple(f"{k}_{a}" for k in RANGE_KINDS for a in AGGS)
MAX_ROWS = 2_000_000
MAX_BOOT = 20_000
MIN_HOUR_N = 10            # hours with fewer bars in a cell are not eligible as a 'peak hour'
SE_MULTIPLE = 2.0          # INV-3: difference must exceed this many combined SE
DECILES = 10
BOOT_LEVEL = 0.95
UK_ZONE, US_ZONE = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
CELLS = {(False, False): "both_winter", (True, True): "both_summer",
         (False, True): "uk_winter_us_summer", (True, False): "uk_summer_us_winter"}
LIMITATIONS = [
    "Bar-close bid/ask from one broker (OANDA); not simultaneous tick snapshots.",
    "volume is assumed to be OANDA's per-bar tick count (UNVERIFIED); nulls/zeros are reported.",
    "Eleven INV-2 columns x three triangles is a multiple-comparisons surface; the primary column was named in advance (R2_max).",
    "INV-3 mismatch cell has few bars per hour: its verdict is low power by construction.",
    "Quantisation floor assumes independent uniform rounding of bid and ask (hypothesis).",
    "Triangles share GBP_USD, so the three triangles are not independent samples.",
]


# ------------------------------------------------------------- loading
def load_bars(con, instrument: str, granularity: str) -> dict:
    """epoch -> (bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, volume)."""
    assert instrument and granularity, "instrument and granularity required"
    rows = con.execute(
        "SELECT timestamp, bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, volume "
        "FROM candles WHERE instrument=? AND granularity=? ORDER BY timestamp", (instrument, granularity))
    out = {}
    for row in rows:
        out[stored_ts_to_epoch(row[0])] = tuple(row[1:])
        assert len(out) <= MAX_ROWS, "row ceiling exceeded"
    return out


def derive_leg_rows(bars: dict) -> dict:
    """epoch -> (R2, R3, R4, R5, R6, volume, bid_close, ask_close, mid_close), ranges in bps (R6 in bps^2)."""
    assert bars, "no bars"
    out, prev = {}, None
    for t in sorted(bars):
        bo, bh, bl, bc, ao, ah, al, ac, vol = bars[t]
        mid_o, mid_h, mid_l, mid_c = (bo + ao) / 2, (bh + ah) / 2, (bl + al) / 2, (bc + ac) / 2
        assert mid_h >= mid_l > 0 and bl > 0, f"invalid high/low at {t}"
        r2 = math.log(mid_h / mid_l) * BPS
        r3 = math.log(ah / bl) * BPS
        r4 = abs(math.log(mid_c / mid_o)) * BPS
        r5 = math.log(max(mid_h, prev) / min(mid_l, prev)) * BPS if prev is not None else r2
        out[t] = (r2, r3, r4, r5, r2 * r2 / (4 * LN2), vol, bc, ac, mid_c)
        prev = mid_c
    return out


def triangle_specs(fx_names: list) -> list:
    """[(label, spec)] with spec = ((instrument, d), ...), from currency_graph, sorted by instrument set."""
    assert fx_names, "no FX instruments"
    edges = au.build_topology(sorted(fx_names), [])
    cycles = enumerate_cycles(edges, "asset", max_len=3).cycles
    specs = [tuple((edges[i].instrument, d) for i, d in cyc) for cyc in cycles if len(cyc) == 3]
    specs.sort(key=lambda s: sorted(n for n, _d in s))
    return [(au.cycle_label(s), s) for s in specs]


# --------------------------------------------------------------- frame
def build_frame(spec: tuple, legs: dict) -> dict:
    """Column lists over the bars common to all legs of one triangle."""
    assert len(spec) == 3, "a triangle has three legs"
    series = [legs[n] for n, _d in spec]
    dirs = [d for _n, d in spec]
    common = sorted(set.intersection(*(set(s) for s in series)))
    assert 0 < len(common) <= MAX_ROWS, "no common bars or ceiling exceeded"
    cols = {"stamps": common, "resid": [], "abs": [], "spread": [], "ratio": [], "tier": [], "volume_min": []}
    cols.update({v: [] for v in VARIANTS})
    for t in common:
        rows = [s[t] for s in series]
        resid, _f, _r, spread = evaluate_quotes([(r[6], r[7], d) for r, d in zip(rows, dirs)])
        a = abs(resid)
        cols["resid"].append(resid)
        cols["abs"].append(a)
        cols["spread"].append(spread)
        cols["ratio"].append(a / spread if spread > 0 else None)
        cols["tier"].append(au.bar_tier(a, spread))
        vols = [r[5] for r in rows]
        cols["volume_min"].append(None if any(v is None for v in vols) else min(vols))
        for k, name in enumerate(RANGE_KINDS):
            vals = [r[k] for r in rows]
            cols[f"{name}_max"].append(max(vals))
            cols[f"{name}_sum"].append(sum(vals))
    return cols


# ---------------------------------------------------------------- INV-2
def _spearman(x: list, y: list) -> float | None:
    assert len(x) == len(y), "length mismatch"
    if len(x) < 3:
        return None
    try:
        return statistics.correlation(x, y, method="ranked")
    except statistics.StatisticsError:
        return None


def decile_groups(values: list) -> list:
    """Index groups of (near-)equal size after sorting by value; ties may split across groups."""
    n = len(values)
    assert n >= DECILES, f"need at least {DECILES} values for deciles"
    order = sorted(range(n), key=lambda i: values[i])
    return [order[d * n // DECILES:(d + 1) * n // DECILES] for d in range(DECILES)]


def decile_table(values: list, abs_resid: list, tiers: list) -> list:
    rows = []
    for d, grp in enumerate(decile_groups(values)):
        p = au.pcts([abs_resid[i] for i in grp], (50, 99))
        rows.append({"decile": d + 1, "n": len(grp), "lo": values[grp[0]], "hi": values[grp[-1]],
                     "abs_p50": p["50"], "abs_p99": p["99"],
                     "suspect_share": sum(tiers[i] == "suspect" for i in grp) / len(grp)})
    return rows


def analyse_inv2(frame: dict) -> dict:
    assert frame["abs"], "empty frame"
    columns = {v: frame[v] for v in VARIANTS}
    columns["volume_min"] = frame["volume_min"]
    out = {"primary": PRIMARY, "columns": {}, "decile_tables": {}}
    for name, vals in columns.items():
        idx = [i for i, v in enumerate(vals) if v is not None]
        if len(idx) < DECILES:
            out["columns"][name] = {"n": len(idx), "note": "too few non-null values"}
            continue
        ridx = [i for i in idx if frame["ratio"][i] is not None]
        x = [vals[i] for i in idx]
        sub_abs = [frame["abs"][i] for i in idx]
        tables = decile_table(x, sub_abs, [frame["tier"][i] for i in idx])
        out["columns"][name] = {
            "n": len(idx),
            "spearman_vs_abs_resid": _spearman(x, sub_abs),
            "spearman_vs_cost_ratio": _spearman([vals[i] for i in ridx], [frame["ratio"][i] for i in ridx]),
            "decile_p99_abs_resid": [r["abs_p99"] for r in tables],
        }
        if name in (PRIMARY, "volume_min"):
            out["decile_tables"][name] = tables
    vidx = [i for i, v in enumerate(frame["volume_min"]) if v is not None]
    out["collinearity_spearman_volume_vs_R2_max"] = _spearman([frame["volume_min"][i] for i in vidx], [frame["R2_max"][i] for i in vidx])
    vols = [v for v in frame["volume_min"] if v is not None]
    out["volume_diagnostics"] = {
        "bars": len(frame["volume_min"]), "null_bars": len(frame["volume_min"]) - len(vols),
        "zero_bars": sum(v == 0 for v in vols), "min": min(vols) if vols else None,
        "median": statistics.median(vols) if vols else None, "max": max(vols) if vols else None}
    return out


# ---------------------------------------------------------------- INV-3
def dst_cell(epoch: int) -> str:
    assert isinstance(epoch, int) and epoch >= 0, "epoch must be a non-negative int"
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    uk = dt.astimezone(UK_ZONE).dst() != timedelta(0)
    us = dt.astimezone(US_ZONE).dst() != timedelta(0)
    return CELLS[(uk, us)]


def _hour_stats(values: list, tiers: list) -> dict:
    n = len(values)
    mean = sum(values) / n
    se = (statistics.stdev(values) / math.sqrt(n)) if n >= 2 else None
    p = au.pcts(values, (50, 90))
    return {"n": n, "mean_abs": mean, "se_mean": se, "median_abs": p["50"], "p90_abs": p["90"],
            "suspect_share": sum(t == "suspect" for t in tiers) / n}


def _peak_hour(by_hour: dict) -> int | None:
    eligible = {h: s for h, s in by_hour.items() if s["n"] >= MIN_HOUR_N}
    return max(eligible, key=lambda h: eligible[h]["mean_abs"]) if eligible else None


def mismatch_verdict(cells: dict) -> dict:
    """Compare the mismatch cell's means at the winter-peak hour (UK-anchored
    prediction) and the summer-peak hour (US-anchored prediction)."""
    assert isinstance(cells, dict), "cells must be a dict"
    winter, summer = cells.get("both_winter", {}), cells.get("both_summer", {})
    mism = cells.get("uk_winter_us_summer", {})
    w_peak, s_peak = _peak_hour(winter), _peak_hour(summer)
    out = {"winter_peak_hour": w_peak, "summer_peak_hour": s_peak}
    if w_peak is None or s_peak is None:
        return out | {"verdict": "indeterminate (a season cell has no eligible hour)"}
    if w_peak == s_peak:
        return out | {"verdict": f"no clock shift between seasons (peak {w_peak} UTC in both): cannot separate UK from US anchoring"}
    uk_stats, us_stats = mism.get(w_peak), mism.get(s_peak)
    if not uk_stats or not us_stats or uk_stats["n"] < 2 or us_stats["n"] < 2:
        return out | {"verdict": "indeterminate (too few mismatch-cell bars at the candidate hours)"}
    diff = us_stats["mean_abs"] - uk_stats["mean_abs"]
    se = math.sqrt((us_stats["se_mean"] or 0) ** 2 + (uk_stats["se_mean"] or 0) ** 2)
    out |= {"mean_at_us_anchored_hour": us_stats["mean_abs"], "mean_at_uk_anchored_hour": uk_stats["mean_abs"],
            "n_us_hour": us_stats["n"], "n_uk_hour": uk_stats["n"], "difference": diff, "combined_se": se}
    if se > 0 and diff > SE_MULTIPLE * se:
        return out | {"verdict": "US-anchored (moves with the New York clock)"}
    if se > 0 and -diff > SE_MULTIPLE * se:
        return out | {"verdict": "UK-anchored (moves with the London clock)"}
    return out | {"verdict": "indeterminate (difference within 2 combined SE: low power)"}


def analyse_inv3(frame: dict) -> dict:
    assert frame["abs"], "empty frame"
    grouped = defaultdict(lambda: defaultdict(lambda: ([], [])))
    for t, a, tier in zip(frame["stamps"], frame["abs"], frame["tier"]):
        vals, tiers = grouped[dst_cell(t)][(t // 3600) % 24]
        vals.append(a)
        tiers.append(tier)
    cells = {cell: {h: _hour_stats(v, tr) for h, (v, tr) in sorted(hours.items())} for cell, hours in grouped.items()}
    summary = {cell: {"bars": sum(s["n"] for s in hs.values()),
                      "top_hours_by_mean_abs": sorted((h for h, s in hs.items() if s["n"] >= MIN_HOUR_N),
                                                      key=lambda h: -hs[h]["mean_abs"])[:3]}
               for cell, hs in cells.items()}
    return {"cells": cells, "cell_summary": summary, "mismatch_test": mismatch_verdict(cells)}


# ---------------------------------------------------------------- INV-5
def day_block_bootstrap(values: list, days: list, n_boot: int, seed: int) -> dict:
    """UTC-day block bootstrap of the mean and median. Returns the two lists of
    resampled statistics (length n_boot)."""
    assert len(values) == len(days) and values, "values/days mismatch or empty"
    assert 1 <= n_boot <= MAX_BOOT, f"n_boot must be in [1, {MAX_BOOT}]"
    groups = defaultdict(list)
    for v, d in zip(values, days):
        groups[d].append(v)
    keys = sorted(groups)
    rng = random.Random(seed)
    means, medians = [], []
    for _ in range(n_boot):
        sample = []
        for _k in range(len(keys)):
            sample.extend(groups[keys[rng.randrange(len(keys))]])
        means.append(sum(sample) / len(sample))
        medians.append(statistics.median(sample))
    return {"mean": means, "median": medians}


def _ci(samples: list) -> tuple:
    tail = (1 - BOOT_LEVEL) / 2 * 100
    p = au.pcts(samples, (tail, 100 - tail))
    return p[str(tail)], p[str(100 - tail)]


def winsorised_mean(values: list, pct: float = 1.0) -> float:
    assert values and 0 <= pct < 50, "bad winsorising inputs"
    p = au.pcts(values, (pct, 100 - pct))
    lo, hi = p[str(pct)], p[str(100 - pct)]
    return sum(min(max(v, lo), hi) for v in values) / len(values)


def observed_decimals(values, max_d: int = 8) -> int | None:
    """Smallest d such that every value equals round(value, d); None if > max_d."""
    assert max_d >= 1, "max_d must be positive"
    vals = list(values)
    assert vals, "no values"
    for d in range(max_d + 1):
        if all(round(v, d) == v for v in vals):
            return d
    return None


def floor_std_bps(decimals: list, mids: list) -> float:
    """Std (bps) of a 3-leg cycle residual from independent uniform rounding of
    bid and ask at the given decimals; mid error variance = unit^2/24."""
    assert len(decimals) == len(mids) and decimals, "decimals/mids mismatch"
    var = sum(((10.0 ** -d) ** 2 / 24) / (m * m) for d, m in zip(decimals, mids))
    return math.sqrt(var) * BPS


def designation_table(frame: dict, spec: tuple, boots: dict) -> list:
    """Per leg called 'direct': synthetic - direct = -d_j x residual, in that leg's quoted orientation."""
    resid = frame["resid"]
    rows = []
    for name, d in spec:
        s = [-d * r for r in resid]
        m_lo, m_hi = _ci([-d * x for x in boots["mean"]])
        md_lo, md_hi = _ci([-d * x for x in boots["median"]])
        rows.append({"direct": name, "d_in_walk": d, "mean_bps": sum(s) / len(s), "median_bps": statistics.median(s),
                     "mean_ci95": [m_lo, m_hi], "median_ci95": [md_lo, md_hi],
                     "mean_ci_contains_0": m_lo <= 0 <= m_hi, "median_ci_contains_0": md_lo <= 0 <= md_hi})
    return rows


def analyse_inv5(frame: dict, spec: tuple, legs: dict, n_boot: int, seed: int) -> dict:
    resid, spread = frame["resid"], frame["spread"]
    assert len(resid) >= 30, "too few bars for INV-5"
    days = [datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d") for t in frame["stamps"]]
    boots = day_block_bootstrap(resid, days, n_boot, seed)
    ex = [r for r, t in zip(resid, frame["tier"]) if t != "suspect"]
    slope, intercept = statistics.linear_regression(spread, resid)
    decimals = {n: observed_decimals([r[6] for r in legs[n].values()] + [r[7] for r in legs[n].values()]) for n, _d in spec}
    mids = [statistics.median(r[8] for r in legs[n].values()) for n, _d in spec]
    floor = floor_std_bps([decimals[n] if decimals[n] is not None else 8 for n, _d in spec], mids) if all(
        decimals[n] is not None for n, _d in spec) else None
    lo, hi = _ci(boots["mean"])
    mlo, mhi = _ci(boots["median"])
    n = len(resid)
    return {
        "n": n, "n_days": len(set(days)), "n_boot": n_boot, "seed": seed,
        "signed_residual_in_walk_orientation": {
            "mean_bps": sum(resid) / n, "median_bps": statistics.median(resid),
            "mean_excluding_suspect_bps": sum(ex) / len(ex) if ex else None, "n_excluding_suspect": len(ex),
            "winsorised_1pct_mean_bps": winsorised_mean(resid),
            "mean_ci95": [lo, hi], "median_ci95": [mlo, mhi],
            "mean_ci_contains_0": lo <= 0 <= hi, "median_ci_contains_0": mlo <= 0 <= mhi},
        "regression_on_summed_spread": {"slope": slope, "intercept_bps": intercept,
                                        "pearson_r": statistics.correlation(spread, resid) if len(set(spread)) > 1 else None},
        "designation_table": designation_table(frame, spec, boots),
        "observed_decimals": decimals, "floor_std_bps": floor,
        "floor_se_of_mean_bps": (floor / math.sqrt(n)) if floor is not None else None,
    }


def consistent_sign_combinations(per_triangle: list) -> dict:
    """Of the 27 one-designation-per-triangle choices, how many give all-same-sign means."""
    assert len(per_triangle) == 3, "expects three triangles"
    signs = [[1 if row["mean_bps"] > 0 else -1 for row in t["designation_table"]] for t in per_triangle]
    total = consistent = 0
    for a in signs[0]:
        for b in signs[1]:
            for c in signs[2]:
                total += 1
                consistent += (a == b == c)
    return {"combinations": total, "all_same_sign": consistent}


# ------------------------------------------------------------------ run
def run_investigations(cfg: dict, instruments: list, hooks: dict | None = None) -> dict:
    hooks = hooks or {}
    con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
    try:
        bars = {n: load_bars(con, n, g) for n, g, _c, _p in instruments if g and "/" not in n}
    finally:
        con.close()
    bars = {n: b for n, b in bars.items() if b}
    legs = {n: derive_leg_rows(b) for n, b in bars.items()}
    triangles = triangle_specs(sorted(legs))
    assert len(triangles) >= 1, "no FX triangles found"
    report = {"schema": 1, "tool": "run_residual_investigations", "chat": cfg["chat"], "sandbox_db": cfg["sandbox_path"],
              "primary_range_variant": PRIMARY, "variants": list(VARIANTS), "n_boot": cfg["n_boot"], "seed": cfg["seed"],
              "limitations": LIMITATIONS, "triangles": []}
    for label, spec in triangles:
        t0 = time.perf_counter()
        frame = build_frame(spec, legs)
        entry = {"cycle": label, "n": len(frame["abs"]), "inv2": analyse_inv2(frame), "inv3": analyse_inv3(frame),
                 "inv5": analyse_inv5(frame, spec, legs, cfg["n_boot"], cfg["seed"])}
        report["triangles"].append(entry)
        if hooks.get("count"):
            hooks["count"]("bars", len(frame["abs"]))
        if hooks.get("item"):
            hooks["item"](f"triangle/{label}", time.perf_counter() - t0)
    if len(report["triangles"]) == 3:
        report["inv5_designation_combinations"] = consistent_sign_combinations([t["inv5"] for t in report["triangles"]])
    return report


def render_text(report: dict) -> str:
    assert report["triangles"], "no triangles"
    lines = [f"Residual investigations | chat {report['chat']} | {report['sandbox_db']} | primary range variant {report['primary_range_variant']}"]
    for t in report["triangles"]:
        lines.append(f"\n=== {t['cycle']}  (n={t['n']})")
        i2 = t["inv2"]
        vd = i2["volume_diagnostics"]
        lines.append(f"INV-2 volume: null bars {vd['null_bars']}, zero bars {vd['zero_bars']}, min/median/max {vd['min']}/{vd['median']}/{vd['max']}")
        lines.append(f"  {'column':<12} {'rho vs |resid|':>15} {'rho vs ratio':>13}   p99 by decile (1..10)")
        for name, c in i2["columns"].items():
            if "note" in c:
                lines.append(f"  {name:<12} {c['note']}")
                continue
            f = lambda x: f"{x:+.3f}" if x is not None else "n/a"
            deciles = " ".join(f"{v:.1f}" for v in c["decile_p99_abs_resid"])
            lines.append(f"  {name:<12} {f(c['spearman_vs_abs_resid']):>15} {f(c['spearman_vs_cost_ratio']):>13}   {deciles}")
        lines.append(f"  collinearity rho(volume_min, R2_max) = {i2['collinearity_spearman_volume_vs_R2_max']}")
        i3 = t["inv3"]
        for cell, s in i3["cell_summary"].items():
            lines.append(f"INV-3 {cell}: bars={s['bars']} top hours (UTC) by mean |resid|: {s['top_hours_by_mean_abs']}")
        lines.append(f"  mismatch test: {i3['mismatch_test']}")
        i5 = t["inv5"]
        s = i5["signed_residual_in_walk_orientation"]
        lines.append(f"INV-5 signed residual (walk orientation) bps: mean {s['mean_bps']:+.4f}, median {s['median_bps']:+.4f}, "
                     f"mean ex-suspect {s['mean_excluding_suspect_bps']:+.4f}, winsorised {s['winsorised_1pct_mean_bps']:+.4f}")
        lines.append(f"  day-block bootstrap 95% CI ({i5['n_days']} days, B={i5['n_boot']}): mean [{s['mean_ci95'][0]:+.4f}, {s['mean_ci95'][1]:+.4f}] "
                     f"contains 0: {s['mean_ci_contains_0']}; median [{s['median_ci95'][0]:+.4f}, {s['median_ci95'][1]:+.4f}] contains 0: {s['median_ci_contains_0']}")
        r = i5["regression_on_summed_spread"]
        lines.append(f"  regression on summed spread: slope {r['slope']:+.4f}, intercept {r['intercept_bps']:+.4f} bps, r {r['pearson_r']}")
        for row in i5["designation_table"]:
            lines.append(f"  direct={row['direct']:<8} mean {row['mean_bps']:+.4f} median {row['median_bps']:+.4f} bps  CI(mean) contains 0: {row['mean_ci_contains_0']}")
        lines.append(f"  observed decimals {i5['observed_decimals']}; floor std {i5['floor_std_bps']} bps; floor SE of the mean {i5['floor_se_of_mean_bps']} bps")
    if "inv5_designation_combinations" in report:
        lines.append(f"\nINV-5 one-designation-per-triangle choices with an all-same-sign pattern: {report['inv5_designation_combinations']}")
    lines.append("LIMITATIONS: " + " | ".join(report["limitations"]))
    return "\n".join(lines)


# ------------------------------------------------------------------ cli
def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="INV-2 / INV-3 / INV-5 residual investigations (read-only).")
    p.add_argument("--chat", type=int, required=True, help="chat number, used in the output name")
    p.add_argument("--sandbox-db", default=None, help="sqlite path; default = sandbox_config.py's sandbox")
    p.add_argument("--tag", default="", help="optional output-name suffix (letters, digits, hyphen)")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--bootstrap", type=int, default=1000, help=f"day-block bootstrap draws, 1..{MAX_BOOT}")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert 1 <= args.bootstrap <= MAX_BOOT, f"--bootstrap must be in [1, {MAX_BOOT}]"
    sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    first, last = au.db_coverage(sandbox)
    suffix = f"_{args.tag}" if args.tag else ""
    name = f"residual_inv_{parse_ts(first):%y%m%d}to{parse_ts(last):%y%m%d}_chat{args.chat}_{run_date}{suffix}.json"
    out_path = str(Path(args.out_dir) / name)
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    return {"sandbox_path": sandbox, "out_path": out_path, "chat": args.chat, "tag": args.tag,
            "n_boot": args.bootstrap, "seed": args.seed}


def main(argv: list | None = None) -> None:
    from instrument_config import INSTRUMENTS
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_residual_investigations", chat=args.chat, tag=args.tag, sandbox_db=cfg["sandbox_path"],
                       out_path=cfg["out_path"], n_boot=cfg["n_boot"], seed=cfg["seed"]):
        note("sandbox_db", cfg["sandbox_path"])
        report = run_investigations(cfg, INSTRUMENTS, {"item": item, "count": count})
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}")


if __name__ == "__main__":
    main()
