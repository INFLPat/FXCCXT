# Validation Hierarchy

Gates what's allowed to reach `RunStore.record_run()`. Cheap checks run
against the whole candidate space first; each tier is more expensive and
runs only on survivors of the tier before it. Implemented by
`backtest/validation_orchestrator.py` (built and run against real data -
see `CONTEXT_HANDOFF.md`).

## Tiers

**Tier 0 - Sanity** (per candidate, near-free): `total_trades >= 20`.
Below this, every other metric is dominated by noise.

**Tier 1 - Light metrics, full grid** (`metrics.py`): `total_return_pct > 0`,
`profit_factor > 1` (or `None`), `max_drawdown_pct < 20%`. Tiers 0+1 are
computed together from one `run_sensitivity_analysis()` call - Tier 0 is
just an extra filter on the same grid.

**Tier 2 - Plateau check** (`sensitivity.py::neighbor_gap()`, free - reuses
the Tier 1 grid): keep a candidate only if `neighbor_gap` is defined (not a
grid edge with nothing to compare) and within 1 std dev of the grid's score
spread. A gap far above that is an isolated spike - fitted to this
dataset's noise, reject regardless of how good Tier 1 looked.

**Tier 3 - Time-generalisation** (`walk_forward.py`): re-run walk-forward
with `param_grid` narrowed to Tier 1+2 survivors only. A "finalist" is any
param set chosen as a window winner at least once, provided the walk-
forward run's combined out-of-sample return is positive.

**Tier 4 - Path/luck check** (`bootstrap.py`, most expensive per-candidate,
runs on the fewest): each finalist is re-run once against the full history
to get a concrete trade list, then bootstrapped (resample, 2000 iterations).
Gate: 90% CI lower bound for `total_return_pct` > 0, and
`probability_of_loss < 40%`.

**Persist**: only Tier 4 survivors call `RunStore.record_run(...,
validation_stage="tier4_bootstrap")`.

## Thresholds (revisit as more real data accumulates)

`MIN_CLOSED_TRADES=20`, `MAX_DRAWDOWN_CEILING_PCT=20`,
`PROBABILITY_OF_LOSS_CEILING_PCT=40`. All defined as constants at the top
of `validation_orchestrator.py`.

## Status

Built and run against all 16 real 2025 H2 sandbox instruments with
`SmaCrossoverStrategy` (`run_validation_hierarchy_real_data.py`). Result:
0/16 instruments produced a Tier 4 survivor - every apparent in-sample edge
failed the bootstrap gate. See `CONTEXT_HANDOFF.md` for the full breakdown
and what it does/doesn't imply.
