# Roadmap: Multi-Year Sandbox (2022-01-01 -> latest Kraken quarter)

Draft plan for the next chat to work through, alongside `ROADMAP.md`'s
existing sequencing. Written before the larger sandbox has actually been
built (this session fixed the fetch/ingest scripts and confirmed the
current `sandbox_history.db` is misaligned - see Section 1). Nothing here
is executed yet.

## 0. What changed this session

Old: a single fixed 6-month window (`sandbox_2025h2.db`), hardcoded
`START`/`END` in `fetch_sandbox_data.py`, a hardcoded 2-quarter Kraken file
list in `ingest_kraken_gbp_csv.py`. New: `sandbox_config.py` is the single
shared source of truth - `GLOBAL_START` fixed at 2022-01-01,
`GLOBAL_END` auto-detected from whichever `kraken_csv/YYQ#/` folders
exist, output filename dynamically encodes the window (e.g.
`sandbox_22Q1to26Q1.db`). `ingest_kraken_gbp_csv.py` now also reads a
`kraken_csv/historical/` folder (Kraken's "Complete OHLCVT" bulk export)
for everything before the quarterly exports begin (23Q1).

## 1. Confirmed defect in the uploaded `sandbox_history.db`

Inspected directly this session. FX (OANDA) and USD-crypto (Binance) run
2022-01-0{1,2} through 2026-09-25 ("today"). GBP-crypto (Kraken) runs
**2023-01-01 through 2026-03-31 only** - no pre-2023 data at all (no
`historical/` folder was ever ingested), and stalled ~6 months behind
everything else (bounded by the last quarterly folder present, 26Q1).
This is exactly the misalignment sandbox_config.py's design fixes -
confirms the fetch script in the pulled repo context was still the old
single-window version, not the "already generalized" version described
from a prior chat. **Re-run both fetch scripts against the newly-created
`kraken_csv/historical/` folder before trusting any figure below.**

## 2. Immediate validation of the rebuilt dataset (before any strategy work)

1. Re-run the coverage/cross-validation check `CONTEXT_HANDOFF.md` Section
   4 originally did for the 6-month sandbox ("16/16 instruments
   cross-validated via cross-rate arithmetic") - sample-based this time
   (spot-check a handful of months per instrument), not exhaustive, given
   the much larger row count.
2. Confirm `kraken_csv/historical/`'s CSVs actually contain GBP-pair data
   back to 2022-01-01 - Kraken's GBP pairs may have listed later than the
   USD pairs, or the bulk export's own start date may not reach 2022 at
   all. **Check this before assuming the gap closes** - if it doesn't,
   `GLOBAL_START` needs moving forward to whatever Kraken GBP-crypto
   actually supports, not silently left producing a shorter GBP-crypto
   series than the other 12 instruments.
3. Gap-count check per instrument (missing hourly candles vs. expected
   count for FX's weekday-only calendar vs. crypto's 24/7 calendar) -
   `FxStore.coverage()` already gives min/max; add a count-vs-expected
   check, since min/max alone can hide internal gaps.
4. Rough compute-budget estimate before committing to a full re-sweep: the
   original 80-combination Tier 0-4 sweep took 186.6s on 6 months of data.
   ~4.25 years is roughly 8.5x the data - Tier 0/1 (`sensitivity.py`)
   scales with candle count x grid size; Tier 3 (`walk_forward.py`) adds
   more windows; Tier 4 (`bootstrap.py`) is per-finalist, not per-grid-
   point, so should scale much more mildly. Worth a short dry run on one
   instrument before running the full 16 x 5-strategy sweep.

## 3. Two-part out-of-time methodology

Both scripts already exist and are now generic-ready (this session
re-points their `SANDBOX_DB`/`HISTORY_DB` constants at
`sandbox_config.sandbox_db_path(...)` - see the session's patch list).

- **Frozen-candidate replay** (`run_out_of_time_validation.py`): the
  original 17 Tier-4 survivors, unchanged params, replayed against every
  6-month block outside the original 2025H2 training window - judged
  against the *same* gate values, no loosening. With the wider sandbox
  this now covers far more out-of-training blocks (2022 through early
  2025, plus 2026 Q1) instead of whatever partial data existed before.
- **Independent re-discovery** (`run_full_sweep.py`): point at the new
  sandbox file, re-run the full 80-combination sweep blind, compare
  survivors to the frozen-replay's PASS list by hand. New option worth
  considering given the much larger window (see Section 5, question 2):
  restrict this re-discovery pass to ONLY the non-2025H2 portion first
  (a genuine held-out discovery, not re-seeing the training data), before
  a second pass across the full combined window.

## 4. Visualization

- `visualize_period_comparison.py`'s calendar-year view was previously
  "not interesting yet" (one partial window) - now spans ~4-5 real
  calendar years, the comparison it was built for finally has data to
  show.
- New, lightweight (not full regime detection, which stays deferred per
  `CONFIDENCE_SIZING_DESIGN.md` Section 3/13): colour-code each period bar
  by a simple realized-volatility bucket, so a viewer can see at a glance
  whether a survivor held up across both calm and volatile stretches,
  without building a real regime classifier.
- New: an instrument x era grid ("profitability heatmap"), coloured by
  bootstrap CI lower bound (or another `base_metrics.py` candidate) per
  cell - surfaces the most promising (strategy, instrument, era)
  combinations visually, and gives the still-open
  `SUBTASK_BASE_METRICS.md` "don't pick a winner yet" question a second,
  independent dataset to test candidates against.
- Service-tier question (see Section 5, question 5): does this newly-
  useful long-run static view stay bronze, or does the richer historical
  comparison push toward reconsidering `PERIOD_COMPARISON_*_SERVICE_TIER`?

## 5. Other scripts/features this unlocks

- `backtest/periods.py`'s stubbed `calendar_quarter_periods()` and
  `regime_labeled_periods()` - both flagged "not yet implemented," now
  have enough real data to be worth building (regime detection itself
  stays a deferred future project; the *periods* generator is a smaller,
  separate piece).
- `SUBTASK_CROSS_STRATEGY_CORRELATION.md`'s explicitly-deferred dynamic/
  rolling correlation - the multi-year data removes the "not enough
  history" blocker, though it's still real new infrastructure to build,
  not a formula change.
- `SUBTASK_BASE_METRICS.md`'s deciding test (re-rank all four base-metric
  candidates against out-of-time data) was blocked on exactly this
  dataset - now unblocked.
- `validation_orchestrator.MAX_GRID_COMBINATIONS` (currently 200) may be
  worth raising now that there's enough data per grid point for a finer
  grid to mean something rather than mostly noise - a real, separate
  decision from `CONFIDENCE_SIZING_DESIGN.md` Section 2.4's own reason for
  eventually raising it (the combination layer's own grid).
- A genuinely fresh Tier 0-4 discovery sweep across 2022-2023 (data no
  strategy has been tuned against at all) could surface different
  survivors, not just validate the existing 17 - worth treating as its
  own discovery run in the next chat's plan, not folded silently into the
  out-of-time replay.

## 6. Open questions for the next chat

1. Does `kraken_csv/historical/` actually contain GBP-pair data back to
   2022-01-01? Confirm before trusting `GLOBAL_START`.
2. Scope of the independent re-discovery leg: full window, non-2025H2
   portion only, or both (sequenced)?
3. Does a strong new discovery from the wider window replace the original
   17 as the reference survivor set going into Phase 1 Step 2 (confidence
   sizing), or do the original 17 stay canonical with the new data used
   only to validate them?
4. Raise `MAX_GRID_COMBINATIONS` now, or leave it until Phase 1 Step 2's
   own grid forces the question?
5. `RunStore` output naming (`full_sweep_runs.db`, `validated_runs.db`) -
   should these also become window-aware/dynamic, or stay static as a
   running log across whichever sandbox window produced each entry?
6. Does the larger backtest (more trades, more years) make sourcing the
   real fee schedule (`CONFIDENCE_SIZING_DESIGN.md` Section 9.1, still
   blocked on OANDA account type/credentials) more urgent, or unchanged?
7. Compute budget for the full historical re-sweep - run the Section 2
   dry-run estimate before committing to it in the same session as
   everything else above.
