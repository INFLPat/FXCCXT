# CONTEXT HANDOFF - FX/Crypto Trading Analysis Project

Read this before touching the code. Trimmed to current state only - prior
sessions' blow-by-blow narrative has been cut; only decision-relevant facts
kept. GitHub sync is pull-only - see root `README.md`.

**Jurisdiction**: UK-registered entity, UK law (FCA, financial promotions,
UK GDPR). Core currencies: GBP (primary), USD, EUR represented.

**A NEW DESIGN SESSION HAPPENED AFTER THE 4b STRATEGIES WORK, BEFORE ANY OF
IT WAS BUILT - see Section 4c and read
[`CONFIDENCE_SIZING_DESIGN.md`](CONFIDENCE_SIZING_DESIGN.md) in full before
starting any work on multi-strategy combination, position sizing, or
confidence scoring. That document is the actual spec; this file only
summarizes it.**

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
- **Cost-tier and service-tier are separate axes** (added this session) -
  "how expensive is this to compute, and at which validation stage" vs.
  "which bronze/silver/gold subscriber gets to see it" are independent
  decisions, deliberately kept in separate files (`VALIDATION_HIERARCHY.md`
  vs. `backtest/service_tiers.py`) so one can change without the other. See
  Section 4 below.
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
| Backtest engine, cost math, metrics, walk-forward, sensitivity, bootstrap | Run + hand-verified against manually computed numbers |
| Extended metrics (Sortino/Calmar/drawdown duration/expectancy/VaR-CVaR/tail ratio/skew-kurtosis/Kelly/exposure/cost drag/buy&hold - `metrics.py`) | Run + hand-verified this session against an independently-constructed deterministic trade sequence (`tests/test_metrics_extended.py`) - exact assertions where the math is rational, independent-reference-formula assertions where it isn't (Sortino/Omega/Ulcer/skew/kurtosis) |
| `rolling.py` | Run + hand-verified this session (`tests/test_rolling.py`) - cross-checked against `metrics.py`'s whole-curve Sharpe/Sortino at window==full-length as an equivalence proof, not just hand-typed numbers |
| `portfolio.py` | Run + hand-verified this session (`tests/test_portfolio.py`) - identical/negated series give exact 1.0/-1.0 correlation and 1.0 diversification ratio; a distinct-series case checked against an independently-written reference Pearson/variance implementation |
| Extended `bootstrap.py` `TRACKED_METRICS` | Run + hand-verified this session (`tests/test_bootstrap_extended.py`) - proves which new metrics are order-invariant under shuffle (multiset-only) vs. order-dependent, extending the pre-existing shuffle-invariance proof |
| `service_tiers.py` filtering | Run + hand-verified this session (part of `test_metrics_extended.py`) - cumulative bronze subset-of-silver subset-of-gold, fails closed for unclassified fields |
| `validation_orchestrator.py`'s new rolling+CI wiring | Smoke-tested this session directly against `_tier4_gate()` with a deterministic positive-expectancy repeating trade pattern - confirms the wiring is structurally correct (rolling populated, CI dict built for every `TRACKED_METRICS` entry). NOT re-run against the full real 16-instrument sandbox this session - existing Tier 0-3 gating logic is unchanged from what already ran there (see Section 4 below), so that result still stands as-is |
| `indicators.py` (`ema()`/`rsi()`/`macd()`) | Run + hand-verified this session - exact fraction checks on tiny series, plus an independently-restructured reference `macd()` implementation cross-checked over a longer synthetic series (`tests/test_indicators.py`) |
| `RunningSmoothedAverage` (streaming primitive) | Run + hand-verified this session - checked against `ema()`/`rsi()` batch output at EVERY step of a 200-candle series, both EMA and Wilder-alpha modes |
| `RsiStrategy` / `MacdStrategy` | Run + hand-verified this session - `last_rsi`/`last_macd`/`last_signal` matched batch reference at every step over synthetic series; hand-traced small-period crafted price series confirmed exact BUY/SELL timing; RSI-confirmation filter tested directly against both suppression directions; `BacktestEngine` integration smoke-tested. NOT YET run against real sandbox data through the validation hierarchy - see Section 6 |
| `RsiMacdConfluenceStrategy` | Run + hand-verified this session - `_combine()`/`_within_window()` tested directly (no price data needed) across same-candle/within-window/outside-window/one-sided/quiet-candle cases; independence proven by matching composed sub-strategies bit-for-bit against standalone instances across 400 synthetic candles; `BacktestEngine` integration and `reset()` full-state-clear both smoke-tested. Also not yet run against real sandbox data |
| `BollingerBandsStrategy` | Run + hand-verified this session - `last_middle`/`last_upper`/`last_lower` matched batch `bollinger_bands()` at every step over 300 synthetic candles; a crafted crash-and-recovery series' exact signal timing checked against an INDEPENDENT zone classification (Python's own `statistics` module, a second separate reversal-detection pass) rather than hand-picked indices; `go_short=False` and `BacktestEngine` integration both verified. Also not yet run against real sandbox data |
| `FxStore` / `RunStore` on SQLite | Run - round-trip, upsert-not-duplicate, range filtering verified |
| `validation_orchestrator.py` (Tier 0-4 core gating logic) | Run against all 16 real sandbox instruments in a prior session - see Section 4 |
| `OandaBroker.fetch_candles` | Run against a real practice account - 2 bugs found and fixed (timestamp format needs literal `Z`, not `+00:00`; `count` must not be sent alongside both `from`+`to`) |
| `CcxtBroker.fetch_candles` vs Binance | Run - exact expected candle counts, real prices |
| `CcxtBroker.fetch_candles` vs Kraken (live API) | Run - confirmed real limitation: only serves a rolling recent window, not arbitrary history. Worked around via `ingest_kraken_gbp_csv.py` |
| `FxStore` / `RunStore` on Postgres/Snowflake Postgres | Never connected to a real server |
| `OandaBroker.get_quote` / `place_market_order` | **Never run - elevated suspicion** |
| `CcxtBroker` against a real exchange (not fake) | Never run |

## 4. CURRENT STATE

**Sandbox dataset**: `data/sandbox_2025h2.db`, 2025-07-01 to 2025-12-31,
16/16 instruments confirmed real and cross-validated. 8 FX (OANDA, 3,141
candles each), 4 USD-crypto (Binance, 4,416 each), 4 GBP-crypto (Kraken
CSV, 4,409-4,410 each).

**Validation hierarchy result (prior session, unchanged by this one)**:
0/16 instruments produced a Tier 4 survivor with `SmaCrossoverStrategy` -
every apparent in-sample edge failed the bootstrap gate. See
`VALIDATION_HIERARCHY.md`'s Status section and Section 7 below - the next
step is a different strategy family, not more metrics on this one.

**This session: extended performance/risk metrics + tiering flexibility.**
Added, in cost-tier order (see `VALIDATION_HIERARCHY.md` for the full
breakdown and what was deliberately deferred):
- `metrics.py`: ~20 new fields (Sortino, Calmar, drawdown duration, Ulcer
  Index, trade expectancy, payoff ratio, streaks, Kelly, VaR/CVaR, tail
  ratio, skew/kurtosis, exposure %, cost drag, avg trade duration, buy &
  hold benchmark) - all free, computed alongside the existing metrics for
  every Tier 0/1 candidate. Reporting only - none of these are new gates
  (see `VALIDATION_HIERARCHY.md`'s note on why not, yet).
- `engine.py`: additive `BacktestResult.periods_in_market` field (needed
  for exposure %) - existing behavior/tests unaffected, default 0.
- `rolling.py` (new file): rolling Sharpe/Sortino/max-drawdown. Restricted
  to Tier 3 finalists in the orchestrator, never the full Tier 1 grid -
  cheap per-candidate, expensive to store for 200+ grid points.
- `bootstrap.py`: `TRACKED_METRICS` extended from 5 to 22 fields, riding
  the existing resample/shuffle loop - free. Annualized ratios
  (Sortino/Calmar/Omega) and `exposure_pct` deliberately excluded - see
  `bootstrap.py`'s docstring for why (same reasoning that already excluded
  an annualized Sharpe here).
- `portfolio.py` (new file): cross-instrument correlation matrix +
  equal-weight portfolio Sharpe/diversification ratio. Deliberately kept
  OUTSIDE the per-instrument validation hierarchy - needs multiple
  instruments' aligned return series together, a different shape of
  question. Nothing downstream of Tier 4 exists yet to feed it (0/16
  survivors), but it's directly usable now against raw instrument returns.
- `service_tiers.py` (new file): bronze/silver/gold entitlement registry,
  decoupled from cost-tier on purpose - see Section 2 and
  `VALIDATION_HIERARCHY.md`. **Starting allocation, not a decision** -
  every metric got a reasonable-guess tier; revisit once the actual
  subscription tiers are designed in a dedicated conversation.
- `validation_orchestrator.py`: Tier 4's existing full-history re-run of
  each finalist now also feeds `rolling.py` (no extra backtest run) and
  persisted `metrics` carries bootstrap CI for every `TRACKED_METRICS`
  entry, not just the original three. Tier 0-3 gating logic byte-for-byte
  unchanged.

## 4b. RSI / MACD STRATEGIES (added this session, after the metrics work)

Second new-strategy family since SMA crossover was confirmed dead on real
data (Section 4). `strategy/indicators.py` provides both a batch/reference
implementation (`ema()`, `rsi()`, `macd()`) and `RunningSmoothedAverage`,
the O(1)-per-candle streaming primitive both new strategies actually use
in `on_candle()` (EMA is alpha=2/(period+1), Wilder's RSI smoothing is the
exact same primitive with alpha=1/period - not a separate algorithm).

- **`RsiStrategy`**: enters on a RSI reversal out of oversold/overbought
  (crossing back through the threshold), not "RSI is currently past 30/70"
  - deliberately avoids buying into a still-falling move.
- **`MacdStrategy`**: MACD crossover, with an optional RSI-overextension
  filter on by default (`require_rsi_confirmation`) - the textbook "RSI +
  MACD confirmation" combination, toggleable off for pure MACD so the
  filter's real value can be tested through the hierarchy rather than
  assumed. `run_macd_demo.py` runs both variants back to back for a direct
  comparison.
- **`RsiMacdConfluenceStrategy`** (added immediately after, on request -
  the person specifically wanted RSI/MACD as genuinely independent
  signals, not one filtering the other): composes real, standalone
  `RsiStrategy`/`MacdStrategy` instances and only combines their outputs
  AFTER each has decided independently - agreement within
  `confirmation_window` candles fires the combined signal; neither
  indicator is "primary". Independence is proven in
  `tests/test_rsi_macd_confluence.py` by matching the composed
  sub-strategies' internal state bit-for-bit against standalone instances
  run on the same data, not just asserted. `run_confluence_demo.py` prints
  trade count vs. window size (0 through 20) - window=0 is extremely
  restrictive by construction (1 trade in 3000 synthetic candles); this is
  expected, not a bug, and widens quickly as the window loosens.
- **`BollingerBandsStrategy`** (added right after, same session): band-
  reversal mean-reversion, same "wait for the reversal" philosophy as
  RsiStrategy but with volatility-adjusted bands (SMA ± population std
  dev) instead of fixed thresholds. `indicators.bollinger_bands()` is the
  batch reference, itself checked against Python's own `statistics`
  module (a genuinely different code path) in `tests/test_indicators.py`.
  Worth knowing before tuning `period`: the current candle is included in
  its own window, so one sharp move partly widens the band around itself -
  documented in the module's own docstring rather than left as a surprise.

Every streaming value (`last_rsi`/`last_macd`/`last_signal`, all public,
exposed for inspection) is cross-checked against the batch reference at
EVERY step in `tests/test_indicators.py`/`test_rsi_strategy.py`/
`test_macd_strategy.py`, not just at one point - this caught a real bug
during development (see Section 3: `last_macd` wasn't being updated during
the MACD signal-line's own warmup window, even though the underlying value
was already correctly computed - fixed before this was handed off).

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

The single most important thing from that document to internalize
immediately: **the weighting scheme is blocked on real data that doesn't
exist yet.** All five strategies (Section 4, Section 4b) have only ever
been run against synthetic data for correctness-verification - NONE have
been run through `run_validation_hierarchy_real_data.py` against the real
16-instrument sandbox. Confidence weights sourced from "the validation
hierarchy's own results" mean nothing until that run happens. See
`CONFIDENCE_SIZING_DESIGN.md` Section 4.1 - this is now the actual
critical path, promoted from Section 7's long-standing "next step" below.

Also settled in that session, real fee schedules for `CostModel` (OANDA
Core vs. Standard commission, Binance/Kraken maker/taker tiers) need to be
sourced from each broker/exchange's own account-specific data, not
hardcoded - `CONFIDENCE_SIZING_DESIGN.md` Section 9.1 has current public
rates (researched, not account-specific) and the concrete plan to fetch
real ones, mirroring how `fetch_sandbox_data.py` built the real sandbox.

## 5. HOLZMANN REVIEW

Applied to the full repo in a prior session (nesting depth, loop ceilings,
resource handling, assertion coverage, no bare `except: pass`) - see prior
session notes if needed. This session's new files
(`rolling.py`/`portfolio.py`/`service_tiers.py`) and edits
(`metrics.py`/`bootstrap.py`/`engine.py`/`validation_orchestrator.py`) were
written to the same standard from the start: linear control flow, explicit
loop ceilings where relevant (rolling.py's window loop is bounded by
`len(equity_curve)`, same bound every other engine/metrics loop already
respects), >=2 assertions per non-trivial function, real exception handling
(a couple of narrow `except (ValueError):` blocks in `metrics.py`'s
timestamp parsing, never bare).

## 6. OPEN THREADS

- **Next strategy family** - done: `RsiStrategy`, `MacdStrategy`,
  `RsiMacdConfluenceStrategy`, and `BollingerBandsStrategy` all built and
  hand-verified against synthetic data (Section 4b). Running them against
  the real 16-instrument sandbox is no longer just "the natural next
  step" - it's Phase 1's hard blocking dependency, see Section 4c and
  `CONFIDENCE_SIZING_DESIGN.md` Section 4.1.
- **Service tiers are a starting allocation, not a decision** - needs a
  dedicated conversation on the actual bronze/silver/gold product design;
  `service_tiers.py` is built to make that revision cheap (edit one dict,
  touch no computation code) whenever that conversation happens.
- **Cross-pair/cartesian comparator + statistical pairs trading** - still
  deferred to its own future session. `portfolio.py`'s correlation matrix
  is directly relevant groundwork for this, but multi-instrument timestamp/
  candle-boundary alignment across OANDA/Binance/Kraken (flagged in prior
  sessions) is still unsolved and `portfolio.py` explicitly does not
  attempt to solve it - see that file's docstring.
- **Tiered subscription refresh rates** (~5min/~30sec-1min/~1sec) - not
  designed. Refresh rate (infra) and signal granularity (strategy design)
  remain different axes from each other AND from the service-tier work
  added this session (which is about metric visibility, not refresh rate
  or signal granularity) - three separate axes now, not to be conflated.
- `OandaBroker.get_quote` / `place_market_order` - still never run, test
  explicitly before trusting either.
- Deferred metrics with a real reason (see `VALIDATION_HIERARCHY.md`):
  Sterling/Burke ratio (needs drawdown-event segmentation, not just
  running-max tracking), information ratio (needs a real benchmark/index,
  not yet built), R-multiple expectancy (needs a stop-loss concept the
  strategy layer doesn't have at all).
- Position sizing - no longer just an open item, has a full design spec
  now: `CONFIDENCE_SIZING_DESIGN.md`. Multi-user entitlements, live feed,
  deployment infra - still open, unchanged from earlier sessions.

## 7. IMMEDIATE NEXT STEP

**Superseded by `CONFIDENCE_SIZING_DESIGN.md` Section 10's phasing plan -
this list is now historical context, follow that document's Section 10 and
Section 12 instead of this list for what to actually do next.**

1. ~~Decide the next strategy family~~ - done, Section 4b (four strategies
   added). Running them against real data is now Phase 1's hard blocking
   dependency (Section 4c above / design doc Section 4.1), not a standalone
   "next step" anymore - do it as part of starting Phase 1, not before or
   separately from it.
2. Test `OandaBroker.get_quote`/`place_market_order` before trusting them -
   still unstarted, now doubly relevant given the design doc's Section 9.2
   live-execution-realism work.
3. Dedicated session on actual service-tier product design - still open;
   `CONFIDENCE_SIZING_DESIGN.md` Section 11 settled the TIER STRUCTURE
   (3 tiers, shared-computation/personalized-cap boundary) but not the
   full bronze/silver/gold product design this point refers to.
4. Cross-pair/pairs-trading work - still deferred, alignment problem
   (Section 6 below) still unsolved. `portfolio.py` is now also load-
   bearing for the confidence-sizing work (design doc Section 1), so it
   will get exercised sooner than this item implies either way.

## 8. RISKS

Two real bugs found and fixed in `OandaBroker.fetch_candles` (Section 3) -
concrete evidence that "written to spec" isn't "correct". Apply the same
suspicion to anything still unverified in Section 3, especially
`get_quote`/`place_market_order`. Not a lawyer or financial advisor - this
project is about a sound, honest testing/execution pipeline, not investment
advice. Automation doesn't remove trading risk, it executes decisions faster.
