<!-- version: 261003 -->
# Validation Hierarchy

Gates what's allowed to reach `RunStore.record_run()`. Cheap checks run
against the whole candidate space first; each tier is more expensive and
runs only on survivors of the tier before it. Implemented by
`backtest/validation_orchestrator.py`.

## Tiers

**Tier 0 - Sanity** (per candidate, near-free): `total_trades >= 20`.
Below this, every other metric is dominated by noise.

**Tier 1 - Light metrics, full grid** (`metrics.py`): `total_return_pct > 0`,
`profit_factor > 1` (or `None`), `max_drawdown_pct < 20%`. Computed
together with Tier 0 from one `run_sensitivity_analysis()` call. Every
candidate's full `Metrics` object (~25 fields) is on the `GridPointResult`,
not just the three gated on.

**Tier 2 - Plateau check** (`sensitivity.py::neighbor_gap()`, free - reuses
the Tier 1 grid): keep a candidate only if `neighbor_gap` is defined (not
a grid edge) and within 1 std dev of the grid's score spread.

**Tier 3 - Time-generalisation** (`walk_forward.py`): re-run walk-forward
with `param_grid` narrowed to Tier 1+2 survivors. A "finalist" is any
param set chosen as a window winner at least once, provided the combined
out-of-sample return is positive.

**Tier 4 - Path/luck check + rolling diagnostics** (`bootstrap.py` +
`rolling.py`, most expensive per-candidate, fewest candidates): each
finalist is re-run once against the full history for a concrete trade
list, feeding two things at no extra backtest cost:
1. Bootstrap (resample, 2000 iterations). Gate: 90% CI lower bound for
   `total_return_pct` > 0, and `probability_of_loss < 40%`. Every
   finalist's CI is computed regardless of pass/fail (used as the base
   metric for confidence-weighting - see "Weighting-formula note" below).
2. Rolling Sharpe/Sortino/max-drawdown (`rolling.py`, default 500-period
   window) - a diagnostic, not part of the gate. Tier 3 finalists only.

**Persist**: only Tier 4 survivors call `RunStore.record_run(...,
validation_stage="tier4_bootstrap")`.

## Thresholds (revisit as more real data accumulates)

`MIN_CLOSED_TRADES=20`, `MAX_DRAWDOWN_CEILING_PCT=20`,
`PROBABILITY_OF_LOSS_CEILING_PCT=40`, `DEFAULT_ROLLING_WINDOW=500`. All
defined as constants at the top of `validation_orchestrator.py`.

## Extended metrics

`backtest/metrics.py` computes ~25 fields per Tier 0/1 candidate, free
relative to the backtest that already ran: risk-adjusted ratios (Sortino,
Calmar, Omega, recovery factor), drawdown shape (duration, Ulcer Index,
still-underwater flag), per-trade economics (expectancy, payoff ratio,
streaks, avg duration, cost drag %), tail/distribution risk (VaR, CVaR,
tail ratio, skewness, kurtosis), Kelly fraction (informational only),
exposure %, optional buy & hold benchmark/alpha.

`backtest/rolling.py` (Tier 3 finalists only) and `backtest/portfolio.py`
(cross-instrument and cross-strategy correlation) stay separate modules
for their different cost/scope characteristics.

## Annualisation-dependent metrics (MC-1, chat 14)

Tier 0-4 gates use only annualisation-independent quantities (trades, return, profit factor, drawdown, bootstrap CI and probability of loss). Sharpe, Sortino, Calmar, the rolling metrics and the portfolio Sharpe depend on `periods_per_year`; the FX constant (252*24 = 6,048) understates the observed 6,224 bars/year (x1.029; Sharpe-family ~1.4%). Flagged, not changed; variants are compared in chat 28. Detail: `CONTEXT_HANDOFF.md` Section 4f.7.

## Service tiers (bronze/silver/gold) - a SEPARATE axis from the above

`backtest/service_tiers.py` maps each metric to a minimum subscription
tier, independent of cost-tier placement. Starting allocation, not a
final decision - see `CONTEXT_HANDOFF.md`'s open threads.

## Weighting-formula note

`CONFIDENCE_SIZING_DESIGN.md` Section 4's strawman `tier_multiplier`
formula originally used the raw Tier-1 grid-winner return as base_metric
for any candidate short of Tier 4 - including rejected Tier-3 finalists,
letting a rejected candidate outscore a real Tier 4 survivor. Fix: use the
bootstrap CI-90 lower bound - already computed during Tier 4 evaluation
for every finalist, not just passing ones - as the base metric for every
candidate reaching Tier 3+. Full numbers: `CONTEXT_HANDOFF.md` Section 4d.
Still needs to land in the real scoring engine, not just the sanity-check
script.

## Considered and deliberately NOT added

- **Sterling/Burke ratios**: need drawdown-event segmentation, likely low
  marginal value over Calmar at this stage.
- **Information ratio**: needs a defined benchmark/index return series -
  only single-instrument buy & hold exists.
- **R-multiple expectancy**: needs a defined risk-per-trade (stop-loss
  distance) the strategy layer doesn't have.
- **Gain-to-pain ratio**: essentially redundant with Omega/profit factor.
- **Parametric (variance-covariance) VaR**: the historical/percentile VaR
  already added doesn't assume a return-distribution shape, which matters
  given this project's crypto data is visibly fat-tailed.

## Status

Real-data results (7 Tier 4 survivors, full breakdown, param grids used):
`CONTEXT_HANDOFF.md` Section 4d.
