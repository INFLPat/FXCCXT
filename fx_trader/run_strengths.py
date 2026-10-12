# version: 261011
"""
run_strengths.py  (fx_trader/run_strengths.py)

Currency strengths over the FX pairs of the sandbox (ROADMAP 14p3; engine in
currency_graph/strengths.py - read its docstring for the maths). Read-only on
the sandbox DB. FX only: crypto bars have bid == ask, so 1/spread is undefined
(crypto comes after the weights are resolved).

PER BAR, for each weight scheme (unweighted, inv_spread = 1/s, inv_spread2 =
1/s^2): zero-sum WLS fit on the largest connected component of the bar's
pairs; leave-one-pair-out residuals by the hat-matrix FAST path and (unless
--skip-oracle) the exact-refit ORACLE, cross-checked; leave-one-currency-out.
Wall time per method is measured (the 14p3 runtime deliverable).

MISSING-PAIR POLICIES (both computed, one pass):
  B  inner join: only FULL bars (every pair present).
  D  all bars: a bar missing pairs is fitted on what remains and counted as
     DEGRADED; currencies cut off from the main component get no strength.
Statistics are reported for B_full, D_degraded and D_all. The tally of which
pairs are missing on degraded bars is reported too.

OUTPUT: reports/strengths_<first>to<last>_chat<N>_<run date, London>[_<tag>].json
(summary; refuses to overwrite). --full also writes one JSON line per
(bar, scheme) to reports/strengths_full_<same stem>.jsonl (large, gitignored,
local only). --stride N uses every Nth bar (quick test run).

NOT covered: crypto and the USDT alias (later), any profitability claim (costs
gate), event/calendar attribution of outliers. Results are bar-close bid/ask
from one broker. The top-outlier list is reported as measured; it is not
interpreted here (INV-1 bad bars may or may not appear).

Run (from fx_trader/):
  python3 run_strengths.py --chat 14 --tag p3 --sandbox-db data/sandbox_22Q1to26Q1.db
  quick: add --stride 50      full dump: add --full
  optional: --ref USD --user-weights USD=0.4,EUR=0.3,GBP=0.3
"""

import argparse
import heapq
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import audit_sandbox_alignment as au
from currency_graph.strengths import (
    BPS, Obs, SCHEMES, fit_zero_sum, loco, loo_fast, loo_oracle, select_component, strength_views, weights_for,
)
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import epoch_to_stored_ts, parse_ts

MAX_BARS = 200_000
MAX_RECORDS = 2_000_000
TOP_K = 10
TOL_ORACLE = 1e-9
GROUP_FULL, GROUP_DEG = "B_full", "D_degraded"
LIMITATIONS = [
    "FX only; bar-close bid/ask from one broker (OANDA), not simultaneous snapshots.",
    "Weights use the bar's own summed leg spread; zero-spread bars abort the run (none expected: audit).",
    "A bridge pair (USD_CAD: CAD has one pair) has an undefined LOO residual by construction.",
    "LOCO is None when the remaining currencies disconnect (USD whenever CAD is in the fit, since only USD_CAD links it).",
    "Policy D fits degraded bars on the largest remaining component; level views shift slightly there, differences do not.",
    "Runtimes are pure-Python on the machine that ran this; numpy path is a later chat (14p4).",
]


# ------------------------------------------------------------------ data
def build_bars(series_by_pair: dict) -> dict:
    """epoch -> [Obs per pair present], pairs in sorted order."""
    assert series_by_pair, "no FX series"
    bars: dict = {}
    for pair in sorted(series_by_pair):
        a, b = au.split_pair(pair)
        for t, (bid, ask) in series_by_pair[pair].items():
            if not (0 < bid < ask):
                raise ValueError(f"{pair} at {t}: need 0 < bid < ask (zero spread breaks 1/spread), got {bid}, {ask}")
            y = math.log((bid + ask) / 2) * BPS
            s = (math.log(ask) - math.log(bid)) * BPS
            bars.setdefault(t, []).append(Obs(a, b, y, s, pair))
    assert len(bars) <= MAX_BARS, f"{len(bars)} bars exceeds MAX_BARS={MAX_BARS}"
    return bars


# --------------------------------------------------------------- accumulators
@dataclass
class SchemeAcc:
    scheme: str
    t_fit: float = 0.0
    t_oracle: float = 0.0
    t_loco: float = 0.0
    n_bars: Counter = field(default_factory=Counter)
    loo: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))      # group -> pair -> |LOO| list
    loo_none: Counter = field(default_factory=Counter)                                      # (group, pair)
    loco_max: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))  # group -> dropped -> max |shift| list
    loco_none: Counter = field(default_factory=Counter)                                     # (group, dropped)
    oracle_compared: int = 0
    oracle_max_diff: float = 0.0
    oracle_none_mismatch: int = 0
    top: list = field(default_factory=list)                                                 # min-heap of (|LOO|, epoch, pair)
    zero_sum_full: dict = field(default_factory=dict)                                       # epoch -> {ccy: c} (full bars)


def _record_loo(acc: SchemeAcc, group: str, t: int, sub: list, fast: list) -> None:
    assert len(sub) == len(fast), "obs/LOO mismatch"
    for o, v in zip(sub, fast):
        if v is None:
            acc.loo_none[(group, o.name)] += 1
            continue
        acc.loo[group][o.name].append(abs(v))
        heapq.heappush(acc.top, (abs(v), t, o.name))
        if len(acc.top) > TOP_K:
            heapq.heappop(acc.top)


def _compare(acc: SchemeAcc, fast: list, orc: list) -> None:
    assert len(fast) == len(orc), "fast/oracle length mismatch"
    for a, b in zip(fast, orc):
        if (a is None) != (b is None):
            acc.oracle_none_mismatch += 1
        elif a is not None:
            acc.oracle_max_diff = max(acc.oracle_max_diff, abs(a - b))
    acc.oracle_compared += len(fast)


def _record_loco(acc: SchemeAcc, group: str, lo: dict) -> None:
    assert lo, "empty LOCO result"
    for k, v in lo.items():
        if v is None:
            acc.loco_none[(group, k)] += 1
        else:
            acc.loco_max[group][k].append(max(abs(x) for x in v.values()))


def _full_record(t: int, acc: SchemeAcc, group: str, main: list, sub: list, fit, fast: list, lo: dict, ctx: dict) -> dict:
    rnd = lambda x: None if x is None else round(x, 6)
    views = strength_views(fit.c, sub, ctx["ref"], ctx["user_weights"])
    return {"t": epoch_to_stored_ts(t), "scheme": acc.scheme, "group": group, "nodes": main,
            "views": {k: (None if v is None else {c: rnd(x) for c, x in v.items()}) for k, v in views.items()},
            "loo": {o.name: rnd(v) for o, v in zip(sub, fast)},
            "loco_max_abs_shift": {k: (None if v is None else rnd(max(abs(x) for x in v.values()))) for k, v in lo.items()}}


def process_bar(t: int, obs: list, acc: SchemeAcc, ctx: dict, out) -> None:
    """One bar, one scheme. out = open file or None (--full)."""
    assert obs, "bar without observations"
    group = GROUP_FULL if len(obs) == ctx["n_pairs"] else GROUP_DEG
    acc.n_bars[group] += 1
    main, sub = select_component(ctx["nodes"], obs)
    w = weights_for(acc.scheme, sub)
    t0 = time.perf_counter()
    fit = fit_zero_sum(main, sub, w)
    fast = loo_fast(fit)
    acc.t_fit += time.perf_counter() - t0
    _record_loo(acc, group, t, sub, fast)
    if ctx["oracle"]:
        t0 = time.perf_counter()
        orc = loo_oracle(main, sub, w)
        acc.t_oracle += time.perf_counter() - t0
        _compare(acc, fast, orc)
    t0 = time.perf_counter()
    lo = loco(main, sub, w, fit.c)
    acc.t_loco += time.perf_counter() - t0
    _record_loco(acc, group, lo)
    if group == GROUP_FULL:
        acc.zero_sum_full[t] = fit.c
    if out is not None:
        out.write(json.dumps(_full_record(t, acc, group, main, sub, fit, fast, lo, ctx)) + "\n")


# ---------------------------------------------------------------- summary
def _pstats(vals: list) -> dict:
    if not vals:
        return {"n": 0}
    p = au.pcts(vals, (50, 99, 100))
    return {"n": len(vals), "p50": p["50"], "p99": p["99"], "max": p["100"]}


def _grouped(acc: SchemeAcc, store: dict, nones: Counter) -> dict:
    labels = (("B_full", (GROUP_FULL,)), ("D_degraded", (GROUP_DEG,)), ("D_all", (GROUP_FULL, GROUP_DEG)))
    keys = sorted({k for g in store for k in store[g]} | {k for (_g, k) in nones})
    return {label: {k: _pstats([v for g in groups for v in store[g].get(k, [])]) | {"none_n": sum(nones[(g, k)] for g in groups)}
                    for k in keys} for label, groups in labels}


def scheme_summary(acc: SchemeAcc) -> dict:
    total = sum(acc.n_bars.values())
    per_bar = lambda s: (s / total * 1000) if total else None
    return {
        "bars": {"total": total, "B_full": acc.n_bars[GROUP_FULL], "D_degraded": acc.n_bars[GROUP_DEG]},
        "timing_s": {"fit_and_fast_loo": acc.t_fit, "oracle_loo": acc.t_oracle, "loco": acc.t_loco},
        "timing_ms_per_bar": {"fit_and_fast_loo": per_bar(acc.t_fit), "oracle_loo": per_bar(acc.t_oracle), "loco": per_bar(acc.t_loco)},
        "fast_vs_oracle": {"compared_pairs": acc.oracle_compared, "max_abs_diff_bps": acc.oracle_max_diff,
                           "none_mismatches": acc.oracle_none_mismatch,
                           "within_1e-9": (acc.oracle_max_diff < TOL_ORACLE and acc.oracle_none_mismatch == 0) if acc.oracle_compared else None},
        "loo_abs_bps": _grouped(acc, acc.loo, acc.loo_none),
        "loco_max_abs_shift_bps": _grouped(acc, acc.loco_max, acc.loco_none),
        "top_loo_events": [{"pair": p, "utc": epoch_to_stored_ts(t), "abs_loo_bps": round(v, 4)} for v, t, p in sorted(acc.top, reverse=True)],
    }


def scheme_comparison(accs: dict) -> dict:
    """Per-currency |zero-sum strength difference| between each scheme and unweighted, on full bars (B)."""
    base = accs["unweighted"].zero_sum_full
    out = {}
    for scheme, acc in accs.items():
        if scheme == "unweighted":
            continue
        diffs: dict = defaultdict(list)
        for t, c in acc.zero_sum_full.items():
            for k, v in c.items():
                diffs[k].append(abs(v - base[t][k]))
        out[f"{scheme}_vs_unweighted_abs_bps"] = {k: _pstats(v) for k, v in sorted(diffs.items())}
    return out


# -------------------------------------------------------------------- run
def run_strengths(cfg: dict, instruments: list, hooks: dict | None = None) -> dict:
    hooks = hooks or {}
    con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
    try:
        series = {n: au.load_series(con, n, g) for n, g, _c, _p in instruments if g and not au.is_crypto(n)}
    finally:
        con.close()
    series = {n: s for n, s in series.items() if s}
    bars = build_bars(series)
    pairs = sorted(series)
    nodes = sorted({c for p in pairs for c in au.split_pair(p)})
    epochs = sorted(bars)[::cfg["stride"]]
    ctx = {"nodes": nodes, "n_pairs": len(pairs), "oracle": not cfg["skip_oracle"], "ref": cfg["ref"], "user_weights": cfg["user_weights"]}
    accs = {s: SchemeAcc(s) for s in SCHEMES}
    missing = Counter()
    out = open(cfg["full_path"], "w", encoding="utf-8") if cfg["full_path"] else None
    try:
        for t in epochs:
            obs = bars[t]
            if len(obs) < len(pairs):
                missing[",".join(sorted(set(pairs) - {o.name for o in obs}))] += 1
            for acc in accs.values():
                process_bar(t, obs, acc, ctx, out)
    finally:
        if out is not None:
            out.close()
    if hooks.get("count"):
        hooks["count"]("bars", len(epochs))
    for s, acc in accs.items():
        if hooks.get("item"):
            hooks["item"](f"scheme/{s}", acc.t_fit + acc.t_oracle + acc.t_loco)
    return {"schema": 1, "tool": "run_strengths", "chat": cfg["chat"], "sandbox_db": cfg["sandbox_path"], "stride": cfg["stride"],
            "pairs": pairs, "currencies": nodes, "ref": cfg["ref"], "user_weights": cfg["user_weights"], "oracle": ctx["oracle"],
            "full_dump": cfg["full_path"], "limitations": LIMITATIONS,
            "missing_pair_sets_on_degraded_bars": dict(missing.most_common(30)),
            "schemes": {s: scheme_summary(a) for s, a in accs.items()}, "scheme_comparison": scheme_comparison(accs)}


def render_text(report: dict) -> str:
    lines = [f"Strengths | chat {report['chat']} | {report['sandbox_db']} | stride {report['stride']} | oracle {report['oracle']}"]
    for s, r in report["schemes"].items():
        b, tm, fo = r["bars"], r["timing_ms_per_bar"], r["fast_vs_oracle"]
        f = lambda x: "n/a" if x is None else f"{x:.3f}"
        lines.append(f"\n=== {s}: bars total {b['total']} (B_full {b['B_full']}, D_degraded {b['D_degraded']})")
        lines.append(f"  ms/bar: fit+fastLOO {f(tm['fit_and_fast_loo'])}, oracle {f(tm['oracle_loo'])}, LOCO {f(tm['loco'])}  "
                     f"(total s: {r['timing_s']['fit_and_fast_loo']:.1f} / {r['timing_s']['oracle_loo']:.1f} / {r['timing_s']['loco']:.1f})")
        lines.append(f"  fast vs oracle: max diff {fo['max_abs_diff_bps']:.3e} bps over {fo['compared_pairs']} pairs, None mismatches {fo['none_mismatches']}, within 1e-9: {fo['within_1e-9']}")
        for pair, st in r["loo_abs_bps"]["D_all"].items():
            if st["n"]:
                lines.append(f"  LOO |bps| {pair:<8} p50 {st['p50']:.3f} p99 {st['p99']:.3f} max {st['max']:.2f}  (None {st['none_n']})")
            else:
                lines.append(f"  LOO {pair:<8} always None ({st['none_n']} bars)")
        lines.append("  top LOO events: " + "; ".join(f"{e['pair']} {e['utc'][:13]} {e['abs_loo_bps']}" for e in r["top_loo_events"][:5]))
    lines.append(f"\nmissing-pair sets on degraded bars: {report['missing_pair_sets_on_degraded_bars']}")
    lines.append("LIMITATIONS: " + " | ".join(report["limitations"]))
    return "\n".join(lines)


# -------------------------------------------------------------------- cli
def parse_user_weights(text: str | None) -> dict | None:
    if not text:
        return None
    out = {}
    for part in text.split(","):
        k, _sep, v = part.partition("=")
        assert k.strip() and _sep, f"--user-weights entry must look like USD=0.4, got {part!r}"
        out[k.strip()] = float(v)
    assert all(x >= 0 for x in out.values()) and sum(out.values()) > 0, "--user-weights must be >= 0 with a positive sum"
    return out


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Currency strengths, leave-one-out and views over the FX sandbox (read-only).")
    p.add_argument("--chat", type=int, required=True, help="chat number, used in the output name")
    p.add_argument("--sandbox-db", default=None, help="sqlite path; default = sandbox_config.py's sandbox")
    p.add_argument("--tag", default="", help="optional output-name suffix (letters, digits, hyphen)")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--stride", type=int, default=1, help="use every Nth bar (1 = all)")
    p.add_argument("--skip-oracle", action="store_true", help="skip the exact-refit oracle (faster; no cross-check)")
    p.add_argument("--full", action="store_true", help="also write per-bar JSONL (large, local only)")
    p.add_argument("--ref", default="USD", help="reference currency for the vs_<ref> view")
    p.add_argument("--user-weights", default=None, help="basket weights, e.g. USD=0.4,EUR=0.3,GBP=0.3")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert args.stride >= 1, "--stride must be >= 1"
    sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    first, last = au.db_coverage(sandbox)
    suffix = f"_{args.tag}" if args.tag else ""
    stem = f"{parse_ts(first):%y%m%d}to{parse_ts(last):%y%m%d}_chat{args.chat}_{run_date}{suffix}"
    out_path = str(Path(args.out_dir) / f"strengths_{stem}.json")
    full_path = str(Path(args.out_dir) / f"strengths_full_{stem}.jsonl") if args.full else None
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    assert not (full_path and Path(full_path).exists()), f"output already exists: {full_path} - pick a different --tag"
    return {"sandbox_path": sandbox, "out_path": out_path, "full_path": full_path, "chat": args.chat, "tag": args.tag,
            "stride": args.stride, "skip_oracle": args.skip_oracle, "ref": args.ref, "user_weights": parse_user_weights(args.user_weights)}


def main(argv: list | None = None) -> None:
    from instrument_config import INSTRUMENTS
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_strengths", chat=args.chat, tag=args.tag, sandbox_db=cfg["sandbox_path"], out_path=cfg["out_path"],
                       stride=cfg["stride"], oracle=not cfg["skip_oracle"], full=bool(cfg["full_path"])):
        note("sandbox_db", cfg["sandbox_path"])
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        report = run_strengths(cfg, INSTRUMENTS, {"item": item, "count": count})
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}" + (f"\nFull dump: {cfg['full_path']}" if cfg["full_path"] else ""))


if __name__ == "__main__":
    main()
