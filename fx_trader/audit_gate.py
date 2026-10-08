# version: 261008
"""
audit_gate.py  (fx_trader/audit_gate.py)

Golden-file gate for the chat-14p2 audit refactor (ROADMAP 14p2, CONTEXT_HANDOFF
4g). Compares an audit report with the golden report
(reports/audit_220101to260331_chat14_261005.json) and says exactly how they differ.

TWO MODES (either or both; each is one comparison in the output):
  --fresh <report.json>   whole-report comparison of a re-run of the CURRENT
                          audit with the golden (a regression baseline: it
                          should show zero differences, and it validates this
                          comparer before it judges the engine).
  --engine                builds the `cycles` and `basis` sections from the
                          sandbox DB with currency_graph (cycle discovery,
                          evaluate_quotes) and the audit's OWN summarize_cycle,
                          then compares just those two sections with the golden.
                          The audit file is not modified.

COMPARISON RULES
- Cycles are matched by their SET of instruments (alias leg and the
  "X via USDT/GBP:" prefix ignored), not by label or list position.
- Only the label and signed_mean_bps depend on the direction a loop is walked.
  The sign (+1/-1) is derived from the legs shared by both labels and applied
  to signed_mean_bps only; legs that disagree in direction = FAIL.
- Tolerance 1e-9 absolute for unrounded numbers. Fields the audit rounds
  (by_year_abs_bps, worst_utc_hours_p99, stress_days.median_ratio: 4 dp;
  worst_events abs_bps/spread_bps: 3 dp) may differ by ONE rounding unit.
- Three severities, never silent: FAIL; FLIP (boundary effects: one rounding
  unit, a count of 1-2 in tiers / share_* fields that a strict > threshold could
  move, or tied entries in worst_events / stress_days in another order);
  INFO (label, list order, orientation, not-compared sections).
- Keys ignored: chat, sandbox_db, checks, manifest.written_to,
  manifest.verified_against.
EXIT CODE: 0 = every comparison PASS; 3 = no FAIL but at least one FLIP;
2 = at least one FAIL.

NOT covered: the engine path reuses the audit's series loader and summariser,
so those are tested only by being the golden's own code. A pass says the engine's
cycles and per-bar numbers reproduce the audit's, nothing more.

Run (from fx_trader/):
  python3 audit_gate.py --chat 14 --tag p2 --golden reports/audit_220101to260331_chat14_261005.json --engine
  python3 audit_gate.py --chat 14 --tag p2b --golden <golden> --fresh reports/<new audit report>.json
"""

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import audit_sandbox_alignment as au
from currency_graph import EdgeType, basis_edge, enumerate_cycles, evaluate_quotes, quoted_edge
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import local_view

FAIL, FLIP, INFO = "FAIL", "FLIP", "INFO"
ABS_TOL = 1e-9
FLIP_COUNT_MAX = 2
MAX_DIFFS = 5_000
MAX_SHOWN = 60
CYCLE_SECTIONS = ("cycles", "basis")
ENGINE_SECTIONS = CYCLE_SECTIONS
SHARE_FIELDS = ("share_ratio_gt_clean", "share_ratio_gt_suspect", "exec_violation_share")
IGNORED_PATHS = frozenset({("chat",), ("sandbox_db",), ("checks",), ("manifest", "written_to"), ("manifest", "verified_against")})
LEG_RE = re.compile(r"^([+-])(\S+)$")
ENGINE_MAX_LEN = 4


@dataclass
class Diff:
    severity: str
    path: str
    golden: object
    fresh: object
    note: str = ""


@dataclass
class GateResult:
    diffs: list
    not_compared: list

    def count(self, severity: str) -> int:
        assert severity in (FAIL, FLIP, INFO), f"unknown severity {severity}"
        return sum(1 for d in self.diffs if d.severity == severity)

    @property
    def verdict(self) -> str:
        if self.count(FAIL):
            return "FAIL"
        return "FLIP" if self.count(FLIP) else "PASS"


# ------------------------------------------------------------- comparer
def _add(diffs: list, severity: str, path: tuple, golden, fresh, note_text: str = "") -> None:
    assert severity in (FAIL, FLIP, INFO), f"unknown severity {severity}"
    assert isinstance(path, tuple), "path must be a tuple"
    if len(diffs) == MAX_DIFFS - 1:
        diffs.append(Diff(FAIL, "(gate)", None, None, f"more than {MAX_DIFFS} differences - output truncated"))
    if len(diffs) >= MAX_DIFFS:
        return
    diffs.append(Diff(severity, "/".join(str(p) for p in path), golden, fresh, note_text))


def rounding_unit(path: tuple) -> float | None:
    """One rounding unit of the audit's own rounding for this field, else None."""
    assert isinstance(path, tuple) and path, "path must be a non-empty tuple"
    if "worst_events" in path and path[-1] in ("abs_bps", "spread_bps"):
        return 1e-3
    if "by_year_abs_bps" in path or "worst_utc_hours_p99" in path:
        return 1e-4
    if "stress_days" in path and path[-1] == "median_ratio":
        return 1e-4
    return None


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _cmp_number(g, f, path: tuple, diffs: list, n) -> None:
    assert _is_num(g) and _is_num(f), "numbers only"
    if isinstance(g, int) and isinstance(f, int):
        if g == f:
            return
        if "tiers" in path and abs(g - f) <= FLIP_COUNT_MAX:
            _add(diffs, FLIP, path, g, f, "tier count off by a boundary-sized amount")
        else:
            _add(diffs, FAIL, path, g, f, "integer differs")
        return
    if math.isnan(g) and math.isnan(f):
        return
    d = abs(g - f)
    if d <= ABS_TOL:
        return
    unit = rounding_unit(path)
    if unit is not None and d <= unit * 1.0001:
        _add(diffs, FLIP, path, g, f, "differs by one rounding unit (boundary)")
        return
    if path[-1] in SHARE_FIELDS and n:
        c = d * n
        if abs(c - round(c)) < 1e-6 and 1 <= round(c) <= FLIP_COUNT_MAX:
            _add(diffs, FLIP, path, g, f, f"share differs by {round(c)} bar(s): a strict-threshold boundary flip?")
            return
    _add(diffs, FAIL, path, g, f, f"differs by {d:.3g}")


def _event_spec(path: tuple):
    """(key_fn, value field, one rounding unit) for the audit's top-N event lists, else None."""
    assert path, "empty path"
    if path[-1] == "worst_events":
        return (lambda e: e["at"]["utc"]), "abs_bps", 1e-3
    if path[-1] == "stress_days":
        return (lambda e: e["date"]), "median_ratio", 1e-4
    return None


def _cmp_keyed(g: list, f: list, spec: tuple, path: tuple, diffs: list, n, ignored) -> None:
    """Top-N lists are matched by member, not position. Members that appear on one
    side only are tolerated (as a FLIP) when every one of them rounds to zero:
    those are float-noise ties at the cut-off, not findings."""
    key_fn, value_field, unit = spec
    gk, fk = [key_fn(e) for e in g], [key_fn(e) for e in f]
    if len(set(gk)) != len(gk) or len(set(fk)) != len(fk):
        _add(diffs, FAIL, path, gk, fk, "duplicate members")
        return
    only = [e for e in g if key_fn(e) not in set(fk)] + [e for e in f if key_fn(e) not in set(gk)]
    if only and not all(abs(e[value_field]) < unit for e in only):
        _add(diffs, FAIL, path, gk, fk, "different members")
        return
    if only:
        _add(diffs, FLIP, path, gk, fk, f"members differ only among entries whose {value_field} rounds to 0 (float-noise ties)")
    elif gk != fk:
        _add(diffs, FLIP, path, gk, fk, "same members, different order (tie ordering?)")
    fmap = dict(zip(fk, f))
    for k, e in zip(gk, g):
        if k in fmap:
            _cmp(e, fmap[k], path + (k,), diffs, n, ignored)


def _cmp_list(g: list, f: list, path: tuple, diffs: list, n, ignored) -> None:
    if len(g) != len(f):
        _add(diffs, FAIL, path, len(g), len(f), "list length differs")
        return
    spec = _event_spec(path)
    if spec is not None and g and f and isinstance(g[0], dict) and isinstance(f[0], dict):
        _cmp_keyed(g, f, spec, path, diffs, n, ignored)
        return
    for i, (a, b) in enumerate(zip(g, f)):
        _cmp(a, b, path + (i,), diffs, n, ignored)


def _cmp_dict(g: dict, f: dict, path: tuple, diffs: list, n, ignored) -> None:
    is_counter = bool(path) and path[-1] == "tiers"
    for k in sorted(set(g) | set(f)):
        if path + (k,) in ignored:
            continue
        if is_counter:
            _cmp(g.get(k, 0), f.get(k, 0), path + (k,), diffs, n, ignored)
        elif k not in f:
            _add(diffs, FAIL, path + (k,), g[k], None, "key missing in fresh")
        elif k not in g:
            _add(diffs, FAIL, path + (k,), None, f[k], "key missing in golden")
        else:
            _cmp(g[k], f[k], path + (k,), diffs, n, ignored)


def _cmp(g, f, path: tuple, diffs: list, n=None, ignored=IGNORED_PATHS) -> None:
    assert isinstance(path, tuple), "path must be a tuple"
    if path in ignored:
        return
    if isinstance(g, dict) and isinstance(f, dict):
        _cmp_dict(g, f, path, diffs, n, ignored)
    elif isinstance(g, list) and isinstance(f, list):
        _cmp_list(g, f, path, diffs, n, ignored)
    elif _is_num(g) and _is_num(f):
        _cmp_number(g, f, path, diffs, n)
    elif type(g) is not type(f) or g != f:
        _add(diffs, FAIL, path, g, f, "value differs")


# --------------------------------------------------------- cycle matching
def parse_label(label: str) -> dict:
    """{instrument: +1/-1}. Handles the 'BTC via USDT/GBP: ' basis prefix."""
    assert isinstance(label, str) and label, "label must be non-empty text"
    body = label.split(": ", 1)[1] if ": " in label else label
    tokens = body.split()
    legs = {}
    for token in tokens:
        m = LEG_RE.match(token)
        if not m:
            raise ValueError(f"cannot parse leg {token!r} in label {label!r}")
        legs[m.group(2)] = 1 if m.group(1) == "+" else -1
    if not legs or len(legs) != len(tokens):
        raise ValueError(f"empty or repeated legs in label {label!r}")
    return legs


def orientation_sign(g_legs: dict, f_legs: dict) -> int | None:
    """+1 same direction, -1 reversed, None if the shared legs disagree."""
    assert g_legs.keys() == f_legs.keys(), "orientation needs the same instrument set"
    signs = {g_legs[k] * f_legs[k] for k in g_legs}
    return signs.pop() if len(signs) == 1 else None


def _index_cycles(name: str, cycles: list, side: str, diffs: list) -> dict:
    assert len(cycles) <= 10_000, "too many cycles"
    out = {}
    for pos, cyc in enumerate(cycles):
        try:
            key = frozenset(parse_label(cyc["cycle"]))
        except (KeyError, ValueError) as exc:
            _add(diffs, FAIL, (name, f"{side}[{pos}]"), None, None, f"unparseable cycle: {exc}")
            continue
        if key in out:
            _add(diffs, FAIL, (name, cyc["cycle"]), None, None, f"duplicate cycle in {side}")
            continue
        out[key] = (pos, cyc)
    return out


def _compare_matched(name: str, g_cyc: dict, f_cyc: dict, diffs: list, ignored) -> None:
    g_label, f_label = g_cyc["cycle"], f_cyc["cycle"]
    sign = orientation_sign(parse_label(g_label), parse_label(f_label))
    if sign is None:
        _add(diffs, FAIL, (name, g_label), g_label, f_label, "shared legs disagree in direction - not the same loop")
        return
    if g_label != f_label:
        _add(diffs, INFO, (name, g_label), g_label, f_label, f"label differs (orientation sign {sign:+d})")
    g_body = {k: v for k, v in g_cyc.items() if k != "cycle"}
    f_body = {k: v for k, v in f_cyc.items() if k != "cycle"}
    if f_body.get("signed_mean_bps") is not None:
        f_body["signed_mean_bps"] = f_body["signed_mean_bps"] * sign
    _cmp(g_body, f_body, (name, g_label), diffs, g_body.get("n"), ignored)


def _compare_cycle_section(name: str, g_sec: dict, f_sec: dict, diffs: list, ignored) -> None:
    assert name in CYCLE_SECTIONS, f"{name} is not a cycle section"
    _cmp(g_sec.get("n_cycles"), f_sec.get("n_cycles"), (name, "n_cycles"), diffs, None, ignored)
    g_map = _index_cycles(name, g_sec.get("cycles", []), "golden", diffs)
    f_map = _index_cycles(name, f_sec.get("cycles", []), "fresh", diffs)
    for key in sorted(set(g_map) - set(f_map), key=sorted):
        _add(diffs, FAIL, (name, g_map[key][1]["cycle"]), "present", "absent", "cycle in golden is missing from fresh")
    for key in sorted(set(f_map) - set(g_map), key=sorted):
        _add(diffs, FAIL, (name, f_map[key][1]["cycle"]), "absent", "present", "cycle in fresh is not in golden")
    common = sorted(set(g_map) & set(f_map), key=lambda k: g_map[k][0])
    if [f_map[k][0] for k in common] != sorted(f_map[k][0] for k in common):
        _add(diffs, INFO, (name, "order"), None, None, "cycle list order differs (matched by instrument set)")
    for key in common:
        _compare_matched(name, g_map[key][1], f_map[key][1], diffs, ignored)


def compare_reports(golden: dict, fresh: dict, sections=None, ignored=IGNORED_PATHS) -> GateResult:
    """sections=None compares every top-level key; otherwise only those."""
    assert isinstance(golden, dict) and isinstance(fresh, dict), "reports must be dicts"
    golden, fresh = json.loads(json.dumps(golden)), json.loads(json.dumps(fresh))   # JSON-normalised copies
    diffs: list = []
    keys = list(sections) if sections is not None else sorted(set(golden) | set(fresh))
    for key in keys:
        if (key,) in ignored:
            continue
        if key not in golden or key not in fresh:
            _add(diffs, FAIL, (key,), key in golden, key in fresh, "section missing on one side")
        elif key in CYCLE_SECTIONS:
            _compare_cycle_section(key, golden[key], fresh[key], diffs, ignored)
        else:
            _cmp(golden[key], fresh[key], (key,), diffs, None, ignored)
    skipped = sorted(k for k in golden if sections is not None and k not in keys and (k,) not in ignored)
    return GateResult(diffs=diffs, not_compared=skipped)


# --------------------------------------------------------- engine report
def build_topology(fx_names: list, crypto_names: list) -> list:
    """Edge list with placeholder quotes (topology only): FX on 'oanda',
    crypto on its quote currency's venue, plus the USDT~USD alias edge."""
    assert fx_names or crypto_names, "no instruments"
    edges = [quoted_edge(n, "oanda", 1.0, 1.0, spread_known=True) for n in fx_names]
    quotes = set()
    for n in crypto_names:
        quote = au.split_pair(n)[1]
        quotes.add(quote)
        edges.append(quoted_edge(n, au.QUOTE_TO_VENUE.get(quote, "other"), 1.0, 1.0, spread_known=False))
    for proxy, usd in sorted(au.USD_PROXY.items()):
        if proxy in quotes:
            edges.append(basis_edge(proxy, usd, au.QUOTE_TO_VENUE.get(proxy, "other"), venue_b="oanda"))
    return edges


def classify_cycle(edges: list, cycle: tuple) -> str:
    assert cycle, "empty cycle"
    legs = [edges[i] for i, _d in cycle]
    if any(e.edge_type is EdgeType.BASIS for e in legs):
        return "basis"
    n_crypto = sum(1 for e in legs if au.is_crypto(e.instrument))
    if n_crypto == 0 and len(legs) == 3:
        return "triangle"
    return "crypto_only" if n_crypto else "fx_other"


def _spec_and_dirs(edges: list, cycle: tuple) -> tuple:
    """(name, d) pairs for the series legs only (the alias contributes exactly 0)."""
    spec = tuple((edges[i].instrument, d) for i, d in cycle if edges[i].edge_type is EdgeType.QUOTED)
    assert spec, "cycle has no quoted legs"
    return spec


def _evaluate(spec: tuple, series: dict) -> dict:
    legs_series = [series[name] for name, _d in spec]
    dirs = [d for _n, d in spec]
    common = sorted(set.intersection(*(set(s) for s in legs_series)))
    assert len(common) <= au.MAX_ROWS, "row ceiling exceeded"
    ev = {"stamps": common, "resid": [], "gain_fwd": [], "gain_rev": [], "spread": []}
    for t in common:
        resid, fwd, rev, spread = evaluate_quotes([(s[t][0], s[t][1], d) for s, d in zip(legs_series, dirs)])
        ev["resid"].append(resid)
        ev["gain_fwd"].append(fwd)
        ev["gain_rev"].append(rev)
        ev["spread"].append(spread)
    return ev


def _basis_label(spec: tuple) -> str:
    """'BTC via USDT/GBP: ...' - same wording as the audit; instrument-set matching makes it informational."""
    body = au.cycle_label(spec)
    crypto = [n for n, _d in spec if au.is_crypto(n)]
    base = au.split_pair(crypto[0])[0]
    quotes = [au.split_pair(n)[1] for n in crypto]
    proxy = next((q for q in quotes if q in au.USD_PROXY), None)
    other = next((q for q in quotes if q != proxy), None)
    return f"{base} via {proxy}/{other}: {body}" if proxy and other else body


def build_engine_report(sandbox_path: str, instruments: list, display_tz: str, hooks: dict | None = None) -> tuple:
    """(report, extra). report has only `cycles` and `basis`, shaped like the
    audit's; extra = enumeration counts and the crypto-only 4-cycles (info)."""
    assert Path(sandbox_path).exists(), f"sandbox not found: {sandbox_path}"
    assert display_tz, "display tz required"
    hooks = hooks or {}
    con = sqlite3.connect(f"file:{sandbox_path}?mode=ro", uri=True)
    try:
        series = {n: au.load_series(con, n, g) for n, g, _c, _p in instruments if g}
    finally:
        con.close()
    series = {n: s for n, s in series.items() if s}
    fx = sorted(n for n in series if not au.is_crypto(n))
    crypto = sorted(n for n in series if au.is_crypto(n))
    edges = build_topology(fx, crypto)
    cset = enumerate_cycles(edges, "asset", max_len=ENGINE_MAX_LEN)
    assert not cset.truncated, "cycle enumeration truncated"
    groups = {"triangle": [], "basis": [], "crypto_only": [], "fx_other": []}
    for cyc in cset.cycles:
        groups[classify_cycle(edges, cyc)].append(cyc)

    def run(kind: str, rated: bool, labeller) -> list:
        out = []
        for cyc in sorted(groups[kind], key=lambda c: sorted(n for n, _d in _spec_and_dirs(edges, c))):
            t0 = time.perf_counter()
            spec = _spec_and_dirs(edges, cyc)
            label = labeller(spec)
            out.append((label, au.summarize_cycle(label, _evaluate(spec, series), display_tz, rated=rated), spec))
            if hooks.get("item"):
                hooks["item"](f"engine/{kind}/{label}", time.perf_counter() - t0)
        return out

    tri = run("triangle", True, au.cycle_label)
    bas = run("basis", False, _basis_label)
    cry = run("crypto_only", False, au.cycle_label)
    report = {"cycles": {"n_cycles": len(tri), "cycles": [s for _l, s, _p in tri]},
              "basis": {"n_cycles": len(bas), "cycles": [s for _l, s, _p in bas]}}
    extra = {"enumeration": {k: len(v) for k, v in groups.items()} | {"truncated": cset.truncated},
             "crypto_only_info": [
                 {"cycle": label, "n": s["n"], "abs_bps_p99": s["abs_bps"]["99"], "abs_bps_max": s["abs_bps"]["100"]}
                 for label, s, _p in cry if s["n"]]}
    return report, extra


# ------------------------------------------------------------------ run
def run_gate(cfg: dict, golden: dict, hooks: dict | None = None) -> list:
    """List of comparison dicts (JSON-serialisable)."""
    assert cfg["fresh_path"] or cfg["engine"], "nothing to compare"
    hooks = hooks or {}
    out = []
    if cfg["fresh_path"]:
        fresh = json.loads(Path(cfg["fresh_path"]).read_text())
        out.append(_summarise("fresh audit report vs golden (whole report)", compare_reports(golden, fresh), {}))
    if cfg["engine"]:
        report, extra = build_engine_report(cfg["sandbox_path"], cfg["instruments"], golden["display_tz"], hooks)
        out.append(_summarise("engine (currency_graph) vs golden (cycles + basis only)",
                              compare_reports(golden, report, sections=ENGINE_SECTIONS), extra))
    return out


def _summarise(label: str, result: GateResult, extra: dict) -> dict:
    return {"label": label, "verdict": result.verdict, "n_fail": result.count(FAIL), "n_flip": result.count(FLIP),
            "n_info": result.count(INFO), "not_compared": result.not_compared,
            "diffs": [asdict(d) for d in result.diffs], "extra": extra}


def exit_code(comparisons: list) -> int:
    assert comparisons, "no comparisons"
    verdicts = {c["verdict"] for c in comparisons}
    return 2 if "FAIL" in verdicts else 3 if "FLIP" in verdicts else 0


def render_text(comparisons: list) -> str:
    assert comparisons, "no comparisons"
    lines = []
    for c in comparisons:
        lines.append(f"=== {c['label']}: {c['verdict']}  (FAIL {c['n_fail']}, FLIP {c['n_flip']}, INFO {c['n_info']})")
        if c["not_compared"]:
            lines.append(f"  sections not compared: {c['not_compared']}")
        for sev in (FAIL, FLIP, INFO):
            shown = [d for d in c["diffs"] if d["severity"] == sev][:MAX_SHOWN]
            for d in shown:
                lines.append(f"  {sev:<4} {d['path']}: golden={d['golden']!r} fresh={d['fresh']!r} {d['note']}")
        extra = c["extra"]
        if extra:
            lines.append(f"  enumeration: {extra['enumeration']}")
            for e in extra["crypto_only_info"]:
                lines.append(f"  info (not gated) crypto-only {e['cycle']}: n={e['n']} abs_bps p99={e['abs_bps_p99']:.3f} max={e['abs_bps_max']:.3f}")
    return "\n".join(lines)


# ------------------------------------------------------------------ cli
def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Compare audit reports with the golden report.")
    p.add_argument("--chat", type=int, required=True, help="chat number, used in the output name")
    p.add_argument("--golden", required=True, help="golden audit report JSON")
    p.add_argument("--fresh", default=None, help="a new full audit report JSON to compare whole")
    p.add_argument("--engine", action="store_true", help="build cycles/basis with currency_graph from the sandbox and compare")
    p.add_argument("--sandbox-db", default=None, help="sqlite path; default = sandbox_config.py's sandbox")
    p.add_argument("--tag", default="", help="optional output-name suffix (letters, digits, hyphen)")
    p.add_argument("--out-dir", default="reports")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace, run_date: str, instruments: list | None = None) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    assert args.fresh or args.engine, "nothing to do: pass --fresh and/or --engine"
    assert Path(args.golden).exists(), f"golden report not found: {args.golden}"
    assert not args.fresh or Path(args.fresh).exists(), f"fresh report not found: {args.fresh}"
    golden = json.loads(Path(args.golden).read_text())
    assert "cycles" in golden and "basis" in golden and golden.get("display_tz"), "golden lacks cycles/basis/display_tz"
    local_view(0, golden["display_tz"])
    sandbox = None
    if args.engine:
        sandbox = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
        assert Path(sandbox).exists(), f"sandbox DB not found: {sandbox}"
    suffix = f"_{args.tag}" if args.tag else ""
    out_path = str(Path(args.out_dir) / f"audit_gate_chat{args.chat}_{run_date}{suffix}.json")
    assert not Path(out_path).exists(), f"gate output already exists: {out_path} - pick a different --tag"
    if instruments is None:
        from instrument_config import INSTRUMENTS
        instruments = INSTRUMENTS
    return {"golden_path": args.golden, "fresh_path": args.fresh, "engine": bool(args.engine), "sandbox_path": sandbox,
            "out_path": out_path, "chat": args.chat, "tag": args.tag, "instruments": instruments, "golden": golden}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("audit_gate", chat=args.chat, tag=args.tag, golden=cfg["golden_path"], fresh=cfg["fresh_path"],
                       engine=cfg["engine"], sandbox_db=cfg["sandbox_path"]):
        note("out_path", cfg["out_path"])
        comparisons = run_gate(cfg, cfg["golden"], {"item": item})
        count("comparisons", len(comparisons))
        Path(cfg["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["out_path"]).write_text(json.dumps({"schema": 1, "tool": "audit_gate", "chat": args.chat,
                                                      "golden": cfg["golden_path"], "comparisons": comparisons}, indent=1))
        print(render_text(comparisons))
        print(f"\nGate result written: {cfg['out_path']}")
    code = exit_code(comparisons)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
