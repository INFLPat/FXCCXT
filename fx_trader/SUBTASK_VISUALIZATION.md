# Sub-task: Visualizations for Period-to-Period Comparison

Per your instruction: prioritized a FLEXIBLE PROCESS over interesting
metrics right now - the actual comparison (calendar-year, 2025 only so
far) isn't visually rich yet because only one window of real data exists,
but the mechanism is built, tested, and proven end-to-end, ready to show
a real multi-year comparison the moment `data/sandbox_history.db` exists
(`ROADMAP.md` Section 1).

## Architecture: three layers, each independently reusable

1. **`backtest/periods.py`** - period DEFINITION. `calendar_year_periods()`
   built now (your instruction: concentrate here first). Structured so
   `calendar_quarter_periods`, `financial_year_periods`, `custom_periods`,
   and `regime_labeled_periods` (trending-vs-ranging, etc.) slot in later
   as siblings returning the exact same `Period` shape - nothing
   downstream (comparison, rendering, out-of-time validation) needs to
   change when a new period type is added, only a new generator function.
   **Refactored `run_out_of_time_validation.py` to use this module's
   `fixed_month_periods()` instead of its own private chunking logic** -
   this caught and fixed a real bug in the process (see below).
2. **`backtest/period_comparison.py`** - period EXECUTION. Runs one fresh
   backtest per period (no state carried across periods - each judged
   independently), returns structured `Metrics` per period. No
   rendering code at all, so a future live dashboard
   (`ROADMAP.md` Section 5) can reuse this layer without a matplotlib/
   Chart.js dependency.
3. **`visualize_period_comparison.py`** - RENDERING, both forms, from the
   same underlying data:
   - **Static** (`matplotlib`, PNG): bronze-tier per
     `backtest/service_tiers.py`'s new `PERIOD_COMPARISON_STATIC_
     SERVICE_TIER` constant - cheap, no JS, default.
   - **Interactive** (self-contained HTML + Chart.js via CDN):
     `PERIOD_COMPARISON_INTERACTIVE_SERVICE_TIER = GOLD` - richer (full-
     resolution equity curve up to 2000 downsampled points, toggle
     between views), same tiering pattern already used for rolling/
     portfolio diagnostics, not a new concept.

## Two views, matching "long phases and short phases"

- **Short phases**: one bar-group per `Period` - total_return_pct and
  max_drawdown_pct side by side. Currently ONE bar group (2025 H2 is the
  only window loaded) - genuinely not an interesting comparison yet, and
  said so plainly rather than dressed up.
- **Long phases**: one continuous equity curve + rolling Sharpe across
  the ENTIRE available history, ignoring period boundaries - reuses
  `backtest/rolling.py` rather than reimplementing it. This one IS
  already informative even with one window: `RsiStrategy`/GBP_JPY's
  rolling Sharpe swings between -4 and +6 across the 6 months (see
  `charts/RsiStrategy_GBP_JPY_long_run.png`) - a genuinely useful, mildly
  concerning finding (this survivor is NOT steadily positive, it whipsaws
  hard) that the single CI-lower-bound number from the original sweep
  doesn't show on its own.

## A real bug the refactor caught

Building `calendar_year_periods()` and testing it against `fixed_month_
periods()` for cross-generator consistency (same `tests/test_periods.py`
discipline as every other module this session) surfaced a genuine
boundary inconsistency: one generator ended periods at `23:59:59`, the
other at exact midnight of the next period - which could have silently
double-counted a candle landing exactly on a year boundary. Fixed with a
shared `_end_of_period()` helper both generators now use identically.
Concrete value of the "test everything against something else" discipline
established this session, not just a code-quality nicety.

## Proven end-to-end (`charts/RsiStrategy_GBP_JPY_*`)

Ran `visualize_period_comparison.py` against real 2025 H2 data - all
three outputs generated and visually verified: `..._period_comparison.png`
(one bar group, as expected), `..._long_run.png` (the Sharpe-swing finding
above), `..._interactive.html` (64KB, Chart.js loads correctly, both tabs
render, 3141-point equity curve embedded).

## Testing plan (built)

- `tests/test_periods.py` (4 tests): partial-year edge handling,
  no-coverage edge case, cross-generator consistency (caught the boundary
  bug above), training-window exclusion correctness.
- End-to-end run against real data (this document's "Proven end-to-end"
  section) - visual inspection of both PNGs, structural validation of the
  HTML (Chart.js script tag present, embedded JSON parses, correct point
  count).
- **Not yet done**: a real multi-period visual check - blocked on the same
  `data/sandbox_history.db` as every other out-of-time item this session.
  Re-run `visualize_period_comparison.py` unchanged once it exists.

## Deferred to a later session (per your instruction)

- `calendar_quarter_periods` / `financial_year_periods` - trivial once
  `calendar_year_periods` is proven; same pattern, different chunk size.
- `custom_periods` - trivial wrapper around user-supplied (label, start,
  end) tuples.
- `regime_labeled_periods` (trending-vs-ranging, volatility regime, etc.)
  - genuinely new detection logic, not just a chunking rule. Explicitly
    NOT attempted here, consistent with `CONFIDENCE_SIZING_DESIGN.md`
    Section 3/13's own deferral of regime detection as a separate future
    project.
- A real live dashboard consuming `period_comparison.py`'s data layer
  directly (`ROADMAP.md` Section 5) - the separation between execution
  and rendering built this session is specifically what makes that
  addition cheap later.
