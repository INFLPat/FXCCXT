# version: 261026
"""
run_runlevel_report.py  (fx_trader/run_runlevel_report.py)

Chat 14p4-iv close-out: turns the LOCAL artefacts of run_runlevel_experiment.py (CONTEXT_HANDOFF 4l.4) into ONE
markdown reference of numbers. READ-ONLY on the artefacts; REPORT ONLY: it states what was measured, it chooses and
recommends nothing. The all-columns lookup (--survey-out, one row per column of the full layout, about 1 MB, gitignored) is attached to a chat only on demand. Run it only when the tables must be regenerated (new run, new columns): the committed copy of
the 261025 tables is fx_trader/RUNLEVEL_RESULTS_14p4-iv.md and later chats should read that instead.

Inputs (all in --dir, produced by run_runlevel_experiment.py; tags as used on the command line):
  runlevel_meta_<stem>_<tag>.json + runlevel_real_<stem>_<tag>.npz      (--real-tag)
  runlevel_meta_<stem>_<tag>.json + runlevel_inj_<stem>_<tag>.npz       (--inj-tags, comma list, one per path)
  runlevel_tier2_<stem>_<tag>.jsonl                                     (--t2-tag, optional)
  runlevel_real_<stem>_<tag>.npz                                        (--pol-tag, optional extra gap policies)
  <stem> = chat<N>_<YYMMDD>; --date is the run date of the files. --real-date (optional) is the run date of the REAL artefact only,
  for a later real-only re-run (e.g. --run-extents) whose date differs from the injection files.

Columns shown: REPORT_COLS (a fixed, NON-selective sample of the headline columns, chosen for coverage of window,
scale and statistic, NOT because they perform well) plus anything in --cols (comma list of column ids, e.g.
"median|W12|full|trailing|raw|abs|4"; tier-2 columns only: median / mean, W6 / 12 / 24, trailing, full fill).
14p4-vi: artefacts now carry ci_cols (tier-2 columns with block / stretch-clustered CIs) next to hcols (the bank: tiers 1 + 2);
the survey CSV also lists tail / cover / the 2.5 bps and step_plateau cells and the real runs under every stored run extent
(blank for artefacts that predate them). Older artefacts without ci_cols are read with hcols = ci_cols.

Run (from fx_trader/): python3 run_runlevel_report.py --chat 14 --date 261025 --real-tag r2real
  --inj-tags r2injHUB,r2injCHF,r2injEUR,r2injJPY --t2-tag r2t2 --pol-tag r2pol --out RUNLEVEL_RESULTS_14p4-iv.md --survey-out reports/runlevel_survey_chat14_261025.csv
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

HOUR = 3600
REPORT_COLS = (
    "median|W6|full|trailing|raw|abs|3", "median|W6|full|trailing|raw|abs|5",
    "median|W12|full|trailing|raw|abs|3", "median|W12|full|trailing|raw|abs|5",
    "median|W24|full|trailing|raw|abs|3", "median|W24|full|trailing|raw|abs|5",
    "mean|W12|full|trailing|raw|abs|3", "median|W12|full|trailing|raw|path|1",
    "median|W12|full|trailing|raw|rz|4", "median|W12|full|trailing|norm|unit|1",
)
METHODS_SHOW = ("unweighted|LS", "unweighted|H1.0|mad", "inv_spread|LS", "inv_spread|H1.0|spread", "inv_spread2|H1.0|spread")
PATH_SHORT = {"hub": "hub", "arm:CHF": "CHF", "arm:EUR": "EUR", "arm:JPY": "JPY"}


# ------------------------------------------------------------------ helpers
def md_table(header: list, rows: list) -> str:
    assert header and all(len(r) == len(header) for r in rows), "ragged table"
    esc = lambda v: str(v).replace("|", "\\|")      # column ids contain pipes
    lines = ["| " + " | ".join(esc(h) for h in header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def fmt(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def load_pair(d: Path, kind: str, stem: str, tag: str) -> tuple:
    meta_p = d / f"runlevel_meta_{stem}_{tag}.json"
    arr_p = d / f"runlevel_{kind}_{stem}_{tag}.npz"
    assert meta_p.exists() and arr_p.exists(), f"missing artefact: {meta_p} or {arr_p}"
    with np.load(arr_p) as z:
        arrays = {k: z[k] for k in z.files}
    return json.loads(meta_p.read_text()), arrays


def real_stem(args: argparse.Namespace) -> str:
    """Stem of the REAL artefact: --real-date when given (a real-only re-run, e.g. --run-extents, has its own run date), else --date."""
    rd = args.real_date or args.date
    assert re.fullmatch(r"\d{6}", rd), "--real-date must be YYMMDD"
    return f"chat{args.chat}_{rd}"


def pick_cols(columns: list, extra: list) -> list:
    cols = list(REPORT_COLS) + [c for c in extra if c not in REPORT_COLS]
    for c in cols:
        assert c in columns, f"column {c!r} not in the artefact"
    return cols


def ci_index(z: dict, ci: int) -> int:
    """Position of column ci in the CI arrays (ci_cols; older artefacts: hcols)."""
    return list(z["ci_cols"] if "ci_cols" in z else z["hcols"]).index(ci)


def cell_index(axes: dict, pi: int, si: int, mi: int, shi: int, di: int) -> int:
    S, M, H, D = len(axes["signs"]), len(axes["mags"]), len(axes["shapes"]), len(axes["durs"])
    return (((pi * S + si) * M + mi) * H + shi) * D + di


# ------------------------------------------------------------------- real
def section_real(meta: dict, z: dict, cols: list) -> str:
    r = meta["real"]
    ids = r["columns"]
    pols = r["policies"]
    hyst = r["hyst_variants"]
    assert "mask_reset" in pols and "all_chron" in pols, "real artefact needs the default policies"
    pm, pa = pols.index("mask_reset"), pols.index("all_chron")
    gi3 = r["run_gaps"].index(3)
    bl = r["block_labels"]
    out = ["## Real chronology (ref population = mask_reset, plain flag)", "",
           f"Rows {meta['n_rows']}, ref bars {meta['n_ref']}, ref stretches {meta['constants']['n_ref_stretches']}. "
           "Counts are flagged bars and runs (RUN_GAP 3) over the whole ref chronology, per path (hub / CHF / EUR / JPY). "
           "Ref is contaminated by unknown real runs: these are UPPER bounds on clean-world false runs.", ""]
    rows = []
    for c in cols:
        i = ids.index(c)
        rows.append([c, "/".join(str(int(v)) for v in z["flagbars"][pm, 0, :, i]), "/".join(str(int(v)) for v in z["runs_total"][pm, 0, gi3, :, i])])
    out += [md_table(["column", "flagged bars h/C/E/J", "runs (gap 3) h/C/E/J"], rows), ""]
    out += ["### Block false-run rate (mean runs per block, RUN_GAP 3, ref, plain flag; JPY path; 95% segment-bootstrap CI)", ""]
    rows = []
    for c in cols:
        i, h = ids.index(c), ci_index(z, ids.index(c))
        cells = [c]
        for lab in ("6", "12", "24", "48"):
            li = bl.index(lab)
            cells.append(f"{z['blk_runs'][pm, 0, li, gi3, 3, i]:.4f} [{z['blk_ci_lo'][pm, 0, li, gi3, 3, h]:.4f}, {z['blk_ci_hi'][pm, 0, li, gi3, 3, h]:.4f}]")
        rows.append(cells)
    out += [md_table(["column", "6 h", "12 h", "24 h", "48 h"], rows), ""]
    out += ["### Hysteresis on the false-run side (ref, runs RUN_GAP 3, JPY path / hub path; exit fraction of the entry threshold)", ""]
    rows = []
    for c in cols:
        i = ids.index(c)
        rows.append([c] + [f"{int(z['runs_total'][pm, k, gi3, 3, i])} / {int(z['runs_total'][pm, k, gi3, 0, i])}" for k in range(len(hyst))])
    out += [md_table(["column"] + [f"exit {h:g}" if h < 1 else "plain" for h in hyst], rows), ""]
    out += ["### The six real JPY runs, UNTUNED (all_chron, plain flag, JPY path)", "",
            "Cell = first-flag latency in hours from the detector-defined run start (circular: 4i.11) / flagged bars in the run / bars in the run; `-` = not detected.", ""]
    rows = []
    for c in cols:
        i = ids.index(c)
        cells = [c]
        for ri in range(len(r["runs"])):
            nv, fl, lat = (z["real_runs"][pa, 0, ri, 3, i, k] for k in (0, 1, 2))
            cells.append(f"{int(lat)}h {int(fl)}/{int(nv)}" if lat >= 0 else f"- 0/{int(nv)}")
        rows.append(cells)
    out += [md_table(["column"] + r["runs"], rows), ""]
    pre = [int(z["real_runs"][pa, 0, ri, 3, ids.index(cols[0]), 3]) for ri in range(len(r["runs"]))]
    post = [int(z["real_runs"][pa, 0, ri, 3, ids.index(cols[0]), 4]) for ri in range(len(r["runs"]))]
    out += [f"Flagged bars in the 48 h before / after each run for `{cols[0]}`: before {pre}, after {post} (after-run flags include window carry-over from the run itself).", ""]
    return "\n".join(out)


def section_policies(meta: dict, z: dict, cols: list) -> str:
    r = meta["real"]
    ids, pols = r["columns"], r["policies"]
    gi3 = r["run_gaps"].index(3)
    out = ["## Extra gap policies (real chronology, plain flag, runs RUN_GAP 3 and flagged bars, hub / JPY paths)", "",
           "Segments per policy: " + "; ".join(f"{p}: {r['segments'][p]['n_segments']} segments, longest {r['segments'][p]['longest_segment']}" for p in pols) + ". "
           "carryN bridges up to N missing bars with NaN placeholders; barindex ignores gaps (DIAGNOSTIC ONLY, violates 4j.5).", ""]
    rows = []
    for c in cols:
        i = ids.index(c)
        rows.append([c] + [f"{int(z['flagbars'][pi, 0, 0, i])}/{int(z['flagbars'][pi, 0, 3, i])} bars; {int(z['runs_total'][pi, 0, gi3, 0, i])}/{int(z['runs_total'][pi, 0, gi3, 3, i])} runs" for pi in range(len(pols))])
    out += [md_table(["column"] + pols, rows), ""]
    return "\n".join(out)


# -------------------------------------------------------------- injection
def section_injection(runs: list, cols: list) -> str:
    """runs: [(meta, arrays)] one per path."""
    first = runs[0][0]["inj"]
    ids = first["columns"]
    out = ["## Injection (R1: offset added to the path residual, median of paths recomputed; real ref chronology)", "",
           f"Per path and duration: n = {sorted(set(int(v) for _m, z in runs for v in z['n_trials']))} trials. Detection = a flag during the run bars. "
           "D >= 24 trials come from overlapping windows inside <= 120-bar stretches: the effective n is the number of stretches (236), use the CI columns. "
           "Clean rows are the paired windows without injection.", ""]
    for c in cols:
        ci = ids.index(c)
        out += [f"### Detection rate, flat, + sign: `{c}`", ""]
        rows = []
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            p = PATH_SHORT[ax["paths"][0]]
            for mi, mag in enumerate(ax["mags"]):
                rows.append([p, f"{mag:g}"] + [fmt(z["det"][cell_index(ax, 0, 0, mi, ax["shapes"].index("flat"), di), ci] / z["n_trials"][di]) for di in range(len(ax["durs"]))])
            rows.append([p, "clean"] + [fmt(z["c_det"][di, ci] / z["n_trials"][di], 3) for di in range(len(ax["durs"]))])
        out += [md_table(["path", "bps"] + [f"{d} h" for d in first["cells_axes"]["durs"]], rows), ""]
    out += ["### Mean first-flag index among detected trials (hours from run start), flat, + sign, D = 24 / 72 h", ""]
    rows = []
    for c in cols:
        ci = ids.index(c)
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            cells = [c, PATH_SHORT[ax["paths"][0]]]
            for d in (24, 72):
                di = ax["durs"].index(d)
                for mag in (4.0, 9.0):
                    k = cell_index(ax, 0, 0, ax["mags"].index(mag), ax["shapes"].index("flat"), di)
                    cells.append(fmt(z["lat"][k, ci] / z["det"][k, ci]) if z["det"][k, ci] else "-")
            rows.append(cells)
    out += [md_table(["column", "path", "24h 4bps", "24h 9bps", "72h 4bps", "72h 9bps"], rows), ""]
    out += ["### Shapes: detection at 6 bps, D = 24 h, + sign", ""]
    rows = []
    for c in cols:
        ci = ids.index(c)
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            di, mi = ax["durs"].index(24), ax["mags"].index(6.0)
            rows.append([c, PATH_SHORT[ax["paths"][0]]] + [fmt(z["det"][cell_index(ax, 0, 0, mi, ax["shapes"].index(s), di), ci] / z["n_trials"][di]) for s in ax["shapes"]])
    out += [md_table(["column", "path"] + list(first["cells_axes"]["shapes"]), rows), ""]
    out += ["### Borderline offset (3 bps, flat, D = 24 h): sign, pre-context bucket, stretch-clustered 95% CI", ""]
    rows = []
    bl = first["bucket_labels"]
    for c in cols:
        ci = ids.index(c)
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            h, k = list(z["hcols"]).index(ci), ci_index(z, ci)
            di, mi, shi = ax["durs"].index(24), ax["mags"].index(3.0), ax["shapes"].index("flat")
            kp, kn = cell_index(ax, 0, 0, mi, shi, di), cell_index(ax, 0, 1, mi, shi, di) if len(ax["signs"]) > 1 else None
            bkt = "/".join(fmt(z["hb_det"][b, kp, h] / z["n_trials_bucket"][b, di]) if z["n_trials_bucket"][b, di] else "-" for b in range(len(bl)))
            rows.append([c, PATH_SHORT[ax["paths"][0]], fmt(z["det"][kp, ci] / z["n_trials"][di]),
                         fmt(z["det"][kn, ci] / z["n_trials"][di]) if kn is not None else "n/a",
                         f"[{fmt(z['ci_det_lo'][kp, k])}, {fmt(z['ci_det_hi'][kp, k])}]" if "ci_det_lo" in z else "n/a", bkt])
    out += [md_table(["column", "path", "+3 bps", "-3 bps", "CI of +", "by bucket " + "/".join(bl)], rows),
            f"Trials per bucket at D = 24 (first path): {[int(v) for v in runs[0][1]['n_trials_bucket'][:, first['cells_axes']['durs'].index(24)]]}", ""]
    out += ["### Hysteresis on the run side: share of the 24 post-run bars still flagged, and share of run bars flagged (6 bps flat, D = 24 h, + sign)", ""]
    rows = []
    for c in cols:
        ci = ids.index(c)
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            h = list(z["hcols"]).index(ci)
            di, mi = ax["durs"].index(24), ax["mags"].index(6.0)
            k = cell_index(ax, 0, 0, mi, ax["shapes"].index("flat"), di)
            tv, n = z["tail_valid"][di], z["n_trials"][di]
            rows.append([c, PATH_SHORT[ax["paths"][0]], fmt(z["cover"][k, ci] / (24 * n)), fmt(z["hy75_cover"][k, h] / (24 * n)), fmt(z["hy50_cover"][k, h] / (24 * n)),
                         fmt(z["tail"][k, ci] / tv), fmt(z["hy75_tail"][k, h] / tv), fmt(z["hy50_tail"][k, h] / tv), fmt(z["c_tail"][di, ci] / tv, 3)])
    out += [md_table(["column", "path", "cover plain", "cover 0.75", "cover 0.5", "tail plain", "tail 0.75", "tail 0.5", "clean tail"], rows), ""]
    return "\n".join(out)


# ----------------------------------------------------------------- tier 2
def section_tier2(meta: dict, records: list) -> str:
    methods = meta["tier2"]["methods"]
    mi = [methods.index(m) for m in METHODS_SHOW]
    out = ["## Tier 2 (R2 leg-level offset, 27-method Huber grid; R3 offset-corrected refit)", "",
           f"{len(records)} trials. err = mean innocent-currency strength error (bps) over the run bars; top1 = share of trials whose top-ranked arm is the faulted arm. "
           "`_r3` = after the offset estimate was subtracted wherever the fixed reference flag fired. "
           "CAUTION: top1_r3 is measured on the CORRECTED fit, where the fault has been (partly) removed, so a lower top1_r3 is expected and is not a loss of the flag itself.", ""]
    keys = sorted({(r["mag"], r["shape"], r["dur"]) for r in records})
    rows = []
    for mag, shp, dur in keys:
        rs = [r for r in records if (r["mag"], r["shape"], r["dur"]) == (mag, shp, dur)]
        e, e3 = np.mean([r["err_mean"] for r in rs], 0), np.mean([r["err_mean_r3"] for r in rs], 0)
        t, t3 = np.mean([r["top1"] for r in rs], 0), np.mean([r["top1_r3"] for r in rs], 0)
        fl = np.mean([r["r3_flag_bars"] / r["dur"] for r in rs])
        rows.append([f"{mag:g}", shp, dur, len(rs), fmt(fl)] + [f"{e[i]:.2f}->{e3[i]:.2f}" for i in mi] + [f"{t[mi[0]]:.2f}->{t3[mi[0]]:.2f}", f"{t[mi[3]]:.2f}->{t3[mi[3]]:.2f}"])
    out += [md_table(["bps", "shape", "D", "n", "R3 flag share"] + [f"err {m}" for m in METHODS_SHOW] + ["top1 LS", "top1 inv_spread|H1.0"], rows), ""]
    buckets = {}
    for r in records:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    out += [f"Trials by pre-context bucket: {buckets}. Legs: {sorted({r['leg'] for r in records})} (errors are nearly identical between the two legs).", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ survey
def build_survey(args: argparse.Namespace) -> str:
    """ONE csv row per column of the full layout (all statistics, windows, fills, scales, thresholds): a lookup so a later chat
    need not re-upload artefacts. Fields are fixed and non-selective. Injection = flat, + sign, R1, final checkpoint totals."""
    d, stem = Path(args.dir), f"chat{args.chat}_{args.date}"
    meta_r, z_r = load_pair(d, "real", real_stem(args), args.real_tag)
    r = meta_r["real"]
    ids, pols = r["columns"], r["policies"]
    pm, pa, gi3 = pols.index("mask_reset"), pols.index("all_chron"), r["run_gaps"].index(3)
    runs = [load_pair(d, "inj", stem, t) for t in args.inj_tags.split(",") if t]
    assert runs, "survey needs --inj-tags"
    ext = r.get("extents", {}).get("definitions", [])
    head = ["column", "ref_flagbars_JPY", "ref_runs_gap3_JPY", "real_runs_detected_of_6", "real_median_latency_h"] + [f"real_ext_{n}_detected_of_6" for n in ext]
    for meta, _z in runs:
        p = PATH_SHORT[meta["inj"]["cells_axes"]["paths"][0]]
        head += [f"det_{p}_3bps_D24", f"det_{p}_4bps_D12", f"det_{p}_4bps_D24", f"firstflag_h_{p}_4bps_D24", f"clean_det_{p}_D24",
                 f"det_{p}_2.5bps_D24", f"cover_{p}_4bps_D24", f"tail_{p}_4bps_D24", f"clean_tail_{p}_D24", f"det_{p}_step6_plat3_D24"]
    rows = [",".join(head)]
    for ci, c in enumerate(ids):
        lats = [z_r["real_runs"][pa, 0, ri, 3, ci, 2] for ri in range(len(r["runs"]))]
        hit = sorted(float(v) for v in lats if v >= 0)
        cells = [c, str(int(z_r["flagbars"][pm, 0, 3, ci])), str(int(z_r["runs_total"][pm, 0, gi3, 3, ci])), str(len(hit)),
                 fmt(hit[len(hit) // 2], 1) if hit else ""]
        cells += [str(int((z_r["real_ext"][ei, :, ci, 2] >= 0).sum())) if "real_ext" in z_r else "" for ei in range(len(ext))]
        for meta, z in runs:
            ax = meta["inj"]["cells_axes"]
            assert meta["inj"]["columns"][ci] == c, "injection and real column layouts differ"
            fl = ax["shapes"].index("flat")
            k3 = cell_index(ax, 0, 0, ax["mags"].index(3.0), fl, ax["durs"].index(24))
            k412 = cell_index(ax, 0, 0, ax["mags"].index(4.0), fl, ax["durs"].index(12))
            k424 = cell_index(ax, 0, 0, ax["mags"].index(4.0), fl, ax["durs"].index(24))
            d24, d12 = ax["durs"].index(24), ax["durs"].index(12)
            dt = z["det"][k424, ci]
            cells += [fmt(z["det"][k3, ci] / z["n_trials"][d24], 3), fmt(z["det"][k412, ci] / z["n_trials"][d12], 3),
                      fmt(dt / z["n_trials"][d24], 3), fmt(z["lat"][k424, ci] / dt, 1) if dt else "", fmt(z["c_det"][d24, ci] / z["n_trials"][d24], 4)]
            k25 = cell_index(ax, 0, 0, ax["mags"].index(2.5), fl, d24) if 2.5 in ax["mags"] else None
            ksp = cell_index(ax, 0, 0, ax["mags"].index(6.0), ax["shapes"].index("sp3_p3"), d24) if "sp3_p3" in ax["shapes"] and 6.0 in ax["mags"] else None
            tv = z["tail_valid"][d24]
            cells += [fmt(z["det"][k25, ci] / z["n_trials"][d24], 3) if k25 is not None else "",
                      fmt(z["cover"][k424, ci] / (24 * z["n_trials"][d24]), 3), fmt(z["tail"][k424, ci] / tv, 3) if tv else "",
                      fmt(z["c_tail"][d24, ci] / tv, 4) if tv else "",
                      fmt(z["det"][ksp, ci] / z["n_trials"][d24], 3) if ksp is not None else ""]
        rows.append(",".join(cells))
    return "\n".join(rows) + "\n"


# --------------------------------------------------------------------- main
def build_report(args: argparse.Namespace) -> str:
    d, stem = Path(args.dir), f"chat{args.chat}_{args.date}"
    extra = [c for c in args.cols.split(",") if c]
    meta_r, z_r = load_pair(d, "real", real_stem(args), args.real_tag)
    cols = pick_cols(meta_r["real"]["columns"], extra)
    hc = set(int(i) for i in (z_r["ci_cols"] if "ci_cols" in z_r else z_r["hcols"]))
    for c in cols:
        assert meta_r["real"]["columns"].index(c) in hc, f"column {c!r} is not a headline column (block CIs and class splits exist for headline columns only)"
    parts = [f"<!-- version: {args.date} -->", f"# Run-level view for persistent arm offsets: numbers reference (chat {args.chat}, files dated {args.date})", "",
             "REPORT ONLY. Nothing is chosen or recommended here. Generated by `run_runlevel_report.py` from the local artefacts listed in CONTEXT_HANDOFF 4l.4 "
             f"(tags: real `{args.real_tag}`, injection `{args.inj_tags}`, tier 2 `{args.t2_tag or 'none'}`, policies `{args.pol_tag or 'none'}`). "
             f"Columns are a fixed, non-selective sample of the headline set; the artefacts hold all {len(meta_r['real']['columns']):,} columns. Column id = statistic|window|fill|causality|input|scale|threshold.", "",
             "Dump-route check (median d_JPY of the six real runs against the 4i.11 deviations): " + ", ".join(f"{r['run']} {'n/a' if r['median_d_jpy_bps'] is None else format(r['median_d_jpy_bps'], '+.2f')} vs {r['expected_4i11_dev_bps']:+.1f}" for r in meta_r["real_run_check"]) + ".", "",
             section_real(meta_r, z_r, cols)]
    if args.pol_tag:
        meta_p, z_p = load_pair(d, "real", stem, args.pol_tag)
        parts.append(section_policies(meta_p, z_p, cols))
    runs = [load_pair(d, "inj", stem, t) for t in args.inj_tags.split(",") if t]
    if runs:
        assert all(m["inj"]["columns"] == runs[0][0]["inj"]["columns"] for m, _z in runs), "injection artefacts must share one column layout"
        parts.append(section_injection(runs, cols))
    if args.t2_tag:
        meta_t = json.loads((d / f"runlevel_meta_{stem}_{args.t2_tag}.json").read_text())
        p = d / f"runlevel_tier2_{stem}_{args.t2_tag}.jsonl"
        assert p.exists(), f"missing {p}"
        records = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        assert records, "empty tier 2 file"
        parts.append(section_tier2(meta_t, records))
    return "\n".join(parts) + "\n"


def parse_args(argv: list) -> argparse.Namespace:
    assert isinstance(argv, list), "argv must be a list"
    p = argparse.ArgumentParser(description="Numbers reference for the run-level view (14p4-iv); read-only, report only.")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--date", required=True, help="YYMMDD of the artefact files")
    p.add_argument("--dir", default="reports")
    p.add_argument("--real-tag", required=True)
    p.add_argument("--real-date", default="", help="YYMMDD of the REAL artefact when it differs from --date (a later real-only run, e.g. with --run-extents)")
    p.add_argument("--inj-tags", default="")
    p.add_argument("--t2-tag", default="")
    p.add_argument("--pol-tag", default="")
    p.add_argument("--cols", default="", help="extra headline column ids, comma list")
    p.add_argument("--out", required=True)
    p.add_argument("--survey-out", default="", help="also write the all-columns lookup csv (needs --inj-tags)")
    return p.parse_args(argv)


def main(argv: list | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    assert re.fullmatch(r"\d{6}", args.date), "--date must be YYMMDD"
    out = Path(args.out)
    assert not out.exists(), f"output already exists: {out}"
    sv = Path(args.survey_out) if args.survey_out else None
    assert sv is None or not sv.exists(), f"survey output already exists: {sv}"
    text = build_report(args)
    survey = build_survey(args) if sv else ""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"Written: {out} ({len(text)} bytes)")
    if sv:
        sv.write_text(survey)
        print(f"Written: {sv} ({len(survey)} bytes)")


if __name__ == "__main__":
    main()
