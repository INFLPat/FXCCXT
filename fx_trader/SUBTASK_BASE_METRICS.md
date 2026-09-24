# Sub-task: Base-Metric Review

Resolves `CONFIDENCE_SIZING_DESIGN.md` Section 5's gap: the table there
seriously discusses 7 candidates while `metrics.py` actually computes
~25+ fields, and no cross-role blend other than "point estimate x trust
discount" (already validated via the CI-lower-bound fix) had been built
or tested. Code: `backtest/base_metrics.py`. Tests:
`tests/test_base_metrics.py` (5 tests, all passing).

Per your instruction: role-grouped the full field list, built and tested
the meaningful cross-role blends now, full cartesian product deferred.

## All Metrics fields, grouped by role

| Role | Fields | Answers |
|---|---|---|
| **Magnitude** (edge size) | `total_return_pct`, `trade_expectancy_pct`, `profit_factor`, `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`, `omega_ratio`, `recovery_factor`, `payoff_ratio`, `alpha_vs_buy_hold_pct` | How big is the edge, on average? |
| **Reliability** (trust) | bootstrap CI width/lower-bound, `probability_of_loss` (both from `bootstrap.py`, not `Metrics` fields), `total_trades`, `skewness`, `kurtosis`, `win_rate_pct` | How much should I trust the magnitude number? |
| **Drawdown-shape** (risk) | `max_drawdown_pct`, `ulcer_index`, `max_drawdown_duration`, `avg_drawdown_duration`, `downside_deviation`, `var_95`, `cvar_95`, `tail_ratio` | How painful is the ride, separate from average return? |
| **Cost-realism** | `cost_drag_pct`, `exposure_pct` | How much of the edge is real vs. eaten by friction/idle capital? |
| **Sizing-informational only** | `kelly_fraction` | Not a "how good" signal - already flagged informational-only, not a weight input |
| **Excluded from blends** | `total_commission_paid`, `avg_win`/`avg_loss` (currency, superseded by normalized forms), `max_consecutive_wins/losses`, `avg_trade_duration_hours`, `still_in_drawdown_at_end`, `buy_hold_return_pct` (benchmark reference, not about the strategy) | Informational/gating/benchmark, not "how good is this edge" signals |

**Meaningful cross-role blends** (same-role blends generally redundant -
e.g. Sharpe+Sortino measure similar things - deprioritized per the
curated approach):
- Magnitude x Reliability - **already built and validated this session**
  (the bootstrap-CI-lower-bound fix in `SUBTASK_CROSS_STRATEGY_
  CORRELATION.md`'s sibling defect).
- Magnitude x Cost-realism - **was a total gap, built this session**
  (`cost_adjusted()`).
- Magnitude x Reliability via classical statistics (not bootstrap) -
  **Section 5's explicitly-flagged-as-missing candidate, built this
  session** (`effect_size_over_sample_size()`).
- Magnitude x Drawdown-shape - already exists as Calmar/recovery_factor,
  nothing new needed.
- Drawdown-shape x Reliability - lower priority, deferred to the full
  cartesian pass (less obviously actionable - "how painful, and how
  trustworthy is that pain estimate" doesn't map as directly onto a
  single scoring weight as the above three).

## Four candidates built and tested

1. **`ci_lower_bound`** - bootstrap CI-90 lower bound of `total_return_pct`
   (the currently-used one, already fixed for the tier-consistency defect).
2. **`ci_lower_bound_cost_adjusted`** - `ci_lower_bound` further discounted
   by `cost_drag_pct` - three factors blended (magnitude, reliability,
   cost-realism).
3. **`expectancy_cost_adjusted`** - `trade_expectancy_pct` discounted by
   `cost_drag_pct` - cheaper (no bootstrap needed), magnitude x
   cost-realism only, no reliability discount.
4. **`effect_size`** - `mean_trade_pnl / standard_error` - magnitude x
   reliability via classical statistics rather than resampling; a
   genuinely different assumption set than bootstrap (assumes the
   variance estimate generalizes forward; bootstrap doesn't need that
   assumption since it resamples the actual trades) - built specifically
   as an independent cross-check, not a replacement.

## Real-data comparison: all 17 survivors, every candidate

Full numbers computed fresh (not from persisted JSON) against
`data/sandbox_2025h2.db`. Two honest findings, one null-ish and one real:

**Finding 1 (null-ish, still worth knowing): cost-adjustment barely moves
`ci_lower_bound`'s rankings in this sample.** `ci_lower_bound_cost_
adjusted` produced the IDENTICAL rank order to `ci_lower_bound` on every
single one of the 17 survivors. Cause: `cost_drag_pct` is a flat 0.0% for
every GBP_JPY/USD_JPY survivor (FX commission at this position size is
genuinely negligible against realized trade P&L under the current generic
`CostModel`) and only varies 6.1-10.5% across the LTC survivors - not
enough spread in this specific sample to flip anything. **Not evidence the
idea is wrong** - worth re-testing once Section 9.1's REAL fee schedule
(not the generic researched public rates) is sourced, since a real OANDA
Core commission or a higher-tier crypto fee could show materially more
drag than what's modeled here.

**Finding 2 (real, actionable): `expectancy_cost_adjusted` and
`effect_size` both meaningfully DISAGREE with `ci_lower_bound` on which
survivor is best.** Concrete examples:
- `RsiStrategy`/LTC_GBP/(period=10, oversold=30, overbought=70) - the
  single BEST survivor by `ci_lower_bound` (rank #1, +5.62%) - drops to
  rank #8 under `expectancy_cost_adjusted`, because its per-trade
  expectancy is unremarkable (0.183%) relative to others despite its
  bootstrap CI being the widest/most favorable.
- `RsiStrategy`/USD_JPY/(period=14, oversold=30, overbought=75) - a
  middling `ci_lower_bound` rank (#8) - jumps to rank #1 under
  `effect_size`, because its 23 trades show unusually LOW variance
  relative to their mean, which the classical t-statistic rewards more
  than bootstrap resampling does for the same data.

This is real evidence for Section 5's existing "don't commit to one
metric, let results decide" leaning - not caution for its own sake, an
actual demonstrated disagreement between reasonable candidates.

## Recommendation: don't pick a winner yet - here's the actual deciding test

Given the real disagreement above, picking one candidate now would be
exactly the kind of premature commitment the validation hierarchy's whole
philosophy argues against. **The right test**: once out-of-time data
exists (`ROADMAP.md` Section 1 / `run_out_of_time_validation.py`), re-rank
all four candidates' TOP picks against the out-of-training windows and
check which candidate's #1-ranked survivor per instrument actually holds
up best out-of-sample. That's a real, decisive answer instead of a-priori
reasoning about which formula "should" be better. Sequence this
immediately after out-of-time data is available, before Phase 1 Step 2
commits to one base metric.

## Deferred to a later session (full cartesian product)

Per your instruction - the framework above makes this tractable when
wanted: for each of the ~25 fields, classify its role (table above), then
only generate cross-role pairs (same-role pairs are low-priority by
default, stated reason: measure similar things). That's roughly 10x15 +
10x8 + 10x2 + 15x8 + 15x2 + 8x2 candidate pairs across the four non-
sizing-informational roles (~450 before filtering obviously-degenerate
combinations) - tractable to generate and screen programmatically in a
dedicated session, not attempted here.

## Testing plan (built, `tests/test_base_metrics.py`)

- `cost_adjusted`: hand-computed discount, cost_drag>100% floored at zero
  discount (not a sign flip), None-propagation from either input.
- `effect_size_over_sample_size`: hand-computed against a 3-trade case,
  edge cases (fewer than 2 trades, zero-variance), and a sample-size-
  awareness check (doubling trade count at the same distribution must
  raise the effect size - the whole point of the metric existing).
- **Not yet done**: the out-of-time re-ranking test proposed above -
  blocked on the same real multi-year data as Sub-task cross-strategy
  correlation's remaining open items.
