<!-- version: 261025 -->
# Roadmap

Read after `CONTEXT_HANDOFF.md` and `CONFIDENCE_SIZING_DESIGN.md`; research context in `RESEARCH_NOTES.md`. Rewritten 260929 (chat 10); housekeeping 260930 (chat 11); restructured 261003 (chat 14: p-suffix chat IDs, Status/Date columns, research chats, deferred-capabilities table, new risks). Finished chats are recorded in `CHAT_LOG.md`, not here. Contents: 0 baseline, 1 MVP, 2 sandbox and validation data, 3 phases, 4 chat plan, 5 parallel/post-MVP, 6 de-bloat, 7 risk register, 8 extras, 9 deferred capabilities.

## 0. Honest baseline

7 of 80 tested (strategy, instrument) combinations cleared Tier 4, on a single 6-month window (2025 H2), several on wafer-thin CI lower bounds (GBP_JPY +0.14%, +0.13%). Not a demonstrated edge. Everything below is infrastructure to find out whether an edge exists. Pace: project started 260910; chats 1-14 complete by 261005. Target: 3-4 tighter one-topic chats per week. Chat 14 added about 14 chats/items to the plan (research, second-source QC, universe, storage, manifest): roughly +4 weeks (low confidence).

## 1. MVP

**Definition (agreed 260929):** everything needed to actually trade live, single user. Going live at MVP is possible, not automatic.
- Survivors validated out-of-time; multiple-testing controlled.
- Cartesian currency-pair comparison (relative-strength ranking of currencies, spread mean-reversion between related pairs, possibly more) - believed to be the core edge; ample dedicated time. Universe = a cartesian product of as many FX and crypto instruments as possible, grown in stages (Section 2).
- Additional signal generators explored (built, or scoped for post-MVP).
- Shadow scoring engine, then sizing (hysteresis, debounce, rescaling), then cross-instrument allocation and confidence modules.
- User risk setting (risk-appetite slider), pulled forward from old Phase 3 into MVP. It adjusts only the confidence discount, never data-validity thresholds.
- Live capability, single user, small real fund (about GBP 100-200) once signed off.
- Minimal dashboard; investor documentation, flow chart and presentation; multiple refine and de-bloat rounds.

**First live run success criteria (operational, NOT a return target):** live fills match backtest-implied fills within a pre-set divergence threshold (set BEFORE seeing data); no risk-limit breaches; reconciliation clean; realised costs match the cost model. A month of GBP 100-200 is only a handful of trades, so it tests plumbing and cost parity, not profitability. Return targets (e.g. 1.2x-2x monthly) are aspirations resting on the untested cartesian work, not on current evidence.

**Not in MVP:** multi-user, subscriptions, deployment at scale, regime detection (Option C). Post-MVP placeholder: latency/triangular-arbitrage-style strategies (need tick data, latency, depth; likely not Python).

**Live-trading checklist (L1-L18; all needed before real money):**
- L1 live feed (completed-candle detection, gap/stale detection, weekend/rollover, holiday-vs-outage classification) sharing one interface with a REPLAY feed (sandbox replayed as if live), including FAULT INJECTION (missing bars, stale quotes, duplicates, inverted quotes, DST shifts, out-of-order)
- L2 order manager (state machine, idempotent client order IDs, retries/backoff, partial fills, rejections)
- L3 startup reconciliation of positions/balance against the broker
- L4 stops (engine has none; broker-side protective stops for live)
- L5 kill switch and hard limits (max daily loss, exposure, order size, fat-finger check)
- L6 persistent audit log (signals, orders, fills; usable by an accountant)
- L7 alerts (email/Telegram)
- L8 supervision and hosting (always-on runner host, auto-restart, heartbeat) - NOTE database host (Snowflake Postgres/Neon) is not the runner host; hosting decision open (chat 30)
- L9 financing (swap/rollover) costs in CostModel (absent today)
- L10 small-account viability (min order sizes, fee floors; find the sweet spot for return % vs fees at GBP 100-200)
- L11 FX leverage/margin limits (UK retail) and margin close-out behaviour
- L12 UK access/legality of chosen broker and exchange
- L13 secrets handling (.env hygiene, no tokens in logs, pinned dependencies)
- L14 CI (GitHub Actions running the test suite)
- L15 go-live checklist and rollback plan, rehearsed on paper first
- L16 tax record-keeping design (not tax advice; accountant to confirm)
- L17 clock sync (NTP) and bar-timestamp convention checks on every live source (new, chat 14)
- L18 data-source redundancy: at least two independent live sources, outage vs holiday policy, failover (new, chat 14; design in 30p1)

## 2. Sandbox and validation data

**Development sandbox (DECIDED 260929): the current window is final.** Kraken 26Q2 is unpublished and may not appear; no further quarters are planned. All 16 instruments aligned on START/END (`CONTEXT_HANDOFF.md` Section 4, audited chat 14: Section 4f). Data integrity is tracked by a manifest (16 instruments, 508,413 rows, 261003; `sandbox_manifest_22Q1to26Q1_261003.json`, kept in gitignored `fx_trader/reports/` and backed up with the DB). Verify before every sweep/analysis (`--verify-manifest`).

**Universe growth (decided 261003):** stage 1 = the 16-instrument sandbox as-is, to test theory and pipelines; stage 2 = augment the sandbox with identified instruments (incl. stablecoin pairs such as USDT/USD, USDT/GBP) to learn more edges and profitability (25p1); stage 3 = the full MVP suite before MVP (P11b); later additions (new crypto assets etc.) stay possible. The registry is designed for flexibility and future-proofing (19p1). `instrument_config.py` currently asserts exactly 16 - to be replaced.

**Unseen-data caveat:** out-of-time replay, re-discovery and the base-metric test all consume the existing data. The only genuinely unseen data will be forward paper/live data. Chat 17 decides a holdout policy inside the sandbox (e.g. reserve 2026 Q1 untouched until the final pre-live check). Not decided yet.

**Single-source caveat:** FX is OANDA only; crypto is Binance and Kraken. No independent QC yet. Second-source QC (16p1) must precede any cartesian conclusion that depends on outlier handling.

**Still open:** (a) compute-budget dry run on the ~4.25-year window (chat 15; run-monitor data replaces the extrapolation). **Closed in chat 14:** cross-rate arithmetic validation and cross-instrument timestamp alignment and timezone audit (results `CONTEXT_HANDOFF.md` Section 4f). **Closed in chat 13:** reproducing the 7 survivors.

**Out-of-time methodology (two parts, both built):** (1) frozen-candidate replay (`run_out_of_time_validation.py`; unblocked by the chat-14 periods fix - it would have crashed before): the 17 persisted survivors, unchanged params, every window outside 2025 H2, same gate values, no loosening. Expect stress results in windows containing May 2022 (crypto crash, USDT dip, FX gaps). (2) independent re-discovery (`run_full_sweep.py` on the wider window, restricted to non-2025-H2 windows first), compared by hand with (1). Open questions: does a strong new discovery replace the 17 as the reference set; `RunStore` output naming; whether `MAX_GRID_COMBINATIONS` (200) should rise.

## 3. Phases

**Ordering principle (agreed 260929):** cartesian pair work is believed to be the most profitable piece, so it is front-loaded: do the quick, necessary baseline work (P0-P1), then cartesian (P2). Nothing that cartesian would force redoing gets built first. Durations are low confidence. ~84 chats at ~3.5/week: cartesian go/no-go around late Nov; MVP sign-off around Apr 2027, then the small live run.

| Phase | Content | Chats | Target |
|---|---|---|---|
| P0 | Baseline integrity and instrumentation (11-15, 14p1) | 6 | 29 Sep-mid Oct |
| P1 | Minimal validation before cartesian: OOT replay, second-source QC, holdout policy, research R1/R2 (16, 16p1, 17, 17p1, 17p2) | 5 | mid-late Oct |
| P2 | CARTESIAN: definition, architecture, universe registry, R3, multiple-testing control, alignment layer, weekend-proxy feasibility, multi-leg engine, signals, hierarchy validation, go/no-go (18-25, 19p1, 19p2, 21p1) | ~11 | late Oct-late Nov |
| P3 | Deferred single-instrument validation, sandbox augmentation stage 2, de-bloat #2 (25p1, 26-29) | 5 | Nov-Dec |
| P4 | R4, broker/account/hosting, live-feed sources and redundancy, storage source dimension, fees, API tests, cost model (29p1, 30, 30p1, 30p2, 31-33) | 8 | Dec |
| P5 | Scoring engine (shadow), built for cartesian/multi-leg inputs from the start; run manifest/reproducibility (33p1) | 4 | Dec |
| P6 | Sizing wiring, hysteresis, debounce | 3 | Dec-Jan |
| P7 | Live infrastructure L1-L8, L17, L18 (incl. fault-injection replay) | ~10 | Jan |
| P8 | User risk setting (design and build) | 3 | Jan |
| P9 | Cross-instrument allocation and confidence modules (incl. data-completeness input) | 4 | Jan-Feb |
| P10 | Additional signal generators beyond cartesian (scope, build 1-2) | 3 | parallel |
| P11 | Minimal dashboard and visualisation; P11b MVP universe suite | 4 | Feb |
| P12 | Paper trading (4-8 weeks elapsed, weekly short check-ins). EXPLICIT AIMS: measure depth, last look and latency/fill realism, live-vs-backtest parity, outage handling | ~6 short | Feb-Mar |
| P13 | Investor pack: process explainer, flow chart, market/competitor research, feedback loops | ~5 | staggered |
| P14 | Security and compliance basics, CI (L13, L14) | 3 | staggered |
| P15 | MVP hardening, refine/de-bloat rounds, go-live checklist (L15), sign-off; P15b data licensing and redistribution licence set-up | 4 | Mar-Apr |

**Rework-exposed modules (single-instrument assumptions; do NOT extend or restructure until chat 19 decides the cartesian architecture; deletions and housekeeping only):** `backtest/engine.py` (`Trade`, `BacktestResult`), `data/run_store.py` schema (instrument-keyed), `backtest/validation_orchestrator.py`, `backtest/portfolio.py`, `strategy/base.py`, and anything new that would key on one instrument: scoring engine, sizing wiring, order manager, audit-log schema, runner. **Safe before chat 19:** run monitor, audit script, `time_policy.py`, dry run, holdout policy, OOT replay, broker/fee research, the currency-graph engine (14p1, data-level), second-source QC.

**Dependency rules:**
- Timestamp alignment and cross-rate audit before any cartesian work: DONE chat 14.
- Cartesian definition (18), then architecture (19): `Strategy.on_candle` takes one instrument's candle and cannot express cross-instrument signals; cartesian may need an extended interface or a standalone module with a multi-leg engine.
- Multiple-testing control (20) is designed against the cartesian search space.
- Scoring engine and everything instrument-keyed after cartesian architecture is settled.
- Base-metric test (28) after cartesian, so the ranking can include cartesian candidates; it also carries the annualisation-variant axis (MC-1).
- Cartesian conclusions are PROVISIONAL until real fees are applied (33) and until second-source QC (16p1) has been done. COSTS GATE (settled 261005): profitability is a core requirement; no profitability or arbitrage conclusion from 14p1-14p4 or any cartesian work is reportable as such until real costs are applied; data-validity work (residuals, strengths, attribution) does not depend on fees.
- Replay feed (L1) before the live runner; kill switch, audit log and reconciliation (L3, L5, L6) before paper trading; paper trading before real money.
- User risk setting after scoring and sizing exist.
- Run `--verify-manifest` before any sweep/analysis (Section 4 chats marked M).
- All time handling goes through `time_policy.py` (CONTEXT_HANDOFF 4f.5).
- Research chats (R1-R4) before the phases they inform; findings folded into `RESEARCH_NOTES.md`.
- Investor pack staggered through the build; its feedback can raise new work items.
- Fill-ins for waiting on local runs or a usage-window lockout: independent chats (26 trade frequency, 30 broker/hosting research, research chats) may be pulled forward.

## 4. Chat plan

Rules: one topic per chat, finished inside one 24h window. Roadmap chat IDs: a topic split across chats takes `p1`, `p2` (no renumbering); several PATCHES in one chat take letters. The Context panel repo sync supplies every committed repo file, so the Attach column names repo files to LOOK AT (in context); UPLOAD only what is not in the repo (gitignored DBs, logs, manifests, reports, historical copies, pasted terminal output) and mark it (upload). A long attachment list means the scope is too big: split it. All chats run on Sonnet. Sizes (provisional): S under half a 5-hour window, M about one, L one to two. Status: DONE (in `CHAT_LOG.md`), IN PROGRESS, or blank (not started); Date = last day worked. M = run `--verify-manifest` first.

| # | Topic | Size | Attach (besides CONTEXT_HANDOFF.md) | After | Status | Date |
|---|---|---|---|---|---|---|
| 14 | S1: cross-rate / alignment / timezone audit, bug fixes, `time_policy.py`, docs. Payloads: 8dd6c71 (code), 3a2fc4b (docs), close-out chat14c | M-L | sandbox_22Q1to26Q1.db (upload) | 12 | DONE | 261005 |
| 14p1 | S2a BUILT 261005 (rest split into 14p2-14p4 rows below; CONTEXT_HANDOFF 4g): currency-graph engine core - typed edges (quoted/basis/venue), crypto nodes, orientation canonicalisation, executable directed edges, cycle enumeration (+Bellman-Ford); built: package currency_graph (model, graph projections asset/asset_venue/per_venue, cycles incl. cycle basis and Bellman-Ford, evaluate with verdicts), property + differential tests. Strengths/LOO, numpy path and INV-2/3/4/5 moved to 14p2-14p4 | L | audit_sandbox_alignment.py, time_policy.py, data/store.py, backtest/portfolio.py, instrument_config.py, RESEARCH_NOTES.md; audit report json + sandbox DB (upload) | 14 | DONE (core) | 261005 |
| 14p2 | Refactor `audit_sandbox_alignment.py` onto currency_graph (gate: tests/test_audit_sandbox_alignment passes unchanged AND new audit run's cycles + basis sections match the golden `audit_220101to260331_chat14_261005.json` to 1e-9; user runs; explain or reassess any difference); plus sandbox-only INV-2, INV-3, INV-5 (need cycles, not strengths) (M) | M | audit_sandbox_alignment.py, currency_graph/*, tests/test_audit_sandbox_alignment.py, RESEARCH_NOTES.md; golden audit JSON, sandbox DB, manifest, chat_start.txt (upload) | 14p1 | DONE | 261010 |
| 14p3 | Strengths: unweighted and 1/spread-weighted least squares, leave-one-pair-out by exact refit (oracle) AND hat-matrix (fast path, 1e-9 cross-check), leave-one-currency-out, strength views (zero-sum, vs USD, equal-weight and user-weighted basket, spread-weighted); measure LOO runtime on the real sandbox | L | currency_graph/*, backtest/portfolio.py, RESEARCH_NOTES.md | 14p2 | DONE (CHAT_LOG; CONTEXT_HANDOFF 4h) | 261012 |
| 14p4 | FAULT DEFINITION and flags for strengths (CONTEXT_HANDOFF 4h.4: run `run_strengths_by_hour.py` first, then settle the threshold and fault types), Huber/IRLS robust strength view plus hub/arm median-of-paths attribution, scheme choice for fault bars or a better scheme (test on injected faults of known type: stale, wide, missing; numpy path moved to 14p5), INV-4 leg attribution (event-calendar half stays 17p1), INV-21 (cause of the small nonzero signed cycle mean) and INV-22 (hour effect in spread-normalised units), and the hour-of-day scheme results; MOVED to 14p5: the THREE-PROJECTION COMPARISON on real data (asset vs asset_venue vs per_venue: which serves estimation vs executability; written decision) (M) | L | currency_graph/*, run_strengths.py, run_strengths_by_hour.py; strengths summary JSON p3f and by-hour JSON (upload; NOT the 80 MB dump unless asked), sandbox DB (upload) | 14p3 | DONE (CHAT_LOG; CONTEXT_HANDOFF 4i; residual scope moved to 14p4b and 14p4c) | 261014 |
| 14p4-ii | Calibrate and compare detectors and Huber on real data: (1) calibrate the synthetic world's noise and wide-fault error ratios against real per-hour LOO/spread (the db-spread run overstates A's false rate at rollover, about 3% against about 1% real: CONTEXT_HANDOFF 4i.10); (2) threshold sweeps for A, the B ratio and C: real flag counts plus synthetic detection and false-rate curves per threshold, REPORT ONLY (no detector, threshold, k per job or scheme is chosen: user decision 261013); (3) a RUN-LEVEL view for persistent arm offsets (the six day-long JPY runs, 4i.11): rolling median of the arm path deviation, tested on the real runs and on injected persistent offsets; (4) two simultaneous faults: hub/arm ambiguity rate, Huber and LOO behaviour; user runs `run_inv4_attribution.py` and the updated `run_detector_scan.py` locally and shares any event information for the fault days | M-L | run_fault_experiment.py, run_detector_scan.py, run_inv4_attribution.py, currency_graph/robust.py, detectors.py; chat_start.txt, sandbox DB, manifest, fault_experiment p4db JSON, detector_scan JSON (with runs), inv4 JSON, strengths_by_hour JSON (upload; NOT the 80 MB dump unless asked) | 14p4 | PARTIAL (a1 done: calibration statistics, CONTEXT_HANDOFF 4j; remainder split into 14p4-iii and 14p4-iv) | 261015 |
| 14p4-iii | Worlds and sweeps (continuation of 14p4-ii, CONTEXT_HANDOFF 4j.6): `run_world_experiment.py` with three worlds - parametric (unchanged; regression against the p4db clean rates), empirical-residual (real bars rebuilt from the a1 bar dump: residual = z x spread x sqrt(1-h), unweighted hat from the topology; check the 4 dp rounding first), calibrated-parametric (fitted from the a1 JSON: noise both proportional and absolute, per-pair spread quantiles with copula correlation, heavy-tail df per class); injection into every ref bar for detector statistics (200-bin histograms per detector statistic per DST cell x hour class) and a stratified subsample for the Huber 27-method grid (raw per-trial JSONL, cap about 50 MB); axes in one pass: A, B x3 and C grids, wide-error ratio 0.2/0.5/1.0, stale-signature variants; PLUS two simultaneous faults (same arm, different arms, hub + arm; stale/stale, stale/wide, wide/wide: hub/arm verdict split, LOO and Huber top-1 hits either fault, innocent-currency error); REPORT ONLY - no detector, threshold, k or scheme chosen | L | CONTEXT_HANDOFF 4i and 4j; run_calibration_stats.py, run_fault_experiment.py, run_detector_scan.py, run_inv4_attribution.py, currency_graph/robust.py, detectors.py, strengths.py (in context); chat_start.txt, sandbox DB, manifest, calibration_stats a1 JSON, calibration_bars a1 JSONL (about 15 MB), fault_experiment p4db JSON, inv4 p4i JSON (upload; NOT the 80 MB dump, NOT p4d2) | 14p4-ii | DONE (CHAT_LOG; CONTEXT_HANDOFF 4k; follow-ups: stratified synthetic-class sampling, family (c) volume as a fraction of typical, and the other 4k.14 gaps, now chat 14p4-v) | 261018 |
| 14p4-iv | Run-level view for persistent arm offsets (CONTEXT_HANDOFF 4j.7 item 3): rolling median of each arm's path deviation (window W = 6, 12, 24; ratio r swept), tuned on INJECTED offsets (4, 6, 9 bps; flat, ramp and decay shapes; 1 to 72 h) in real chronology outside the mask, then reported UNTUNED on the six real JPY runs (onset latency, bars covered); false-run rates on real unmasked stretches (contaminated by unknown real runs: say so); blocks of 1, 3, 6, 12, 24, 48 h and the mixed scheme, tagged, block-aware CIs (BLOCKS MATTER FOR FOUR REASONS: false-run rates, the run-level view, adjacent two-fault placements, block-aware CIs - CONTEXT_HANDOFF 4j.5 item 7 and 4k.15; the machinery already exists in run_world_experiment.py: segments, block_stats, count_runs, run_adjacent, cluster_ci - REUSE it, do not rebuild; per-bar single-fault detection does not depend on block length); the 4j.5 block caveats are binding; the 4k.8 false-run table is the null for run flags | M | CONTEXT_HANDOFF 4i and 4j; run_calibration_stats.py, the 14p4-iii script (in context); chat_start.txt, sandbox DB, manifest, calibration_stats a1 JSON, calibration_bars a1 JSONL, detector_scan p4d3 JSON, world_experiment w1 meta JSON (upload); run_world_experiment.py (in context) | 14p4-iii | DONE (CHAT_LOG; CONTEXT_HANDOFF 4l.9; numbers RUNLEVEL_RESULTS_14p4-iv.md; nothing chosen; interpretation is chat 14p4-vi) | 261025 |
| 14p4-vi | INTERPRETATION of the 14p4-iv run-level results (CONTEXT_HANDOFF 4l.9; REPORT / ANALYSIS ONLY, no code unless a slice is missing; user decision 261025: split from 14p4-iv because that chat was too long): read RUNLEVEL_RESULTS_14p4-iv.md and the survey CSV, compare statistics, windows, fills, scales and thresholds on detection, latency, false-run rate and post-run tail; address the open items in 4l.9.6 (borderline sign asymmetry, robust-z saturation, post-run flags vs carry-over, effective sample for D >= 24, hysteresis, gap policies, Tier 2 / R3 caveat); say what is MEASURED vs INFERRED; list the decisions the USER must take and what each would cost; NOTHING is chosen by Claude. Does NOT cover 14p4-v gaps, INV-21/22, crypto, R4 | S | CONTEXT_HANDOFF 4l.9 and RUNLEVEL_RESULTS_14p4-iv.md (repo, via sync); reports/runlevel_survey_chat14_261025.csv (upload, about 1 MB); chat_start.txt; only if a missing slice is needed, ONE of the r2inj*/r2real npz plus its meta JSON (upload) | 14p4-iv | | |
| 14p4-v | Close the 14p4-iii gaps (CONTEXT_HANDOFF 4k.14; user decision 261018): (1) family (c) volume-fraction sweep: volume as a FRACTION of the pair's typical volume (3%, 10%, 30%, 100%; the absolute 1/2/5/20 kept) x range x{1,2,4} x spread x{2,4,10,25} x E{0,5,15,40}, with C's stale-volume x thin-fraction grid, so C's thin boundary is mapped (14p4-iii: every volume was below 5% of typical, so C-any was trivially 1.00); (2) STRATIFIED class sampling for the synthetic worlds (equal trials per class: flat, rollover_ny, adjacent_ny, other) so rollover cells reach n >= 1,000 instead of about 80, plus a rollover-heavy Tier 2 reservoir; (3) histograms per DST cell x hour class (the 4j.6 decision 14p4-iii did NOT meet: only flag counts per hour|cell were stored); (4) a faulted-pair / faulted-arm stratum (per-pair detection rates were not stored); (5) further strata: spread-dispersion, range and volume-bucket deciles, weekday, post-gap bar; (6) USER TO DECIDE: a compact Tier 1 per-trial dump for the empirical world (about 28 MB, one line per trial: epoch, class, hour|cell, pair, flags, statistics) so new strata and thresholds can be re-sliced WITHOUT re-running (today only the 26,280-line Tier 2 sample carries per-trial tags); (7) smaller: stale signature (b) as the previous bar's frozen spread (14p4-iii used a quiet-spread approximation), optional contamination of B_hour's history by injected faults, mixed block lengths in the adjacent cells, a larger --boot (100 draws is coarse); REPORT ONLY, nothing chosen; EXTEND run_world_experiment.py, do not fork it | S-M | CONTEXT_HANDOFF 4k (esp. 4k.14), run_world_experiment.py, run_calibration_stats.py (in context); chat_start.txt, sandbox DB, manifest, calibration_stats a1 JSON, calibration_bars a1 JSONL, world_experiment w1 meta JSON (upload; NOT the per-world JSONs unless asked); the 14p4-iv PER-BAR DUMP runlevel_bars_*.npz (CONTEXT_HANDOFF 4l.4) holds pr/ps/d/scales per bar: re-upload it, do not recompute | 14p4-iii | | |
| 14p4c | INV-21 (cause of the small nonzero signed cycle mean: regress the per-bar residual on the predicted rounding offset) and INV-22 (residual by UTC hour and DST cell in spread-normalised units) | S-M | run_residual_investigations.py, RESEARCH_NOTES.md; sandbox DB, manifest (upload) | 14p4 | | |
| 14p5 | numpy path for strengths, LOO and LOCO (batched across bars, 1e-9 cross-check against the stdlib oracle; DECIDED 261012 worthwhile because the universe will be much larger; measure the crossover size and memory against pure Python at larger N; baseline: the 27-method Huber grid costs about 21 ms per bar in pure Python on the user's machine, CONTEXT_HANDOFF 4i.10) and the THREE-PROJECTION COMPARISON on real data (asset vs asset_venue vs per_venue: which serves estimation vs executability; written decision) (M) | M | currency_graph/*, runlevel.py (first numpy module: stdlib oracle pattern); strengths summary JSON; sandbox DB (upload) | 14p4 | | |
| 15 | Full-window compute dry run (M), including a robust (Huber 27-method) pass over real bars | S | run_full_sweep.py; `python3 run_monitor.py summary <log>` output or logs/run_log_*.jsonl (upload); runlevel run_monitor logs from 14p4-iv give a numpy timing baseline | 13 | | |
| 16 | Out-of-time frozen-candidate replay: run and interpret (M). Unblocked by the chat-14 periods fix | M | run_out_of_time_validation.py, backtest/periods.py, backtest/validation_orchestrator.py | 13, 14, 15 | | |
| 16p1 | Sandbox second-source QC and source inventory (M): Dukascopy / HistData / OANDA M1 / other ccxt venues, Internet Archive for broker notices; INV-1, 6, 9-14, 16, 19, 20, plus an INV-3 independent-pair check (Kraken FX pairs, no GBP_USD leg) (INV-16: Kraken stablecoin and FX pairs; Kraken FX pairs are a candidate second FX source; reconcile the Kraken folder naming `Kraken_OHLCVT_Q#_YYYY` vs `kraken_csv/YYQ#`); QC side table; raw-vs-masked dual run; decide source-column vs separate tables (input to 30p2); prioritise a second source for the 2-leg arms (USD_CHF vs GBP_CHF, EUR, JPY legs): a parallel edge breaks the LOO degeneracy (CONTEXT_HANDOFF 4h.3); check the 17 fault bars (4i.3) and the six day-long JPY-arm runs (4i.11) against the second source (exact dates, times and a research plan: RESEARCH_NOTES Section 17) | M | RESEARCH_NOTES.md; audit report (upload); user OANDA token for local M1 runs | 14p1, 16 | | |
| 17 | Holdout policy inside the sandbox (matters most for cartesian) | S | none | 16 | | |
| 17p1 | R1 research: cross-rate, tick-data cleaning, Epps/asynchrony, microstructure; read the BIS papers; event calendar for INV-4 (dates to match: the JPY runs and the 17 fault bars, CONTEXT_HANDOFF 4i.3/4i.11; exact list and plan: RESEARCH_NOTES Section 17) | S | RESEARCH_NOTES.md | 16p1 | | |
| 17p2 | R2 research: cartesian / relative strength / stat-arb, crypto cross-venue arbitrage | S | RESEARCH_NOTES.md | 17p1 | | |
| 18 | Cartesian definition (relative strength, spread mean-reversion, others; strength definitions as views; executability; crypto nodes; universe stages); no code | L | CONFIDENCE_SIZING_DESIGN.md, backtest/portfolio.py, RESEARCH_NOTES.md | 14p1, 17, 17p2 | | |
| 19 | Cartesian architecture: Strategy interface vs standalone module, multi-leg Trade/BacktestResult, RunStore schema, orchestrator, PositionManager inputs; no code | L | CONFIDENCE_SIZING_DESIGN.md, strategy/base.py, backtest/engine.py, data/run_store.py, backtest/validation_orchestrator.py | 18 | | |
| 19p1 | Universe registry: data-driven, point-in-time membership, staged growth, replaces the 16-instrument asserts | M | instrument_config.py, sandbox_config.py, run_full_sweep.py | 19 | | |
| 19p2 | R3 research: multiple testing, Sharpe and annualisation (deflated Sharpe etc.) | S | RESEARCH_NOTES.md, backtest/metrics.py | 19 | | |
| 20 | Multiple-testing control sized for the cartesian search space (design plus implementation) | M | backtest/validation_orchestrator.py, backtest/sensitivity.py, backtest/bootstrap.py, VALIDATION_HIERARCHY.md | 19, 19p2 | | |
| 21 | Alignment layer and multi-instrument data access (build); join policies, gap masks, Epps guard | M | data/store.py, backtest/portfolio.py; audit report | 19 | | |
| 21p1 | Weekend-proxy feasibility: crypto-implied weekend GBPUSD vs the Monday FX gap (H1); H2 parking-venue scoping | S | alignment layer output | 21 | | |
| 22 | Multi-leg engine/interface extension per chat-19 design; regression: existing suite and sweep byte-identical | L | backtest/engine.py, strategy/base.py, tests/test_engine.py, tests/test_rescale.py | 19, 21 | | |
| 23 | Relative-strength currency ranking signal (build and first validation) | M | chat-19 design, chat-21 layer, chat-22 engine | 22 | | |
| 24 | Spread mean-reversion signal (build and first validation; residual dynamics as null benchmark) | M | as 23 | 22 | | |
| 25 | Cartesian through the hierarchy with multiple-testing control; go/no-go review (M) | L | outputs of 23, 24; validation_orchestrator.py | 20, 23, 24, 16p1 | | |
| 25p1 | Sandbox augmentation stage 2 (identified instruments incl. stablecoin pairs); new manifest (M) | M | sandbox_config.py, instrument_config.py, fetch_sandbox_data.py, ingest_kraken_gbp_csv.py | 25, 19p1 | | |
| 26 | Trade-frequency extraction (feeds debounce) | S | data/run_store.py; user pastes output | fill-in | | |
| 27 | Independent re-discovery on non-training windows (single-instrument plus cartesian candidates) (M) | M | run_full_sweep.py; outputs of 16, 25 | 25 | | |
| 28 | Base-metric re-ranking test (deciding test, CONTEXT_HANDOFF 4e) (M), PLUS annualisation-variant axis (MC-1: flag / observed / re-baseline / time-based; rank stability) | M | backtest/base_metrics.py; outputs of 16, 27 | 27 | | |
| 29 | Full de-bloat pass #2; resolve `run_validation_hierarchy_real_data.py` vs `run_full_sweep.py`; add a `--dry-run` option to `apply_patch.command` and its README (the launcher cannot pass flags today) | L | per process; utilities/apply_patch.command, utilities/apply_patch.py | 28 | | |
| 29p1 | R4 research: execution, venues, UK regulation (FCA, financial promotions) | S | RESEARCH_NOTES.md | 29 | | |
| 29p2 | Data, broker and package selection with profitability economics: which paid feeds/accounts are needed before MVP, multiple concurrent sources and their subscription overheads, break-even, what to buy when (current state: OANDA practice/basic, Kraken basic/free, no paid feeds) | M | RESEARCH_NOTES.md Sections 5-6, CONFIDENCE_SIZING_DESIGN.md Section 9; 16p1 source inventory | 16p1, 29p1 | | |
| 30 | Broker/account choice (OANDA Standard vs Core, UK crypto exchange access, min order sizes, small-account viability) and hosting (runner host vs database host) | M | CONFIDENCE_SIZING_DESIGN.md; user: budget and restrictions | fill-in | | |
| 30p1 | Live-feed sources, redundancy and licensing: at least two independent live sources, outage vs holiday policy, failover, redistribution-licence plan and cost register | M | RESEARCH_NOTES.md Sections 5-6, brokers/router.py | 30, 29p1 | | |
| 30p2 | Storage: source dimension (column vs tables vs DB per source), canonical integer `ts_utc`, per-source stamp convention, migration and new manifest | M | data/store.py, data/run_store.py, time_policy.py | 30p1, 16p1 | | |
| 31 | OANDA get_quote/place_market_order practice test (also tradeable flag, heartbeat, financing fields: INV-18) | M | brokers/oanda.py, brokers/base.py | 30 | | |
| 32 | ccxt testnet order test | M | brokers/ccxt_broker.py | 30 | | |
| 33 | Cost-model update (real fees, financing, small-account floors, real crypto spreads, same-bar-fill sensitivity) and re-run of cartesian conclusions | M | backtest/engine.py (CostModel), instrument_config.py | 30 | | |
| 33p1 | Run manifest and reproducibility: per-run metadata (commit, data hash, config versions, libraries, seeds, decision-time quotes/flags), preflight gate before backtest or live (M) | M | run_monitor.py, data/run_store.py | 33 | | |

Chats 11-13 are DONE and recorded in `CHAT_LOG.md`; chat 14 is DONE (its row is kept above for the attach/after references).

**Run-monitor adoption (from chat 12; API in `run_monitor.py`'s docstring). Rule: every new long-running script adopts it from creation.**
- ADOPTED: `run_full_sweep.py` (chat 12), `audit_sandbox_alignment.py` (chat 14; `main()` run by the user 261005: ok, 3.9 s), `audit_gate.py` and `run_residual_investigations.py` (14p2), `run_strengths.py` (14p3; full run ok, 47.4 s) and `run_strengths_by_hour.py` (14p3b); also `run_fault_experiment.py`, `run_inv4_attribution.py` and `run_detector_scan.py` (14p4) and `run_calibration_stats.py` (14p4-ii; run by the user 261015: ok, 9.0 s, 255 MB) and `run_world_experiment.py` (14p4-iii; run by the user 261018: ok, 858.8 s) and `run_runlevel_experiment.py` / `run_runlevel_report.py` (14p4-iv; run by the user 261025: smoke2 17 s, r2real 481 s, four injection paths about 7 min each, Tier 2 1,037 s, r2pol 813 s).
- Chat 13: DONE (CLI args, window/coverage notes) - re-windowed `run_full_sweep.py`. The old 2025 H2 script copy is unmonitored by definition.
- `fetch_sandbox_data.py` adopts if touched, else at its next re-run (sandbox is final, so unlikely; 25p1 may touch it).
- Chat 16: `run_out_of_time_validation.py` adopts when run (one `item` per candidate x window; `count` windows/candles).
- Chats 21-25 (cartesian): every new script and sweep adopts from creation; one `item` per pair/signal/grid combination; `count` candles and legs. The 100,000 item-line ceiling may bite; the end-record summary stays exact.
- Chats 26, 30-33 (trade frequency, `fetch_real_cost_data.py`, OANDA/ccxt tests): adopt from creation; network latency via `item`.
- Chat 29 (de-bloat #2): first resolve `run_validation_hierarchy_real_data.py` vs `run_full_sweep.py`; adopt only in the survivor. `visualize_period_comparison.py` and `ingest_kraken_gbp_csv.py`: adopt if kept and run in batch.
- P7 / L8 (decided chat 30): the always-on live/paper runner is NOT a fit (needs heartbeats/events, not one run record). Keep separate from the L6 audit log.
- Deliberately NOT adopted: `run_*_demo.py` (synthetic, seconds-long).

## 5. Parallel tracks and post-MVP placeholders

**Parallel:** regular de-bloat (documentation folds in); investor pack (plain-language explainer, flow chart, potential and base of facts, competitor and market comparison, feedback loop); research chats before major phases (R1-R4, plus placeholders for scoring/sizing, live infrastructure, risk setting, investor pack). Return language stays scaled down to potential and the training-data facts, with risk and exclusion wording; a UK financial-promotion check by a lawyer before anything leaves the circle (not legal advice).

**Post-MVP dedicated blocks (placeholders, to be scoped in a dedicated session):**
- Efficiency (parallelised sweeps, faster feeds); latency-sensitive strategies (triangular-arbitrage-style; not Python)
- Profitability (weight/parameter refinement, cost optimisation)
- Signal generation (more strategy families, regime detection / Option C)
- UX (dashboard depth, alerts, explainability)
- Security (vault, access control, dependency scanning)
- Compliance: retain a lawyer and an accountant; implement whatever they specify (FCA, UK GDPR, Consumer Duty, record-keeping)
- ISA/tax-efficient wrapper investigation with the accountant. Open flag (medium confidence, accountant to confirm): ISAs are for individuals, the project entity is a company, and ISA-eligible holdings may not include spot FX/crypto/leveraged products
- Multi-user and subscription tiers (per-user timezone selection, redistribution licences); deployment and monitoring; finer granularity and more instruments (multi-instrument alignment across OANDA/Binance/Kraken)

## 6. De-bloat schedule

Mini-pass in chat 11; full pass in chat 29 (after the cartesian go/no-go and deferred validation); then every 8 chats or at each phase boundary, whichever first; final full pass before MVP sign-off. Process: `CONTEXT_HANDOFF.md` Working Conventions.

## 7. Risk register

| Risk | Area | Severity | Status |
|---|---|---|---|
| Out-of-time validation not yet run | Accuracy | High | Chat 16 (unblocked by chat-14 fixes) |
| Multiple-comparisons inflation (80 combos; cartesian multiplies it) | Accuracy | High | Chat 20 |
| Sandbox data consumed by validation; no unseen data except forward | Accuracy | Medium-High | Chat 17 |
| Cartesian edge is a hypothesis, not evidenced; conclusions provisional until real fees | Profitability | High | Chats 25, 33 |
| Work built on single-instrument assumptions may need redoing | Efficiency | Medium-High | Rework-exposed list, Section 3 |
| Thin margins on several survivors | Profitability | High | No capital before real fees are applied |
| Real fee schedule, financing and swap costs not modeled | Profitability | High | Chats 30, 33 |
| Small-account viability (min order sizes, fee floors) | Profitability | Medium-High | Chats 30, 33 |
| No stops in the engine | Operational | High before live | L4 |
| No kill switch, idempotency or reconciliation | Operational | High before live | L2, L3, L5 |
| `Strategy` interface cannot express cross-instrument signals | Design | Medium-High | Chat 19 |
| No live-vs-backtest parity test | Accuracy | Medium-High | P12 paper trading |
| `OandaBroker.get_quote`/`place_market_order` never run | Operational | Medium | Chat 31 |
| `CcxtBroker` never run against a real exchange | Operational | Medium | Chat 32 |
| Broker/exchange UK access and legality unchecked | Regulatory | Medium | Chat 30 |
| Runner hosting undecided | Operational | Medium | Chat 30 |
| Timezone policy across data and periods | Accuracy | Medium | AUDITED chat 14; policy settled (CONTEXT_HANDOFF 4f.5); per-user display and DST test matrices ongoing |
| Same-bar-close fills: engine fills at the signal bar's close (optimistic, esp. mean-reversion and multi-leg timing) | Accuracy | Medium-High | NEW chat 14 (AR-2); sensitivity at 33 |
| Sandbox integrity manifest not generated/verified regularly (risk of NOT doing it) | Accuracy | Medium | NEW chat 14; gate before sweeps (chats marked M); regenerate after re-ingest; back up with the DB |
| Data licensing: free licences only during R&D; redistribution licence needed for subscribers/MVP | Regulatory/Cost | Medium-High | NEW chat 14; 30p1, P15b; cost register |
| Single-source dependency (FX = OANDA only; crypto = Binance, Kraken) incl. unexplained outliers | Accuracy | Medium-High | NEW chat 14; 16p1 (sandbox), 30p1 (live) |
| No `source` dimension in storage (second source would overwrite) | Design | Medium | NEW chat 14; 30p2 |
| Hard-coded 16-instrument universe; no point-in-time membership | Design | Medium | NEW chat 14; 19p1 |
| Sharpe-family annualisation constant (MC-1: FX 6,048 vs observed 6,224 bars/yr) | Accuracy | Low-Medium (gates unaffected) | NEW chat 14; flagged; variants at 28 |
| Stablecoin basis (USDT~USD) unmodelled; depeg episodes in 2022-2025 data | Accuracy | Medium | NEW chat 14; 14p1, 25p1 |
| Crypto bars have bid = ask: real spreads unknown (Kraken taker 0.40% vs model 0.15%) | Profitability | Medium-High | 30, 33 (INV-8) |
| Residual-tail and stress-day causes unexplained (INV-1..7) | Accuracy | Medium | RESEARCH_NOTES 10; 14p1, 16p1, 17p1 |
| Strengths are scheme-dependent on fault bars (up to ~47 bps); 'fault' only provisionally defined; plain LOO smears faults onto innocent pairs | Accuracy | Medium-High | NEW 14p3; 14p4 (Huber, hub/arm and detectors A/B/C built: CONTEXT_HANDOFF 4i; no threshold, detector, k or scheme chosen); binding Strength fault rule (Working Conventions, CONTEXT_HANDOFF 4h) |
| Detector thresholds and the synthetic world's assumptions (noise ratio, wide-fault error ratio, stale signature, independent pair spreads) are unvalidated on real data; persistent day-long JPY-arm offsets (4-9 bps) are unexplained and invisible to a 10 bps per-bar flag | Accuracy | Medium | NEW 14p4; 14p4-iii, 14p4-iv, 16p1 |
| Real bars differ from the parametric world's assumptions (noise ratio about 0.065 x spread vs 0.15 assumed; absolute noise roughly constant across pairs at rollover; LOO tails p99 / MAD sigma 6.5-6.9 and kurtosis 7-29 after masking; pair spreads correlated 0.5-0.7 vs independent): every synthetic detection rate, false rate and strength error in CONTEXT_HANDOFF 4i.6/4i.10 comes from a misspecified world and is NOT calibrated to real data | Accuracy | Medium | NEW 14p4-ii (CONTEXT_HANDOFF 4j.3, 4j.4); 14p4-iii |
| Equal-and-opposite faults on the two series legs of one arm cancel in the arm path and are invisible to every residual-based detector and to hub/arm (only detector C sees them); in two-fault bars plain LS-LOO finds both faulted arms only about 50% of the time (1/spread^2 + Huber k 1.0: 0.83-0.93) | Accuracy | Medium | NEW 14p4-iii (CONTEXT_HANDOFF 4k.6); second source (16p1) is the structural fix |
| Block resampling cannot reproduce bars next to gaps (Sunday opens), the 9 degraded bars or anything beyond one stretch (max 120 bars); overlapping blocks need block-aware CIs; the real JPY runs are masked out of the reference population | Accuracy | Low-Medium | NEW 14p4-ii (CONTEXT_HANDOFF 4j.5); 14p4-iii, 14p4-iv |
| Numpy path for a much larger universe not built (pure Python: 47 s for 16 instruments, 3 schemes, oracle on) | Efficiency | Low-Medium now, grows | NEW 14p3; 14p5 |
| Rebalance-order slippage not modeled distinctly | Accuracy | Medium | After sizing wiring |
| Secrets via plain env vars | Security | Low now, grows | L13 |
| No audit trail of live orders | Security/Regulatory | Medium | L6 (`fills` table is the foundation) |
| Unpinned `requirements.txt`; no CI | Security/Efficiency | Low-Medium | L13, L14 |
| Chat context load (whole repo per chat) on a free plan | Efficiency | Medium | Attachment lists per chat (Section 4) |
| Investor return language could count as a financial promotion | Regulatory | Medium | Lawyer check |

## 8. Also worth doing (unscheduled, cheap)

Pin requirements (L13); calendar-quarter/custom/regime-labeled periods in `periods.py` (with an optional `tz` resolved to UTC); half-open interval convention for the tick/live era; volatility-bucket colouring and instrument-by-era heatmap in period-comparison visualisations; minimal live dashboard once sizing exists (`period_comparison.py` is already split for reuse); occasional `coverage.py` branch-coverage run on the user's machine.

## 9. Deferred capabilities (cannot be done now - data or infrastructure missing)

| Capability | Why not now | How / when to plug |
|---|---|---|
| Financing / swap / rollover costs | No history in the sandbox | OANDA financing fields (verify, 31); policy-rate carry approximation; chat 33 (L9) |
| Forward points / cross-currency basis (interest-rate parity effects) | Needs forward data (paid) | Spread widening as a proxy now; paid data post-MVP |
| Depth, last look, latency | Bars carry none | Paper trading (P12) measures them |
| Tick timestamps (true close time, async skew) | Bars only | Dukascopy ticks (16p1 / 17p1) |
| Direct USDT/USD basis | No such quote in the sandbox | Kraken stablecoin pairs (INV-16, 25p1) |
| Independent QC | Single source | 16p1 |
| Real crypto spreads | bid = ask | Order-book/fee data (30, 33) |
| Holiday ground truth | Inferred from data only | OANDA schedules, Internet Archive (16p1, 30p1) |
| Tradeable flag / heartbeat for live outage detection | Never tested | Chat 31 (INV-18) |
