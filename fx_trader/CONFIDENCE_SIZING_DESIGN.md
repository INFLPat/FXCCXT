# Confidence-Weighted Multi-Strategy Sizing - Design Spec

Produced by a long discovery conversation (not yet any code) about how the
five existing strategies (SMA crossover, RSI, MACD, RSI+MACD confluence,
Bollinger Bands) should work TOGETHER, and how their combined agreement
should drive position size - continuously, across instruments, in a way
that also has to work live, not just in backtest.

**Read this after `CONTEXT_HANDOFF.md`, alongside `VALIDATION_HIERARCHY.md`
and `backtest/service_tiers.py`.** Nothing in this document has been built
yet. Where a decision was actually settled in discussion, it's stated
plainly as a decision. Where it's still open, it says so - don't infer a
decision from silence.

## 0. The one-sentence version

A new layer sits ABOVE the existing `Strategy` interface: it runs several
strategies against the same candle stream, turns their individual
positions into one continuous confidence score per instrument, turns that
score into a position size (not just a direction), and does the same
thing across instruments to split capital - all via a design that has to
run identically whether it's replaying history or watching a live feed.

## 1. Scope and architecture (SETTLED)

- **Full portfolio**, not just "one instrument, several strategies voting."
  Confidence also decides capital split ACROSS instruments.
- **Live-execution-aware from the start.** Whatever combines strategies'
  decisions into one action must be a pure function of "current stances +
  current weights" that doesn't care whether it's called from inside
  `BacktestEngine.run()`'s loop or a live event loop - the same principle
  `strategy/base.py` already states for one strategy, extended one layer up.
- **This does NOT live inside any one `Strategy`.** `RsiMacdConfluenceStrategy`'s
  pattern (compose sub-strategies inside one `Strategy`) does not generalize
  to "also spans instruments" and "also needs to run identically live." This
  needs a new component - working name **`PositionManager`** - that sits
  above multiple `Strategy` instances, not inside one.
- Cross-instrument capital split reuses `backtest/portfolio.py`'s
  correlation matrix FOR REAL this time, not just as a diagnostic: two
  correlated instruments both showing high confidence should not both get
  full allocation for what's substantially the same bet twice.

## 2. Core scoring mechanism

### 2.1 Why raw signals don't work directly (the key insight)

`on_candle()` returns BUY/SELL/CLOSE/HOLD, and BUY only fires ONCE, on the
crossing candle - every candle after, until the next crossing, the
strategy returns HOLD. A continuous confidence score needs each strategy's
CURRENT STANCE (long/flat/short right now), not "did it just fire."

**Decision**: run each strategy's signal through the same open/flat/short
state machine `BacktestEngine._apply_signal` already uses internally, once
per strategy, producing a persistent shadow stance ∈ {-1, 0, +1} that only
changes when that strategy actually fires. Reuses trusted logic rather than
inventing new state tracking.

### 2.2 The score formula (SETTLED shape, open on exact weight source)

```
weight_i = clip(weight_fn(strategy_i, instrument), min=0)   # see Section 4
raw_score = Σ(weight_i * stance_i) / Σ(weight_i)             if Σweight_i > 0 else 0
shaped_score_k = sign(raw_score) * |raw_score| ** k
```

- **Flat/neutral strategies COUNT in the denominator** (decision made
  explicitly): 3-of-4 flat and 1 long should read as "mostly no conviction,"
  not "unanimous among those with an opinion." A strategy past its own
  warmup that's currently flat is itself expressing "no edge seen right
  now," and that should dilute the score.
- **Compute all three shapes in parallel, always** - don't pick one:
  - `k = 1.0` - linear
  - `k = 2.0` (or similar >1) - convex, rewards near-unanimity, shrinks
    weak agreement toward zero
  - `k = 0.5` (or similar <1) - concave, rewards the FIRST confirmation
    most, diminishing after
  - One parameter spans the family, so adding another curve later is
    adding a `k` value to a list, same pattern as `bootstrap.TRACKED_METRICS`.
- **Negative weights**: a strategy with negative historical performance
  would mathematically invert its own vote if weight went negative.
  Decision: **clip at 0 (exclude), never invert automatically.** Log/flag
  any candidate that would go negative and review those cases specifically
  as they come up - there may be a genuine inverse-signal case sometimes,
  but it's not assumed, and it's not automatic.

### 2.3 Hysteresis (pattern SETTLED, exact numbers open)

Asymmetric entry/exit thresholds - need a HIGHER |score| to open a
position than to keep it open, so the score doesn't have to cross the same
line twice and flip-flop at the boundary. The pattern itself (wider gate
in, narrower gate to stay in) is standard and not in question. The actual
threshold VALUES are not decided - they're exactly like RSI's 30/70 or
Bollinger's `num_std=2.0`: parameters the validation hierarchy tests, AND
inputs the user's risk-appetite slider adjusts (Section 7).

With continuous rescaling (Section 8) confirmed as the sizing mechanism,
hysteresis effectively also becomes the exit-timing logic: a position
opens when score crosses the entry threshold, grows/shrinks as score
drifts, and closes itself when score decays through the exit threshold.

### 2.4 This layer is itself a hyperparameter set (SETTLED)

The combination layer (weights, thresholds, shape curve choice) must go
through the SAME Tier 0-4 validation any strategy does - it does not sit
above the hierarchy unvalidated just because it isn't a `Strategy`
subclass. Concrete consequence to plan for: its own grid
(`entry_threshold × exit_threshold × base_metric_choice × shape_k × ...`)
is larger than any single strategy's grid -
`validation_orchestrator.MAX_GRID_COMBINATIONS` (currently 200) will need
revisiting once this grid is built.

## 3. Trend vs. mean-reversion grouping

Measured (synthetic data, one instrument, illustrative only - re-checked on
real data, see Section 4.2 below) SMA vs. Bollinger equity-curve
correlation was **-0.61**, RSI vs. Bollinger **+0.34**, MACD oddly
uncorrelated with everything (different trade frequency - 251 vs 120 vs 21
vs 73 trades in that run).

The relationship is genuinely two different things at two timescales:
- **Moment-to-moment, within one persistent trend: opposed.** An
  oscillator will flash "overbought" repeatedly while a trend still has
  a long way to run - a well-documented failure mode, not informative on
  its own during a strong trend.
- **Across a trend's lifecycle: potentially complementary, but the useful
  version of that is DIVERGENCE** (price makes a new high while the
  oscillator makes a lower high at the same time), not "oscillator says
  overbought, therefore trend ending." Divergence needs peak/trough
  tracking on both price and indicator series - genuinely new, bigger
  machinery than voting, not a config flag.

**Decision - sequencing, not a final pick between A/B/C:**
- **Option A (no bucketing, single weighted vote across all strategies)** -
  build now. Cheapest path to a working system; relies entirely on
  Section 4's weights to downweight whichever family isn't working on a
  given instrument.
- **Option B (two bucket scores - trend consensus, reversion consensus -
  blended)** - build now, as a toggle to compare against A.
- **Option C (explicit regime detector deciding which bucket gets
  listened to, including real divergence detection)** — **NOT part of
  Phases 1-4. Flagged explicitly as a future, separate project** to come
  back to once A and B have real performance data to justify the added
  complexity and validation surface.

Real-data correlations (Section 4.2) confirmed this split is genuine, not
a synthetic-data coincidence - see below.

## 4. Weighting source (SETTLED mechanism - CRITICAL DEPENDENCY, see 4.1)

**Decision**: weight comes from the validation hierarchy's own results for
that (strategy, instrument) pair - not equal weighting, not hand-picked.

Strawman formula (a starting point to validate, not final - **see Section
4.2's real-data check: a real defect was found and fixed in the tier-4
handling of this formula**):

```
weight(strategy, instrument) = 0                                     if it never cleared Tier 1 here
weight(strategy, instrument) = base_metric(strategy, instrument)
                                * tier_multiplier(highest_tier_cleared)  otherwise
```

`tier_multiplier`: Tier 1 only = 0.25, Tier 2 = 0.5, Tier 3 = 0.75,
Tier 4 = 1.0 (strawman values) - a strategy that only cleared the cheapest
gate shouldn't carry the same weight as one that survived bootstrapping.

### 4.1 RESOLVED THIS SESSION — see CONTEXT_HANDOFF.md Section 4d for results

All five strategies have now been run against the real 16-instrument
sandbox. 7 (strategy, instrument) pairs cleared Tier 4 - all
`RsiStrategy` or `RsiMacdConfluenceStrategy`, all on JPY crosses or
Litecoin pairs (GBP_JPY, USD_JPY, LTC/USDT, LTC/GBP). `SmaCrossoverStrategy`
and `MacdStrategy` and `BollingerBandsStrategy` all produced 0/16. Full
breakdown, the param grids used (none existed before that session), and
the Section 4.2 checks below: `CONTEXT_HANDOFF.md` Section 4d. The rest of
this subsection is kept as the historical record of why the run was
blocking, not as a current statement of status.

**Originally: this weighting scheme requires real validation-hierarchy
output to exist. It currently does not, for any of the five strategies.**
As of the document's original writing: 0/16 real sandbox instruments had
EVER produced a Tier 4 survivor (SmaCrossoverStrategy was run and failed
everywhere - `CONTEXT_HANDOFF.md` Section 4). RsiStrategy, MacdStrategy,
RsiMacdConfluenceStrategy, and BollingerBandsStrategy had **never been run
against real sandbox data at all** - only synthetic data, for
correctness-verification purposes.

**Before Phase 1 Step 2 (scoring engine) can compute a meaningful weight
for anything, run all five strategies through
`run_validation_hierarchy_real_data.py` (or its equivalent, extended to
loop over strategies) against the real 16-instrument sandbox.** This ran in
parallel with Phase 1 Step 1 (position accounting) as originally planned,
since that step doesn't need real weights - see Phase 1 Step 1's actual
status in `CONTEXT_HANDOFF.md` Section 7 for what's still outstanding
there.

### 4.2 This is an analysis task, not a mechanical checkbox

Confirmed explicitly: running the hierarchy is the FIRST thing to do in
the next chat - but the point isn't just to get a pass/fail number per
(strategy, instrument). Once real results exist, read them back against
EVERYTHING in this document, not in isolation:

- **Against Section 3/4/5**: do real agreement rates between the five
  strategies resemble the synthetic-data correlation numbers measured in
  discussion (SMA vs. Bollinger at -0.61, RSI vs. Bollinger at +0.34,
  MACD oddly uncorrelated with the others)? If real correlations differ
  substantially, that changes whether Option A (unbucketed) or B
  (bucketed) looks more sensible to build out first, and may reshape
  which base metric (Section 5) is worth prioritizing.
- **Against Section 2's weighting formula**: does the strawman
  `tier_multiplier` scheme (0.25/0.5/0.75/1.0 by tier cleared) produce
  sane-looking weights on real Sortino/CI numbers, or does it need
  reshaping before Step 2 is built on top of it?
- **Against Section 8's debounce/rescale assumptions**: real trade
  frequency per strategy (not synthetic-data trade counts) should inform
  what a sensible debounce threshold and rebalance cadence actually are,
  before those numbers get hardcoded anywhere.
- **Against the wider product/feature and user-tier context** (Section 6,
  Section 11): does any strategy turn out to only work on certain
  instrument classes (fiat vs. crypto) in a way that should shape which
  confidence modules matter most, or how the three service tiers'
  module sets should actually be split?
- **Against what Phase 2-4 will need**: results here may surface things
  worth feeding forward into the pluggable-modules design (Section 6) or
  the regime-detection future work (Section 3, Section 13) even before
  those phases start - note anything relevant rather than setting it
  aside until "later."

In short: treat the real-data run as the point where this whole design
gets its first contact with real evidence, and revisit anything above that
the results call into question - not just a gate to clear before writing
the scoring code.

**RESULTS (this session)** — see `CONTEXT_HANDOFF.md` Section 4d for the
full breakdown; summarized against each bullet above:

- *Against Section 3/4/5*: real correlations closely matched the
  synthetic-data numbers. SMA vs. Bollinger averaged **-0.59** across all
  16 instruments (synthetic: -0.61). RSI vs. Bollinger averaged **+0.39**
  (synthetic: +0.34). MACD stayed the least-correlated strategy with
  everything else (avg 0.08-0.27), matching the "oddly uncorrelated" read.
  The trend-vs-reversion opposition Section 3 leaned on is real, not a
  synthetic-data artifact - some support for prioritizing Option B
  (bucketed trend/reversion scores) over treating it as coincidence.
  **New finding, not anticipated by this section**: RSI vs.
  `RsiMacdConfluenceStrategy` averaged **+0.65** correlation (up to +0.82
  on LTC/GBP) - not surprising given Confluence literally embeds RSI's own
  signal as half its logic, but it means these two are not independent
  votes for weighting purposes. Section 1's "don't double-allocate
  correlated instruments" principle needs to also apply cross-*strategy*,
  not just cross-instrument - not currently in this design, needs adding
  to Section 6's pluggable modules.
- *Against Section 2's weighting formula*: **did NOT produce sane weights
  as originally written - a real defect, found and fixed.** Using each
  candidate's raw uncorrected Tier-1 grid-winner return as base_metric for
  anything short of Tier 4 let a REJECTED Tier-3 finalist outscore an
  ACTUAL Tier 4 survivor: `BollingerBandsStrategy`/LTC_GBP (rejected at
  Tier 4) scored a strawman weight of 9.96 against `RsiStrategy`/LTC_GBP
  (an actual survivor) at 5.62. Cause: a Tier-3 finalist that fails Tier 4
  already has a real bootstrap CI computed during that evaluation - the
  sanity-check script was discarding it on rejection and falling back to
  the raw, uncorrected Tier-1 score instead. **Fix, now applied**: use the
  bootstrap CI-90 lower bound - computed for every Tier-3+ finalist
  regardless of individual pass/fail - as the base metric uniformly for
  every candidate that reaches Tier 3 or higher. After the fix, every
  rejected finalist correctly shows a negative CI lower bound (clipped to
  weight 0) - monotonicity restored. **This fix needs to be carried into
  the real Phase 1 Step 2 scoring engine implementation**, not left only in
  the sanity-check script (`run_full_sweep.py`) that found it.
- *Against Section 8's debounce/rescale assumptions*: not yet assessed.
  Per-survivor trade counts exist in the persisted run data but weren't
  extracted or summarized this session - still open, needed before Section
  8's debounce numbers can be set from real data rather than guessed.
- *Against the wider product/feature and user-tier context*: no obvious
  fiat-vs-crypto split in which strategies survived - survivors split
  between JPY crosses (fiat) and Litecoin pairs (crypto), not crypto as a
  class. If anything, Litecoin specifically stands out. Worth a closer look
  before assuming a fiat/crypto module split in Section 6.
- *Against what Phase 2-4 will need*: the RSI/Confluence correlation
  finding above is new input for Section 6's correlation-discount module -
  it needs to look at open positions across STRATEGIES on the same
  instrument, not only across instruments, before Phase 2 is built.

## 5. Base metric candidates (compute several, let real data pick)

**Decision**: don't commit to one metric. Compute candidates (and likely
pairs/blends of them) side by side once real data exists, compare, and
let results decide - possibly per-context (different metric better suited
to different strategy/instrument/situation) rather than one global winner.

| Metric | Pro | Con |
|---|---|---|
| Sharpe | Standard, well understood | Penalizes upside volatility same as downside - wrong shape for a strategy meant to cut losses short and let winners run |
| Sortino (`metrics.py`) | Only penalizes downside | Still a point estimate - says nothing about how much to TRUST it |
| Calmar/Recovery (`metrics.py`) | Maps to what users actually fear (drawdown) | Driven by a single worst historical event - noisy, sample-luck-sensitive |
| Bootstrap CI lower bound (`bootstrap.py`, Tier 4's own gate) | Inherently conservative - penalizes a weak point estimate AND high uncertainty in one number, nothing new to build | Needs enough trades to bootstrap meaningfully; thin history gives a wide, uninformative CI |
| `1 - probability_of_loss` (`bootstrap.py`) | Simple, bounded [0,1], directly explainable to a non-technical user | Ignores magnitude - rarely-but-big losses look deceptively good |
| Profit factor / expectancy (`metrics.py`) | Simple, intuitive | No risk-adjustment at all |
| Effect-size-over-sample-size, e.g. `mean_return / standard_error` | Rewards an edge well-SUPPORTED by data, not just a good-looking average | Not yet implemented anywhere - new formula to build and validate |

**Leaning, not a decision**: a two-part structure - point estimate
(Sortino) × trust discount (CI width or lower bound) - rather than a
single metric or an N-way blend with its own untested blend-weights. The
bootstrap CI-lower-bound approach used to fix Section 4.2's weighting
defect (above) is a concrete instance of this leaning already working on
real data - worth treating as the default rather than re-litigating from
scratch in Phase 1 Step 2.

**Named explicitly so it isn't lost**: every "blend N things" choice in
this whole document spawns new blend-weights that themselves need
validating. Not a reason to avoid blending - a reason to expect this
layer's own hyperparameter surface to be large (see Section 2.4).

## 6. Pluggable confidence modules (SETTLED - build this, Phase 2)

Confidence isn't one number from agreement alone - it's the base
agreement score ADJUSTED by independent factors, each gated by
subscription tier exactly like `service_tiers.py` already gates individual
metrics. Split by mechanism, since they don't combine the same way:

| Mechanism | What it does | Applied |
|---|---|---|
| Weight input | Feeds the base weighted average | Section 4's strategy weight |
| Scalar multiplier | Discounts/boosts the score after computing it | Volatility discount, cost-drag discount, correlation-with-open-positions discount |
| Hard gate/cap | Clips the final size regardless of score | Section 7 |

Candidate modules (cheap → expensive to build):

- **Instrument class/tier** (fiat vs. crypto, major vs. minor) - a small
  curated lookup table, same spirit as `service_tiers.py`'s registry.
  Confirmed intent: **(a) a volatility/uncertainty discount**, symmetric
  regardless of direction - NOT a directional bias toward fiat. Applies
  identically to a long or a short. **Real-data caveat (Section 4.2):** the
  actual Tier 4 survivor set didn't split cleanly by fiat vs. crypto - a
  fiat/crypto-keyed lookup table may not be the right shape for this
  discount; worth checking against Litecoin-vs-other-crypto specifically
  before building the table.
- **Trailing realized volatility of the instrument** - already computable
  from candles, no new data source.
- **Historical cost drag** (`cost_drag_pct`, already computed) - a
  strategy/instrument pair that's historically eaten a lot of its edge to
  costs should need a higher confidence bar, not the same bar.
- **Bootstrap CI width** - a currently-unused number (only the lower bound
  is used as a Tier 4 gate today) that's directly informative here.
- **Correlation with currently-open positions** (`portfolio.py`, live use
  not just diagnostic) - a new confident signal correlated with an
  existing open position should get less incremental allocation. **Real-
  data finding (Section 4.2): this needs to run cross-STRATEGY as well as
  cross-instrument** - RSI and RsiMacdConfluenceStrategy correlate at
  +0.65 average (up to +0.82) on the same instrument, not just across
  different instruments, since Confluence embeds RSI's own signal.
- **Market cap / liquidity** (crypto) - BLOCKED ON DATA: OANDA/ccxt give
  price, not market cap; this pipeline has no source for it yet.
- **Regulatory/eligibility status** - keep as a separate GATE ("allowed to
  trade this at all"), not a confidence input - different question.
- **Session/liquidity time-of-day** - mostly a live-trading refinement;
  historical spread partially captures it in backtest already.

**Starting tier allocation (a starting allocation, not a decision, exactly
like `service_tiers.py`'s own caveat)**: bronze = base agreement score
only. Silver = + instrument-class/volatility discount. Gold = +
cost-drag discount + CI-width discount + open-position-correlation
discount (including the cross-strategy case above) + (later) lifecycle
caps.

## 7. Hard caps and risk appetite (SETTLED shape)

**Architecture**: caps are GATES applied AFTER the confidence-scaled size
is computed, not blended into the score - `final_size =
min(confidence_scaled_size, cap)`. Keep as genuinely separate code from
the scoring layer (standard split in real systems: an "alpha"/sizing layer
proposes size, a separate risk layer imposes hard limits), tuned
independently.

**Cap inputs**: instrument robustness tier, account-lifecycle state
(opt-in), user risk-appetite setting.

**Account-lifecycle ("house money") caps - opt-in feature, confirmed.**
Scaling risk up after gains is a named behavioral bias (the house-money
effect); professional risk management usually guards against it rather
than encodes it, since it tends to amplify exactly the drawdowns that
follow overconfidence. Building it anyway as an explicit, opt-in toggle is
fine - the person chooses to take more risk with the system's help, rather
than the system doing it to them silently. Guardrails agreed:
1. Cap the UPWARD adjustment hard - never more than ~1.5-2x the base cap,
   however far up the account is.
2. Make the DOWNWARD adjustment (shrink cap after losses) the
   default-ON, more aggressive direction - uncontroversial good risk
   management, unlike the upward half.

**Risk-appetite slider**: one scalar parametrizing THREE things at once
(not three separate settings):
(a) entry/exit thresholds (Section 2.3) - lower risk tolerance = higher
    bar to enter;
(b) steepness of confidence→size scaling (Section 8);
(c) the cap ceiling itself (this section).
The actual mapping from slider value to those three is a concrete design
task for later, not decided here.

## 8. Position sizing mechanism (SETTLED - the bigger of the two builds)

**Decision: (b) continuous rescaling**, via **weighted-average-entry
partial-fill accounting** (not close-and-reopen) - "let's play it out on
real data" was the caveat, i.e. this is the design to build and validate,
not swap for something else without cause.

Putting the pieces together: this isn't "size at entry, plus separately
rescale" - confidence drives the WHOLE position lifecycle continuously. A
position opens when score crosses the entry threshold, grows/shrinks as
score drifts, and closes itself when score decays through the exit
threshold (Section 2.3). Hysteresis is doing double duty as both
anti-whipsaw protection and exit-timing logic.

### 8.1 Debounce (SETTLED: both guardrails, numbers open)

- **Magnitude-based**: don't rescale for a tiny change (e.g., don't act
  unless target size differs from current size by more than X%).
- **Time-based**: build this too, even though the original framing was
  magnitude-only - worth having both guardrails against noise (a big-
  looking change that reverses immediately shouldn't cause action either).
  Bring into testing alongside the magnitude-based one.
- **Actual numbers**: decide from real fee/cost data (Section 9.1), not
  guessed - a debounce threshold only means something relative to what a
  rescale actually costs in commission. **Still blocked - see Section 9.1;
  real per-survivor trade frequency (Section 4.2) also still needed and not
  yet extracted.**

### 8.2 Trade/Metrics semantics (SETTLED)

- The full open→...→fully-flat lifecycle, however many partial adds/
  reduces happen inside it, is ONE trade for `Metrics` purposes (win-rate,
  expectancy, streaks etc.) - matches how a person would describe it.
  Every individual fill still gets its own ledger entry underneath for
  cost-drag/rebalance-frequency analysis.
- **Every partial add/reduce pays its own commission** (flat + per-unit,
  same `CostModel` as a full trade today) - each is a real execution.
  This is exactly why the debounce threshold matters: frequent small
  rescales could bleed commission disproportionately to their size without
  one.
- Rescale check evaluated every candle (strategies already update every
  candle - no separate cadence needed beyond the debounce guardrails
  above).

### 8.3 Engineering scope (flagging the size of this, not deciding less)

`Trade`/`BacktestResult` today assume one fixed-size open→close pair.
Weighted-average-entry accounting needs real new capability: multiple
partial fills per logical position, running weighted-average entry price,
cost accounting per fill, not just per trade. This is a genuine data-model
redesign touching `backtest/engine.py`, `backtest/metrics.py`,
`backtest/bootstrap.py`, and `data/run_store.py`'s schema.

**Backward-compatibility target (CONFIRMED - not just a recommendation
anymore)**: the existing four single-strategy paths (SmaCrossoverStrategy,
RsiStrategy alone, MacdStrategy alone, BollingerBandsStrategy alone) run
through the redesigned model as the trivial one-fill case and MUST produce
IDENTICAL results to today - this is a hard regression-test requirement
(re-run `tests/test_engine.py` and friends bit-for-bit). **If it doesn't
match on the first attempt, that's not a green light to ship anyway** -
test thoroughly until either (a) it matches exactly, or (b) the specific
cause of any difference is understood and there's a genuine, articulable
reason the new model's number is MORE correct than the old one (not just
different) - and that reason gets written down here before moving on, not
quietly accepted. Protects 14 currently-passing test modules and four
working strategies from silent regressions.

## 9. Real-world realism

### 9.1 Fee schedule sourcing (researched a prior session - starting point, not final)

Public 2026 rates found via web search, for planning only - EXACT rates
depend on account type/tier and must be sourced per-account, not
hardcoded from a blog post:

- **OANDA**: two account models, chosen at account opening, not
  API-discoverable generically.
  - *Standard* (spread-only): ~1.1 pips EUR/USD, no separate commission.
  - *Core*: raw spread from ~0.1 pips + **~$5/lot commission** (roughly
    $50/million traded) - needs $10,000 minimum deposit historically.
  - **Still not set up, choice still not made.** This remains a discussion
    topic, not yet resolved: at what point in the Phase 1 build this
    actually needs to be settled (it blocks real commission numbers, not
    the position-accounting or scoring-engine steps), what's actually
    involved in opening each account type, and the pros/cons of Standard
    vs. Core specifically for a systematic strategy that may rebalance
    positions more often than a typical discretionary account (Core's
    transparent, volume-scaling commission is usually the better fit for
    frequent, systematic trading than Standard's cost-hidden-in-spread
    model - but confirm that properly rather than treating this as decided
    here).
- **Binance** (ccxt): spot base rate **0.10% maker / 0.10% taker**,
  dropping with VIP volume tier (e.g. VIP 3 ≈ 0.04-0.07%), 25% off with
  BNB fee payment.
- **Kraken** (ccxt): spot base rate **0.25% maker / 0.40% taker** - notably
  higher entry point than Binance - dropping fast with volume (e.g. 0.10%/
  0.20% by $250k/30-day volume; near 0%/0.05% at the top tier). Also newly
  (as of mid-2026) qualifies tier by Assets-on-Platform, not just volume.

**Plan - mirrors `fetch_sandbox_data.py`'s pattern**: a new script
(working name `fetch_real_cost_data.py`) that:
1. For ccxt exchanges: calls `exchange.fetch_trading_fees()` (needs real,
   authenticated API keys - the account's OWN current tier, not a public
   guess) to get actual maker/taker rates, and `exchange.load_markets()`
   for per-symbol `limits`/`precision` (minimum order size, step size -
   see 9.2). `CcxtBroker` already exists; this needs one or two new thin
   methods on it, not a new broker class.
2. For OANDA: the v20 API's instrument-details endpoints give
   `marginRate`, `pipLocation`, `minimumTradeSize` per instrument
   (programmatic, no guessing) - but NOT commission, which is account-type
   configuration (see above, needs the person to state it).
3. Output feeds directly into `CostModel` instantiation - a small
   reference file (e.g. `data/fee_schedules.json`), not hardcoded
   constants, so it can be refreshed without a code change.
4. **Cannot be run in this sandbox** (no network access) - same
   constraint `fetch_sandbox_data.py` already has. The person runs it
   locally with real credentials and reports back, exactly like the
   sandbox dataset was built. Still true of the current sandbox
   environment as of this session too.

### 9.2 Live-execution realism (SETTLED: build this, alongside Phase 1 Step 1)

"Trades should not be possible in the test if they're not possible in the
real world."

- **Minimum order size / lot step**: source programmatically per
  instrument (ccxt `market['limits']`/`precision`; OANDA's
  `minimumTradeSize`) - not hardcoded - and have `BacktestEngine` actually
  reject/round a rescale that would violate it, rather than silently
  allowing a fractional-unit trade no real broker would accept.
- **Slippage on rebalance orders specifically**: the existing `CostModel`
  slippage fields were tuned for single entry/exit trades; a small,
  frequent rebalance order may face WORSE effective slippage relative to
  its size than a full entry does (thinner book depth at the exact
  moment, if latency-sensitive) - needs its own consideration, not
  necessarily the same slippage assumption reused unchanged.
- **Latency/delay**: flagged as a real gap versus live behavior, not
  solved here - backtest rescaling assumes adjusting size at exactly the
  decided moment/price; live, that's a real order into a real book.
  Expect live results to undershoot backtest MORE here than the existing
  single-entry/exit model already undershoots (same "elevated suspicion"
  category as `OandaBroker.place_market_order`, itself still never run
  live).

## 10. Phasing plan

**Phase 1** (core - broken into separately checkable, sequential steps,
confirmed as the right approach rather than one monolithic build):

1. **Position accounting.** Redesign `Trade`/`BacktestResult` for
   weighted-average-entry partial fills. Prove it against a strategy that
   already works (e.g. run `MacdStrategy` through it) BEFORE any scoring
   logic touches it - the backward-compatibility regression target
   (Section 8.3) is the exit criterion for this step. Build Section 9.2's
   realism constraints (min order size, precision) alongside this step,
   not after. **DONE - see `CONTEXT_HANDOFF.md` Section 7.** Exit
   criterion met: full test suite plus the entire real-data sweep
   re-verified byte-identical against the redesigned engine.
   `rescale_trade()` exists and is unit-tested; wiring it into a live
   signal path is Step 3, not this step.
2. **Scoring engine, shadow-computed only.** All three shapes × all
   candidate base metrics, logged per candle as data - does NOT yet
   affect any real trade size. **Section 4.1's real-data hierarchy runs are
   now complete** (`CONTEXT_HANDOFF.md` Section 4d) - there is something
   meaningful to weight with now, including the corrected base-metric
   definition from Section 4.2's fix above. Still needs Step 1 done first.
3. **Wire scoring → sizing**, one instrument, single-strategy-ensemble
   (start with the philosophically-similar strategies, e.g. RSI+MACD+
   Bollinger, before mixing in SMA - see Section 3's Option A/B note).
4. **Cross-instrument allocation** via `portfolio.py`.

**Phase 2**: pluggable confidence modules (Section 6), tiered
bronze/silver/gold.

**Phase 3**: account-lifecycle caps (house-money guardrails) + risk-
appetite slider (Section 7).

**Phase 4 / Option C**: explicit regime detection, specifically including
real MACD/RSI divergence detection (peak/trough tracking) - confirmed as
a future, separate project, not folded into Phases 1-3.

## 11. Per-user tiering architecture (SETTLED)

Confirmed structure: **exactly 3 core tiers each time** (bronze/silver/
gold - matching `service_tiers.py`'s existing `ServiceTier` enum), with
more sophistication plugged in at each tier, not a different NUMBER of
tiers per feature area.

**Computation boundary (confirmed)**: the shared, expensive parts
(strategy weights, signal agreement, instrument-class/volatility
discounts - anything not user-specific) are computed ONCE per instrument,
shared across all users watching it. Only the final cap/scaling
(risk-appetite slider, account-lifecycle state) is personalized per user,
applied on top of that shared number. Mirrors the cost-tier/service-tier
split already in the codebase (`VALIDATION_HIERARCHY.md`'s own framing).

Each of the 3 tiers' module sets should be independently testable, and
this boundary is explicitly there to protect a lower tier from ever seeing
something a higher tier doesn't for some reason, among other benefits -
not just an implementation convenience.

## 12. Open questions to confirm at the START of the next chat

Two of these were resolved in discussion (backward compatibility is now a
confirmed requirement, Section 8.3); the two below remain CONFIRMED as
open - not just unanswered, actively "don't have this yet" - and both are
explicitly deferred to a proper discussion rather than decided here.
**Neither was addressed this session** - this session's work was Section
4.1's real-data run, not fee sourcing (see `CONTEXT_HANDOFF.md` Section 6
for why that's a deliberate sequencing choice, not an oversight):

1. **OANDA account type** (Section 9.1) - not set up yet. Discuss at
   kickoff: when in the Phase 1 sequence this actually needs deciding,
   what setting up each account type involves, and the Standard-vs-Core
   pricing tradeoff specifically for this project's use case (a
   systematic strategy, potentially rebalancing more often than a typical
   discretionary trader once Section 8's continuous sizing is built).
2. **Real broker/exchange API credentials** (Section 9.1) - not available
   yet, for OANDA or any ccxt exchange. Discuss options at kickoff rather
   than assume - candidates worth putting on the table: starting from a
   free demo/practice account's published fee schedule as an interim
   stand-in (same practice already used for `OandaBroker`'s
   `environment='practice'` default), using the public default-tier rates
   already researched in Section 9.1 as a placeholder in `CostModel` until
   real credentials exist, or sequencing real-account setup alongside
   whichever Phase 1 step first needs accurate costs rather than blocking
   on it upfront.

## 13. Future-work register (consolidated - do not lose these)

- **Option C / regime detection / real divergence detection** (Section 3,
  Section 10 Phase 4) - explicitly deferred, not part of Phases 1-3.
- **Market-cap/liquidity confidence module** (Section 6) - blocked on a
  data source this pipeline doesn't have.
- **`MAX_GRID_COMBINATIONS` revisit** (Section 2.4) - will need raising
  once the combination layer's own grid is added to validation.
- **Session/liquidity time-of-day module** (Section 6) - lower priority,
  live-trading refinement.
- **Effect-size-over-sample-size base metric** (Section 5) - not yet
  implemented anywhere, would need building before it can be compared.
- ~~**STILL OPEN FROM BEFORE THIS DISCUSSION** (`CONTEXT_HANDOFF.md`): all
  five strategies still need their real-16-instrument Tier 0-4 run - now
  promoted from "next step" to "hard blocking dependency" by Section 4.1.~~
  **DONE - see `CONTEXT_HANDOFF.md` Section 4d.** Replaced by two new
  items surfaced by that run: real per-survivor trade frequency for
  Section 8.1's debounce thresholds (not yet extracted), and carrying
  Section 4.2's bootstrap-CI base-metric fix into the real Phase 1 Step 2
  implementation (currently only fixed in the sanity-check script).
- `OandaBroker.get_quote`/`place_market_order` - still never run live,
  unchanged status, now doubly relevant given Section 9.2's live-realism
  work.
