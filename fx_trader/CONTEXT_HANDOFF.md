# CONTEXT HANDOFF - FX/Crypto Trading Analysis Project

Read this before touching the code. Trimmed to current state only - prior
sessions' blow-by-blow narrative has been cut; only decision-relevant facts
kept. GitHub sync is pull-only - see root `README.md`.

**Jurisdiction**: UK-registered entity, UK law (FCA, financial promotions,
UK GDPR). Core currencies: GBP (primary), USD, EUR represented.

**SECTION 4.1'S BLOCKER IS CLEARED - see Section 4d.** All five strategies
have now been run against the real 16-instrument sandbox. Read Section 4d
before starting Phase 1 Step 2 (scoring engine) of
[`CONFIDENCE_SIZING_DESIGN.md`](CONFIDENCE_SIZING_DESIGN.md) - real weights
now exist to build against, and a real defect in the Section 4 strawman
weighting formula was found and fixed this session (also Section 4d).

---

## Interrogation — how we work together across chat instances

Process, not trading system - read before anything else, since it governs
how the rest of this handoff should be used.

**Turn-pacing signal (adopted this session, refined after a real failure)**:
for a large multi-part request spanning several replies, a reply that has
NOT finished the full original ask ends with an explicit status line -
what's done, what's next - so the person can tell "still working, say
continue" apart from a silent processing cutoff. **Never end a turn with
no closing text at all** - a turn that stops right after a tool call with
nothing said is indistinguishable from a cutoff and defeats the whole
point of this protocol; say something, even one line. Only a reply that
has genuinely finished EVERYTHING asked (not just its own chunk) ends
with the literal word `FNORD` on its own line - "I'm done, your turn,"
borrowed from walkie-talkie "over" conventions. `FNORD` and "still
working, here's my status" are the only two valid endings - never
neither. Two honest caveats, stated rather than glossed over: (1) a hard
length cutoff mid-reply would also cut off the marker - its absence isn't
proof of failure, only its presence is proof of real completion; (2) this
is pacing for one instruction, not a standing persona change - say it
again if a future session needs it.

**Doc-bloat discipline**: these docs grow session over session by default;
the standing goal is dense and current, not narrative history. Edit/
replace a stale section rather than append a new one on top of it. Prefer
one canonical script over near-duplicate variants (this session replaced
a one-off `fetch_sandbox_data_window2.py` with a generalized
`fetch_sandbox_data.py` rather than keeping both). When a real-data
results section would otherwise grow every session, consider a dated
results-log file instead of bloating the main doc further.

**Correcting earlier claims, precisely, not silently**: when new evidence
contradicts an earlier session's summary, fix it explicitly with the new
evidence (see Section 4d's addenda for an example: `MacdStrategy`/
`BollingerBandsStrategy` were mischaracterized as trade-count-constrained
on GBP_USD/BTC_USDT last session - they weren't, only `RsiStrategy`/
`RsiMacdConfluenceStrategy` were, and only partially). A stale, uncorrected
claim misleads the next session more than an honest "not yet known."

---

## 1. OBJECTIVE

Python system analysing historical/live FX + crypto price data to find the
optimum moment to trade, via multiple complementary analytical processes
(not one signal). Near-term: a thoroughly-tested single-user pipeline.
Long-term: multi-user subscription product, UK compliance as a first-order
constraint throughout.

## 2. KEY DECISIONS (settled - re-open only with a genuinely new reason)

- **Python throughout**, not Rust - bottleneck is network I/O, not CPU.
- **SQLite (local/test) + raw psycopg2 (cloud Postgres)**, not SQLAlchemy.
  Snowflake Postgres is the chosen cloud DB - plain `postgresql://` URL,
  zero code changes needed.
- **The frozen sandbox dataset stays out of any cloud DB, forever** - static
  reference data, no need for cloud infra.
- **`BrokerAdapter` interface** - every broker/exchange implements
  `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`.
  OANDA (FX) + ccxt (crypto, currently Binance/Kraken).
- **`CostModel`** supports pip-based (FX) and percentage-based (crypto)
  costs, additively.
- **Validation hierarchy** (`VALIDATION_HIERARCHY.md`) is an explicit,
  ordered, cost-tiered gate on what gets persisted.
- **Cost-tier and service-tier are separate axes** - "how expensive is this
  to compute, and at which validation stage" vs. "which bronze/silver/gold
  subscriber gets to see it" are independent decisions, deliberately kept
  in separate files (`VALIDATION_HIERARCHY.md` vs. `backtest/service_tiers.py`).
- **Holzmann's "Power of Ten" coding rules apply to all code in this
  project (and are a standing rule across all chats, not project-specific):**
  linear control flow (max 2 nesting levels); every loop has an explicit
  ceiling; close every resource, including on error paths; one function
  does one job, fits on one screen; >=2 assertions per function; never
  swallow an error (no bare `except: pass`); zero warnings tolerated.

## 3. VERIFIED VS. NOT

Build sandbox has no network access - anything needing OANDA/an exchange/a
real cloud DB is written carefully but untested by Claude directly; the
person re-runs and reports back.

| Component | Status |
|---|---|
| Backtest engine, cost math, metrics, walk-forward, sensitivity, bootstrap | Run + hand-verified against manually computed numbers. **This session: `backtest/engine.py`'s `Trade`/`BacktestResult` redesigned for weighted-average-entry partial fills (Phase 1 Step 1) - full 9-module regression suite passes unchanged, plus the entire 80-run real-data sweep re-verified byte-identical. New `rescale_trade()` capability unit-tested (`tests/test_rescale.py`) but not yet wired into any live code path.** |
| Extended metrics, `rolling.py`, `portfolio.py`, extended `bootstrap.py`, `service_tiers.py`, `indicators.py`, `RunningSmoothedAverage` | Run + hand-verified in earlier sessions - see prior session notes if the detail is needed |
| `RsiStrategy` / `MacdStrategy` / `RsiMacdConfluenceStrategy` / `BollingerBandsStrategy` | Hand-verified against synthetic data in an earlier session (exact timing checks, independent-reference cross-checks). **This session: all four run against all 16 real sandbox instruments through the full Tier 0-4 hierarchy - see Section 4d for results.** |
| `SmaCrossoverStrategy` real-data run | Prior session: 0/16 Tier 4 survivors. **This session: re-run as part of the 5-strategy sweep, same result (0/16) - see Section 4d.** |
| `validation_orchestrator.py` Tier 0-4 gating logic | Exercised this session across 80 real (strategy, instrument) combinations, 186.6s total - see Section 4d. Gating logic itself unchanged from prior sessions. |
| `FxStore` / `RunStore` on SQLite | Run - round-trip, upsert-not-duplicate, range filtering verified |
| `FxStore` / `RunStore` on Postgres/Snowflake Postgres | Never connected to a real server |
| `OandaBroker.fetch_candles` | Run against a real practice account - 2 bugs found and fixed (timestamp format needs literal `Z`, not `+00:00`; `count` must not be sent alongside both `from`+`to`) |
| `CcxtBroker.fetch_candles` vs Binance | Run - exact expected candle counts, real prices |
| `CcxtBroker.fetch_candles` vs Kraken (live API) | Run - confirmed real limitation: only serves a rolling recent window, not arbitrary history. Worked around via `ingest_kraken_gbp_csv.py` |
| `OandaBroker.get_quote` / `place_market_order` | **Never run - elevated suspicion** |
| `CcxtBroker` against a real exchange (not fake) | Never run |

## 4. CURRENT STATE

**Sandbox dataset**: `data/sandbox_2025h2.db`, 2025-07-01 to 2025-12-31,
16/16 instruments confirmed real and cross-validated. 8 FX (OANDA, 3,141
candles each), 4 USD-crypto (Binance, 4,416 each), 4 GBP-crypto (Kraken
CSV, 4,409-4,410 each).

Extended performance/risk metrics, rolling diagnostics, portfolio
correlation tooling, and service-tier entitlement filtering were all built
in earlier sessions - see `VALIDATION_HIERARCHY.md` for the full metrics
list and `README.md` for the module layout. Not repeated here.

## 4b. RSI / MACD STRATEGIES (built in an earlier session)

Four strategies beyond SMA crossover: `RsiStrategy` (Wilder RSI
oversold/overbought reversal), `MacdStrategy` (MACD crossover with optional
RSI-confirmation filter), `RsiMacdConfluenceStrategy` (RSI and MACD fire
independently, combined only on agreement within a confirmation window -
composes real standalone `RsiStrategy`/`MacdStrategy` instances rather than
one filtering the other), `BollingerBandsStrategy` (band-reversal
mean-reversion). All hand-verified against synthetic data with independent
reference implementations - see `README.md`'s Strategies section for
per-strategy detail. See Section 4d below for their real-data results.

## 4c. CONFIDENCE-WEIGHTED MULTI-STRATEGY SIZING - DESIGN ONLY, NOT BUILT

A long discovery/discussion session (no code changes) worked out how the
five strategies from Section 4b should work TOGETHER: continuous
confidence scoring from multiple strategies' current stances (not raw
event signals), weighted by each strategy's OWN validation-hierarchy
results per instrument, driving CONTINUOUS position rescaling
(weighted-average-entry partial fills, not fixed-size open/close), across
instruments, in a way that has to work identically live.

**Full spec: [`CONFIDENCE_SIZING_DESIGN.md`](CONFIDENCE_SIZING_DESIGN.md).
Read it before touching anything described above - do not start from this
summary alone.**

Section 4.1's real-data blocker (weighting requires real validation-
hierarchy results, which didn't exist) is now cleared - see Section 4d.
Real fee schedules for `CostModel` (Section 9.1) remain unresolved - see
Section 6 below, still needs your input (OANDA account type, real API
credentials), and needs to run outside this sandbox regardless (no network
access here).

## 4d. REAL-DATA VALIDATION HIERARCHY RUN (this session)

**What ran**: all 5 strategies x all 16 real sandbox instruments through
the full Tier 0-4 hierarchy - 80 combinations, 186.6s total, no network
required (pure backtest against the local sandbox DB). Param grids (none
of these existed before this session - designed fresh, kept modest to stay
well under `MAX_GRID_COMBINATIONS=200`):

| Strategy | Grid | Size |
|---|---|---|
| SmaCrossoverStrategy | fast_period[5,8,10,12,15,20] x slow_period[20,25,30,40,50,60] | 36 (unchanged from prior session) |
| RsiStrategy | period[10,14,20] x oversold[20,25,30] x overbought[70,75,80] | 27 |
| MacdStrategy | fast[8,12] x slow[21,26] x signal[7,9] x require_rsi_confirmation[T,F] | 16 |
| RsiMacdConfluenceStrategy | confirmation_window[0,3,5,10,20] | 5 |
| BollingerBandsStrategy | period[10,15,20,25] x num_std[1.5,2.0,2.5] | 12 |

Same cost models / periods_per_year / TARGET_NOTIONAL sizing convention as
the original SMA-only run (`run_validation_hierarchy_real_data.py`) - see
that file. Driver: `run_full_sweep.py` (new this session, not yet merged
into `run_validation_hierarchy_real_data.py` - see Section 6).

**Result: 7 (strategy, instrument) pairs cleared Tier 4** - the first Tier
4 survivors this project has ever produced (prior SMA-only run: 0/16).

| Strategy | Instrument | Tier 4 survivors | Best CI-90 lower bound |
|---|---|---|---|
| RsiStrategy | LTC/GBP | 2 | +5.62% |
| RsiMacdConfluenceStrategy | LTC/GBP | 1 | +4.31% |
| RsiMacdConfluenceStrategy | LTC/USDT | 3 | +3.39% |
| RsiStrategy | USD_JPY | 3 | +0.73% |
| RsiStrategy | LTC/USDT | 2 | +1.12% |
| RsiStrategy | GBP_JPY | 4 | +0.14% |
| RsiMacdConfluenceStrategy | GBP_JPY | 2 | +0.13% |

Only mean-reversion strategies (RSI, RSI+MACD confluence) survived, only
on JPY crosses and Litecoin pairs. `SmaCrossoverStrategy`: 0/16 (consistent
with the prior session). `MacdStrategy`: 0/16 - reached Tier 3 repeatedly
(e.g. USD_JPY: 12->11->6 survivors through Tiers 1-3) but never cleared
bootstrap. `BollingerBandsStrategy`: 0/16 - same pattern, reached Tier 3 six
times, zero Tier 4 passes. Persisted survivors: `data/full_sweep_runs.db`
(not yet in the repo - see Section 6).

**Section 4.2 checks against `CONFIDENCE_SIZING_DESIGN.md`:**

1. *Real correlation vs. the synthetic-data numbers in Section 3*: holds up
   closely. SMA vs. Bollinger averaged **-0.59** across all 16 instruments
   (synthetic: -0.61). RSI vs. Bollinger averaged **+0.39** (synthetic:
   +0.34). MACD stayed the least-correlated strategy with everything else
   (avg 0.08-0.27), matching the synthetic "oddly uncorrelated" read. The
   trend-vs-reversion opposition Section 3 leaned on is not a synthetic-data
   artifact.
2. **New finding, not in the design doc**: RSI vs. RsiMacdConfluenceStrategy
   averaged **+0.65** correlation (up to +0.82 on LTC/GBP) - expected,
   since Confluence literally embeds RSI's own signal as half its logic,
   but it means these two are not independent votes. Section 1's
   "don't double-allocate correlated instruments" principle needs to also
   apply cross-strategy, not just cross-instrument - not currently in the
   design. Flagged for Section 6 (pluggable confidence modules) when that's
   built.
3. **Real defect found and fixed in the Section 4 strawman
   `tier_multiplier` formula**: as originally sanity-checked,
   `BollingerBandsStrategy`/LTC_GBP - REJECTED at Tier 4 - scored a higher
   strawman weight (9.96, using its raw uncorrected Tier-1 grid-winner
   return as base_metric) than `RsiStrategy`/LTC_GBP - an ACTUAL Tier 4
   survivor (weight 5.62, using its bootstrap-corrected CI lower bound).
   Cause: candidates that reached Tier 3 but failed the Tier 4 bootstrap
   gate were falling back to the raw, uncorrected Tier-1 score instead of
   the bootstrap CI already computed (and discarded) during their Tier 4
   evaluation. **Fix**: use the same base-metric type (bootstrap CI-90
   lower bound) for every candidate that reaches Tier 3+, computed for
   every finalist regardless of individual pass/fail, not just passing
   ones. After the fix, every rejected Tier-3 finalist correctly shows a
   negative CI lower bound (clipped to weight 0) - monotonicity restored.
   **This needs to be carried into the real scoring-engine implementation
   in Phase 1 Step 2** - the strawman formula as literally written in
   `CONFIDENCE_SIZING_DESIGN.md` Section 4 has this defect baked in.
4. *Section 8 debounce / real trade frequency*: not yet assessed this
   session - per-survivor trade counts exist in `data/full_sweep_runs.db`
   but weren't extracted/summarized. Still open.
5. *Wider product/tier context*: no strategy showed an obvious fiat-vs-
   crypto split (survivors split between USD_JPY/GBP_JPY and LTC/USDT,
   LTC/GBP) - if anything, Litecoin specifically stands out, not crypto as
   a class. Worth a closer look before assuming a fiat/crypto module split
   in Section 6.

**Correction (next session)**: the smoke-test-derived claim that "RSI/
MACD/Confluence/Bollinger mostly produce too few trades on GBP_USD/
BTC_USDT to clear Tier 0's 20-trade floor" was only half right - measured
precisely: `MacdStrategy` (16/16 grid combos clear it easily, 253-439
trades) and `BollingerBandsStrategy` (12/12 clear it, 31-287 trades) never
had a floor problem on either instrument. Only `RsiStrategy` (7/27 and
14/27 combos clear it) and `RsiMacdConfluenceStrategy` (3/5 and 4/5 clear
it) are genuinely trade-count-constrained there - both are reversal-only
oscillators (fire on crossing back through a threshold, not "currently
past" it, deliberate by design - see their own docstrings), so tighter
threshold combinations naturally produce fewer qualifying crossings in a
single 4-6 month window. Not a bug; a real interaction between
conservative entry logic and a specific instrument's realized price path
in that specific window.

## 4e. THREE SUB-TASKS RESOLVED (this session)

Requested as explicit sub-tasks, each fully built, tested against real
data, and documented in its own file (kept separate from this handoff -
see the "Doc-bloat discipline" entry in Interrogation above for why):

- **Cross-strategy correlation** (`SUBTASK_CROSS_STRATEGY_CORRELATION.md`,
  `backtest/strategy_clustering.py`): both cluster/group and pairwise-
  discount mechanisms built, tested (7 unit tests), and compared head-to-
  head on real GBP_JPY data. Pairwise discount recommended - cluster/
  group was found to nearly erase genuine disagreement between a strong
  and a correlated-weaker strategy (0.0082 vs pairwise's 0.0979 in the
  disagreement case). Dynamic/rolling correlation explicitly deferred to
  a future session, not dropped.
- **Base-metric review** (`SUBTASK_BASE_METRICS.md`,
  `backtest/base_metrics.py`): all ~25 `Metrics` fields grouped into 5
  roles; 4 candidate scoring-weight blends built and tested (5 unit
  tests), including Section 5's explicitly-missing `effect_size_over_
  sample_size` candidate. Real-data comparison across all 17 survivors
  found two of the four candidates meaningfully DISAGREE with the
  currently-used bootstrap CI lower bound on ranking - no winner picked
  yet; the deciding test (re-rank against out-of-time data) is specified
  and sequenced, not resolved. Full cartesian product of all role pairs
  explicitly deferred, framework left ready for it.
- **Period-comparison visualization** (`SUBTASK_VISUALIZATION.md`,
  `backtest/periods.py`, `backtest/period_comparison.py`,
  `visualize_period_comparison.py`): three-layer architecture (period
  definition / execution / rendering), calendar-year comparison built
  first per your instruction, both static (PNG) and interactive (HTML +
  Chart.js) outputs from the same data, tied into `service_tiers.py`'s
  existing bronze/gold convention. `run_out_of_time_validation.py`
  refactored to use the same shared period-definition module instead of
  its own duplicated logic - this refactor caught and fixed a real
  period-boundary bug (see the sub-task doc). Proven end-to-end against
  real 2025 H2 data (`charts/RsiStrategy_GBP_JPY_*`); genuinely
  interesting multi-period comparisons still need `data/sandbox_
  history.db` to exist.

All three explicitly deferred dynamic/rolling correlation, the full
metric cartesian product, and regime-labeled periods to a FUTURE session
- read as "scoped out, not forgotten," each sub-task doc's own "Deferred"
section is the authoritative list.

## 5. HOLZMANN REVIEW

Applied to the full repo in a prior session - see prior session notes if
needed. This session's new file (`run_full_sweep.py`) was written to the
same standard: linear control flow, explicit ceilings where relevant,
assertions on grid size, no bare `except: pass`. Not yet merged into a
permanent script location - see Section 6.

## 6. OPEN THREADS

- **`run_full_sweep.py` needs a permanent home** - built this session as a
  one-off driver, re-run twice more since (weighting-formula fix, engine
  redesign regression proof) with byte-identical results both times. Not
  yet Holzmann-reviewed to the same depth as
  `run_validation_hierarchy_real_data.py`, not yet merged/renamed into the
  main script set, `data/full_sweep_runs.db` not yet committed. Decide
  whether it replaces or extends `run_validation_hierarchy_real_data.py`.
- **Section 4.2 point 3's weighting-formula fix needs to land in the real
  scoring engine**, not just this session's sanity-check script - see
  Phase 1 Step 2 in `CONFIDENCE_SIZING_DESIGN.md`.
- **Real trade frequency per Tier 4 survivor** (Section 4.2 point 4) -
  needed before Section 8's debounce thresholds can be set from real data
  rather than guessed.
- **Service tiers are a starting allocation, not a decision** - needs a
  dedicated conversation on the actual bronze/silver/gold product design.
- **Cross-pair/cartesian comparator + statistical pairs trading** - still
  deferred to its own future session; multi-instrument timestamp/candle-
  boundary alignment across OANDA/Binance/Kraken still unsolved.
- **Tiered subscription refresh rates** (~5min/~30sec-1min/~1sec) - not
  designed.
- `OandaBroker.get_quote` / `place_market_order` - still never run, test
  explicitly before trusting either.
- Position sizing - full design spec exists (`CONFIDENCE_SIZING_DESIGN.md`);
  Section 4.1's blocking dependency is now cleared (Section 4d). Multi-user
  entitlements, live feed, deployment infra - still open.
- Real fee schedule sourcing (Section 9.1) - blocked on your input (OANDA
  account type, real credentials), and can't run in this sandbox regardless
  (no network access here).

## 7. IMMEDIATE NEXT STEP

Per `CONFIDENCE_SIZING_DESIGN.md` Section 10's phasing plan, and
`ROADMAP.md`'s out-of-time-validation priority:

0. **Out-of-time validation tooling - built, blocked on real data.**
   `fetch_sandbox_data.py` and `ingest_kraken_gbp_csv.py` were both
   generalized this session (arbitrary/wide date range; auto-discovery of
   any number of Kraken quarterly CSV folders, not a hardcoded pair) to
   build ONE continuous `data/sandbox_history.db` rather than one file per
   window. `run_out_of_time_validation.py` auto-chunks whatever ends up
   outside the original 2025 H2 training window into 6-month blocks per
   instrument and replays all 17 persisted survivors against every block.
   **Needs you to run the two fetch scripts locally (real credentials,
   real network) and upload the resulting `data/sandbox_history.db`** -
   nothing else is blocking this from running immediately once that
   exists. See `ROADMAP.md` for the full out-of-time-validation rationale.

1. ~~Run all five strategies against real sandbox data~~ - **DONE, see
   Section 4d.** Section 4.2's weighting-formula defect was found and
   fixed in the sanity-check script; still needs to land in the real
   scoring engine (Phase 1 Step 2).
2. ~~Phase 1 Step 1: position accounting redesign~~ - **DONE this
   session.** `Trade`/`BacktestResult` now support weighted-average-entry
   partial fills via a new `Fill` dataclass and `BacktestEngine.
   rescale_trade()`; Section 9.2's min-order-size/lot-step constraints are
   built (default off - no real per-instrument limits exist yet, see
   Section 9.1). **Backward-compatibility exit criterion (Section 8.3)
   met**: full existing test suite (9 modules) passes unchanged, AND the
   entire 80-run real-data sweep (Section 4d) re-run against the
   redesigned engine and produced byte-identical Tier 4 survivors and CI
   values. A real shallow-copy bug (`copy.copy` sharing the new mutable
   `fills` list between a walk-forward carry-forward snapshot and the live
   trade) was found and fixed as part of this - see `backtest/engine.py`'s
   `run()` docstring/comment. `rescale_trade()` itself is built and unit-
   tested (`tests/test_rescale.py`) but **not yet wired into `run()`'s
   signal loop** - no strategy or signal type for "partial rescale" exists
   yet; that wiring is Phase 1 Step 3 (PositionManager), not this step.
   `RunStore`'s schema gained an additive `fills` table, verified against
   a real run (36 fills for 18 round-trip trades, exactly 2 each).
3. **Phase 1 Step 2 (next): scoring engine, shadow-computed only.** All
   three shape curves (Section 2.2) x candidate base metrics (Section 5),
   logged per candle - does NOT yet affect real trade size. Real weights
   now exist to build against (Section 4d). **Two previously-open design
   questions for this step are now resolved, see Section 4e**: cross-
   strategy correlation (pairwise discount recommended) and base-metric
   choice (4 candidates built, real-data comparison run, deciding test
   specified but not yet executed - needs out-of-time data first, see
   item 0 above). Not started.
4. Test `OandaBroker.get_quote`/`place_market_order` before trusting them -
   still unstarted.
5. Dedicated session on actual service-tier product design - still open.
6. Cross-pair/pairs-trading work - still deferred.

## 8. RISKS

Two real bugs found and fixed in `OandaBroker.fetch_candles` (Section 3) -
concrete evidence that "written to spec" isn't "correct". Apply the same
suspicion to anything still unverified in Section 3, especially
`get_quote`/`place_market_order`. The Section 4.2 weighting-formula defect
(Section 4d) is the same lesson at a different layer: a plausible-looking
strawman formula, unexercised against real numbers, silently inverted the
hierarchy's own trust ordering - always sanity-check a formula against real
results before it becomes load-bearing. Not a lawyer or financial advisor -
this project is about a sound, honest testing/execution pipeline, not
investment advice. Automation doesn't remove trading risk, it executes
decisions faster.
