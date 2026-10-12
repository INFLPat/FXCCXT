# version: 261012
"""
run_strengths_by_hour.py  (fx_trader/run_strengths_by_hour.py)

Does the weight scheme matter more at some hours of the day? Streams the
per-bar JSONL written by `run_strengths.py --full` (never loads it whole) and
summarises, per UTC hour and per DST cell (both_winter / both_summer /
uk_winter_us_summer, as in run_residual_investigations.py):
  max_diff_<scheme>   per bar, max over currencies of |zero-sum strength under
                      the scheme - zero-sum strength unweighted|  (bps)
  max_loo_<scheme>    per bar, max |leave-one-pair-out residual| (bps)
and, if the sandbox DB is read (default; --no-spread skips it):
  dispersion          per bar, max/min of the bar's pair spreads (1 = equal
                      spreads: the schemes then coincide)
  mean_spread         per bar, mean pair spread (bps)
Bars are split by a PROVISIONAL FAULT flag: max |LOO| under the unweighted fit
> --fault-bps (default 10, the audit's absolute-suspect edge). The flag is
bar-level on purpose: LOO cannot attribute a fault to a pair (CONTEXT_HANDOFF
4h). Hour tables for non-fault bars show the ORDINARY behaviour; fault bars
are counted per hour and their largest scheme difference reported.
Also reported: bar counts above several thresholds (3, 5, 10, 20, 50 bps; to
choose the fault definition), the days with most fault bars, and the Spearman
correlation of dispersion with max_diff on non-fault bars.

NOT covered: why (causes), per-pair attribution, any spread normalisation of
LOO (INV-22 stays open). Results are about this sandbox's bar closes.

Run (from fx_trader/):
  python3 run_strengths_by_hour.py --chat 14 --tag p3h --full-dump reports/strengths_full_<...>.jsonl
  optional: --fault-bps 5   --no-spread   --sandbox-db data/sandbox_22Q1to26Q1.db
Output: reports/strengths_by_hour_chat<N>_<run date, London>[_<tag>].json
(refuses to overwrite) plus a text summary on stdout.
"""

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import audit_sandbox_alignment as au
import run_strengths as rs
from currency_graph.strengths import SCHEMES
from run_monitor import LONDON, count, item, monitored_run, note
from run_residual_investigations import dst_cell
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import stored_ts_to_epoch

MAX_RECORDS = 3 * rs.MAX_BARS
THRESHOLDS = (3, 5, 10, 20, 50)
METRICS = ("max_diff_inv_spread", "max_diff_inv_spread2", "max_loo_unweighted", "max_loo_inv_spread",
           "max_loo_inv_spread2", "dispersion", "mean_spread")
TOP_DAYS = 20
LIMITATIONS = [
    "FX only; bar-close bid/ask from one broker. Fault flag is PROVISIONAL (absolute 10 bps on max |LOO|, unweighted).",
    "Hour tables use UTC hours; the DST-cell split separates New-York-anchored from London-anchored effects (INV-3).",
    "LOO is in absolute bps, not spread-normalised (INV-22 open): rollover hours widen spreads.",
    "Dispersion needs the sandbox DB; without it only strength/LOO columns are produced.",
]


def iter_bars(path: str):
    """Yield (t, {scheme: record}) for consecutive records sharing one bar stamp."""
    assert Path(path).exists(), f"dump not found: {path}"
    cur_t, cur, n = None, {}, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            n += 1
            assert n <= MAX_RECORDS, "record ceiling exceeded"
            rec = json.loads(line)
            if cur_t is not None and rec["t"] != cur_t:
                yield cur_t, cur
                cur = {}
            cur_t = rec["t"]
            cur[rec["scheme"]] = rec
    if cur_t is not None:
        yield cur_t, cur


def bar_metrics(recs: dict) -> dict:
    assert set(recs) == set(SCHEMES), f"bar must carry every scheme, got {sorted(recs)}"
    base = recs["unweighted"]["views"]["zero_sum"]
    out = {"group": recs["unweighted"]["group"]}
    for s in SCHEMES:
        loo = [abs(v) for v in recs[s]["loo"].values() if v is not None]
        out[f"max_loo_{s}"] = max(loo) if loo else None
        if s != "unweighted":
            z = recs[s]["views"]["zero_sum"]
            out[f"max_diff_{s}"] = max(abs(z[k] - base[k]) for k in base)
    return out


def _spread_metrics(obs: list | None) -> dict:
    if not obs:
        return {"dispersion": None, "mean_spread": None}
    spreads = [o.s for o in obs]
    return {"dispersion": max(spreads) / min(spreads), "mean_spread": sum(spreads) / len(spreads)}


def _hour_stats(rows: list) -> dict:
    ok = [r for r in rows if not r["fault"]]
    bad = [r for r in rows if r["fault"]]
    diffs = [r["max_diff_inv_spread"] for r in bad if r["max_diff_inv_spread"] is not None]
    return {"n": len(rows), "n_fault": len(bad),
            "non_fault": {m: rs._pstats([r[m] for r in ok if r.get(m) is not None]) for m in METRICS},
            "fault_max_diff_inv_spread_bps": max(diffs) if diffs else None}


def _by_hour(rows: list) -> dict:
    return {str(h): _hour_stats([r for r in rows if r["hour"] == h]) for h in range(24) if any(r["hour"] == h for r in rows)}


def _rho(rows: list, a: str, b: str):
    pts = [(r[a], r[b]) for r in rows if r.get(a) is not None and r.get(b) is not None]
    if len(pts) < 3:
        return None
    try:
        return statistics.correlation([p[0] for p in pts], [p[1] for p in pts], method="ranked")
    except statistics.StatisticsError:
        return None


def run_by_hour(cfg: dict, bars: dict | None = None, hooks: dict | None = None) -> dict:
    """bars: epoch -> [Obs] (run_strengths.build_bars) or None to skip spread columns."""
    assert cfg["fault_bps"] > 0, "fault_bps must be positive"
    rows = []
    for t, recs in iter_bars(cfg["dump_path"]):
        epoch = stored_ts_to_epoch(t)
        m = bar_metrics(recs)
        loo = m["max_loo_unweighted"]
        rows.append(m | _spread_metrics(bars.get(epoch) if bars else None)
                    | {"t": t, "hour": int(t[11:13]), "cell": dst_cell(epoch), "fault": loo is not None and loo > cfg["fault_bps"]})
    assert rows, "dump contains no bars"
    if hooks and hooks.get("count"):
        hooks["count"]("bars", len(rows))
    nf = [r for r in rows if not r["fault"]]
    days = Counter(r["t"][:10] for r in rows if r["fault"])
    return {"schema": 1, "tool": "run_strengths_by_hour", "chat": cfg["chat"], "dump": cfg["dump_path"], "fault_bps": cfg["fault_bps"],
            "spread_columns": bars is not None, "limitations": LIMITATIONS, "n_bars": len(rows),
            "n_fault_bars": sum(r["fault"] for r in rows), "n_degraded_bars": sum(r["group"] != rs.GROUP_FULL for r in rows),
            "bars_above_threshold_bps": {str(th): sum(1 for r in rows if r["max_loo_unweighted"] is not None and r["max_loo_unweighted"] > th) for th in THRESHOLDS},
            "fault_days_top": [{"date": d, "fault_bars": n} for d, n in days.most_common(TOP_DAYS)],
            "spearman_dispersion_vs_max_diff_non_fault": {s: _rho(nf, "dispersion", f"max_diff_{s}") for s in SCHEMES if s != "unweighted"},
            "by_hour": _by_hour(rows),
            "by_cell_hour": {c: _by_hour([r for r in rows if r["cell"] == c]) for c in sorted({r["cell"] for r in rows})}}


def render_text(rep: dict) -> str:
    assert rep["by_hour"], "no hours"
    f = lambda x: "  n/a" if x is None else f"{x:5.2f}"
    lines = [f"Strengths by hour | chat {rep['chat']} | {rep['dump']} | fault > {rep['fault_bps']} bps on max|LOO| (unweighted)",
             f"bars {rep['n_bars']}, fault {rep['n_fault_bars']}, degraded {rep['n_degraded_bars']}; bars above thresholds {rep['bars_above_threshold_bps']}",
             f"Spearman(dispersion, max_diff) non-fault: {rep['spearman_dispersion_vs_max_diff_non_fault']}",
             "fault days (top): " + ", ".join(f"{d['date']}:{d['fault_bars']}" for d in rep["fault_days_top"][:10]),
             f"{'UTC h':>5} {'n':>6} {'fault':>5} | non-fault p50/p99 max_diff(1/s) | max_diff(1/s2) p50/p99 | max_loo(unw) p50/p99 | disp p50 | spread p50"]
    for h, s in rep["by_hour"].items():
        nf = s["non_fault"]
        g = lambda m, k: nf[m].get(k) if nf[m]["n"] else None
        lines.append(f"{h:>5} {s['n']:>6} {s['n_fault']:>5} | {f(g('max_diff_inv_spread', 'p50'))} {f(g('max_diff_inv_spread', 'p99'))} | "
                     f"{f(g('max_diff_inv_spread2', 'p50'))} {f(g('max_diff_inv_spread2', 'p99'))} | {f(g('max_loo_unweighted', 'p50'))} "
                     f"{f(g('max_loo_unweighted', 'p99'))} | {f(g('dispersion', 'p50'))} | {f(g('mean_spread', 'p50'))}")
    lines.append("LIMITATIONS: " + " | ".join(rep["limitations"]))
    return "\n".join(lines)


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Weight-scheme dependence by hour of day, from a run_strengths --full dump.")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--full-dump", required=True, help="reports/strengths_full_*.jsonl from run_strengths.py --full")
    p.add_argument("--sandbox-db", default=None, help="sqlite path for spread columns; default = sandbox_config.py's sandbox")
    p.add_argument("--no-spread", action="store_true", help="skip reading the sandbox DB (no dispersion/spread columns)")
    p.add_argument("--fault-bps", type=float, default=10.0, help="provisional fault threshold on max |LOO| (unweighted), bps")
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert args.fault_bps > 0, "--fault-bps must be positive"
    assert Path(args.full_dump).exists(), f"dump not found: {args.full_dump}"
    sandbox = None
    if not args.no_spread:
        sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
        assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    out_path = str(Path(args.out_dir) / f"strengths_by_hour_chat{args.chat}_{run_date}{suffix}.json")
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    return {"dump_path": args.full_dump, "sandbox_path": sandbox, "out_path": out_path, "chat": args.chat, "tag": args.tag, "fault_bps": args.fault_bps}


def _load_bars(cfg: dict) -> dict:
    from instrument_config import INSTRUMENTS
    con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
    try:
        series = {n: au.load_series(con, n, g) for n, g, _c, _p in INSTRUMENTS if g and not au.is_crypto(n)}
    finally:
        con.close()
    return rs.build_bars({n: s for n, s in series.items() if s})


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_strengths_by_hour", chat=args.chat, tag=args.tag, dump=cfg["dump_path"], out_path=cfg["out_path"],
                       fault_bps=cfg["fault_bps"], spread=cfg["sandbox_path"] is not None):
        note("dump", cfg["dump_path"])
        bars = _load_bars(cfg) if cfg["sandbox_path"] else None
        report = run_by_hour(cfg, bars, {"count": count})
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        item("summarise", 0.0, bars=report["n_bars"])
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}")


if __name__ == "__main__":
    main()
