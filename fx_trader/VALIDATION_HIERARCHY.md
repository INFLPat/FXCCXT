# Validation Hierarchy

Gates what's allowed to reach `RunStore.record_run()`. Cheap checks run
against the whole candidate space first; each tier is more expensive and
runs only on survivors of the tier before it. Implemented by
`backtest/validation_orchestrator.py`.

**GATING LOGIC IS UNCHANGED FROM THE ORIGINAL SPEC** (see Section "Extended
metrics" below for what's new). The Tier 0/1/2/3 pass/fail conditions below
are exactly what they were before this session's metrics expansion - the
new metrics are reported alongside every candidate, not gated on. That's a
deliberate choice, not an oversight: with 0/16 real instruments currently
clearing Tier 4 (see Section "Status"), adding new hard gates blind - before
seeing their real distributions on this data - risks either making
everything fail for a different, unexamined reason, or being redundant with
the existing three gates. Revisit once there's a reason grounded in what
the new metrics actually show.

## Tiers

**Tier 0 - Sanity** (per candidate, near-free): `total_trades >= 20`.
Below this, every other metric is dominated by noise.

**Tier 1 - Light metrics, full grid** (`metrics.py`): `total_return_pct > 0`,
`profit_factor > 1` (or `None`), `max_drawdown_pct < 20%`. Tiers 0+1 are
computed together from one `run_sensitivity_analysis()` call. Every
candidate's full `Metrics` object - now ~25 fields, see "Extended metrics"
below - is available on the `GridPointResult`, not just the three gated on.

**Tier 2 - Plateau check** (`sensitivity.py::neighbor_gap()`, free - reuses
the Tier 1 grid): keep a candidate only if `neighbor_gap` is defined (not a
grid edge with nothing to compare) and within 1 std dev of the grid's score
spread.

**Tier 3 - Time-generalisation** (`walk_forward.py`): re-run walk-forward
with `param_grid` narrowed to Tier 1+2 survivors only. A "finalist" is any
param set chosen as a window winner at least once, provided the combined
out-of-sample return is positive.

**Tier 4 - Path/luck check + rolling diagnostics** (`bootstrap.py` +
`rolling.py`, most expensive per-candidate, runs on the fewest): each
finalist is re-run once against the full history to get a concrete trade
list. That single re-run now feeds TWO things, at no extra backtest cost:
1. Bootstrapped (resample, 2000 iterations). Gate: 90% CI lower bound for
   `total_return_pct` > 0, and `probability_of_loss < 40%`. The bootstrap's
   `TRACKED_METRICS` grew this session (see below) - every finalist's
   persisted `metrics` now carries observed value + 90% CI for each one,
   not just the original three.
2. Rolling Sharpe/Sortino/max-drawdown (`rolling.py`, default 500-period
   window) - a diagnostic, not part of the gate. Kept to Tier 3 finalists
   only (never the full Tier 1 grid) specifically because storing a full
   rolling series for every grid point across every instrument is a lot of
   dead data for something that's a chart, not a scalar - see
   `rolling.py`'s docstring.

**Persist**: only Tier 4 survivors call `RunStore.record_run(...,
validation_stage="tier4_bootstrap")`.

## Thresholds (revisit as more real data accumulates)

`MIN_CLOSED_TRADES=20`, `MAX_DRAWDOWN_CEILING_PCT=20`,
`PROBABILITY_OF_LOSS_CEILING_PCT=40`, `DEFAULT_ROLLING_WINDOW=500`. All
defined as constants at the top of `validation_orchestrator.py`.

## Extended metrics (added this session)

`backtest/metrics.py` computes all of the following for every Tier 0/1
candidate - free, since it's pure post-processing of a backtest that
already ran, same cost class as the original Sharpe/profit-factor/drawdown:

- **Risk-adjusted ratios**: Sortino, Calmar, Omega, recovery factor.
- **Drawdown shape**: duration (max/avg, in periods), Ulcer Index,
  still-underwater-at-end flag.
- **Per-trade economics**: expectancy (currency and %), payoff ratio,
  win/loss streaks, average trade duration (real wall-clock hours, parsed
  from each trade's own timestamps), cost drag % (commission as a share of
  gross pre-cost P&L).
- **Tail/distribution risk** (trade-P&L based, not period-return based -
  see `metrics.py` docstring for why the two are kept separate): historical
  VaR/CVaR at 95%, tail ratio, skewness, excess kurtosis.
- **Kelly fraction** - informational only, not a leverage recommendation;
  unreliable on small trade counts by construction, flagged in-line rather
  than suppressed.
- **Exposure %** - needs `BacktestResult.periods_in_market`, a new field on
  the engine's result (additive, doesn't change any existing behavior).
- **Buy & hold benchmark + alpha** - only populated if `compute_metrics()`
  is given the original `candles`; `None` otherwise, no gate impact.

`backtest/rolling.py` (Tier 3 finalists only - see Tier 4 above) and
`backtest/portfolio.py` (cross-instrument correlation/diversification, NOT
part of the per-instrument gate at all - see its own docstring) are kept as
separate modules specifically so their different cost/scope characteristics
stay visible rather than being buried inside `metrics.py`.

## Service tiers (bronze/silver/gold) - a SEPARATE axis from the above

`backtest/service_tiers.py` maps each metric to a minimum subscription tier
allowed to see it. This is independent of the cost-tier placement above by
design: a metric can be cheap to compute (cost-tier: Tier 1) and still be
gated to a higher subscription tier (service-tier: gold) as a pure product
decision, and vice versa - e.g. bootstrap shuffling is genuinely expensive
(cost-tier: Tier 4) but was deliberately left available to silver, not
gold-only, in the initial allocation.

**This is a starting allocation, not a decision** - expect it to be revised
once the subscription tiers are actually designed. Changing an entry in
`service_tiers.py` changes nothing about how anything is computed; that's
the point of keeping it a separate, small, "dumb" registry rather than
threading tier checks through `compute_metrics()`/`bootstrap.py`/the
orchestrator directly.

## Considered and deliberately NOT added this session

- **Sterling / Burke ratios**: both need identifying and averaging the N
  largest *distinct* drawdown events, not just the single running max this
  project already tracks - real added scope (drawdown-event segmentation),
  likely low marginal value over Calmar at this stage. Revisit if Calmar
  alone proves insufficient once real survivors exist to compare against.
- **Information ratio**: needs a defined benchmark/index return series.
  Only a single-instrument buy & hold exists so far (see "Extended metrics"
  above) - a real benchmark (e.g. a GBP trade-weighted index, or an
  equal-weight crypto basket) is future `portfolio.py` scope, not yet built.
- **R-multiple expectancy**: needs a defined "risk per trade" (a stop-loss
  distance), which the strategy layer doesn't have - `SmaCrossoverStrategy`
  has no stop-loss concept at all. Blocked on that missing prerequisite,
  not silently skipped.
- **Gain-to-pain ratio**: essentially redundant with Omega/profit factor
  already added (same "sum of gains vs. sum of losses" shape, computed at
  a slightly different resolution) - didn't clear the bar for adding a
  fourth near-duplicate ratio.
- **Parametric (variance-covariance) VaR**: the historical/percentile VaR
  already added doesn't assume a return distribution shape, which matters
  given this project's own crypto data is visibly fat-tailed - a normal-
  distribution assumption would be a downgrade, not an upgrade.

## Status

Built and run against all 16 real 2025 H2 sandbox instruments with
`SmaCrossoverStrategy` (`run_validation_hierarchy_real_data.py`) prior to
this session's metrics work. Result: 0/16 instruments produced a Tier 4
survivor - every apparent in-sample edge failed the bootstrap gate. See
`CONTEXT_HANDOFF.md` for the full breakdown. This session's changes don't
alter that result on their own (no gating logic changed) - they add
reporting depth for whatever the next strategy family's candidates look
like when re-run through the hierarchy.
