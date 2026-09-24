# Sub-task: Cross-Strategy Correlation

Resolves the gap flagged in `CONFIDENCE_SIZING_DESIGN.md` Section 4.2 and
`ROADMAP.md`: RSI and RsiMacdConfluenceStrategy are not independent votes
(Confluence embeds RSI's own signal as half its logic), so naively summing
both strategies' weighted votes in Section 2.2's `raw_score` formula
double-counts one edge as two. Code: `backtest/strategy_clustering.py`.
Tests: `tests/test_strategy_clustering.py` (7 tests, all passing,
including one using the actual measured correlations from this session,
not synthetic numbers).

## The problem, precisely

Section 2.2's formula: `raw_score = Σ(weight_i * stance_i) / Σ(weight_i)`.
If two strategies vote the same way BECAUSE they share a root signal, both
terms pull the numerator the same direction without contributing genuinely
independent evidence - inflating confidence (and, downstream, position
size) beyond what the real evidence supports.

## Two mechanisms, both built, compared on real data

**1. Cluster/group** (`cluster_strategies` + `collapse_to_cluster_votes`):
single-linkage clustering on the strategy correlation matrix (two
strategies join the same cluster if `|correlation| >= threshold`,
transitively). Every cluster collapses into ONE effective (stance,
weight) pair - `stance` = the cluster's own weight-weighted average
(left continuous, not forced to ±1); `weight` = the MEAN (not sum) of
member weights.

**2. Pairwise discount** (`pairwise_discount_weights`): every strategy
keeps its own individual vote - only its WEIGHT is discounted, by
`(1 - max|correlation| with any already-counted, higher-weight
strategy)`, processed in descending-weight order. No clustering, no
threshold, no merging of stances.

Both computed STATICALLY (once per instrument, from the historical
validation run) - **dynamic/rolling correlation is explicitly deferred to
a future session**, per your instruction. Reason restated: it turns the
correlation lookback window into a new, unvalidated hyperparameter
(Section 2.4's own warning about the combination layer's hyperparameter
surface already growing large), and live recomputation is genuinely new
infrastructure (streaming per-strategy equity curves), not a formula
change - worth a dedicated session, not a rider on this one.

## Real-data comparison (GBP_JPY, actual winning params - not fixed defaults)

Section 4.2's original correlation check used FIXED DEFAULT params for
every strategy. Redone here with each strategy's REAL best-surviving
params on GBP_JPY (`RsiStrategy(period=10, oversold=25, overbought=80)`,
CI-90 lower bound +0.1424%; `RsiMacdConfluenceStrategy
(confirmation_window=10)`, +0.1259%) - **measured correlation: 0.6464**
(close to Section 4.2's fixed-default-param average of 0.65 across all
16 instruments - a useful cross-check that the earlier, cheaper
fixed-param measurement wasn't misleading, even though the precise
per-survivor number is the more correct one to actually use going
forward).

Using the strawman weight formula (base_metric = CI-90 lower bound,
tier_multiplier = 1.0 for both, both Tier 4 survivors):

| Scenario | Naive (uncorrected) | Cluster/group | Pairwise discount |
|---|---|---|---|
| Both agree (RSI +1, Confl +1) | 0.2683 (simple sum) | 0.1341 (one term, stance +1.000) | 0.1869 (RSI 0.1424 + Confl discounted to 0.0445) |
| They disagree (RSI +1, Confl -1) | n/a (naive has no way to express this as a single number) | **0.0082** (0.061 stance x 0.1341 weight - the disagreement nearly cancels) | **0.0979** (0.1424 - 0.0445 - RSI's confident vote still dominates) |

## The real behavioral difference this surfaces

**When they agree**, cluster/group discounts harder (0.1341 vs 0.1869) -
roughly halves the naive sum, vs. pairwise discount's milder ~30%
reduction, because pairwise discount always preserves the top-weighted
strategy's FULL weight and only shaves the correlated follower.

**When they disagree**, the difference is much more consequential:
cluster/group's merged stance (+0.061) nearly cancels the disagreement to
near-zero - RSI's confident +1 vote gets almost entirely washed out by
Confluence's -1, because they're treated as one blended voice. Pairwise
discount preserves most of RSI's signal (0.0979, clearly positive) even
though a correlated, lower-confidence strategy disagrees with it.

**This is the deciding factor.** Cluster/group can erase a real,
meaningful disagreement between a strong signal and a weaker, derivative
one almost entirely - the stronger strategy's genuine conviction
shouldn't be that easily cancelled by the very strategy that's mostly
just a filtered version of it. Pairwise discount's behavior is more
defensible here, though this is a real judgment call, not a provable
fact - stated as a design decision, not asserted as objectively correct.

## Recommendation (revised from last turn, on seeing real numbers)

**Ship pairwise discount as the primary mechanism for Phase 1 Step 2.**
Last turn's preliminary lean toward cluster/group was made before running
real numbers - worth being explicit that seeing the actual comparison
changed the recommendation, not just noting the conclusion. Reasons,
against the four stated goals:

- **Accuracy**: preserves genuine disagreement signal (shown above);
  continuous `(1-|corr|)` discount has no threshold hyperparameter at all
  (cluster/group's threshold=0.5 is itself unvalidated - pairwise discount
  removes that entire class of concern rather than needing to validate a
  cutoff value later).
- **Profitability**: still directly fixes the concrete defect (agreement
  case discounted from 0.2683 to 0.1869, not left uncorrected at 0.2683) -
  protects against oversizing on what's really one edge counted twice,
  while not overcorrecting into near-total signal loss the way cluster/
  group's disagreement case does.
- **Security/operational**: both mechanisms are static, deterministic,
  auditable - roughly equal here. One real risk specific to pairwise
  discount, stated plainly: the priority ORDER depends on the weights
  themselves, which come from the same validation hierarchy being
  corrected - if two strategies' weights are close/noisy, small
  differences could flip which one is "higher priority" and gets full
  weight vs. discount. Not disqualifying, but worth testing for stability
  once Phase 1 Step 2's real weights exist (does re-running the hierarchy
  ever flip RSI/Confluence's relative CI-lower-bound ranking on the same
  instrument? - cheap to check, not done yet).
- **Efficiency**: both are O(n^2) at most for n active strategies (5
  today) - trivially cheap either way, not a deciding factor.

**Keep cluster/group's code** (already built, tested, cheap to maintain)
as a diagnostic/reporting view even though it won't drive the primary
math - "these strategies are functionally one cluster" is a useful
human-readable summary for monitoring/dashboards (ROADMAP.md Section 5's
proposed minimal live dashboard) even when pairwise discount is what
actually adjusts weights.

## Testing plan (built and passing, `tests/test_strategy_clustering.py`)

- Hand-computed cluster/group cases: simple two-cluster split, transitive
  single-linkage (A-C below threshold but merged via B), all-singleton
  no-correlation regression case.
- Hand-computed pairwise-discount cases: 3-strategy discount chain,
  no-correlation regression case (weights must pass through unchanged).
- Real-measured-correlation integration check: confirms RSI/Confluence
  (0.65 avg) actually cluster together and MACD (0.08-0.27 avg) does not,
  using this session's real numbers, not synthetic ones.
- **Not yet done** (next session, once Phase 1 Step 2's scoring engine
  exists to embed either mechanism into): an end-to-end shadow-scoring
  integration test comparing scored candles WITH and WITHOUT the discount
  applied, per ROADMAP.md's Phase 1 Step 2 test plan.

## Open items for a future session

- Dynamic/rolling correlation (deferred, per your instruction) - once
  built, re-run this same GBP_JPY comparison with a rolling window and
  check whether 0.6464 is stable across sub-periods of even the single
  2025 H2 window, before trusting it across genuinely different regimes
  (ties directly into the out-of-time validation work).
- Priority-order stability check (flagged above) once real Phase 1 Step 2
  weights exist.
- Multiple surviving param sets per (strategy, instrument) pair - Section
  4's weight formula is written as `weight(strategy, instrument)`,
  singular, but GBP_JPY alone has 4 RSI survivors and 2 Confluence
  survivors with different params. This demo used each strategy's single
  BEST (highest CI lower bound) survivor as representative - a reasonable
  default, but the real system needs an explicit decision on how multiple
  survivors of the same (strategy, instrument) combine into one weight,
  which isn't addressed anywhere in the current design and should be
  scoped as its own small sub-task before Phase 1 Step 2 ships.
