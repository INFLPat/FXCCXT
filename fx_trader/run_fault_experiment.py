# version: 261013
"""
run_fault_experiment.py  (fx_trader/run_fault_experiment.py)

Synthetic fault-injection experiment for chat 14p4 (CONTEXT_HANDOFF 4i). One
bar = one independent trial on the 8-pair FX topology: latent currency values
plus ordinary noise (sd = NOISE_RATIO_TRUE x the pair's spread), spreads drawn
per pair from a model or, with --spreads db, from the sandbox's own spreads
(read-only; "flat" = hours 08-15 UTC pooled, "rollover" = hour 21 UTC; pairs
are drawn independently, so cross-pair spread correlation is NOT reproduced).

FAULTS (one pair per faulted bar; USD_CAD is never faulted: it is a bridge and
cannot be checked):
  stale    quote off by +/-E bps (E in STALE_MAGS) with a NORMAL spread,
           volume 1, range 0 - the INV-1 pattern (4h/4i).
  wide     spread x m (m in WIDE_MULTS), error sd = WIDE_ERR_RATIO x new
           spread, volume x 0.1 - the rollover/event pattern.
  missing  the pair is absent (policy D).
  clean    no fault (error floor and false-flag rates).

PER TRIAL, for every scheme (strengths.SCHEMES) x method (plain least squares,
Huber k in robust.HUBER_KS x scale axes robust.SCALE_AXES):
  err_innocent  max |strength error| over the INNOCENT currencies, gauge-free
                (re-centred over the innocents; the faulted arm's own
                currency is excluded: its strength is genuinely unidentifiable
                when one of its two legs is bad).
  attribution   top-1 hit: the arm/hub holding the largest |residual| equals
                the faulted pair's arm/hub (plain LS uses |LOO|; Huber uses
                its robust residuals).
Also per trial: hub/arm median-of-paths verdict counts, detectors A, B (three
scales; hour_median uses the model's TYPICAL mean spread, an idealised
history), C, and the scheme gap max|c_inv_spread - c_unweighted| (tests the
4i hypothesis: gap ~ fault size x spread dispersion).

NOT covered: real-data validation of any threshold (this is a synthetic
world; thresholds NOISE_RATIO, MAD floor, A/B/C values are provisional), two
simultaneous faults, time structure (every bar independent), crypto.

Run (from fx_trader/):
  python3 run_fault_experiment.py --chat 14 --tag p4s --trials 100
  real spreads: add --spreads db [--sandbox-db data/sandbox_22Q1to26Q1.db]
Output: reports/fault_experiment_chat<N>_<run date, London>[_<tag>].json
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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from currency_graph.detectors import B_KINDS, LegInfo, detector_a, detector_b, detector_c
from currency_graph.robust import HUBER_KS, SCALE_AXES, group_of, hub_arms, huber_fit, path_attribution
from currency_graph.strengths import SCHEMES, loo_fast, select_component, weights_for, Obs
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path

PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
FAULT_PAIRS = [p for p in PAIRS if p != "USD_CAD"]
LAT_BPS = {"USD": 0.0, "GBP": 2390.0, "EUR": 950.0, "JPY": -50100.0, "CHF": 950.0, "CAD": -3000.0}
# Model spreads (bps). Flat: rough sandbox-typical values; USD_CAD is a GUESS (not measured here).
BASE_SPREAD = {"GBP_USD": 1.4, "EUR_GBP": 1.6, "EUR_USD": 1.4, "GBP_CHF": 1.8, "USD_CHF": 1.7, "GBP_JPY": 1.6, "USD_JPY": 1.1, "USD_CAD": 1.5}
# Rollover multipliers = typical spread at 21 UTC / flat (MEASURED from the 261012 raw-bar look; USD_CAD guessed).
ROLLOVER_MULT = {"GBP_USD": 3.0, "EUR_GBP": 3.0, "EUR_USD": 1.8, "GBP_CHF": 6.0, "USD_CHF": 3.9, "GBP_JPY": 4.1, "USD_JPY": 2.6, "USD_CAD": 3.0}
PROFILES = ("flat", "rollover")
NOISE_RATIO_TRUE = 0.15
WIDE_ERR_RATIO = 0.5
STALE_MAGS = (5.0, 15.0, 40.0, 100.0)
WIDE_MULTS = (4.0, 10.0, 25.0)
FLAT_HOURS = tuple(f"{h:02d}" for h in range(8, 16))
ROLLOVER_HOUR = "21"
MAX_TRIALS = 5_000
TYP_VOLUME = 1000.0
LEVEL_SD_BPS = 30.0


def method_specs() -> list:
    """[(name, scheme, k, scale)] - plain LS once per scheme, Huber over k x axis."""
    out = []
    for scheme in SCHEMES:
        out.append((f"{scheme}|LS", scheme, math.inf, "spread"))
        out.extend((f"{scheme}|H{k}|{ax}", scheme, k, ax) for k in HUBER_KS if not math.isinf(k) for ax in SCALE_AXES)
    return out


# ---------------------------------------------------------------- spreads
class ModelSpreads:
    name = "model"

    def draw(self, rng, pair: str, profile: str) -> float:
        assert profile in PROFILES and pair in BASE_SPREAD, "bad pair/profile"
        mult = ROLLOVER_MULT[pair] if profile == "rollover" else 1.0
        return BASE_SPREAD[pair] * mult * math.exp(rng.gauss(0, 0.25))

    def typical_mean(self, profile: str) -> float:
        assert profile in PROFILES, "bad profile"
        return sum(BASE_SPREAD[p] * (ROLLOVER_MULT[p] if profile == "rollover" else 1.0) for p in PAIRS) / len(PAIRS)


class DbSpreads:
    name = "db"

    def __init__(self, path: str):
        assert Path(path).exists(), f"sandbox DB not found: {path}"
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT instrument, substr(timestamp,12,2), (ask_close-bid_close)/((ask_close+bid_close)/2)*1e4 FROM candles "
                f"WHERE granularity='H1' AND instrument IN ({','.join('?' * len(PAIRS))}) AND ask_close > bid_close", PAIRS).fetchall()
        finally:
            con.close()
        self.pool = {(p, prof): [] for p in PAIRS for prof in PROFILES}
        for pair, hour, spread in rows:
            if hour == ROLLOVER_HOUR:
                self.pool[(pair, "rollover")].append(spread)
            elif hour in FLAT_HOURS:
                self.pool[(pair, "flat")].append(spread)
        assert all(self.pool.values()), "a (pair, profile) pool is empty"

    def draw(self, rng, pair: str, profile: str) -> float:
        return rng.choice(self.pool[(pair, profile)])

    def typical_mean(self, profile: str) -> float:
        return sum(statistics.median(self.pool[(p, profile)]) for p in PAIRS) / len(PAIRS)


# ------------------------------------------------------------------ bars
def make_bar(rng, spreads, profile: str) -> tuple:
    assert profile in PROFILES, "bad profile"
    lat = {k: v + rng.gauss(0, LEVEL_SD_BPS) for k, v in LAT_BPS.items()}
    obs, vol, rng_bps = [], [], []
    for p in PAIRS:
        a, b = p.split("_")
        s = spreads.draw(rng, p, profile)
        obs.append(Obs(a, b, lat[a] - lat[b] + rng.gauss(0, NOISE_RATIO_TRUE * s), s, p))
        vol.append(max(3, int(TYP_VOLUME * math.exp(rng.gauss(0, 0.6)))))
        rng_bps.append(12.0 * math.exp(rng.gauss(0, 0.4)))
    return obs, vol, rng_bps, lat


def inject(rng, obs: list, vol: list, rng_bps: list, kind: str, mag: float, idx: int) -> tuple:
    """Copies of (obs, vol, rng_bps) with one pair faulted. 'missing' removes it."""
    assert kind in ("stale", "wide", "missing"), f"unknown fault kind {kind}"
    assert 0 <= idx < len(obs), "bad pair index"
    obs, vol, rng_bps = list(obs), list(vol), list(rng_bps)
    o = obs[idx]
    if kind == "missing":
        for seq in (obs, vol, rng_bps):
            del seq[idx]
    elif kind == "stale":
        obs[idx] = o._replace(y=o.y + rng.choice((-1.0, 1.0)) * mag)
        vol[idx], rng_bps[idx] = 1, 0.0
    else:
        s = o.s * mag
        obs[idx] = o._replace(s=s, y=o.y + rng.gauss(0, WIDE_ERR_RATIO * s))
        vol[idx] = max(3, int(vol[idx] * 0.1))
    return obs, vol, rng_bps


def innocent_error(c: dict, truth: dict, nodes: list, exclude: set) -> float:
    """Gauge-free max strength error over the innocent currencies."""
    keep = [n for n in nodes if n not in exclude]
    assert len(keep) >= 2, "too few innocent currencies"
    mc, mt = sum(c[n] for n in keep) / len(keep), sum(truth[n] for n in keep) / len(keep)
    return max(abs((c[n] - mc) - (truth[n] - mt)) for n in keep)


def _argmax_group(names: list, scores: list, groups: dict) -> str | None:
    best = max(((abs(v), n) for n, v in zip(names, scores) if v is not None), default=None)
    return groups[best[1]] if best else None


# ---------------------------------------------------------------- trials
class Acc:
    def __init__(self, methods: list):
        self.n = 0
        self.err = {m[0]: [] for m in methods}
        self.hit = Counter()
        self.attr_n = 0
        self.path = Counter()
        self.det = Counter()
        self.det_n = 0
        self.gap = {"inv_spread": [], "inv_spread2": []}


def _fit_all(main, sub, methods, truth, exclude, groups_sub, kind, true_group, acc):
    names = [o.name for o in sub]
    fits = {}
    for name, scheme, k, scale in methods:
        r = huber_fit(main, sub, weights_for(scheme, sub), k, scale)
        fits[name] = r.fit
        acc.err[name].append(innocent_error(r.fit.c, truth, main, exclude))
        if kind in ("stale", "wide"):
            scores = loo_fast(r.fit) if math.isinf(k) else r.fit.resid
            acc.hit[name] += _argmax_group(names, scores, groups_sub) == true_group
    return fits


def run_trial(rng, spreads, profile: str, kind: str, mag: float, methods: list, acc: Acc) -> None:
    obs, vol, rg, lat = make_bar(rng, spreads, profile)
    groups = group_of(hub_arms(obs), obs)
    pair = None if kind == "clean" else FAULT_PAIRS[rng.randrange(len(FAULT_PAIRS))]
    if pair is not None:
        obs, vol, rg = inject(rng, obs, vol, rg, kind, mag, PAIRS.index(pair))
    true_group = groups[pair] if pair else None
    exclude = {true_group.split(":")[1]} if pair and true_group.startswith("arm:") else set()
    main, sub = select_component(sorted(LAT_BPS), obs)
    truth = {n: lat[n] for n in main}
    acc.n += 1
    fits = _fit_all(main, sub, methods, truth, exclude, groups, kind, true_group, acc)
    if kind in ("stale", "wide"):
        acc.attr_n += 1
        verdict = path_attribution(obs).verdict
        acc.path["correct" if verdict == true_group else {"clean": "missed", "ambiguous": "ambiguous"}.get(verdict, "wrong")] += 1
    if kind != "missing":
        acc.det_n += 1
        loo = loo_fast(fits["unweighted|LS"])
        acc.det["A"] += detector_a(sub, loo)["flag"]
        typ = spreads.typical_mean(profile)
        for bk in B_KINDS:
            acc.det[f"B_{bk}"] += bool(detector_b(sub, loo, bk, hour_median=typ)["flag"])
        acc.det["C"] += detector_c([LegInfo(o.name, v, r, TYP_VOLUME) for o, v, r in zip(sub, vol, rg)])["flag"]
    for scheme in ("inv_spread", "inv_spread2"):
        a, b = fits[f"{scheme}|LS"].c, fits["unweighted|LS"].c
        acc.gap[scheme].append(max(abs(a[n] - b[n]) for n in main))


def _pct(values: list, p: float):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (k - lo)


def summarise(acc: Acc) -> dict:
    return {
        "n": acc.n,
        "err_innocent_bps": {m: {"p50": _pct(v, 50), "p95": _pct(v, 95)} for m, v in acc.err.items()},
        "attribution_top1": ({m: h / acc.attr_n for m, h in acc.hit.items()} if acc.attr_n else None),
        "hub_arm_verdicts": ({k: v / acc.attr_n for k, v in acc.path.items()} if acc.attr_n else None),
        "detector_flag_rate": ({k: v / acc.det_n for k, v in acc.det.items()} if acc.det_n else None),
        "scheme_gap_bps": {s: {"p50": _pct(v, 50), "p95": _pct(v, 95)} for s, v in acc.gap.items()},
    }


def cell_list() -> list:
    cells = [("clean", 0.0)] + [("stale", m) for m in STALE_MAGS] + [("wide", m) for m in WIDE_MULTS] + [("missing", 0.0)]
    return [(kind, mag, prof) for prof in PROFILES for kind, mag in cells]


def run_experiment(cfg: dict, spreads, hooks: dict | None = None, cells: list | None = None) -> dict:
    hooks = hooks or {}
    rng = random.Random(cfg["seed"])
    methods = method_specs()
    out = {"schema": 1, "tool": "run_fault_experiment", "chat": cfg["chat"], "seed": cfg["seed"], "trials": cfg["trials"],
           "spreads": spreads.name, "methods": [m[0] for m in methods], "cells": {}}
    for kind, mag, prof in cells or cell_list():
        t0 = time.perf_counter()
        acc = Acc(methods)
        for _ in range(cfg["trials"]):
            run_trial(rng, spreads, prof, kind, mag, methods, acc)
        out["cells"][f"{kind}:{mag:g}|{prof}"] = summarise(acc)
        if hooks.get("item"):
            hooks["item"](f"cell/{kind}:{mag:g}|{prof}", time.perf_counter() - t0)
        if hooks.get("count"):
            hooks["count"]("trials", cfg["trials"])
    return out


def render_text(rep: dict) -> str:
    f = lambda x: "  n/a" if x is None else f"{x:5.2f}"
    lines = [f"Fault experiment | chat {rep['chat']} | spreads {rep['spreads']} | trials/cell {rep['trials']} | seed {rep['seed']}",
             "err = median innocent-currency error (bps, gauge-free); attr = top-1 arm/hub hit; gap = median |c_1/s - c_unw| (bps)",
             f"{'cell':<22} {'LS err':>6} {'H1.345 err':>10} {'H2 mad':>6} | {'LS attr':>7} {'H1.345':>6} {'path':>5} | {'A':>4} {'B_mn':>4} {'B_arm':>5} {'B_hr':>4} {'C':>4} | {'gap1/s':>6}"]
    for key, c in rep["cells"].items():
        e, a, d = c["err_innocent_bps"], c["attribution_top1"], c["detector_flag_rate"]
        v = c["hub_arm_verdicts"]
        p = lambda x: "  n/a" if x is None else f"{x:5.2f}"
        lines.append(
            f"{key:<22} {f(e['unweighted|LS']['p50']):>6} {f(e['unweighted|H1.345|spread']['p50']):>10} {f(e['unweighted|H2.0|mad']['p50']):>6} | "
            f"{p(a and a['unweighted|LS']):>7} {p(a and a['unweighted|H1.345|spread']):>6} {p(v and v.get('correct', 0.0)):>5} | "
            f"{p(d and d['A']):>4} {p(d and d['B_mean_spread']):>4} {p(d and d['B_arm_spread']):>5} {p(d and d['B_hour_median']):>4} {p(d and d['C']):>4} | "
            f"{f(c['scheme_gap_bps']['inv_spread']['p50']):>6}")
    return "\n".join(lines)


# ------------------------------------------------------------------ cli
def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Synthetic fault-injection experiment (stale / wide / missing).")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--trials", type=int, default=100, help=f"trials per cell, 1..{MAX_TRIALS}")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--spreads", choices=("model", "db"), default="model")
    p.add_argument("--sandbox-db", default=None)
    p.add_argument("--tag", default="")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert 1 <= args.trials <= MAX_TRIALS, f"--trials must be in [1, {MAX_TRIALS}]"
    sandbox = None
    if args.spreads == "db":
        sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
        assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    out_path = str(Path(args.out_dir) / f"fault_experiment_chat{args.chat}_{run_date}{suffix}.json")
    assert not Path(out_path).exists(), f"output already exists: {out_path} - pick a different --tag"
    return {"chat": args.chat, "trials": args.trials, "seed": args.seed, "spreads": args.spreads, "sandbox_path": sandbox, "out_path": out_path, "tag": args.tag}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("run_fault_experiment", chat=args.chat, tag=args.tag, trials=cfg["trials"], seed=cfg["seed"],
                       spreads=cfg["spreads"], out_path=cfg["out_path"]):
        note("sandbox_db", cfg["sandbox_path"])
        spreads = DbSpreads(cfg["sandbox_path"]) if cfg["spreads"] == "db" else ModelSpreads()
        report = run_experiment(cfg, spreads, {"item": item, "count": count})
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['out_path']}")


if __name__ == "__main__":
    main()
