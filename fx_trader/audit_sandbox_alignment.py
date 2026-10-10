# version: 261009
"""
audit_sandbox_alignment.py  (fx_trader/audit_sandbox_alignment.py)

Read-only audit of the sandbox DB (chat 14): data format, gap taxonomy,
cross-rate cycle consistency, crypto basis, timing alignment, annualisation
constants and a data-integrity manifest. Findings and decisions this encodes:
CONTEXT_HANDOFF.md Section 4f and RESEARCH_NOTES.md.

CHECKS (--checks, comma list, default all)
  format    timestamp format/hour alignment, bid/ask and OHLC sanity
  gaps      FX gap events (scheduled weekend via the New York 17:00 rule vs
            holiday CANDIDATE vs FX-wide unscheduled vs pair-specific) with
            crypto activity inside each gap; crypto missing hours by venue
  cycles    every FX triangle ENUMERATED from the instrument list (USD is not
            special): mid residual (bps), residual / summed leg spread, and
            the EXECUTABLE no-arbitrage test (bid/ask per direction). Since
            chat 14p2 discovery and the per-bar numbers come from
            currency_graph, in a CANONICAL orientation/order that reproduces
            the pre-refactor report exactly (tests/oracle_audit_261003.py is
            the frozen independent copy of the old logic)
  basis     crypto basis cycles X/USDT -> X/GBP -> GBP_USD (USDT~USD alias)
  lag       Binance-vs-Kraken return correlation at lags; FX-vs-crypto timing
            (loading of the basis change on GBP_USD returns; ~0 = aligned)
  ppy       observed bars per year vs the configured periods_per_year (MC-1)
  manifest  per-instrument row count / first / last / sha256

CLI
  python3 audit_sandbox_alignment.py --chat 14 [--checks gaps,cycles]
      [--sandbox-db PATH] [--display-tz Europe/London] [--tag X]
      [--out-dir reports] [--write-manifest PATH] [--verify-manifest PATH]
  Output: <out-dir>/audit_<first>to<last>_chat<N>_<run date, London>[_<tag>].json
  (refuses to overwrite) plus a text summary on stdout. --verify-manifest
  exits with status 2 on any mismatch. Run it BEFORE any sweep or analysis
  that reads the sandbox, and after any re-ingest (then --write-manifest).
  It is a data gate, NOT part of apply_patch (tests there stay hermetic).

LIMITS (also written into every report): holiday classes are CANDIDATES
until checked against an external source; bar-open vs bar-close is only
verified relative to OANDA, not absolutely; sub-hour skew is not detectable
at H1; thresholds are provisional, set from the chat-14 distribution.
Standard library only; adopts run_monitor.
"""

import argparse
import bisect
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from currency_graph import EdgeType, basis_edge, enumerate_cycles, evaluate_quotes, quoted_edge
from currency_graph.evaluate import ABS_SUSPECT_BPS, RATIO_CLEAN, RATIO_SUSPECT, bar_tier
from instrument_config import INSTRUMENTS
from run_monitor import LONDON, count, item, monitored_run, note
from sandbox_config import GLOBAL_START, discover_sandbox_end, sandbox_db_path
from time_policy import epoch_to_stored_ts, fx_weekly_session, local_view, parse_ts, stored_ts_to_epoch

HOUR = 3600
YEAR_SECONDS = 365.25 * 86400
MAX_ROWS = 10_000_000
MAX_EVENTS = 5_000
CYCLE_MAX_LEN = 4           # longest cycle enumerated (basis cycles have four legs)
CHECKS = ("format", "gaps", "cycles", "basis", "lag", "ppy", "manifest")
QUOTE_TO_VENUE = {"USDT": "binance", "GBP": "kraken"}   # assumption: quote currency identifies the venue
USD_PROXY = {"USDT": "USD"}                               # alias edge: USDT treated as USD (basis measured, not assumed)
PPY_WARN_FRACTION = 0.01
LAG_RANGE = (-2, -1, 0, 1, 2)
LOADING_LAGS = (-1, 0, 1)
PERCENTILES = (50, 90, 99, 99.9, 100)
TOP_N = 5
MIN_DAY_BARS = 10
LIMITATIONS = [
    "Holiday classes are CANDIDATES (inferred from dates); confirm against an external source (roadmap 16p1/30p1).",
    "Bar-open vs bar-close is verified only relative to OANDA; absolute Kraken/Binance convention is unconfirmed.",
    "Bars are bar-close bid/ask, not simultaneous tick snapshots: residuals include close-time asynchrony.",
    "Sub-hour timing skew is not detectable at H1; the loading test only bounds it.",
    "Tier thresholds are provisional, set from the chat-14 distribution. Basis cycles are NOT tiered or executable-tested: crypto bars have bid == ask, so their cost is unknown here.",
    "Single source (OANDA, Binance, Kraken): no independent QC yet (roadmap 16p1).",
]


# ---------------------------------------------------------------- helpers
def pcts(values, points=PERCENTILES) -> dict:
    assert values, "pcts requires at least one value"
    assert all(0 <= p <= 100 for p in points), "percentile points must be in [0, 100]"
    ordered = sorted(values)
    out = {}
    for p in points:
        k = (len(ordered) - 1) * p / 100
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        out[str(p)] = ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)
    return out


def mid(quote) -> float:
    assert len(quote) == 2, "quote must be (bid, ask)"
    assert quote[0] > 0 and quote[1] > 0, f"non-positive price in {quote}"
    return (quote[0] + quote[1]) / 2


def split_pair(instrument: str) -> tuple[str, str]:
    assert instrument, "instrument name required"
    parts = instrument.split("/" if "/" in instrument else "_")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"cannot split instrument {instrument!r} into base/quote")
    return parts[0], parts[1]


def is_crypto(instrument: str) -> bool:
    assert instrument, "instrument name required"
    return "/" in instrument


def utc(epoch: int) -> datetime:
    assert isinstance(epoch, int), "epoch must be int seconds"
    return datetime.fromtimestamp(epoch, timezone.utc)


def stamp(epoch: int, display_tz: str) -> dict:
    assert display_tz, "display tz required"
    return {"utc": epoch_to_stored_ts(epoch), "local": local_view(epoch, display_tz)}


def load_series(con, instrument: str, granularity: str) -> dict:
    """epoch -> (bid_close, ask_close). Strict stored-format parse per row."""
    assert instrument and granularity, "instrument and granularity required"
    rows = con.execute(
        "SELECT timestamp, bid_close, ask_close FROM candles WHERE instrument=? AND granularity=? ORDER BY timestamp",
        (instrument, granularity),
    )
    series = {}
    for ts, bid, ask in rows:
        series[stored_ts_to_epoch(ts)] = (bid, ask)
        assert len(series) <= MAX_ROWS, "row ceiling exceeded"
    return series


# ---------------------------------------------------------------- format
def check_format(con) -> dict:
    assert con is not None, "connection required"
    sql = (
        "SELECT instrument, granularity, COUNT(*), COUNT(DISTINCT LENGTH(timestamp)), "
        "SUM(SUBSTR(timestamp,15,5) != '00:00'), SUM(timestamp NOT LIKE '%.000000000Z'), "
        "SUM(ask_close < bid_close), SUM(ask_close = bid_close), "
        "SUM(bid_high < MAX(bid_open,bid_close) OR bid_low > MIN(bid_open,bid_close) "
        "OR ask_high < MAX(ask_open,ask_close) OR ask_low > MIN(ask_open,ask_close)) "
        "FROM candles GROUP BY 1,2 ORDER BY 1,2"
    )
    out = {}
    for r in con.execute(sql):
        out[f"{r[0]}|{r[1]}"] = {
            "rows": r[2], "distinct_ts_lengths": r[3], "off_hour_rows": r[4], "non_stored_format_rows": r[5],
            "ask_below_bid_rows": r[6], "zero_spread_rows": r[7], "ohlc_inconsistent_rows": r[8],
        }
    assert out, "candles table is empty"
    return out


# ------------------------------------------------------------------ gaps
def is_scheduled_weekend(a: int, b: int) -> bool:
    """True when (last bar a, next bar b) is exactly the Friday-close ->
    Sunday-reopen pair predicted by the New York 17:00 rule."""
    assert b > a, "gap end must follow gap start"
    start = utc(a)
    if start.weekday() != 4:
        return False
    last_bar, first_bar = fx_weekly_session(start.date())
    return a == int(last_bar.timestamp()) and b == int(first_bar.timestamp())


def in_holiday_window(dt: datetime) -> bool:
    assert dt.tzinfo is not None, "aware datetime required"
    return (dt.month == 12 and dt.day >= 20) or (dt.month == 1 and dt.day <= 3)


def classify_fx_event(a: int, sharing: int, total: int) -> str:
    assert 0 < sharing <= total, "sharing must be within 1..total"
    if sharing < total:
        return "PAIR_SPECIFIC"
    return "HOLIDAY_CANDIDATE" if in_holiday_window(utc(a)) else "FX_WIDE_UNSCHEDULED"


def record_gaps(name: str, epochs: list, events: dict, scheduled: Counter) -> None:
    assert epochs, f"{name}: no bars"
    assert isinstance(scheduled, Counter), "scheduled must be a Counter"
    for a, b in zip(epochs, epochs[1:]):
        if b - a <= HOUR:
            continue
        if is_scheduled_weekend(a, b):
            scheduled[name] += 1
        else:
            events[(a, b)].add(name)


def bars_inside(sorted_epochs: list, a: int, b: int) -> int:
    assert b > a, "window end must follow start"
    assert sorted_epochs == sorted(sorted_epochs) or len(sorted_epochs) > 10_000, "epochs must be sorted"
    return bisect.bisect_left(sorted_epochs, b) - bisect.bisect_right(sorted_epochs, a)


def fx_gap_report(fx_series: dict, ref_epochs: list, display_tz: str) -> dict:
    assert fx_series, "no FX series"
    events = defaultdict(set)
    scheduled = Counter()
    for name, series in fx_series.items():
        record_gaps(name, sorted(series), events, scheduled)
    assert len(events) <= MAX_EVENTS, "gap-event ceiling exceeded"
    total = len(fx_series)
    rows = []
    for (a, b), names in sorted(events.items()):
        gap_h = (b - a) // HOUR
        rows.append({
            "start": stamp(a, display_tz), "next_bar": stamp(b, display_tz), "gap_hours": gap_h,
            "class": classify_fx_event(a, len(names), total), "pairs_sharing": len(names), "pairs_total": total,
            "pairs": sorted(names) if len(names) < total else "all",
            "reference_crypto_bars_inside": bars_inside(ref_epochs, a, b) if ref_epochs else None,
            "reference_crypto_bars_expected": gap_h - 1,
        })
    return {"scheduled_weekend_gaps_per_pair": dict(scheduled), "unscheduled_events": rows,
            "class_counts": dict(Counter(r["class"] for r in rows))}


def missing_hour_set(epochs: list) -> set:
    assert epochs, "no bars"
    first, last = epochs[0], epochs[-1]
    assert (last - first) // HOUR <= MAX_ROWS, "grid ceiling exceeded"
    have = set(epochs)
    return {t for t in range(first, last + 1, HOUR) if t not in have}


def hour_runs(missing: set) -> list:
    """Consecutive missing hours -> [(start_epoch, length_hours)]."""
    assert isinstance(missing, set), "missing must be a set"
    runs, start, prev = [], None, None
    for t in sorted(missing):
        if prev is not None and t - prev == HOUR:
            prev = t
            continue
        if start is not None:
            runs.append((start, (prev - start) // HOUR + 1))
        start = prev = t
    if start is not None:
        runs.append((start, (prev - start) // HOUR + 1))
    return runs


def instrument_missing_summary(missing: set, display_tz: str) -> dict:
    assert isinstance(missing, set), "missing must be a set"
    weekend = sum(1 for t in missing if utc(t).weekday() >= 5)
    longest = sorted(hour_runs(missing), key=lambda r: -r[1])[:2]
    return {"missing_hours": len(missing), "weekend_share": (weekend / len(missing)) if missing else None,
            "longest_runs": [{"start": stamp(s, display_tz), "hours": n} for s, n in longest]}


def venue_summary(names: list, missing_by_name: dict, display_tz: str) -> dict:
    assert names, "venue has no instruments"
    sets = [missing_by_name[n] for n in names]
    union = set().union(*sets)
    in_all = set.intersection(*sets)
    histogram = Counter(sum(t in s for s in sets) for t in union)
    return {"instruments": names, "missing_union": len(union), "missing_in_all": len(in_all),
            "missing_in_all_sample": [stamp(t, display_tz) for t in sorted(in_all)[:20]],
            "histogram_by_n_instruments_missing": {str(k): v for k, v in sorted(histogram.items())},
            "single_instrument_venue": len(names) == 1}


def crypto_gap_report(crypto_series: dict, display_tz: str) -> dict:
    if not crypto_series:
        return {}
    missing = {n: missing_hour_set(sorted(s)) for n, s in crypto_series.items()}
    venues = defaultdict(list)
    for n in sorted(crypto_series):
        venues[QUOTE_TO_VENUE.get(split_pair(n)[1], "other")].append(n)
    return {"per_instrument": {n: instrument_missing_summary(m, display_tz) for n, m in missing.items()},
            "venues": {v: venue_summary(names, missing, display_tz) for v, names in venues.items()}}


# ---------------------------------------------------------------- cycles
def build_topology(fx_names: list, crypto_names: list) -> list:
    """Edge list with placeholder quotes (topology only): FX on 'oanda', crypto
    on its quote currency's venue, plus the USDT~USD alias edge when a crypto
    pair is quoted in the proxy currency."""
    assert fx_names or crypto_names, "no instruments"
    edges = [quoted_edge(n, "oanda", 1.0, 1.0, spread_known=True) for n in fx_names]
    quotes = set()
    for n in crypto_names:
        quote = split_pair(n)[1]
        quotes.add(quote)
        edges.append(quoted_edge(n, QUOTE_TO_VENUE.get(quote, "other"), 1.0, 1.0, spread_known=False))
    for proxy, usd in sorted(USD_PROXY.items()):
        if proxy in quotes:
            edges.append(basis_edge(proxy, usd, QUOTE_TO_VENUE.get(proxy, "other"), venue_b="oanda"))
    return edges


def classify_cycle(edges: list, cycle: tuple) -> str:
    """'basis' (contains the alias edge), 'triangle' (three FX legs),
    'crypto_only' (no alias, some crypto leg) or 'fx_other'."""
    assert cycle, "empty cycle"
    legs = [edges[i] for i, _d in cycle]
    if any(e.edge_type is EdgeType.BASIS for e in legs):
        return "basis"
    n_crypto = sum(1 for e in legs if is_crypto(e.instrument))
    if n_crypto == 0 and len(legs) == 3:
        return "triangle"
    return "crypto_only" if n_crypto else "fx_other"


def oriented_spec(edges: list, cycle: tuple, path: list) -> tuple:
    """((instrument, d), ...) for the QUOTED legs of `cycle`, walked along the
    asset `path` (closing back to path[0]); d = +1 when a leg is walked
    base -> quote. The alias hop, if any, contributes nothing (rate exactly 1)."""
    assert len(path) >= 3, "a cycle needs at least three assets"
    remaining = [i for i, _d in cycle]
    spec = []
    for x, y in zip(path, path[1:] + path[:1]):
        hit = next((i for i in remaining if {edges[i].base, edges[i].quote} == {x, y}), None)
        if hit is None:
            raise ValueError(f"no leg joins {x} and {y}")
        remaining.remove(hit)
        if edges[hit].edge_type is EdgeType.QUOTED:
            spec.append((edges[hit].instrument, 1 if edges[hit].base == x else -1))
    assert not remaining, "path did not use every leg of the cycle"
    return tuple(spec)


def _basis_path(edges: list, cycle: tuple) -> list | None:
    """[proxy, base, other, usd] for the shape USDT -> X -> Q -> USD -> USDT, else None."""
    legs = [edges[i] for i, _d in cycle]
    alias = [e for e in legs if e.edge_type is EdgeType.BASIS]
    crypto = [e for e in legs if e.edge_type is EdgeType.QUOTED and is_crypto(e.instrument)]
    fx = [e for e in legs if e.edge_type is EdgeType.QUOTED and not is_crypto(e.instrument)]
    if len(legs) != 4 or len(alias) != 1 or len(crypto) != 2 or len(fx) != 1:
        return None
    proxy, usd = alias[0].base, alias[0].quote
    if crypto[0].base != crypto[1].base:
        return None
    others = {e.quote for e in crypto} - {proxy}
    if len(others) != 1 or proxy not in {e.quote for e in crypto}:
        return None
    other = next(iter(others))
    if {fx[0].base, fx[0].quote} != {other, usd}:
        return None
    return [proxy, crypto[0].base, other, usd]


def discover_cycles(fx_names: list, crypto_names: list, max_len: int = CYCLE_MAX_LEN) -> dict:
    """Cycles found by currency_graph, in the audit's canonical orientation and
    order. Returns {'triangle': [(label, spec)], 'basis': [...], 'crypto_only': [...],
    'fx_other': count, 'truncated': bool}. Triangles walk the sorted currencies
    a -> b -> c -> a and are ordered by that triple; basis cycles walk
    USDT -> X -> Q -> USD and are ordered by (base, proxy, other); crypto-only
    cycles keep the engine's orientation (diagnostic, not part of any report)."""
    edges = build_topology(fx_names, crypto_names)
    cset = enumerate_cycles(edges, "asset", max_len=max_len)
    assert not cset.truncated, "cycle enumeration truncated"
    keyed = {"triangle": [], "basis": [], "crypto_only": []}
    fx_other = 0
    for cyc in cset.cycles:
        kind = classify_cycle(edges, cyc)
        nodes = sorted({a for i, _d in cyc for a in (edges[i].base, edges[i].quote)})
        if kind == "triangle":
            spec = oriented_spec(edges, cyc, nodes)
            keyed["triangle"].append((tuple(nodes), cycle_label(spec), spec))
        elif kind == "basis":
            path = _basis_path(edges, cyc)
            if path is not None:
                spec = oriented_spec(edges, cyc, path)
                keyed["basis"].append(((path[1], path[0], path[2]), f"{path[1]} via {path[0]}/{path[2]}: {cycle_label(spec)}", spec))
            else:
                spec = tuple((edges[i].instrument, d) for i, d in cyc if edges[i].edge_type is EdgeType.QUOTED)
                keyed["basis"].append((("~",) + tuple(nodes), f"basis: {cycle_label(spec)}", spec))
        elif kind == "crypto_only":
            spec = tuple((edges[i].instrument, d) for i, d in cyc)
            keyed["crypto_only"].append((tuple(sorted(n for n, _d in spec)), cycle_label(spec), spec))
        else:
            fx_other += 1
    out = {kind: [(label, spec) for _k, label, spec in sorted(rows, key=lambda r: r[0])] for kind, rows in keyed.items()}
    return out | {"fx_other": fx_other, "truncated": cset.truncated}


def cycle_label(spec: tuple) -> str:
    assert spec, "empty cycle"
    return " ".join(f"{'+' if d > 0 else '-'}{n}" for n, d in spec)


def evaluate_spec(spec: tuple, series_by_name: dict) -> dict:
    """Per common bar, in bps (currency_graph.evaluate_quotes): mid residual
    (sum of signed log mids), executable gain going each way round the loop
    (sell base at bid / buy base at ask), and the summed leg spread."""
    assert spec and all(len(e) == 2 for e in spec), "bad cycle spec"
    legs_series = [series_by_name[n] for n, _d in spec]
    dirs = [d for _n, d in spec]
    common = sorted(set.intersection(*(set(s) for s in legs_series)))
    assert len(common) <= MAX_ROWS, "row ceiling exceeded"
    ev = {"stamps": common, "resid": [], "gain_fwd": [], "gain_rev": [], "spread": []}
    for t in common:
        resid, fwd, rev, spread = evaluate_quotes([(s[t][0], s[t][1], d) for s, d in zip(legs_series, dirs)])
        ev["resid"].append(resid)
        ev["gain_fwd"].append(fwd)
        ev["gain_rev"].append(rev)
        ev["spread"].append(spread)
    return ev


def group_pcts(keys: list, values: list, points=(50, 99)) -> dict:
    assert len(keys) == len(values), "keys/values length mismatch"
    groups = defaultdict(list)
    for k, v in zip(keys, values):
        groups[k].append(v)
    return {str(k): {p: round(x, 4) for p, x in pcts(v, points).items()} for k, v in sorted(groups.items())}


def stress_days(stamps: list, ratios: list) -> list:
    assert len(stamps) == len(ratios), "length mismatch"
    days = defaultdict(list)
    for t, r in zip(stamps, ratios):
        if r is not None:
            days[utc(t).strftime("%Y-%m-%d")].append(r)
    scored = [(statistics.median(v), d, len(v)) for d, v in days.items() if len(v) >= MIN_DAY_BARS]
    return [{"date": d, "median_ratio": round(m, 4), "bars": n} for m, d, n in sorted(scored, reverse=True)[:TOP_N]]


def summarize_cycle(label: str, ev: dict, display_tz: str, rated: bool = True) -> dict:
    """rated=False (crypto basis: bid == ask on the crypto legs, so the summed spread is
    incomplete) reports residual statistics only - no cost ratio, tiers or executable test."""
    n = len(ev["resid"])
    if n == 0:
        return {"cycle": label, "n": 0}
    resid, spread, stamps = ev["resid"], ev["spread"], ev["stamps"]
    ab = [abs(x) for x in resid]
    ratios = [(a / s) if (rated and s > 0) else None for a, s in zip(ab, spread)]
    ratio_vals = [r for r in ratios if r is not None]
    viol = [max(f, g, 0.0) for f, g in zip(ev["gain_fwd"], ev["gain_rev"])]
    order = sorted(range(n), key=lambda i: -ab[i])[:TOP_N]
    return {
        "cycle": label, "n": n, "signed_mean_bps": statistics.fmean(resid), "abs_bps": pcts(ab),
        "rated": rated,
        "spread_bps": pcts(spread, (50, 99)) if rated else None, "ratio": pcts(ratio_vals) if ratio_vals else None,
        "share_ratio_gt_clean": (sum(r > RATIO_CLEAN for r in ratio_vals) / len(ratio_vals)) if ratio_vals else None,
        "share_ratio_gt_suspect": (sum(r > RATIO_SUSPECT for r in ratio_vals) / len(ratio_vals)) if ratio_vals else None,
        "exec_violation_share": (sum(v > 0 for v in viol) / n) if rated else None,
        "exec_violation_bps": pcts(viol, (99, 100)) if rated else None,
        "tiers": dict(Counter(bar_tier(a, s) for a, s in zip(ab, spread))) if rated else None,
        "by_year_abs_bps": group_pcts([utc(t).year for t in stamps], ab),
        "worst_utc_hours_p99": sorted(
            ((h, v["99"]) for h, v in group_pcts([utc(t).hour for t in stamps], ab).items()), key=lambda x: -x[1])[:3],
        "stress_days": stress_days(stamps, ratios),
        "worst_events": [{"at": stamp(stamps[i], display_tz), "abs_bps": round(ab[i], 3), "spread_bps": round(spread[i], 3)} for i in order],
    }


def cycles_report(fx_names: list, series_by_name: dict, display_tz: str) -> dict:
    triangles = discover_cycles(fx_names, [], max_len=3)["triangle"]
    return {"n_cycles": len(triangles),
            "cycles": [summarize_cycle(label, evaluate_spec(spec, series_by_name), display_tz) for label, spec in triangles]}


def basis_report(crypto_names: list, fx_names: list, series_by_name: dict, display_tz: str) -> dict:
    basis = discover_cycles(fx_names, crypto_names)["basis"]
    return {"n_cycles": len(basis),
            "cycles": [summarize_cycle(label, evaluate_spec(spec, series_by_name), display_tz, rated=False) for label, spec in basis]}


# ------------------------------------------------------------------- lag
def hourly_returns(series: dict) -> dict:
    assert series, "empty series"
    out = {}
    for t, quote in series.items():
        prev = series.get(t - HOUR)
        if prev is not None:
            out[t] = math.log(mid(quote) / mid(prev))
    return out


def lag_correlations(ra: dict, rb: dict, lags=LAG_RANGE) -> dict:
    """corr(ra[t], rb[t + k hours]) for each lag k."""
    assert ra and rb, "empty return series"
    out = {}
    for k in lags:
        common = [t for t in ra if (t + k * HOUR) in rb]
        try:
            out[str(k)] = statistics.correlation([ra[t] for t in common], [rb[t + k * HOUR] for t in common])
        except statistics.StatisticsError:
            out[str(k)] = None   # <2 points or constant series: undefined, reported as None
    return out


def basis_loading(gbp: dict, usdt: dict, fx: dict, lags=LOADING_LAGS) -> dict:
    """Slope of the hourly basis change (bps) on GBP_USD returns (bps) at lag k.
    Aligned timestamps give ~0 at lag 0; a one-hour FX offset gives ~+/-1."""
    r_g, r_u, r_f = hourly_returns(gbp), hourly_returns(usdt), hourly_returns(fx)
    times = [t for t in r_g if t in r_u and t in r_f]
    assert len(times) >= 10, "too few common returns for a loading estimate"
    d_basis = {t: 1e4 * (r_g[t] + r_f[t] - r_u[t]) for t in times}
    out = {}
    for k in lags:
        common = [t for t in times if (t + k * HOUR) in r_f]
        x = [1e4 * r_f[t + k * HOUR] for t in common]
        try:
            out[str(k)] = statistics.linear_regression(x, [d_basis[t] for t in common]).slope
        except statistics.StatisticsError:
            out[str(k)] = None
    return out


def lag_report(crypto_names: list, fx_names: list, series_by_name: dict) -> dict:
    pair_of = {frozenset(split_pair(n)): n for n in fx_names}
    by_base = defaultdict(dict)
    for n in crypto_names:
        base, quote = split_pair(n)
        by_base[base][quote] = n
    out = {}
    for base, quotes in sorted(by_base.items()):
        if "USDT" not in quotes or "GBP" not in quotes:
            continue
        u, g = series_by_name[quotes["USDT"]], series_by_name[quotes["GBP"]]
        entry = {"binance_vs_kraken_return_corr_by_lag": lag_correlations(hourly_returns(u), hourly_returns(g))}
        fx = pair_of.get(frozenset(("GBP", "USD")))
        if fx:
            entry["basis_change_loading_on_fx_return_by_lag"] = basis_loading(g, u, series_by_name[fx])
        out[base] = entry
    return out


# ------------------------------------------------------------------- ppy
def ppy_check(name: str, epochs: list, configured: float) -> dict:
    assert len(epochs) >= 2 and configured > 0, "need >=2 bars and a positive configured value"
    span_years = (epochs[-1] - epochs[0]) / YEAR_SECONDS
    observed = len(epochs) / span_years
    ratio = observed / configured
    return {"instrument": name, "bars": len(epochs), "observed_bars_per_year": observed, "configured": configured,
            "ratio": ratio, "sharpe_scale_if_rebased": math.sqrt(ratio), "warn": abs(ratio - 1) > PPY_WARN_FRACTION}


# -------------------------------------------------------------- manifest
def build_manifest(con) -> dict:
    assert con is not None, "connection required"
    hashers, rows_n, first, last = {}, Counter(), {}, {}
    cursor = con.execute(
        "SELECT instrument, granularity, timestamp, bid_open, bid_high, bid_low, bid_close, "
        "ask_open, ask_high, ask_low, ask_close, volume FROM candles ORDER BY instrument, granularity, timestamp")
    total = 0
    for row in cursor:
        total += 1
        assert total <= MAX_ROWS, "row ceiling exceeded"
        key = f"{row[0]}|{row[1]}"
        hashers.setdefault(key, hashlib.sha256()).update((",".join(repr(v) for v in row[2:]) + "\n").encode())
        rows_n[key] += 1
        first.setdefault(key, row[2])
        last[key] = row[2]
    return {k: {"rows": rows_n[k], "first": first[k], "last": last[k], "sha256": h.hexdigest()} for k, h in hashers.items()}


def diff_manifests(expected: dict, actual: dict) -> list:
    assert isinstance(expected, dict) and isinstance(actual, dict), "manifests must be dicts"
    diffs = []
    for key in sorted(set(expected) | set(actual)):
        e, a = expected.get(key), actual.get(key)
        if e is None:
            diffs.append(f"{key}: in DB but not in manifest")
        elif a is None:
            diffs.append(f"{key}: in manifest but missing from DB")
        elif e != a:
            diffs.append(f"{key}: manifest rows={e['rows']} sha={e['sha256'][:12]} vs DB rows={a['rows']} sha={a['sha256'][:12]}")
    return diffs


def write_manifest_file(path: str, manifest: dict, sandbox_path: str) -> None:
    assert manifest, "refusing to write an empty manifest"
    target = Path(path)
    assert not target.exists(), f"manifest already exists: {path} (never overwritten)"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "sandbox_db": sandbox_path, "instruments": manifest}, indent=1))


def load_manifest_file(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    assert data.get("schema") == 1 and "instruments" in data, f"unrecognised manifest file: {path}"
    return data["instruments"]


# ----------------------------------------------------------------- audit
def timed(hooks: dict, label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    if hooks.get("item"):
        hooks["item"](label, time.perf_counter() - t0)
    return result


def run_audit(cfg: dict, instruments: list, hooks: dict | None = None) -> dict:
    """Pure of side effects except reading the DB (read-only connection)."""
    hooks = hooks or {}
    checks = cfg["checks"]
    assert set(checks) <= set(CHECKS), f"unknown checks: {set(checks) - set(CHECKS)}"
    tz = cfg["display_tz"]
    con = sqlite3.connect(f"file:{cfg['sandbox_path']}?mode=ro", uri=True)
    try:
        report = {"schema": 1, "tool": "audit_sandbox_alignment", "chat": cfg["chat"], "sandbox_db": cfg["sandbox_path"],
                  "display_tz": tz, "checks": list(checks), "limitations": LIMITATIONS,
                  "thresholds": {"ratio_clean": RATIO_CLEAN, "ratio_suspect": RATIO_SUSPECT, "abs_suspect_bps": ABS_SUSPECT_BPS}}
        if "format" in checks:
            report["format"] = timed(hooks, "format", lambda: check_format(con))
        if "manifest" in checks or cfg.get("verify_manifest") or cfg.get("write_manifest"):
            manifest = timed(hooks, "manifest", lambda: build_manifest(con))
            report["manifest"] = {"instruments": len(manifest), "rows": sum(m["rows"] for m in manifest.values())}
            _apply_manifest(cfg, manifest, report)
        needs_series = {"gaps", "cycles", "basis", "lag", "ppy"} & set(checks)
        if needs_series:
            series = {n: load_series(con, n, g) for n, g, _c, _p in instruments if g}
            series = {n: s for n, s in series.items() if s}
            if hooks.get("count"):
                hooks["count"]("instruments_loaded", len(series))
            _series_checks(report, checks, instruments, series, tz, hooks)
    finally:
        con.close()
    return report


def _apply_manifest(cfg: dict, manifest: dict, report: dict) -> None:
    assert manifest, "empty manifest"
    if cfg.get("write_manifest"):
        write_manifest_file(cfg["write_manifest"], manifest, cfg["sandbox_path"])
        report["manifest"]["written_to"] = cfg["write_manifest"]
    if cfg.get("verify_manifest"):
        diffs = diff_manifests(load_manifest_file(cfg["verify_manifest"]), manifest)
        report["manifest"]["verified_against"] = cfg["verify_manifest"]
        report["manifest"]["verified_ok"] = not diffs
        report["manifest"]["differences"] = diffs


def _series_checks(report: dict, checks: tuple, instruments: list, series: dict, tz: str, hooks: dict) -> None:
    fx = sorted(n for n in series if not is_crypto(n))
    crypto = sorted(n for n in series if is_crypto(n))
    if "gaps" in checks:
        ref = sorted(series[crypto[0]]) if crypto else []
        report["gaps"] = {"fx": timed(hooks, "gaps_fx", lambda: fx_gap_report({n: series[n] for n in fx}, ref, tz)) if fx else {},
                          "crypto": timed(hooks, "gaps_crypto", lambda: crypto_gap_report({n: series[n] for n in crypto}, tz))}
    if "cycles" in checks and fx:
        report["cycles"] = timed(hooks, "cycles", lambda: cycles_report(fx, series, tz))
    if "basis" in checks and fx and crypto:
        report["basis"] = timed(hooks, "basis", lambda: basis_report(crypto, fx, series, tz))
    if "lag" in checks and crypto:
        report["lag"] = timed(hooks, "lag", lambda: lag_report(crypto, fx, series))
    if "ppy" in checks:
        report["ppy"] = [ppy_check(n, sorted(series[n]), p) for n, _g, _c, p in instruments if n in series]


# ------------------------------------------------------------------ text
def render_text(report: dict) -> str:
    lines = [f"Audit chat {report['chat']} | {report['sandbox_db']} | display tz {report['display_tz']}"]
    for key, row in report.get("format", {}).items():
        flags = {k: v for k, v in row.items() if k not in ("rows", "zero_spread_rows") and v not in (0, 1)}
        lines.append(f"  format {key}: rows={row['rows']} {'OK' if not flags else 'FLAGS ' + str(flags)}")
    gaps = report.get("gaps", {})
    if gaps.get("fx"):
        lines.append(f"  FX gaps: scheduled/pair {gaps['fx']['scheduled_weekend_gaps_per_pair']} | classes {gaps['fx']['class_counts']}")
        for e in gaps["fx"]["unscheduled_events"]:
            lines.append(f"    {e['class']:<20} {e['start']['local']} {e['gap_hours']}h pairs={e['pairs_sharing']}/{e['pairs_total']} crypto_inside={e['reference_crypto_bars_inside']}/{e['reference_crypto_bars_expected']}")
    for venue, v in gaps.get("crypto", {}).get("venues", {}).items():
        lines.append(f"  venue {venue}: union={v['missing_union']} in_all={v['missing_in_all']} hist={v['histogram_by_n_instruments_missing']}")
    for section in ("cycles", "basis"):
        for c in report.get(section, {}).get("cycles", []):
            if c["n"]:
                extra = f" tiers={c['tiers']} exec_viol_share={c['exec_violation_share']:.4f}" if c["rated"] else " (not tiered: no crypto spread)"
                lines.append(f"  {section} {c['cycle']}: n={c['n']} abs_bps p50/p99/max={c['abs_bps']['50']:.2f}/{c['abs_bps']['99']:.2f}/{c['abs_bps']['100']:.2f}{extra}")
    for base, e in report.get("lag", {}).items():
        lines.append(f"  lag {base}: corr0={e['binance_vs_kraken_return_corr_by_lag'].get('0')} loading={e.get('basis_change_loading_on_fx_return_by_lag')}")
    for p in report.get("ppy", []):
        lines.append(f"  ppy {p['instrument']}: observed {p['observed_bars_per_year']:.0f} vs configured {p['configured']:.0f} (x{p['ratio']:.4f}){' WARN' if p['warn'] else ''}")
    if "manifest" in report:
        lines.append(f"  manifest: {report['manifest']}")
    lines.append("LIMITATIONS: " + " | ".join(report["limitations"]))
    return "\n".join(lines)


# ------------------------------------------------------------------- cli
def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Read-only sandbox alignment / cross-rate / timezone audit.")
    p.add_argument("--chat", type=int, required=True, help="chat number, used in the report name")
    p.add_argument("--sandbox-db", default=None, help="sqlite path; default = sandbox_config.py's sandbox")
    p.add_argument("--checks", default="all", help=f"comma list from {','.join(CHECKS)} or 'all'")
    p.add_argument("--display-tz", default="Europe/London", help="IANA zone for DISPLAY only; storage stays UTC")
    p.add_argument("--tag", default="", help="optional report-name suffix (letters, digits, hyphen)")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--write-manifest", default=None, help="write a data manifest here (never overwrites)")
    p.add_argument("--verify-manifest", default=None, help="verify the DB against this manifest; exit 2 on mismatch")
    return p.parse_args(argv)


def parse_checks(text: str) -> tuple:
    assert isinstance(text, str) and text, "--checks must be non-empty"
    chosen = CHECKS if text == "all" else tuple(c.strip() for c in text.split(","))
    unknown = [c for c in chosen if c not in CHECKS]
    assert not unknown, f"--checks: unknown {unknown}; choose from {list(CHECKS)}"
    return chosen


def db_coverage(path: str) -> tuple:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        first, last = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM candles").fetchone()
    finally:
        con.close()
    assert first and last, f"no candles in {path}"
    return first, last


def report_file(out_dir: str, first: str, last: str, chat: int, run_date: str, tag: str) -> str:
    assert chat > 0 and re.fullmatch(r"\d{6}", run_date), "bad chat/run_date"
    f, l = parse_ts(first), parse_ts(last)
    suffix = f"_{tag}" if tag else ""
    return str(Path(out_dir) / f"audit_{f:%y%m%d}to{l:%y%m%d}_chat{chat}_{run_date}{suffix}.json")


def build_config(args: argparse.Namespace, run_date: str) -> dict:
    assert re.fullmatch(r"\d{6}", run_date), f"run_date must be YYMMDD, got {run_date!r}"
    assert args.chat > 0, "--chat must be positive"
    assert re.fullmatch(r"[A-Za-z0-9-]*", args.tag), "--tag may only contain letters, digits, hyphen"
    checks = parse_checks(args.checks)
    try:
        local_view(0, args.display_tz)
    except ValueError as exc:
        raise AssertionError(f"--display-tz: {exc}") from exc
    sandbox_path = args.sandbox_db or sandbox_db_path(GLOBAL_START, discover_sandbox_end())
    assert Path(sandbox_path).exists(), f"sandbox DB not found: {sandbox_path}"
    first, last = db_coverage(sandbox_path)
    path = report_file(args.out_dir, first, last, args.chat, run_date, args.tag)
    assert not Path(path).exists(), f"report already exists: {path} - pick a different --tag"
    assert not (args.write_manifest and Path(args.write_manifest).exists()), f"manifest already exists: {args.write_manifest}"
    assert not (args.verify_manifest and not Path(args.verify_manifest).exists()), f"manifest to verify not found: {args.verify_manifest}"
    return {"checks": checks, "display_tz": args.display_tz, "sandbox_path": sandbox_path, "report_path": path,
            "chat": args.chat, "write_manifest": args.write_manifest, "verify_manifest": args.verify_manifest}


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    run_date = datetime.now(LONDON or timezone.utc).strftime("%y%m%d")
    cfg = build_config(args, run_date)
    with monitored_run("audit_sandbox_alignment", chat=args.chat, checks=",".join(cfg["checks"]),
                       sandbox_db=cfg["sandbox_path"], report=cfg["report_path"], tag=args.tag):
        note("sandbox_db", cfg["sandbox_path"])
        note("display_tz", cfg["display_tz"])
        report = run_audit(cfg, INSTRUMENTS, {"item": item, "count": count})
        Path(cfg["report_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["report_path"]).write_text(json.dumps(report, indent=1))
        print(render_text(report))
        print(f"\nReport written: {cfg['report_path']}")
        if report.get("manifest", {}).get("verified_ok") is False:
            print("MANIFEST MISMATCH:\n  " + "\n  ".join(report["manifest"]["differences"]), file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
