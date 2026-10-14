# version: 261014
"""
run_detector_scan.py  (fx_trader/run_detector_scan.py)

Scans EVERY full bar (all 8 pairs present) of the sandbox with detectors A, B
(mean_spread, arm_spread, hour_median) and C (chat 14p4; currency_graph/
detectors.py) and reports flag counts, the overlap between detectors, and the
UTC-hour distribution of each flag. Read-only on the sandbox DB; the DB is the
only input (no dump needed). The LOO behind A and B is the UNWEIGHTED fit's
fast LOO (as 14p3's by-hour run).

Per bar: B hour_median uses the previous B_WINDOW same-hour full bars (never
the bar itself; None until B_MIN_HISTORY). C uses volume and the bar's bid
high-low range; typical volume = median per pair and UTC hour over the whole
DB (a mild look-ahead, stated; it only affects the THIN rule). Volume is
assumed to be OANDA's tick count (UNVERIFIED).

REPORTED: per detector - flagged bars, share, flags per UTC hour (24 values);
overlap matrix (bars flagged by both); runs of consecutive flagged bars (gap <= RUN_GAP_HOURS) overall and for flags A does not share; for each detector the number of bars it
flags that A does NOT; the same for "A flags, X does not". Thresholds are the
provisional module defaults (A 10 bps, B ratio 1.0, C as documented).

NOT covered: which flagged bars are real faults (needs a second source, 16p1),
degraded bars (skipped), crypto.

Run (from fx_trader/):
  python3 run_detector_scan.py --chat 14 --tag p4d --sandbox-db data/sandbox_22Q1to26Q1.db
Output: reports/detector_scan_chat<N>_<run date, London>[_<tag>].json
(refuses to overwrite) plus a text summary on stdout.
"""

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from currency_graph.detectors import B_KINDS, HourSpreadHistory, LegInfo, detector_a, detector_b, detector_c
from currency_graph.robust import huber_fit
from currency_graph.strengths import Obs, loo_fast, select_component
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path

PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
BPS = 1e4
MAX_BARS = 200_000
DETECTORS = ("A", "B_mean_spread", "B_arm_spread", "B_hour_median", "C")
RUN_GAP_HOURS = 3   # flagged bars at most this many hours apart form one run (14p4b: the 88 B_arm-only bars are SIX day-long runs)


def load_all(con) -> tuple:
    """({stamp: {pair: (bid, ask, high, low, volume)}}, {(pair, hour): median volume})."""
    bars = defaultdict(dict)
    vols = defaultdict(list)
    rows = con.execute(f"SELECT timestamp, instrument, bid_close, ask_close, bid_high, bid_low, volume FROM candles WHERE granularity='H1' AND instrument IN ({','.join('?' * len(PAIRS))}) ORDER BY timestamp", PAIRS)
    for t, p, bid, ask, hi, lo, vol in rows:
        bars[t][p] = (bid, ask, hi, lo, vol)
        if vol is not None:
            vols[(p, t[11:13])].append(vol)
        assert len(bars) <= MAX_BARS, "bar ceiling exceeded"
    return bars, {k: statistics.median(v) for k, v in vols.items()}


def _runs(stamps: list) -> list:
    """Sorted stored-format stamps -> [{start, end, bars}], a new run whenever the gap exceeds RUN_GAP_HOURS."""
    assert stamps == sorted(stamps), "stamps must be sorted"
    assert len(stamps) <= MAX_BARS, "bar ceiling exceeded"
    runs, prev = [], None
    for t in stamps:
        dt = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
        if runs and (dt - prev).total_seconds() <= RUN_GAP_HOURS * 3600:
            runs[-1]["end"] = t
            runs[-1]["bars"] += 1
        else:
            runs.append({"start": t, "end": t, "bars": 1})
        prev = dt
    return runs


def scan(bars: dict, typ_vol: dict, hooks: dict | None = None) -> dict:
    assert bars, "no bars"
    hist = HourSpreadHistory()
    stamps_by = {d: [] for d in DETECTORS}
    stamps_not_a = {d: [] for d in DETECTORS if d != "A"}
    flags = {d: Counter() for d in DETECTORS}          # detector -> hour -> count
    both = Counter()
    only = Counter()                                   # (X, "not_A") and ("A", "not_X")
    n_full = n_skip = n_b_hist = 0
    for t in sorted(bars):
        row = bars[t]
        if len(row) < len(PAIRS):
            n_skip += 1
            continue
        obs = [Obs(p.split("_")[0], p.split("_")[1], math.log((row[p][0] + row[p][1]) / 2) * BPS, (math.log(row[p][1]) - math.log(row[p][0])) * BPS, p) for p in PAIRS]
        main, sub = select_component(sorted({c for o in obs for c in (o.a, o.b)}), obs)
        loo = loo_fast(huber_fit(main, sub, [1.0] * len(sub), math.inf).fit)
        hour = int(t[11:13])
        med = hist.median_before(hour)
        n_b_hist += med is not None
        legs = [LegInfo(p, row[p][4], (row[p][2] - row[p][3]) / ((row[p][0] + row[p][1]) / 2) * BPS, typ_vol.get((p, t[11:13]))) for p in PAIRS]
        got = {"A": detector_a(sub, loo)["flag"], "C": detector_c(legs)["flag"]}
        for k in B_KINDS:
            got[f"B_{k}"] = bool(detector_b(sub, loo, k, hour_median=med)["flag"])
        hist.push(hour, sum(o.s for o in obs) / len(obs))
        n_full += 1
        on = [d for d in DETECTORS if got[d]]
        for d in on:
            flags[d][hour] += 1
            stamps_by[d].append(t)
        for i, d1 in enumerate(on):
            for d2 in on[i:]:
                both[f"{d1}&{d2}"] += 1
        for d in DETECTORS:
            if d != "A":
                only[f"{d}_not_A"] += got[d] and not got["A"]
                if got[d] and not got["A"]:
                    stamps_not_a[d].append(t)
                only[f"A_not_{d}"] += got["A"] and not got[d]
        if hooks and hooks.get("count"):
            hooks["count"]("bars")
    return {"n_full_bars": n_full, "n_skipped_degraded": n_skip, "n_bars_with_hour_history": n_b_hist,
            "flagged": {d: sum(c.values()) for d, c in flags.items()},
            "flagged_share": {d: sum(c.values()) / n_full for d, c in flags.items()},
            "by_hour": {d: [c.get(h, 0) for h in range(24)] for d, c in flags.items()},
            "overlap": dict(both), "disagreement_with_A": dict(only),
            "runs": {d: _runs(s) for d, s in stamps_by.items()}, "runs_not_A": {d: _runs(s) for d, s in stamps_not_a.items()}}


def render_text(rep: dict) -> str:
    lines = [f"Detector scan | chat {rep['chat']} | {rep['sandbox_db']} | full bars {rep['n_full_bars']} (degraded skipped {rep['n_skipped_degraded']}, with hour history {rep['n_bars_with_hour_history']})",
             f"{'detector':<14} {'flagged':>8} {'share %':>8}  top UTC hours (count)"]
    for d in DETECTORS:
        hours = sorted(range(24), key=lambda h: -rep["by_hour"][d][h])[:4]
        lines.append(f"{d:<14} {rep['flagged'][d]:>8} {rep['flagged_share'][d] * 100:>8.3f}  " + " ".join(f"{h:02d}:{rep['by_hour'][d][h]}" for h in hours))
    lines.append("overlap (both flagged): " + ", ".join(f"{k}={v}" for k, v in sorted(rep["overlap"].items()) if "&" in k and k.split("&")[0] != k.split("&")[1]))
    lines.append("disagreement with A: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["disagreement_with_A"].items())))
    lines.append(f"runs (flagged bars grouped, gap <= {RUN_GAP_HOURS} h): " + ", ".join(f"{d}={len(r)}" for d, r in rep["runs"].items()))
    for d, r in rep["runs_not_A"].items():
        if r:
            lines.append(f"  {d} not-A runs (start..end bars): " + "; ".join(f"{x['start'][:13]}..{x['end'][:13]} {x['bars']}" for x in r[:12]) + (f" (+{len(r) - 12} more)" if len(r) > 12 else ""))
    return "\n".join(lines)


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Detector A/B/C scan of every full sandbox bar (read-only).")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--sandbox-db", default=None)
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    out_path = str(Path(args.out_dir) / f"detector_scan_chat{args.chat}_{run_date}{suffix}.json")
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    return {"chat": args.chat, "sandbox_path": sandbox, "out_path": out_path, "tag": args.tag}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_detector_scan", chat=args.chat, tag=args.tag, sandbox_db=cfg["sandbox_path"], out_path=cfg["out_path"]):
        note("sandbox_db", cfg["sandbox_path"])
        con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
        try:
            bars, typ = load_all(con)
        finally:
            con.close()
        report = {"schema": 1, "tool": "run_detector_scan", "chat": args.chat, "sandbox_db": cfg["sandbox_path"]} | scan(bars, typ, {"count": count})
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}")


if __name__ == "__main__":
    main()
