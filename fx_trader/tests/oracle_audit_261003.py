# version: 261009
"""
tests/oracle_audit_261003.py

FROZEN, VERBATIM copy of the pre-refactor cycle logic of
audit_sandbox_alignment.py (as at commit 79492de, chat 14), kept ONLY as the
independent oracle for differential tests (tests/test_currency_graph.py,
tests/test_audit_engine_equivalence.py). Do not edit, and do not import it
from production code. After the 14p2 refactor the audit itself delegates cycle
discovery and per-bar numbers to currency_graph; this file is what lets the
tests still prove the engine against independent code. Functions and constants
are copied unchanged; only this header and the imports were added.
"""

import itertools
import math
from collections import defaultdict

HOUR = 3600
MAX_CYCLES = 5_000
USD_PROXY = {"USDT": "USD"}                               # alias edge: USDT treated as USD (basis measured, not assumed)
RATIO_CLEAN = 0.25          # |residual| / summed leg spread: provisional tier edges (chat 14)
RATIO_SUSPECT = 1.0
ABS_SUSPECT_BPS = 10.0


def split_pair(instrument: str) -> tuple[str, str]:
    assert instrument, "instrument name required"
    parts = instrument.split("/" if "/" in instrument else "_")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"cannot split instrument {instrument!r} into base/quote")
    return parts[0], parts[1]


def edge_spec(instrument: str, from_ccy: str) -> tuple:
    """(instrument, +1 if traversed base->quote else -1) when leaving from_ccy."""
    base, quote = split_pair(instrument)
    assert from_ccy in (base, quote), f"{from_ccy} is not in {instrument}"
    return (instrument, 1 if from_ccy == base else -1)


def enumerate_triangles(fx_names: list) -> list:
    """Every 3-currency cycle whose three pairs all exist - discovered from the
    instrument list, not hard-coded (USD is not special)."""
    assert fx_names, "no FX instruments"
    pair_of = {frozenset(split_pair(n)): n for n in fx_names}
    assert len(pair_of) == len(fx_names), "duplicate currency pair in instrument list"
    ccys = sorted({c for n in fx_names for c in split_pair(n)})
    cycles = []
    for a, b, c in itertools.combinations(ccys, 3):
        edges = [(a, b), (b, c), (c, a)]
        if all(frozenset(e) in pair_of for e in edges):
            cycles.append(tuple(edge_spec(pair_of[frozenset(e)], e[0]) for e in edges))
        assert len(cycles) <= MAX_CYCLES, "cycle ceiling exceeded"
    return cycles


def basis_specs_for_base(base: str, quotes: dict, pair_of: dict) -> dict:
    assert quotes, "no quotes for base"
    specs = {}
    for (q_proxy, usd), q_other in itertools.product(USD_PROXY.items(), sorted(quotes)):
        fx = pair_of.get(frozenset((usd, q_other)))
        if q_proxy in quotes and q_other != q_proxy and fx:
            specs[f"{base} via {q_proxy}/{q_other}"] = (
                (quotes[q_proxy], -1), (quotes[q_other], 1), edge_spec(fx, q_other))
    return specs


def basis_specs(crypto_names: list, fx_names: list) -> dict:
    pair_of = {frozenset(split_pair(n)): n for n in fx_names}
    by_base = defaultdict(dict)
    for n in crypto_names:
        base, quote = split_pair(n)
        by_base[base][quote] = n
    specs = {}
    for base, quotes in sorted(by_base.items()):
        specs.update(basis_specs_for_base(base, quotes, pair_of))
    return specs


def cycle_label(spec: tuple) -> str:
    assert spec, "empty cycle"
    return " ".join(f"{'+' if d > 0 else '-'}{n}" for n, d in spec)


def evaluate_cycle(spec: tuple, series_by_name: dict) -> dict:
    """Per common bar, in bps: mid residual (sum of signed log mids), executable
    gain going each way round the loop (sell base at bid / buy base at ask),
    and the summed leg spread."""
    assert spec and all(len(e) == 2 for e in spec), "bad cycle spec"
    common = sorted(set.intersection(*(set(series_by_name[n]) for n, _ in spec)))
    ev = {"stamps": common, "resid": [], "gain_fwd": [], "gain_rev": [], "spread": []}
    for t in common:
        r = f = g = s = 0.0
        for name, d in spec:
            bid, ask = series_by_name[name][t]
            r += d * math.log((bid + ask) / 2)
            f += math.log(bid) if d > 0 else -math.log(ask)
            g += math.log(bid) if d < 0 else -math.log(ask)
            s += math.log(ask) - math.log(bid)
        for key, val in (("resid", r), ("gain_fwd", f), ("gain_rev", g), ("spread", s)):
            ev[key].append(val * 1e4)
    return ev


def bar_tier(abs_bps: float, spread_bps: float) -> str:
    assert abs_bps >= 0, "abs_bps must be non-negative"
    if abs_bps > ABS_SUSPECT_BPS:
        return "suspect"
    if spread_bps <= 0:
        return "unrated"
    ratio = abs_bps / spread_bps
    if ratio <= RATIO_CLEAN:
        return "clean"
    return "noisy" if ratio <= RATIO_SUSPECT else "suspect"
