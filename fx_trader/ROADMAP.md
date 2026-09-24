# Roadmap — Phase 1 Step 2 Onward

Read after `CONTEXT_HANDOFF.md` and `CONFIDENCE_SIZING_DESIGN.md`. This
document adds one thing neither of those has: a critical, sequenced build
+ test plan across the remaining phases, plus a risk register spanning
accuracy, profitability, security, and efficiency. Where this disagrees
with the sequencing implied elsewhere, this document's reasoning is stated
explicitly - it doesn't override the design doc's settled decisions, only
its ordering.

## 0. Honest baseline (read this before anything below)

Current evidence for the whole 5-strategy system: **7 of 80 tested
(strategy, instrument) combinations cleared Tier 4**, on a **single
6-month window** (2025 H2), with several of those 7 sitting on **wafer-
thin CI lower bounds** (GBP_JPY: +0.14%, +0.13%). That is not yet a
demonstrated edge - it's the first set of candidates that didn't
immediately fail. Treat everything below as building infrastructure to
find out whether a real edge exists, not as building on top of one that's
already confirmed. This isn't pessimism for its own sake - the whole
point of the validation hierarchy is exactly this kind of skepticism, and
it should be applied to its own output, not just to each strategy.

## 1. The most important gap: out-of-time validation doesn't exist yet

`VALIDATION_HIERARCHY.md`'s Tier 3 (walk-forward) and Tier 4 (bootstrap)
both operate **entirely inside the same 6-month sample**. Walk-forward
slides windows within Jul-Dec 2025; bootstrap resamples trades that all
happened within Jul-Dec 2025. Neither can tell you whether a survivor's
edge is real vs. specific to whatever that particular 6 months happened to
look like (a JPY-crosses/Litecoin idiosyncrasy, a specific volatility
regime, etc.) With 80 combinations tested and only 7 survivors - several
with thin margins - the multiple-comparisons risk is real even after three
tiers of filtering, because all three tiers share the same underlying
6-month sample.

**Recommendation, sequenced ahead of Phase 1 Step 2**: acquire a second,
non-overlapping historical window (a different 6-12 month period, ideally
spanning a visibly different regime - more/less trending, different
volatility) and re-run ONLY the 7 survivors' exact (strategy, params,
instrument) combinations against it, unchanged, as a genuine held-out
test - no re-selection, no re-tuning. A survivor that fails on the new
window is dropped before it ever reaches a weight in Phase 1 Step 2, not
after. This is cheap (7 backtests + bootstraps, not a new 80-combination
sweep) and is the single highest-leverage accuracy action available right
now - more valuable than any amount of further work on the scoring engine
itself, because the scoring engine's whole premise is "trust these
weights," and right now those weights rest on one sample.

## 2. Build phases

### Phase 1 Step 2 — Scoring engine (shadow-computed)

**Build**: `PositionManager` (or similar - Section 1's naming) computing,
per candle, per instrument: each strategy's shadow stance (Section 2.1),
the weighted raw/shaped scores (Section 2.2) using the base metric -
**which candidate is still an open decision, see `SUBTASK_BASE_METRICS.md`**:
four candidates built and tested this session (bootstrap CI lower bound,
two cost-adjusted variants, and a classical-statistics effect-size
candidate), and they meaningfully DISAGREE on ranking in real data - the
deciding test (re-rank each candidate's top picks against out-of-time
data once available) hasn't run yet. Don't hardcode one candidate into
Step 2 until that test has - for the three `k` shape values in parallel.
Logged, not acted on.

**Test plan**:
- Unit-test the shadow-stance state machine against `BacktestEngine.
  _apply_signal`'s existing logic directly - same signals in, same stance
  transitions out, hand-verified on a short crafted signal sequence.
- Unit-test the score formula in isolation (no backtest needed): feed
  known stances + known weights, assert `raw_score`/`shaped_score_k`
  against hand computation for a handful of cases including the
  zero-weight and all-flat edge cases.
- Integration: run shadow-scoring over the real 16-instrument sandbox
  using the (now out-of-time-validated, per Section 1 above) survivor
  weights, and manually inspect a sample of scored candles against what
  the individual strategies were doing at that point - a human sanity
  check before Step 3 wires anything to real money-shaped decisions.
- Explicitly test the RSI/Confluence correlation finding (Section 4.2):
  confirm the scoring engine does NOT silently double-weight them as if
  independent - **resolved this session, see
  `SUBTASK_CROSS_STRATEGY_CORRELATION.md`**: pairwise discount
  (`backtest/strategy_clustering.py::pairwise_discount_weights`) is the
  recommended mechanism, tested against real GBP_JPY data (cluster/group
  was built and compared but not chosen - it over-cancels real
  disagreement between a strong and a correlated-but-weaker strategy).
  Mechanism and decision are ready; wiring it into a real scoring engine
  is still Step 2's job, since Step 2 itself hasn't started.
### Phase 1 Step 3 — Wire scoring → sizing

**Build**: hysteresis thresholds (Section 2.3), continuous rescaling via
`rescale_trade()` (built this session, unused until now), debounce guards
(Section 8.1 - magnitude AND time-based).

**Test plan**:
- This is where `rescale_trade()`'s correctness actually matters in
  practice, not just in isolation - add an integration test that runs a
  full backtest with real rescaling wired to a synthetic, hand-designed
  score sequence and hand-verifies the resulting weighted-average entry
  price and realized P&L through several adds/reduces, the same discipline
  `tests/test_rescale.py` already applies standalone.
- Debounce threshold values: **blocked on real trade-frequency data**
  (Section 4.2 point 4, still not extracted from `data/full_sweep_runs.db`
  - do this extraction now, it's cheap and already-collected data, not a
  new run).
- Regression: re-run the existing 4 single-strategy demo scripts
  (`run_rsi_demo.py` etc.) through the new sizing-aware path with a
  single-strategy weight (weight=1, no ensemble) and confirm results match
  the pre-Phase-1 single-strategy numbers - proves the new mechanism
  degrades correctly to the old behavior in the degenerate case.

### Phase 1 Step 4 — Cross-instrument allocation

**Build**: wire `portfolio.py`'s correlation matrix into real capital
splitting, for real this time (Section 1).

**Test plan**: hand-verified allocation on a small synthetic 2-3
instrument portfolio with known correlations (reuse `test_portfolio.py`'s
identical/negated/independent series pattern - it already proves the
correlation math, extend it to prove the ALLOCATION math on top).

### Phase 2 — Pluggable confidence modules

Build order by cheapest-to-verify first: cost-drag discount (data already
computed) → CI-width discount (data already computed) → instrument-class
discount (small lookup table - **build the table from the real Tier 4
results, not assumption**, given Section 4.2's finding that the survivor
set didn't split cleanly fiat/crypto) → open-position correlation discount
(needs Phase 1 Step 4's cross-instrument piece first, and per Section 4.2,
must include the cross-STRATEGY case, not just cross-instrument).

### Phase 3 — Hard caps, risk appetite, house-money guardrails

Test plan should include deliberately adversarial scenarios: a runaway
score that would breach the cap without the gate, a loss streak that
should trigger the (default-on) downward lifecycle adjustment, an
upward-adjustment scenario checked against the hard 1.5-2x ceiling.

### Phase 4 / Option C — Regime detection

Unchanged from the design doc: explicitly deferred, own future project.

## 3. Parallel track — live-readiness (currently sequenced too late)

`CONTEXT_HANDOFF.md` lists `OandaBroker.get_quote`/`place_market_order`
as "still unstarted" and ranks it below the scoring-engine work. That's
backwards for one reason: it's a pure technical-feasibility question,
independent of whether the confidence-sizing design is finished, and it's
cheap to find out now vs. discovering a live-execution blocker after
months of scoring-engine work. **Recommend running this in parallel with
Phase 1 Step 2, not after Phase 3.**

Also currently missing from every document in this repo - a genuine
**paper-trading validation phase** before any real capital, regardless of
how Phase 1-4 goes:
- Once `get_quote`/`place_market_order` are verified on a practice
  account, run the out-of-time-validated survivors live-but-paper for a
  defined period (propose 4-8 weeks, long enough to see a reasonable trade
  count on the thinner-frequency survivors) logging every live signal
  alongside what the backtest pipeline would have said for the same
  candle, in real time.
- Define an explicit divergence threshold up front (e.g. live fills
  differing from backtest-implied fills by more than X pips/bps on
  average) that would block moving to real capital - decide the number
  before seeing the data, not after.
- This directly operationalizes Section 9.2's "live results should
  undershoot backtest" concern, which is currently stated as a caveat
  with no actual test attached to it.

## 4. Risk register

| Risk | Area | Severity | Mitigation / status |
|---|---|---|---|
| Out-of-time validation doesn't exist - survivors validated on one 6-month sample only | Accuracy | **High** | Section 1 above - sequence before Phase 1 Step 2 |
| RSI/Confluence not independent (avg +0.65 correlation) - risk of double-weighting the same edge | Accuracy | Medium | Found this session; needs a cross-strategy discount, moved into Phase 1 Step 2 above |
| Thin margins on several survivors (GBP_JPY CI lower bound +0.14%) - a small increase in real costs could flip them negative | Profitability | **High** | Don't commit capital until Section 9.1's REAL fee schedule is sourced and re-run against the survivors specifically |
| Real fee schedule still not sourced - `CostModel` currently uses researched-but-generic public rates | Profitability | High | Blocked on your input (OANDA account type, credentials) - unchanged from prior sessions, still open |
| No live-vs-backtest parity test exists | Accuracy / Profitability | Medium-High | Section 3's paper-trading phase, not currently planned anywhere else in the repo |
| `OandaBroker.get_quote`/`place_market_order` never run | Accuracy / Operational | Medium | Sequence in parallel with Phase 1 Step 2 (Section 3), don't leave to the end |
| API credentials via plain env vars; no secrets-management story for the stated multi-user future | Security | Medium (low now, grows with scale) | Fine for single-user/practice-account now; needs a real vault/secrets-manager story before any multi-user or live-capital deployment |
| No kill-switch / circuit breaker (max daily loss, emergency flatten-all) | Security / Operational | **High before any live capital** | Not designed anywhere yet - propose adding to Phase 3 alongside the risk-appetite/cap work, since it's the same "hard gate" architecture already planned there |
| No idempotency / duplicate-order protection if the live process restarts mid-decision | Security / Operational | Medium | Not addressed; relevant once Phase 1 Step 3's live rescaling exists |
| No audit trail of live orders for FCA record-keeping | Security / Regulatory | Medium (grows to High for multi-user) | Not addressed; `RunStore`'s new `fills` table is a reasonable foundation to extend for this |
| `requirements.txt` uses unpinned `>=` version ranges | Security / Efficiency | Low-Medium | Supply-chain/reproducibility risk - pin exact versions once the dependency set stabilizes |
| No CI - tests run manually, no automated regression gate on push | Efficiency / Accuracy | Medium | Cheap to fix - see Section 5 |
| Combination-layer's own grid (Section 2.4) will exceed `MAX_GRID_COMBINATIONS=200` | Efficiency | Low now, will become real | Flagged already in the design doc; revisit when Phase 1 Step 2's grid is actually built, not before |
| Section 9.2 slippage-on-rebalance not modeled distinctly from single-entry slippage | Accuracy | Medium | Explicitly flagged in the design doc as unsolved; relevant once Phase 1 Step 3 ships |

## 5. Beneficial additions worth including (not currently planned anywhere)

- **GitHub Actions CI**: run the full test suite (`python -m tests.X` for
  every module) on every push. Given GitHub sync is pull-only for Claude
  sessions, this is the one piece of automated regression protection that
  doesn't depend on remembering to run tests manually before a push -
  directly serves both accuracy (catches regressions Claude's sandbox
  didn't happen to check) and efficiency (no manual step).
- **Trade-frequency extraction from `data/full_sweep_runs.db`**: already
  flagged as blocking Section 8.1's debounce thresholds - cheap, already-
  collected data, do it opportunistically rather than as its own session.
- **Kill-switch / circuit breaker**: see risk register above - propose
  building alongside Phase 3's cap architecture rather than as a late
  addition, since it's the same kind of hard-gate code.
- **A minimal live dashboard / status view** once Phase 1 Step 3 exists -
  `RunStore` and the persisted sweep data already give a foundation;
  even a simple read-only summary (current weights, current stances,
  current sizes) would make live-readiness testing (Section 3) far easier
  to actually observe rather than only inferable from logs. **Built
  this session, ready to reuse**: `backtest/period_comparison.py`'s
  execution/rendering split (`SUBTASK_VISUALIZATION.md`) is specifically
  designed so a dashboard can consume the same data layer without
  duplicating backtest-running logic.
- **Pin `requirements.txt`** to exact versions once Phase 1's dependency
  set stabilizes (currently just `requests`/`psycopg2-binary`/`ccxt`,
  cheap to do now before it grows).

## 6. Recommended sequencing (concrete "what's next")

1. Extract real trade-frequency data from `data/full_sweep_runs.db`
   (cheap, already-collected, unblocks Section 8.1).
2. Acquire a second out-of-time window and re-validate the 7 survivors
   against it (Section 1) - **the highest-leverage single action
   available right now.**
3. In parallel: test `OandaBroker.get_quote`/`place_market_order` against
   a practice account (Section 3) - independent of 1-2, cheap to
   parallelize.
4. Phase 1 Step 2 (scoring engine, shadow-computed), incorporating the
   cross-strategy correlation discount and using only the out-of-time-
   validated survivors from Step 2 above.
5. Phase 1 Step 3 (wire scoring → sizing) + start the paper-trading
   validation clock (Section 3) as soon as Step 3 produces real sizing
   decisions to compare against a live feed.
6. Phase 1 Step 4, Phase 2, Phase 3 (with the kill-switch folded in) as
   currently sequenced in `CONFIDENCE_SIZING_DESIGN.md`.
7. Real fee schedule sourcing (Section 9.1) - needs to land before any
   capital commitment decision, timed against whenever your OANDA account
   type / credentials situation resolves, not gating the build work above.
