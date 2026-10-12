# version: 261011
"""
currency_graph/strengths.py

Currency strengths by least squares over the currency graph (chat 14p3).
Standard library only; imports nothing from the repo (decoupled, like the
rest of currency_graph). Design record: CONTEXT_HANDOFF.md Section 4g.

MODEL. One observation per pair on one bar: y = ln(mid) in bps = c_a - c_b
(+ noise), s = summed bid/ask spread in bps. Strengths c solve weighted least
squares; the free constant is fixed by ZERO-SUM (sum c = 0). With
L = A'WA and M = L + J/N (J = all-ones), c = M^-1 A'Wy, exactly zero-sum on a
connected graph. M^-1 is formed once per fit by Gauss-Jordan (N is tiny); one
refinement pass solves for the correction on the residuals, so accuracy does
not depend on the size of y (~5e4 bps for JPY).

WEIGHT SCHEMES (weights_for; normalised to mean 1 - scale cannot change c or
leave-one-out residuals): unweighted, inv_spread (w = 1/s), inv_spread2
(w = 1/s^2, inverse-variance if noise sd is proportional to spread).

LEAVE-ONE-PAIR-OUT (LOO residual of pair i: y_i minus the value predicted for
it by a fit that excludes pair i):
- loo_fast: e_i / (1 - h_i), h_i = w_i a_i' M^-1 a_i (WLS hat diagonal;
  one fit gives all pairs).
- loo_oracle: exact refit without pair i (EXACT but N_edges fits per bar).
- A pair whose removal disconnects the graph (a BRIDGE, e.g. USD_CAD: CAD has
  one pair) has h = 1: its LOO residual is undefined, both paths return None.
LEAVE-ONE-CURRENCY-OUT (loco): refit without currency k and its pairs. Shift
of each remaining currency = (full-fit strength re-centred over the remaining
currencies) minus (refit strength): gauge-free. None if the rest disconnects.

VIEWS (strength_views) of ONE stored fit: zero_sum; vs_<ref>; basket_equal
(equal-weight basket INCLUDING the currency itself = zero_sum by algebra);
basket_user (caller's weights); basket_inv_spread (weights proportional to the
inverse of each currency's mean incident spread). "Spread-weighted" is built
both ways: the inv_spread / inv_spread2 FITS (reading a) and the
basket_inv_spread VIEW (reading b). Differences c_i - c_j are identical in
every view; only the level (numeraire) differs.

MISSING PAIRS: select_component picks the largest connected component of the
bar's pairs; currencies outside it get no strength. The caller decides policy
(run_strengths.py computes policy B = full bars only and policy D = all bars,
degraded flagged).
"""

import math
from collections import namedtuple
from dataclasses import dataclass

BPS = 1e4
BRIDGE_TOL = 1e-9
PIVOT_MIN = 1e-12
MAX_NODES = 64
MAX_OBS = 4_096
SCHEMES = ("unweighted", "inv_spread", "inv_spread2")

# a, b: currency names (c_a - c_b = y); y: ln(mid) in bps; s: spread in bps; name: label (optional)
Obs = namedtuple("Obs", "a b y s name", defaults=("",))


@dataclass
class StrengthFit:
    nodes: list
    c: dict          # currency -> strength (bps, zero-sum)
    resid: list      # per obs: y - (c_a - c_b)
    hat: list        # per obs: hat-matrix diagonal
    w: list


def from_edges(edges: list) -> list:
    """Duck-typed adapter for currency_graph.model.Edge (needs base, quote,
    mid, spread_bps, instrument). Only QUOTED edges with spread_known make sense."""
    assert edges, "no edges"
    return [Obs(e.base, e.quote, math.log(e.mid) * BPS, e.spread_bps, e.instrument) for e in edges]


def weights_for(scheme: str, obs: list) -> list:
    assert scheme in SCHEMES, f"scheme must be one of {SCHEMES}"
    assert obs, "no observations"
    if scheme == "unweighted":
        return [1.0] * len(obs)
    for o in obs:
        if not o.s > 0:
            raise ValueError(f"{scheme} needs a positive spread; {o.name or (o.a, o.b)} has {o.s}")
    raw = [1.0 / o.s if scheme == "inv_spread" else 1.0 / (o.s * o.s) for o in obs]
    mean = sum(raw) / len(raw)
    return [x / mean for x in raw]


# ---------------------------------------------------------- connectivity
def components(nodes: list, obs: list) -> list:
    """Connected components (sorted node lists), largest first, ties by first
    node name. Isolated nodes are components of size 1."""
    assert nodes, "no nodes"
    assert len(nodes) <= MAX_NODES and len(obs) <= MAX_OBS, "size ceiling exceeded"
    parent = {n: n for n in nodes}

    def find(x):
        for _ in range(len(parent) + 1):
            if parent[x] == x:
                return x
            parent[x] = parent[parent[x]]
            x = parent[x]
        raise AssertionError("union-find did not converge")

    for o in obs:
        ra, rb = find(o.a), find(o.b)
        if ra != rb:
            parent[ra] = rb
    groups: dict = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    comps = [sorted(g) for g in groups.values()]
    comps.sort(key=lambda g: (-len(g), g[0]))
    return comps


def is_connected(nodes: list, obs: list) -> bool:
    return len(components(nodes, obs)) == 1


def select_component(nodes: list, obs: list) -> tuple:
    """(main_nodes, main_obs): the largest connected component and its pairs."""
    assert obs, "no observations"
    main = components(nodes, obs)[0]
    members = set(main)
    sub = [o for o in obs if o.a in members]
    assert sub and len(main) >= 2, "largest component has no pair"
    return main, sub


# ----------------------------------------------------------------- linear algebra
def _eliminate(a: list, col: int) -> None:
    n = len(a)
    for r in range(n):
        f = a[r][col]
        if r != col and f != 0.0:
            a[r] = [x - f * y for x, y in zip(a[r], a[col])]


def invert(m: list) -> list:
    """Gauss-Jordan with partial pivoting. ValueError if singular."""
    n = len(m)
    assert n > 0 and all(len(r) == n for r in m), "square matrix required"
    a = [list(r) + [1.0 if i == j else 0.0 for j in range(n)] for i, r in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < PIVOT_MIN:
            raise ValueError("singular matrix (disconnected graph?)")
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        _eliminate(a, col)
    return [row[n:] for row in a]


def _rhs(idx: dict, obs: list, w: list, values: list, n: int) -> list:
    b = [0.0] * n
    for o, wi, v in zip(obs, w, values):
        b[idx[o.a]] += wi * v
        b[idx[o.b]] -= wi * v
    return b


def _matvec(minv: list, b: list) -> list:
    return [sum(row[k] * b[k] for k in range(len(b))) for row in minv]


def fit_zero_sum(nodes: list, obs: list, w: list) -> StrengthFit:
    """Weighted LS strengths, zero-sum gauge. ValueError if disconnected."""
    n = len(nodes)
    assert n >= 2 and len(obs) == len(w) >= 1, "need >= 2 nodes and matching obs/weights"
    assert all(o.a != o.b and wi > 0 for o, wi in zip(obs, w)), "self-loop or non-positive weight"
    if not is_connected(nodes, obs):
        raise ValueError("graph is not connected")
    idx = {x: i for i, x in enumerate(nodes)}
    m = [[1.0 / n] * n for _ in range(n)]
    for o, wi in zip(obs, w):
        i, j = idx[o.a], idx[o.b]
        m[i][i] += wi
        m[j][j] += wi
        m[i][j] -= wi
        m[j][i] -= wi
    minv = invert(m)
    c = _matvec(minv, _rhs(idx, obs, w, [o.y for o in obs], n))
    r0 = [o.y - (c[idx[o.a]] - c[idx[o.b]]) for o in obs]      # refinement: solve for the correction on small residuals
    dc = _matvec(minv, _rhs(idx, obs, w, r0, n))
    c = [ci + di for ci, di in zip(c, dc)]
    resid = [r - (dc[idx[o.a]] - dc[idx[o.b]]) for r, o in zip(r0, obs)]
    hat = [wi * (minv[idx[o.a]][idx[o.a]] - 2 * minv[idx[o.a]][idx[o.b]] + minv[idx[o.b]][idx[o.b]]) for o, wi in zip(obs, w)]
    return StrengthFit(nodes=list(nodes), c=dict(zip(nodes, c)), resid=resid, hat=hat, w=list(w))


# -------------------------------------------------------- leave-one-out
def loo_fast(fit: StrengthFit) -> list:
    """LOO residual per obs by the hat identity; None for a bridge (1 - h ~ 0)."""
    assert len(fit.resid) == len(fit.hat), "fit is inconsistent"
    out = []
    for r, h in zip(fit.resid, fit.hat):
        d = 1.0 - h
        out.append(None if d < BRIDGE_TOL else r / d)
    return out


def loo_oracle(nodes: list, obs: list, w: list) -> list:
    """LOO residual per obs by exact refit without that obs (weights as given);
    None when its removal disconnects the graph."""
    assert len(obs) == len(w) and obs, "obs/weights mismatch"
    out = []
    for i, o in enumerate(obs):
        keep = [k for k in range(len(obs)) if k != i]
        sub = [obs[k] for k in keep]
        if not sub or not is_connected(nodes, sub):
            out.append(None)
            continue
        f = fit_zero_sum(nodes, sub, [w[k] for k in keep])
        out.append(o.y - (f.c[o.a] - f.c[o.b]))
    return out


def loco(nodes: list, obs: list, w: list, c_full: dict) -> dict:
    """{dropped currency: {remaining currency: shift bps} or None}."""
    assert len(obs) == len(w) and set(nodes) == set(c_full), "inputs inconsistent"
    out: dict = {}
    for k in nodes:
        rem = [x for x in nodes if x != k]
        keep = [i for i, o in enumerate(obs) if k not in (o.a, o.b)]
        sub = [obs[i] for i in keep]
        if len(rem) < 2 or not sub or not is_connected(rem, sub):
            out[k] = None
            continue
        f = fit_zero_sum(rem, sub, [w[i] for i in keep])
        mean = sum(c_full[x] for x in rem) / len(rem)
        out[k] = {x: (c_full[x] - mean) - f.c[x] for x in rem}
    return out


# ------------------------------------------------------------------ views
def _basket(c: dict, weights: dict) -> dict | None:
    total = sum(weights.get(k, 0.0) for k in c)
    if not total > 0:
        return None
    basket = sum(weights.get(k, 0.0) * c[k] for k in c) / total
    return {k: c[k] - basket for k in c}


def mean_incident_spread(nodes: list, obs: list) -> dict:
    assert nodes and obs, "nodes and obs required"
    acc: dict = {n: [] for n in nodes}
    for o in obs:
        acc[o.a].append(o.s)
        acc[o.b].append(o.s)
    return {n: sum(v) / len(v) for n, v in acc.items() if v}


def strength_views(c: dict, obs: list, ref: str = "USD", user_weights: dict | None = None) -> dict:
    """Views of one fit (c = zero-sum strengths of a connected component).
    A view that cannot be formed is None."""
    assert c and obs, "fit and obs required"
    views: dict = {"zero_sum": dict(c), "basket_equal": _basket(c, {k: 1.0 for k in c})}
    views[f"vs_{ref}"] = {k: c[k] - c[ref] for k in c} if ref in c else None
    spreads = mean_incident_spread(list(c), obs)
    ok = len(spreads) == len(c) and all(s > 0 for s in spreads.values())
    views["basket_inv_spread"] = _basket(c, {k: 1.0 / s for k, s in spreads.items()}) if ok else None
    views["basket_user"] = _basket(c, user_weights) if user_weights else None
    return views
