# version: 261013
"""
run_inv4_attribution.py  (fx_trader/run_inv4_attribution.py)

INV-4 leg attribution on REAL bars (chat 14p4). Read-only on the sandbox DB.
For each named bar (default: the 17 bars with max |LOO| > 10 bps found in
14p3's by-hour run, FAULT_BARS_261012) it builds the 8-pair observation set
from bar-close bid/ask and reports, per weight scheme:
  - plain least squares: the arm/hub holding the largest |LOO|
  - Huber (every k in robust.HUBER_KS x scale axis): the arm/hub with the
    largest robust residual, and the gauge-free difference between the plain
    and the robust strengths on the INNOCENT currencies (= the least-squares
    smear on that bar; the faulted arm's own currency excluded)
and, scheme-independent: the hub/arm median-of-paths verdict with its four
path estimates, detectors A, B (mean_spread, arm_spread, hour_median) and C.
hour_median uses the previous B_WINDOW same-hour bars with all 8 pairs (never
the bar itself). C uses volume and the bar's bid high-low range; its typical
volume is the median over the same pair and UTC hour in the whole DB (so it
includes the bar and the other fault bars: a mild, stated leak).

NOT covered: a cause for any fault (needs a second source, 16p1), the other
non-fault bars, crypto. Volume is assumed to be OANDA's tick count (UNVERIFIED).
Arm legs are in series: the verdict names the ARM, never one leg.

Run (from fx_trader/):
  python3 run_inv4_attribution.py --chat 14 --tag p4i --sandbox-db data/sandbox_22Q1to26Q1.db
  optional: --bars 2022-05-04T20:00:00.000000000Z,...   (stored-format stamps)
Output: reports/inv4_attribution_chat<N>_<run date, London>[_<tag>].json
(refuses to overwrite) plus a text summary on stdout.
"""

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from currency_graph.detectors import B_WINDOW, LegInfo, detector_a, detector_b, detector_c
from currency_graph.robust import HUBER_KS, SCALE_AXES, group_of, hub_arms, huber_fit, path_attribution
from currency_graph.strengths import Obs, SCHEMES, loo_fast, select_component, weights_for
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path

PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
BPS = 1e4
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00:00\.000000000Z$")
MAX_BARS = 200
# MEASURED 261012 (chat 14p4, from the dump + by-hour JSON): bars with max |LOO| > 10 bps, unweighted.
FAULT_BARS_261012 = (
    "2022-05-04T19:00:00.000000000Z", "2022-05-04T20:00:00.000000000Z", "2022-05-04T21:00:00.000000000Z",
    "2022-05-08T21:00:00.000000000Z", "2022-05-19T21:00:00.000000000Z", "2022-11-03T11:00:00.000000000Z",
    "2022-12-11T22:00:00.000000000Z", "2023-01-26T22:00:00.000000000Z", "2023-03-19T21:00:00.000000000Z",
    "2023-04-07T19:00:00.000000000Z", "2023-04-16T21:00:00.000000000Z", "2024-08-04T21:00:00.000000000Z",
    "2024-12-24T21:00:00.000000000Z", "2025-04-07T21:00:00.000000000Z", "2025-04-13T21:00:00.000000000Z",
    "2025-04-15T21:00:00.000000000Z", "2025-08-03T21:00:00.000000000Z",
)


def _spread_bps(bid: float, ask: float) -> float:
    return (math.log(ask) - math.log(bid)) * BPS


def load_bar(con, stamp: str) -> list:
    """[(pair, bid_close, ask_close, volume, range_bps)] for the pairs present at `stamp`."""
    assert STAMP_RE.match(stamp), f"stamp must be stored-format on the hour: {stamp!r}"
    rows = []
    for p in PAIRS:
        r = con.execute("SELECT bid_close, ask_close, bid_high, bid_low, volume FROM candles WHERE instrument=? AND granularity='H1' AND timestamp=?", (p, stamp)).fetchone()
        if r:
            rows.append((p, r[0], r[1], r[4], (r[2] - r[3]) / ((r[0] + r[1]) / 2) * BPS))
    return rows


def typical_volume(con, pair: str, hour: str):
    vals = [v for (v,) in con.execute("SELECT volume FROM candles WHERE instrument=? AND granularity='H1' AND substr(timestamp,12,2)=? AND volume IS NOT NULL", (pair, hour))]
    return statistics.median(vals) if vals else None


def trailing_hour_median(con, stamp: str):
    """Median over the previous B_WINDOW same-hour bars (all 8 pairs present) of the bar mean pair spread."""
    assert STAMP_RE.match(stamp), "bad stamp"
    per_t = defaultdict(list)
    for p in PAIRS:
        for t, bid, ask in con.execute(
                "SELECT timestamp, bid_close, ask_close FROM candles WHERE instrument=? AND granularity='H1' AND substr(timestamp,12,2)=? AND timestamp<? ORDER BY timestamp DESC LIMIT ?",
                (p, stamp[11:13], stamp, 4 * B_WINDOW)):
            per_t[t].append(_spread_bps(bid, ask))
    full = sorted((t for t, v in per_t.items() if len(v) == len(PAIRS)), reverse=True)[:B_WINDOW]
    return statistics.median(sum(per_t[t]) / len(PAIRS) for t in full) if len(full) >= 10 else None


def _top_group(names: list, scores: list, groups: dict):
    best = max(((abs(v), n) for n, v in zip(names, scores) if v is not None), default=None)
    return (groups[best[1]], best[1], best[0]) if best else (None, None, None)


def _smear(c_ls: dict, c_rob: dict, main: list, exclude: set) -> float:
    keep = [n for n in main if n not in exclude]
    m1, m2 = sum(c_ls[n] for n in keep) / len(keep), sum(c_rob[n] for n in keep) / len(keep)
    return max(abs((c_ls[n] - m1) - (c_rob[n] - m2)) for n in keep)


def analyse_bar(con, stamp: str) -> dict:
    assert STAMP_RE.match(stamp), "bad stamp"
    rows = load_bar(con, stamp)
    assert len(rows) >= 3, f"{stamp}: fewer than 3 pairs present"
    obs = [Obs(p.split("_")[0], p.split("_")[1], math.log((b + a) / 2) * BPS, _spread_bps(b, a), p) for p, b, a, _v, _r in rows]
    hour = stamp[11:13]
    legs = [LegInfo(p, v, r, typical_volume(con, p, hour)) for p, _b, _a, v, r in rows]
    nodes = sorted({c for o in obs for c in (o.a, o.b)})
    main, sub = select_component(nodes, obs)
    names = [o.name for o in sub]
    groups = group_of(hub_arms(sub), sub) if len(sub) == len(PAIRS) else None
    out = {"t": stamp, "pairs_present": len(rows), "schemes": {}, "legs": {l.name: {"volume": l.volume, "range_bps": round(l.range_bps, 2), "typ_volume": l.typ_volume} for l in legs},
           "spreads_bps": {o.name: round(o.s, 2) for o in obs}}
    for scheme in SCHEMES:
        w = weights_for(scheme, sub)
        ls = huber_fit(main, sub, w, math.inf).fit
        entry = {"ls_loo_top": _top_group(names, loo_fast(ls), groups) if groups else None, "huber": {}}
        culprit = entry["ls_loo_top"][0] if groups else None
        exclude = {culprit.split(":")[1]} if culprit and culprit.startswith("arm:") else set()
        for k in HUBER_KS:
            for ax in SCALE_AXES:
                if math.isinf(k):
                    continue
                r = huber_fit(main, sub, w, k, ax)
                entry["huber"][f"H{k}|{ax}"] = {"top": _top_group(names, r.fit.resid, groups) if groups else None,
                                               "ls_smear_bps": _smear(ls.c, r.fit.c, main, exclude), "iterations": r.iterations, "converged": r.converged}
        out["schemes"][scheme] = entry
    pa = path_attribution(sub) if groups else None
    unw = loo_fast(huber_fit(main, sub, weights_for("unweighted", sub), math.inf).fit)
    mean_hist = trailing_hour_median(con, stamp)
    out["hub_arm"] = None if pa is None else {"verdict": pa.verdict, "median_bps": pa.median_bps, "paths": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()} for p in pa.paths]}
    out["detectors"] = {"A": detector_a(sub, unw), "C": detector_c(legs), "hour_median_used": mean_hist,
                        **{f"B_{k}": detector_b(sub, unw, k, hour_median=mean_hist) for k in ("mean_spread", "arm_spread", "hour_median")}}
    return out


def run_inv4(cfg: dict, hooks: dict | None = None) -> dict:
    hooks = hooks or {}
    assert 1 <= len(cfg["bars"]) <= MAX_BARS, f"bars must be 1..{MAX_BARS}"
    con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
    try:
        bars = []
        for stamp in cfg["bars"]:
            bars.append(analyse_bar(con, stamp))
            if hooks.get("count"):
                hooks["count"]("bars")
    finally:
        con.close()
    return {"schema": 1, "tool": "run_inv4_attribution", "chat": cfg["chat"], "sandbox_db": cfg["sandbox_path"], "n_bars": len(bars), "bars": bars}


def render_text(rep: dict) -> str:
    assert rep["bars"], "no bars"
    lines = [f"INV-4 attribution | chat {rep['chat']} | {rep['sandbox_db']} | {rep['n_bars']} bars",
             f"{'bar (UTC)':<14} {'LS-LOO top (unw)':<22} {'Huber1.0 sp top':<18} {'smear':>6} {'hub/arm':<10} {'A':>2} {'Bmn':>3} {'Barm':>4} {'Bhr':>3} {'C stale/thin'}"]
    for b in rep["bars"]:
        u = b["schemes"]["unweighted"]
        h = u["huber"]["H1.0|spread"]
        d = b["detectors"]
        f = lambda x: {True: "Y", False: "-", None: "?"}[x["flag"]]
        top = lambda t: "n/a" if not t else f"{t[0]}/{t[1]}({t[2]:.0f})"
        lines.append(f"{b['t'][:13]:<14} {top(u['ls_loo_top']):<22} {top(h['top']):<18} {h['ls_smear_bps']:6.1f} {(b['hub_arm'] or {}).get('verdict', 'n/a'):<10} "
                     f"{f(d['A']):>2} {f(d['B_mean_spread']):>3} {f(d['B_arm_spread']):>4} {f(d['B_hour_median']):>3} {d['C']['stale']}/{d['C']['thin']}")
    return "\n".join(lines)


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="INV-4 leg attribution on named real bars (read-only).")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--sandbox-db", default=None)
    p.add_argument("--bars", default=None, help="comma list of stored-format stamps; default = the 17 measured fault bars")
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    bars = tuple(s.strip() for s in args.bars.split(",")) if args.bars else FAULT_BARS_261012
    assert bars and all(STAMP_RE.match(s) for s in bars), "every --bars stamp must be stored-format on the hour"
    sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    out_path = str(Path(args.out_dir) / f"inv4_attribution_chat{args.chat}_{run_date}{suffix}.json")
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    return {"chat": args.chat, "sandbox_path": sandbox, "bars": bars, "out_path": out_path, "tag": args.tag}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_inv4_attribution", chat=args.chat, tag=args.tag, sandbox_db=cfg["sandbox_path"], out_path=cfg["out_path"], n_bars=len(cfg["bars"])):
        note("sandbox_db", cfg["sandbox_path"])
        report = run_inv4(cfg, {"count": count})
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}")


if __name__ == "__main__":
    main()
