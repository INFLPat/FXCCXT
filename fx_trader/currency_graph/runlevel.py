# version: 261023
"""
currency_graph/runlevel.py

Run-level statistics for PERSISTENT offsets (chat 14p4-iv). Universe-agnostic
core: it knows nothing about FX, arms or hubs. It takes 2-D arrays (rows =
independent series, columns = consecutive bars; NaN = bar not available) and
returns rolling statistics, run flags and block run counts.

NUMPY MODULE. The first numpy module in currency_graph; it is NOT imported by
currency_graph/__init__.py, so the rest of the package stays stdlib-only. The
stdlib oracle is ref_stat() (checked against the numpy path in
tests/test_runlevel.py to 1e-12).

STATISTICS (all computed; none chosen):
  median, mean, trim20 (20% trimmed mean, k//5 cut from each end),
  signpers (mean of sign(x) over the window, in [-1, 1]; NO magnitude gate -
  a gated version would couple the statistic to a threshold),
  ewma (half-life HL bars), cusum (two-sided, signed: +S or -S, whichever is
  larger; reference k in the input's units).
WINDOWS: trailing = bars t-W+1..t; centred = bars t-W//2..t+W//2 (HINDSIGHT:
  never to be fed to a consumer). FILL = minimum valid bars in the window:
  full (nominal size), half, third, min3 (expanding; at least 3 bars).
  A statistic is NaN wherever the bar ITSELF is missing (iter_stats gates it),
  so a flag can never sit on a bar that does not exist (fixed 261023: the
  half / third / min3 fills used to produce such flags just past a gap).
INPUTS: raw (bps), norm (x / path spread), none (signpers is sign-only).
SCALES (raw input): path (current path summed spread), roll (trailing median
  of path spread), bar (bar mean pair spread), hour (same-hour trailing
  median), abs (1, i.e. bps), rz (robust z: 1.4826 x trailing MAD of the base
  series). Crypto bars have no spread: spread-based scales are then undefined
  and only abs / rz / norm-free statistics apply.
FLAG: |statistic| / scale >= threshold (NaN anywhere = not flagged).
HYSTERESIS (latch): enter at ratio >= thr, stay ON while ratio >= frac x thr
  (HYST_EXIT = 0.75 and 0.5; 1.0 = the plain flag). First-flag time is
  identical to the plain flag; run length and run counts differ.
Run counting: a flagged bar starts a run unless a flagged bar lies within the
  previous `gap` bars (same rule as run_world_experiment.count_runs).
GAP POLICIES (pack_layout): rows of a population are laid on a time grid.
  carry = 0: reset (NaN separator) at any missing bar; carry = g: gaps of up to
  g missing bars are bridged by NaN placeholder bars (the window keeps its
  real-time length; the fill rules decide); carry = None: bar-index packing,
  gaps ignored (DIAGNOSTIC ONLY - breaks CONTEXT_HANDOFF 4j.5).
EWMA/CUSUM start at column 0 of the array given (segment start in real
  chronology; trial start in injection, so their state has up to 48 bars of
  warm-up, fewer when the stretch gives less context).
Streaming note: the trailing statistics are causal and can be rewritten
incrementally for live use (L1); centred ones are batch-only.
"""

import math
import statistics
from collections import namedtuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

W_GRID = (3, 6, 12, 24, 48)
FILLS = ("full", "half", "third", "min3")
CAUSALITY = ("trailing", "centred")
WINDOWED = ("median", "mean", "trim20")
SIGN_STAT = "signpers"
SCALES_RAW = ("path", "roll", "bar", "hour", "abs", "rz")
RATIO_SCALES = ("path", "roll", "bar", "hour", "unit")
RATIO_GRID = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
ABS_GRID = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
RZ_GRID = (2.0, 3.0, 4.0, 5.0, 6.0)
SHARE_GRID = (0.5, 0.67, 0.8, 0.9, 1.0)
CUSUM_K_RAW = (0.5, 1.0, 2.0)
CUSUM_K_NORM = (0.1, 0.25, 0.5)
EWMA_HL = W_GRID
TRIM_DEN = 5
EWMA_MIN = 3
SEP = 50                      # NaN separator between segments (>= max window back/fwd)
BLOCK_LENGTHS = (1, 3, 6, 12, 24, 48)
BLOCK_LABELS = tuple(str(x) for x in BLOCK_LENGTHS) + ("mixed",)
HEADLINE_W = (6, 12, 24)
HYST_EXIT = (0.75, 0.5)

Spec = namedtuple("Spec", "sid stat param fill caus inp")
Group = namedtuple("Group", "spec scale thr col0 headline")


# ------------------------------------------------------------ windows
def window_bounds(W: int, causality: str) -> tuple:
    assert W >= 2, "W must be >= 2"
    if causality == "trailing":
        return W - 1, 0
    if causality == "centred":
        return W // 2, W // 2
    raise ValueError(f"causality must be one of {CAUSALITY}")


def min_count(nominal: int, fill: str) -> int:
    assert nominal >= 1, "nominal window size must be >= 1"
    if fill == "full":
        return nominal
    if fill == "half":
        return -(-nominal // 2)
    if fill == "third":
        return -(-nominal // 3)
    if fill == "min3":
        return min(3, nominal)
    raise ValueError(f"fill must be one of {FILLS}")


def _win_view(X, back: int, fwd: int, t0: int, t1: int, pad: bool):
    """Windows of X (n, T) as (n, t1 - t0, back + fwd + 1). pad=True pads NaN at
    both ends (output covers original columns t0..t1-1 of a length-T array);
    pad=False slices X[t0-back : t1+fwd] (no NaN padding, caller guarantees room)."""
    assert X.ndim == 2 and 0 <= t0 < t1, "bad window request"
    win = back + fwd + 1
    if pad:
        assert t1 <= X.shape[1], "t1 beyond series"
        Xp = np.pad(X, ((0, 0), (back, fwd)), constant_values=np.nan)
        return sliding_window_view(Xp, win, axis=1)[:, t0:t1]
    lo, hi = t0 - back, t1 + fwd
    assert lo >= 0 and hi <= X.shape[1], "not enough context for an unpadded window"
    return sliding_window_view(X[:, lo:hi], win, axis=1)


def windows(X, back: int, fwd: int, pad: bool):
    X = np.asarray(X, float)
    T = X.shape[1]
    if pad:
        return _win_view(X, back, fwd, 0, T, True)
    return _win_view(X, back, fwd, back, T - fwd, False)


def order_stats(V) -> dict:
    """V (n, T, win) with NaN = missing -> cnt, median, mean, trim20 (NaN where cnt = 0)."""
    assert V.ndim == 3, "V must be 3-D"
    win = V.shape[-1]
    k = (~np.isnan(V)).sum(axis=-1)
    S = np.sort(V, axis=-1)                       # NaN sorts last
    S0 = np.where(np.isnan(S), 0.0, S)
    lo = np.clip((k - 1) // 2, 0, win - 1)
    hi = np.clip(k // 2, 0, win - 1)
    a = np.take_along_axis(S0, lo[..., None], axis=-1)[..., 0]
    b = np.take_along_axis(S0, hi[..., None], axis=-1)[..., 0]
    t = k // TRIM_DEN
    pos = np.arange(win)
    keep = (pos >= t[..., None]) & (pos < (k - t)[..., None])
    ok = k > 0
    kk = np.where(ok, k, 1)
    tc = np.where(ok, k - 2 * t, 1)
    return {
        "cnt": k,
        "median": np.where(ok, 0.5 * (a + b), np.nan),
        "mean": np.where(ok, S0.sum(-1) / kk, np.nan),
        "trim20": np.where(ok, np.where(keep, S0, 0.0).sum(-1) / tc, np.nan),
    }


def sign_stat(V) -> dict:
    assert V.ndim == 3, "V must be 3-D"
    nan = np.isnan(V)
    k = (~nan).sum(axis=-1)
    s = np.sign(np.where(nan, 0.0, V)).sum(-1)
    return {"cnt": k, "sign": np.where(k > 0, s / np.where(k > 0, k, 1), np.nan)}


def apply_fill(stat, cnt, nominal: int, fill: str):
    return np.where(cnt >= min_count(nominal, fill), stat, np.nan)


def ref_stat(values, kind: str) -> float:
    """Stdlib oracle for one window of non-NaN values."""
    v = [float(x) for x in values if not math.isnan(x)]   # float(): numpy scalars break bool arithmetic below
    if not v:
        return math.nan
    if kind == "median":
        return statistics.median(v)
    if kind == "mean":
        return sum(v) / len(v)
    if kind == "trim20":
        s = sorted(v)
        t = len(s) // TRIM_DEN
        core = s[t:len(s) - t]
        return sum(core) / len(core)
    if kind == "signpers":
        return sum((1 if x > 0 else -1 if x < 0 else 0) for x in v) / len(v)
    raise ValueError(f"unknown statistic {kind}")


def rolling_median(X, W: int, min_n: int):
    X = np.asarray(X, float)
    o = order_stats(_win_view(X, W - 1, 0, 0, X.shape[1], True))
    return np.where(o["cnt"] >= min_n, o["median"], np.nan)


def rolling_mad(X, W: int, min_n: int):
    """Trailing median absolute deviation (NOT scaled), row by row to bound memory."""
    X = np.asarray(X, float)
    out = np.full(X.shape, np.nan)
    for r in range(X.shape[0]):
        V = _win_view(X[r:r + 1], W - 1, 0, 0, X.shape[1], True)
        o = order_stats(V)
        o2 = order_stats(np.abs(V - o["median"][..., None]))
        out[r] = np.where(o["cnt"][0] >= min_n, o2["median"][0], np.nan)
    return out


# ------------------------------------------------- sequential statistics
def ewma(X, hl: float, min_n: int = EWMA_MIN):
    assert hl > 0, "half-life must be positive"
    X = np.asarray(X, float)
    a = 1.0 - 0.5 ** (1.0 / hl)
    n, T = X.shape
    out = np.full((n, T), np.nan)
    s = np.full(n, np.nan)
    c = np.zeros(n, int)
    for t in range(T):
        x = X[:, t]
        v = ~np.isnan(x)
        fresh = v & np.isnan(s)
        cont = v & ~np.isnan(s)
        s = np.where(fresh, x, np.where(cont, a * x + (1.0 - a) * s, np.nan))
        c = np.where(fresh, 1, np.where(cont, c + 1, 0))
        out[:, t] = np.where(c >= min_n, s, np.nan)
    return out


def cusum(X, k: float):
    assert k >= 0, "reference k must be >= 0"
    X = np.asarray(X, float)
    n, T = X.shape
    out = np.full((n, T), np.nan)
    sp = np.zeros(n)
    sn = np.zeros(n)
    for t in range(T):
        x = X[:, t]
        v = ~np.isnan(x)
        xv = np.where(v, x, 0.0)
        sp = np.where(v, np.maximum(0.0, sp + xv - k), 0.0)
        sn = np.where(v, np.maximum(0.0, sn - xv - k), 0.0)
        out[:, t] = np.where(v, np.where(sp >= sn, sp, -sn), np.nan)
    return out


# ---------------------------------------------------------------- layout
def _fmt(x) -> str:
    return f"{x:g}"


def make_spec(stat: str, param, fill: str, caus: str, inp: str) -> Spec:
    tag = {"ewma": "HL", "cusum": "K"}.get(stat, "W")
    return Spec(f"{stat}|{tag}{_fmt(param)}|{fill}|{caus}|{inp}", stat, param, fill, caus, inp)


def build_specs(fills) -> list:
    assert fills, "fills must not be empty"
    out = []
    for inp in ("raw", "norm"):
        for stat in WINDOWED:
            for W in W_GRID:
                for caus in CAUSALITY:
                    for fill in fills:
                        out.append(make_spec(stat, W, fill, caus, inp))
        for hl in EWMA_HL:
            out.append(make_spec("ewma", hl, "-", "trailing", inp))
        for k in (CUSUM_K_RAW if inp == "raw" else CUSUM_K_NORM):
            out.append(make_spec("cusum", k, "-", "trailing", inp))
    for W in W_GRID:
        for caus in CAUSALITY:
            for fill in fills:
                out.append(make_spec(SIGN_STAT, W, fill, caus, "none"))
    return out


def scales_for(inp: str) -> tuple:
    return {"raw": SCALES_RAW, "norm": ("unit",), "none": ("share",)}[inp]


def grid_for(scale: str) -> tuple:
    if scale in RATIO_SCALES:
        return RATIO_GRID
    if scale == "abs":
        return ABS_GRID
    if scale == "rz":
        return RZ_GRID
    if scale == "share":
        return SHARE_GRID
    raise ValueError(f"unknown scale {scale}")


def is_headline(sp: Spec, scale: str) -> bool:
    """Compact subset kept per start-hour class and with block CIs."""
    return (sp.stat in ("median", "mean") and sp.param in HEADLINE_W and sp.caus == "trailing" and sp.fill == "full"
            and ((sp.inp == "raw" and scale in ("path", "abs", "rz")) or sp.inp == "norm"))


def build_layout(fills, headline_only: bool = False) -> tuple:
    """(specs, groups, n_columns). A group = (spec, scale) with its threshold grid;
    columns are the concatenated thresholds, in order."""
    specs, groups, c = [], [], 0
    for sp in build_specs(fills):
        gs = []
        for sc in scales_for(sp.inp):
            h = is_headline(sp, sc)
            if headline_only and not h:
                continue
            gs.append((sc, grid_for(sc), h))
        if not gs:
            continue
        si = len(specs)
        specs.append(sp)
        for sc, th, h in gs:
            groups.append(Group(si, sc, tuple(th), c, h))
            c += len(th)
    return specs, groups, c


def column_ids(specs, groups) -> list:
    return [f"{specs[g.spec].sid}|{g.scale}|{t:g}" for g in groups for t in g.thr]


# ------------------------------------------------------------ statistics
def _gate(S, miss):
    """NaN wherever the bar itself is missing."""
    return np.where(miss, np.nan, S)


def iter_stats(Xs: dict, specs, t0: int, t1: int, pad: bool):
    """Yields (spec_index, array (n, t1 - t0)). Xs = {'raw': (n, T), 'norm': (n, T)}.
    Windowed statistics sharing a window are sorted once. Every statistic is NaN
    where its own bar is missing."""
    assert specs, "no specs"
    win_groups, seq_groups = {}, {}
    for i, sp in enumerate(specs):
        if sp.stat in WINDOWED or sp.stat == SIGN_STAT:
            win_groups.setdefault((sp.inp, sp.caus, sp.param), []).append(i)
        else:
            seq_groups.setdefault((sp.stat, sp.inp, sp.param), []).append(i)
    for (inp, caus, W), idxs in win_groups.items():
        X = np.asarray(Xs["raw" if inp == "none" else inp], float)
        back, fwd = window_bounds(W, caus)
        nominal = back + fwd + 1
        V = _win_view(X, back, fwd, t0, t1, pad)
        miss = np.isnan(X[:, t0:t1])
        need_order = any(specs[i].stat in WINDOWED for i in idxs)
        need_sign = any(specs[i].stat == SIGN_STAT for i in idxs)
        o = order_stats(V) if need_order else None
        sg = sign_stat(V) if need_sign else None
        for i in idxs:
            sp = specs[i]
            if sp.stat == SIGN_STAT:
                yield i, _gate(apply_fill(sg["sign"], sg["cnt"], nominal, sp.fill), miss)
            else:
                yield i, _gate(apply_fill(o[sp.stat], o["cnt"], nominal, sp.fill), miss)
    for (stat, inp, param), idxs in seq_groups.items():
        X = np.asarray(Xs[inp], float)
        full = ewma(X, param) if stat == "ewma" else cusum(X, param)
        miss = np.isnan(X[:, t0:t1])
        for i in idxs:
            yield i, _gate(full[:, t0:t1], miss)


def ratio_array(S, scale: str, scale_arr):
    """|S| / scale for the scales that need an array; |S| otherwise. NaN where undefined."""
    a = np.abs(S)
    if scale in ("path", "roll", "bar", "hour", "rz"):
        assert scale_arr is not None, f"scale '{scale}' needs an array"
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(scale_arr > 0, a / scale_arr, np.nan)
    return a


# ----------------------------------------------------------- hysteresis
def latch(ent, ext):
    """Hysteresis latch along the last axis. ent = enter condition, ext = stay
    condition (ent is treated as implying ext). True at t iff ext[t] and some
    s <= t has ent[s] with ext[s..t] all True."""
    ent = np.asarray(ent, bool)
    ext = np.asarray(ext, bool)
    assert ent.shape == ext.shape, "ent / ext shape mismatch"
    T = ent.shape[-1]
    if T == 0:
        return ent.copy()
    ent = ent & ext
    idx = np.arange(T, dtype=np.int32)
    prev = np.zeros(ext.shape, bool)
    prev[..., 1:] = ext[..., :-1]
    start = ext & ~prev
    comp = np.maximum.accumulate(np.where(start, idx, np.int32(-1)), axis=-1)
    cs = np.cumsum(ent, axis=-1, dtype=np.int32)
    before = np.take_along_axis(cs, np.maximum(comp - 1, 0), axis=-1)
    before = np.where(comp > 0, before, 0)
    return ext & ((cs - before) > 0)


# ----------------------------------------------------------- run counting
def block_runs(F, gaps, block_sets) -> tuple:
    """F: bool (..., M). block_sets: list of (starts, lens) arrays. Returns
    (totals {gap: (...)}, runs {gap: [array (..., B) per block set]})."""
    F = np.asarray(F, bool)
    M = F.shape[-1]
    idx = np.arange(M)
    cs = np.zeros(F.shape[:-1] + (M + 1,), np.int32)
    cs[..., 1:] = np.cumsum(F, axis=-1, dtype=np.int32)
    nxt = np.minimum.accumulate(np.where(F, idx, M)[..., ::-1], axis=-1)[..., ::-1]
    totals, runs = {}, {}
    for g in gaps:
        assert g >= 1, "run gap must be >= 1"
        lo = np.maximum(idx - g, 0)
        Pg = F & ((cs[..., :M] - cs[..., lo]) == 0)
        cP = np.zeros_like(cs)
        cP[..., 1:] = np.cumsum(Pg, axis=-1, dtype=np.int32)
        totals[g] = Pg.sum(-1)
        per = []
        for starts, lens in block_sets:
            ends = starts + lens
            r = cP[..., ends] - cP[..., starts]
            first = nxt[..., starts]
            valid = first < ends
            pf = np.take_along_axis(Pg, np.minimum(first, M - 1), axis=-1)
            per.append(r + (valid & ~pf))
        runs[g] = per
    return totals, runs


def draw_blocks(segs, label_len, B: int, rng) -> tuple:
    """Uniform blocks inside segments [(start, len)]. label_len None = mixed lengths
    (drawn from BLOCK_LENGTHS that fit). Returns starts, lens, segment index."""
    empty = (np.zeros(0, int), np.zeros(0, int), np.zeros(0, int))
    if not segs or B <= 0:
        return empty
    s0 = np.array([s for s, _ in segs], int)
    n0 = np.array([n for _, n in segs], int)

    def valid(L):
        return np.maximum(0, n0 - L + 1)

    if label_len is None:
        avail = [L for L in BLOCK_LENGTHS if valid(L).sum() > 0]
        if not avail:
            return empty
        Ls = rng.choice(avail, size=B)
    else:
        if valid(label_len).sum() == 0:
            return empty
        Ls = np.full(B, label_len)
    S = np.zeros(B, int)
    G = np.zeros(B, int)
    for L in np.unique(Ls):
        m = Ls == L
        wgt = valid(int(L))
        cum = np.cumsum(wgt)
        u = rng.integers(0, cum[-1], size=int(m.sum()))
        si = np.searchsorted(cum, u, side="right")
        S[m] = s0[si] + (u - (cum[si] - wgt[si]))
        G[m] = si
    return S, Ls.astype(int), G


# ------------------------------------------------------------ gap policies
def pack_layout(keep, epoch, carry, step: int, sep: int = SEP) -> tuple:
    """Lay the kept rows of a chronological table on a packed time grid.
    keep: bool (N,) rows of the population. epoch: int seconds (N,). carry: max number
    of missing bars bridged inside a segment by NaN placeholder bars (0 = reset at any
    gap), or None = bar-index packing (gaps ignored, one segment; DIAGNOSTIC ONLY).
    Returns (pos (N,) packed index or -1, M packed length, segments [(start, length)])."""
    keep = np.asarray(keep, bool)
    epoch = np.asarray(epoch, np.int64)
    assert keep.shape == epoch.shape and keep.ndim == 1, "keep / epoch must be 1-D and aligned"
    assert step > 0 and (carry is None or carry >= 0), "bad step or carry"
    pos = np.full(len(keep), -1, np.int64)
    p, last, segs = 0, None, []
    for i in range(len(keep)):
        if not keep[i]:
            continue
        ep = int(epoch[i])
        if last is None:
            new = True
        elif carry is None:
            new = False
        else:
            assert ep - last >= step, "epochs must increase by at least one bar"
            miss = (ep - last) // step - 1
            new = miss > carry
            if not new:
                p += miss
        if new:
            if segs:
                p += sep
            segs.append([p, 0])
        pos[i] = p
        segs[-1][1] = p - segs[-1][0] + 1
        p += 1
        last = ep
    return pos, p, [tuple(s) for s in segs]
