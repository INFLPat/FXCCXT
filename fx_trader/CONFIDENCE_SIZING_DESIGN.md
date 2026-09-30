<!-- version: 260929 -->
# Confidence-Weighted Multi-Strategy Sizing - Design Spec

How the five strategies (SMA, RSI, MACD, RSI+MACD confluence, Bollinger)
work TOGETHER, and how their agreement drives position size - continuously,
across instruments, live and backtest alike.

Read after `CONTEXT_HANDOFF.md`, alongside `VALIDATION_HIERARCHY.md` and
`backtest/service_tiers.py`. Nothing here is built yet. A decision is
stated plainly where one was made; silence means still open - don't infer
a decision from an absence.

## 0. One-sentence version

A layer above `Strategy`: runs several strategies on the same candle
stream, turns their individual positions into one continuous confidence
score per instrument, turns that into a position size (not just
direction), and splits capital the same way across instruments - live and
backtest via the same pure function.

## 1. Scope and architecture (SETTLED)

- **Full portfolio**, not one instrument. Confidence also decides capital
  split across instruments.
- **Live-execution-aware from the start**: whatever combines strategies'
  decisions into one action is a pure function of "current stances +
  current weights," indifferent to whether it's called from
  `BacktestEngine.run()`'s loop or a live event loop.
- **Not inside any one `Strategy`.** `RsiMacdConfluenceStrategy`'s
  compose-sub-strategies pattern doesn't generalize to spanning
  instruments and running live - needs a new component, working name
  **`PositionManager`**, sitting above multiple `Strategy` instances.
- Cross-instrument capital split reuses `backtest/portfolio.py`'s
  correlation matrix for real, not just as a diagnostic - two correlated
  instruments both showing high confidence shouldn't both get full
  allocation for the same bet twice.

## 2. Core scoring mechanism

### 2.1 Why raw signals don't work directly

`on_candle()` only fires BUY once, on the crossing candle; every candle
after is HOLD until the next crossing. A continuous score needs each
strategy's CURRENT STANCE (long/flat/short right now), not "did it just
fire."

**Decision**: run each strategy's signal through the same open/flat/short
state machine `BacktestEngine._apply_signal` already uses, once per
strategy, producing a persistent shadow stance in {-1, 0, +1} that only
changes when that strategy fires.

### 2.2 Score formula (SETTLED shape, weight source open)

```
weight_i = clip(weight_fn(strategy_i, instrument), min=0)   # Section 4
raw_score = Σ(weight_i * stance_i) / Σ(weight_i)             if Σweight_i > 0 else 0
shaped_score_k = sign(raw_score) * |raw_score| ** k
```

- **Flat strategies count in the denominator** (decided): 3-of-4 flat and
  1 long reads as "mostly no conviction," not "unanimous among those with
  an opinion." A flat strategy past warmup is itself saying "no edge seen
  now."
- **Compute all three `k` shapes always, in parallel**: `k=1.0` linear,
  `k=2.0` convex (rewards near-unanimity, shrinks weak agreement), `k=0.5`
  concave (rewards the first confirmation most). One parameter spans the
  family - adding a curve later is adding a value to a list, same pattern
  as `bootstrap.TRACKED_METRICS`.
- **Negative weights**: clip at 0 (exclude), never invert automatically.
  Flag/review any candidate that would go negative individually, rather
  than assuming an inverse-signal case.

### 2.3 Hysteresis (pattern SETTLED, thresholds open)

Asymmetric entry/exit: a higher |score| to open than to keep open, so the
score doesn't cross the same line twice and flip-flop. Threshold VALUES
are open - like RSI's 30/70 or Bollinger's `num_std=2.0`, parameters the
validation hierarchy tests AND the risk-appetite slider (Section 7)
adjusts.

With continuous rescaling (Section 8), hysteresis also becomes exit-timing:
position opens on entry-threshold cross, grows/shrinks as score drifts,
closes itself on exit-threshold decay.

### 2.4 The combination layer is itself a hyperparameter set (SETTLED)

Weights, thresholds, shape-curve choice go through the same Tier 0-4
validation any strategy does. Its own grid (entry x exit x base_metric x
shape_k x ...) is larger than any single strategy's -
`validation_orchestrator.MAX_GRID_COMBINATIONS` (200) will need revisiting.

## 3. Trend vs. mean-reversion grouping

Real-data correlations (`CONTEXT_HANDOFF.md` Section 4d) confirmed this is
genuine, not synthetic-data coincidence: SMA vs. Bollinger opposed
(-0.59), RSI vs. Bollinger mildly aligned (+0.39), MACD least-correlated
with everything.

Two different things at two timescales: moment-to-moment within one
trend, oscillators and trend-followers are OPPOSED (an oscillator flashes
overbought while a trend still runs); across a trend's lifecycle, the
useful complementary signal is DIVERGENCE (price new high, oscillator
lower high, at the same time) - needs peak/trough tracking on both
series, genuinely new machinery, not a config flag.

**Decision - sequencing, not a final A/B/C pick**:
- **Option A** (no bucketing, single weighted vote) - build now.
- **Option B** (two bucket scores, trend + reversion, blended) - build
  now too, as a toggle to compare against A.
- **Option C** (explicit regime detector, real divergence detection) -
  **not part of Phases 1-4**, a future separate project.

## 4. Weighting source (SETTLED mechanism)

**Decision**: weight comes from the validation hierarchy's own results for
that (strategy, instrument) pair, not equal or hand-picked weighting.

Strawman (starting point, not final):

```
weight(strategy, instrument) = 0                                        if it never cleared Tier 1
weight(strategy, instrument) = base_metric(strategy, instrument) * tier_multiplier(highest_tier_cleared)  otherwise
```

`tier_multiplier`: Tier 1 = 0.25, Tier 2 = 0.5, Tier 3 = 0.75, Tier 4 = 1.0
(strawman).

**Resolved** (see `CONTEXT_HANDOFF.md` Section 4d/4e): real weighting
data now exists from the 16-instrument sweep; a real defect in the
strawman formula (a rejected finalist outscoring an actual survivor) was
found and fixed - use the bootstrap CI-90 lower bound as base_metric for
every Tier 3+ candidate, not just passing ones. Still needs to land in
the real scoring-engine implementation, not just the sanity-check script.

Open, not yet assessed: real per-survivor trade frequency for Section
8.1's debounce numbers; whether any strategy shows a fiat-vs-crypto split
(so far: no - Litecoin specifically stands out, not crypto as a class).

## 5. Base metric candidates (compute several, let real data pick)

**Decision**: don't commit to one metric - compute candidates side by
side, compare on real data, let results decide, possibly per-context.

| Metric | Pro | Con |
|---|---|---|
| Sharpe | Standard | Penalizes upside volatility same as downside |
| Sortino | Only penalizes downside | Still a point estimate, no trust signal |
| Calmar/Recovery | Maps to what users fear (drawdown) | Driven by one worst event - noisy |
| Bootstrap CI lower bound | Conservative - penalizes weak point estimate AND uncertainty in one number | Needs enough trades; thin history gives a wide, uninformative CI |
| `1 - probability_of_loss` | Simple, bounded, explainable | Ignores magnitude |
| Profit factor / expectancy | Simple, intuitive | No risk-adjustment |
| Effect-size (`mean_return / standard_error`) | Rewards a well-SUPPORTED edge, not just a good average | New formula, not yet built anywhere |

**Leaning**: point estimate (Sortino) x trust discount (CI width/lower
bound), rather than one metric or an untested blend. The bootstrap-CI-
lower-bound fix above is a working instance of this already.

Every "blend N things" choice spawns its own blend-weights needing
validation - expected, not a reason to avoid blending.

Resolved this far (`CONTEXT_HANDOFF.md` Section 4e) - 4 candidates built and tested, real
disagreement found between `ci_lower_bound` and `expectancy_cost_
adjusted`/`effect_size` on ranking, deciding test (re-rank against
out-of-time data) specified but not yet run.

## 6. Pluggable confidence modules (SETTLED - build in Phase 2)

Confidence = base agreement score, adjusted by independent factors, each
gated by subscription tier like `service_tiers.py`. Split by mechanism:

| Mechanism | What it does | Applied |
|---|---|---|
| Weight input | Feeds the base weighted average | Section 4's strategy weight |
| Scalar multiplier | Discounts/boosts after computing | Volatility, cost-drag, open-position-correlation discounts |
| Hard gate/cap | Clips final size regardless of score | Section 7 |

Candidates (cheap -> expensive):
- **Instrument class/tier** (fiat vs. crypto, major vs. minor) - a
  symmetric volatility/uncertainty discount, not a directional bias.
  Real-data caveat: survivors didn't split cleanly fiat/crypto - check
  Litecoin-vs-other-crypto specifically before building the lookup table.
- **Trailing realized volatility** - already computable from candles.
- **Historical cost drag** (already computed) - a pair that's eaten a lot
  of its edge to costs needs a higher confidence bar.
- **Bootstrap CI width** - currently unused beyond the Tier 4 lower-bound
  gate, directly informative here.
- **Correlation with currently-open positions** (`portfolio.py`, live
  use) - must run cross-STRATEGY as well as cross-instrument (RSI/
  Confluence correlate +0.65 avg on the same instrument, Section 4e).
- **Market cap/liquidity** (crypto) - blocked, no data source.
- **Regulatory/eligibility status** - a separate GATE, not a confidence
  input.
- **Session/liquidity time-of-day** - live-trading refinement, lower
  priority.

**Starting tier allocation** (not a decision, same caveat as
`service_tiers.py`): bronze = base agreement score only. Silver = +
instrument-class/volatility discount. Gold = + cost-drag + CI-width +
open-position-correlation (incl. cross-strategy) + later lifecycle caps.

## 7. Hard caps and risk appetite (SETTLED shape)

**Architecture**: caps are GATES applied after the confidence-scaled size
is computed - `final_size = min(confidence_scaled_size, cap)`. Kept as
separate code from the scoring layer (alpha/sizing layer proposes,
separate risk layer imposes limits).

**Cap inputs**: instrument robustness tier, account-lifecycle state
(opt-in), user risk-appetite setting.

**Account-lifecycle ("house money") caps - opt-in, confirmed.** Scaling
risk up after gains is a named behavioral bias; guardrails:
1. Cap the upward adjustment hard - never more than ~1.5-2x the base cap.
2. Downward adjustment (shrink cap after losses) is default-ON and the
   more aggressive direction - uncontroversial good risk management.

**Risk-appetite slider**: one scalar parametrizing three things at once -
(a) entry/exit thresholds (2.3), (b) confidence->size scaling steepness
(8), (c) the cap ceiling (this section). Exact mapping is a later design
task.

## 8. Position sizing mechanism (SETTLED - continuous rescaling)

**Decision**: continuous rescaling via weighted-average-entry partial-fill
accounting (not close-and-reopen) - the design to build and validate, not
swap without cause.

Confidence drives the whole position lifecycle: opens on entry-threshold
cross, grows/shrinks as score drifts, closes on exit-threshold decay.
Hysteresis does double duty as anti-whipsaw protection and exit timing.

### 8.1 Debounce (SETTLED: both guardrails, numbers open)

Magnitude-based (don't rescale for a tiny target-size change) AND
time-based (a big-looking change that reverses immediately shouldn't act
either) - both guardrails, tested together. Actual numbers wait on real
fee/cost data (9.1) and real per-survivor trade frequency
(`CONTEXT_HANDOFF.md` Section 4d point 4, still not extracted) - a
debounce threshold only means something relative to what a rescale
actually costs.

### 8.2 Trade/Metrics semantics (SETTLED)

- The full open->...->flat lifecycle, however many partial adds/reduces,
  is ONE trade for `Metrics` purposes. Every fill still gets its own
  ledger entry for cost-drag/rebalance-frequency analysis.
- Every partial add/reduce pays its own commission (same `CostModel` as a
  full trade) - real execution, which is exactly why debounce matters.
- Rescale check runs every candle - no separate cadence.

### 8.3 Engineering scope

**DONE** - see `CONTEXT_HANDOFF.md` Section 3. `Trade`/`BacktestResult`
redesigned for weighted-average-entry partial fills (`Fill` dataclass,
`BacktestEngine.rescale_trade()`); backward-compatibility exit criterion
met (full suite + entire real-data sweep byte-identical). `rescale_trade()`
built and unit-tested, not yet wired into a live signal path (Phase 1
Step 3's job).

## 9. Real-world realism

### 9.1 Fee schedule sourcing (researched, starting point not final)

Public 2026 rates, for planning only - exact rates need per-account
sourcing:
- **OANDA**: Standard (spread-only, ~1.1 pips EUR/USD) vs. Core (raw
  spread ~0.1 pips + ~$5/lot commission, $10k min deposit historically).
  Core's transparent, volume-scaling commission usually fits frequent
  systematic trading better than Standard's spread-hidden cost - confirm
  properly, not decided here. **Still not set up, still open.**
- **Binance**: 0.10% maker/taker spot base, drops with VIP volume tier,
  25% off paying fees in BNB.
- **Kraken**: 0.25%/0.40% maker/taker base - higher entry point than
  Binance, drops fast with volume; also qualifies tier by Assets-on-
  Platform as of mid-2026, not just volume.

**Plan**: a script (`fetch_real_cost_data.py`) pulling real maker/taker
rates via `exchange.fetch_trading_fees()` (ccxt, needs real credentials)
and `load_markets()` for min order size/precision; OANDA's v20 instrument-
details endpoints for margin/pip/min-trade-size (not commission - that's
account-type config). Outputs to `data/fee_schedules.json`, feeds
`CostModel`. **Cannot run in this sandbox** (no network) - run locally
with real credentials.

### 9.2 Live-execution realism (SETTLED: build this)

"Trades should not be possible in the test if they're not possible in the
real world."
- **Min order size/lot step**: source programmatically per instrument
  (ccxt `market['limits']`/`precision`; OANDA's `minimumTradeSize`), have
  `BacktestEngine` reject/round a violating rescale. **Done** - see
  `CONTEXT_HANDOFF.md` Section 3.
- **Slippage on rebalance orders**: existing `CostModel` slippage was
  tuned for single entry/exit trades; a small frequent rebalance order may
  face worse effective slippage - needs its own consideration, not
  assumed same as a full entry.
- **Latency/delay**: real gap vs. live behavior, not solved - backtest
  rescaling assumes adjusting at exactly the decided moment/price; expect
  live results to undershoot backtest more here than the existing
  single-entry model already does.

## 10. Phasing plan

**Phase 1**:
1. Position accounting - **DONE**.
2. Scoring engine, shadow-computed only - all `k` shapes x candidate base
   metrics, logged, doesn't affect real trade size. Real weights now exist
   to build against (`CONTEXT_HANDOFF.md` Section 4d/4e).
3. Wire scoring -> sizing, one instrument, philosophically-similar
   strategies first (RSI+MACD+Bollinger before mixing in SMA).
4. Cross-instrument allocation via `portfolio.py`.

**Phase 2**: pluggable confidence modules, tiered bronze/silver/gold.

**Phase 3**: account-lifecycle caps. UPDATED 260929: the risk-appetite slider (user risk setting) moves into the MVP, see `ROADMAP.md` Sections 1 and 3; cartesian pair work and its interface come before the scoring engine.

**Phase 4 / Option C**: explicit regime detection, incl. real MACD/RSI
divergence detection - future, separate project.

## 11. Per-user tiering architecture (SETTLED)

Exactly 3 core tiers each time (bronze/silver/gold, matching
`ServiceTier`), more sophistication plugged in per tier, not a different
tier count per feature area.

**Computation boundary**: shared, expensive parts (strategy weights,
signal agreement, instrument-class/volatility discounts - anything not
user-specific) computed once per instrument, shared across users watching
it. Only the final cap/scaling (risk-appetite slider, account-lifecycle
state) is personalized per user, on top. Mirrors the existing cost-tier/
service-tier split.

## 12. Open questions to confirm at the start of the next relevant chat

1. **OANDA account type** (9.1) - not set up. When in Phase 1's sequence
   this needs deciding, what opening each account type involves, and the
   Standard-vs-Core tradeoff for a systematic strategy that may rebalance
   more often than a discretionary trader once Section 8's continuous
   sizing is built.
2. **Real broker/exchange API credentials** (9.1) - not available for
   OANDA or any ccxt exchange. Candidates worth discussing: a free demo
   account's published fee schedule as an interim stand-in (matches
   `OandaBroker`'s `environment='practice'` default); the public
   default-tier rates already researched here as a `CostModel` placeholder
   until real credentials exist; sequencing real-account setup alongside
   whichever Phase 1 step first needs accurate costs, rather than
   blocking on it upfront.

## 13. Future-work register

- Option C / regime detection / real divergence detection - deferred, not
  part of Phases 1-3.
- Market-cap/liquidity confidence module - blocked on a data source this
  pipeline doesn't have.
- `MAX_GRID_COMBINATIONS` revisit - needed once the combination layer's
  own grid is added to validation.
- Session/liquidity time-of-day module - lower priority.
- Effect-size-over-sample-size base metric candidate - built, not yet
  chosen as a winner (Section 5).
- `OandaBroker.get_quote`/`place_market_order` - never run live, doubly
  relevant given Section 9.2's live-realism work.
