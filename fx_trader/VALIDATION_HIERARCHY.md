# Validation Hierarchy

Gates what's allowed to reach `RunStore.record_run()`. Cheap checks run
against the whole candidate space first; each tier is more expensive and
runs only on survivors of the tier before it. Implemented by
`backtest/validation_orchestrator.py`.

**GATING LOGIC IS UNCHANGED FROM THE ORIGINAL SPEC** (see Section "Extended
metrics" below for what's new). The Tier 0/1/2/3 pass/fail conditions below
are exactly what they were before the metrics-expansion session - the
extended metrics are reported alongside every candidate, not gated on.
That's a deliberate choice, not an oversight.

## Tiers

**Tier 0 - Sanity** (per candidate, near-free): `total_trades >= 20`.
Below this, every other metric is dominated by noise.

**Tier 1 - Light metrics, full grid** (`metrics.py`): `total_return_pct > 0`,
`profit_factor > 1` (or `None`), `max_drawdown_pct < 20%`. Tiers 0+1 are
computed together from one `run_sensitivity_analysis()` call. Every
candidate's full `Metrics` object - ~25 fields - is available on the
`GridPointResult`, not just the three gated on.

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
list. That single re-run feeds TWO things, at no extra backtest cost:
1. Bootstrapped (resample, 2000 iterations). Gate: 90% CI lower bound for
   `total_return_pct` > 0, and `probability_of_loss < 40%`. Every
   finalist's bootstrap CI is now used consistently as the base metric for
   confidence-weighting purposes (see "Weighting-formula note" below) -
   not just the ones that pass.
2. Rolling Sharpe/Sortino/max-drawdown (`rolling.py`, default 500-period
   window) - a diagnostic, not part of the gate. Kept to Tier 3 finalists
   only (never the full Tier 1 grid).

**Persist**: only Tier 4 survivors call `RunStore.record_run(...,
validation_stage="tier4_bootstrap")`.

## Thresholds (revisit as more real data accumulates)

`MIN_CLOSED_TRADES=20`, `MAX_DRAWDOWN_CEILING_PCT=20`,
`PROBABILITY_OF_LOSS_CEILING_PCT=40`, `DEFAULT_ROLLING_WINDOW=500`. All
defined as constants at the top of `validation_orchestrator.py`.

## Extended metrics

`backtest/metrics.py` computes ~25 fields for every Tier 0/1 candidate,
free relative to the backtest that already ran: risk-adjusted ratios
(Sortino, Calmar, Omega, recovery factor), drawdown shape (duration, Ulcer
Index, still-underwater flag), per-trade economics (expectancy, payoff
ratio, streaks, avg duration, cost drag %), tail/distribution risk (VaR,
CVaR, tail ratio, skewness, kurtosis), Kelly fraction (informational only),
exposure %, and an optional buy & hold benchmark/alpha.

`backtest/rolling.py` (Tier 3 finalists only) and `backtest/portfolio.py`
(cross-instrument AND, as of this session, cross-strategy correlation - see
"Real-data run" below) are kept as separate modules for their different
cost/scope characteristics.

## Service tiers (bronze/silver/gold) - a SEPARATE axis from the above

`backtest/service_tiers.py` maps each metric to a minimum subscription
tier allowed to see it, independent of cost-tier placement. **This is a
starting allocation, not a decision** - expect it to be revised once the
subscription tiers are actually designed.

## Weighting-formula note (added this session - see CONTEXT_HANDOFF.md Section 4d)

`CONFIDENCE_SIZING_DESIGN.md` Section 4's strawman `tier_multiplier`
weighting formula, as originally written, used the raw (uncorrected) Tier-1
grid-winner return as the base metric for any candidate that didn't reach
Tier 4 - including Tier-3 finalists that reached Tier 4 evaluation and were
REJECTED there. That let a rejected candidate outscore a real Tier 4
survivor (a concrete real-data example: `BollingerBandsStrategy`/LTC_GBP,
rejected, scored 9.96 vs. `RsiStrategy`/LTC_GBP, an actual survivor, at
5.62). Fix: use the bootstrap CI-90 lower bound - already computed during
Tier 4 evaluation for every finalist, not just passing ones - as the base
metric for every candidate that reaches Tier 3+. This needs to be carried
into the real scoring engine when `CONFIDENCE_SIZING_DESIGN.md` Phase 1
Step 2 is built, not left only in this session's sanity-check script.

## Considered and deliberately NOT added

- **Sterling / Burke ratios**: need drawdown-event segmentation, not just
  running-max tracking - real added scope, likely low marginal value over
  Calmar at this stage.
- **Information ratio**: needs a defined benchmark/index return series -
  only single-instrument buy & hold exists so far.
- **R-multiple expectancy**: needs a defined "risk per trade" (stop-loss
  distance) the strategy layer doesn't have.
- **Gain-to-pain ratio**: essentially redundant with Omega/profit factor.
- **Parametric (variance-covariance) VaR**: the historical/percentile VaR
  already added doesn't assume a return distribution shape, which matters
  given this project's crypto data is visibly fat-tailed.

## Status

**Real-data run (this session, extends the prior SMA-only run):** all 5
strategies (SMA crossover, RSI, MACD, RSI+MACD confluence, Bollinger Bands)
run against all 16 real 2025 H2 sandbox instruments -
80 (strategy, instrument) combinations, 186.6s total. **Result: 7
(strategy, instrument) pairs cleared Tier 4** - the first Tier 4 survivors
this project has produced. All 7 are RSI or RSI+MACD Confluence, all on
JPY crosses or Litecoin pairs (GBP_JPY, USD_JPY, LTC/USDT, LTC/GBP). SMA
crossover: 0/16 (unchanged from the prior session). MACD and Bollinger
Bands: 0/16 each, despite both reaching Tier 3 repeatedly - always failed
the bootstrap gate. Full breakdown, param grids used, and the Section 4.2
correlation/weighting-formula checks: `CONTEXT_HANDOFF.md` Section 4d.
