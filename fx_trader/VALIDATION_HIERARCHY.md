# Validation Hierarchy

Sits above the raw market data layer (`data/store.py`) and above the persisted
run/results layer (`data/run_store.py`). Governs what's allowed to reach
`RunStore.record_run()` at all: cheap checks run against the *whole*
candidate space first; each successive tier is more expensive and runs only
against whatever survived the tier before it. Nothing from the bulk-filtering
tiers is ever persisted - only candidates that clear the full hierarchy are,
tagged with the tier they reached (`validation_stage`).

Every tier maps onto a tool that already exists and is already tested -
this is an explicit *order and gate criteria* on top of them, not new
computation.

## The tiers

**Tier 0 - Sanity gate** (per single run, near-free)
- Engine completed without error
- `len(result.closed_trades) >= 20` (arbitrary but reasoned floor - below
  this, every other metric is dominated by noise, not signal)
- Fail either check -> discard immediately, compute nothing further

**Tier 1 - Light single-run metrics** (`backtest/metrics.py::compute_metrics()`)
- `total_return_pct > 0`
- `profit_factor > 1` (or `None` - i.e. no losing trades at all - also passes)
- `max_drawdown_pct` under a chosen ceiling (e.g. 20% - revisit once real
  data gives a feel for typical drawdowns across the sandbox instruments)
- This is exactly what a full `sensitivity.py` grid sweep already computes
  per point. Tier 1 = "run the grid, keep only points clearing the bar."
  Cheapest possible filter, applied to the *entire* search space -
  every instrument x strategy x parameter combination.

**Tier 2 - Plateau check** (`sensitivity.py::neighbor_gap()`)
- Reuses the Tier 1 grid - no new backtests run
- `neighbor_gap` within ~1 std of the grid's own std dev -> plateau, proceed
- `neighbor_gap` far above that -> isolated spike, almost certainly fitted
  to this dataset's noise - reject regardless of how good Tier 1 looked
- Free: a diagnostic on data already in hand from Tier 1

**Tier 3 - Time-generalisation check** (`backtest/walk_forward.py`)
- Re-run walk-forward with `param_grid` narrowed to *only* the Tier 1+2
  survivors - not the full original grid. Cost scales with how many
  candidates survived filtering, not with the original search space size.
- Gate: combined out-of-sample `total_return_pct > 0`, and no severe,
  consistent in-sample-vs-out-of-sample gap across windows

**Tier 4 - Path/luck check** (`backtest/bootstrap.py`)
- Most expensive per-candidate (thousands of resamples), but by now only a
  handful of finalists remain - the heaviest tool runs least often
- Gate: resample-mode 90% CI for `total_return_pct` clears zero, and
  `probability_of_loss` comfortably under 50%
- Shuffle mode is informational here, not a strict gate: it flags whether
  the *actual* historical path was an unusually lucky or unlucky ordering
  of the same trades - worth knowing before trusting the headline number,
  but not itself a pass/fail criterion

**Persist** - only Tier 4 survivors call
`RunStore.record_run(..., validation_stage="tier4_bootstrap", metrics={...})`.
Nothing from Tiers 0-2's bulk filtering pass is ever written - that noise is
exactly what this hierarchy exists to keep out of the store.

## Flow

```
ALL candidates (every instrument x strategy x param combination)
        |
        v
  Tier 0: sanity  ----fail---->  discard, no record
        |
       pass
        v
  Tier 1: light metrics, full grid  ----fail---->  discard
        |
       pass
        v
  Tier 2: neighbor_gap (reuses Tier 1 grid)  ----spike---->  discard
        |
     plateau
        v
  Tier 3: walk-forward, grid narrowed to survivors  ----fails OOS---->  discard
        |
   survives OOS
        v
  Tier 4: bootstrap, finalists only  ----CI includes large loss risk---->  discard
        |
      clears
        v
  RunStore.record_run(validation_stage="tier4_bootstrap")
```

## Why this order

Ordered strictly by cost, cheapest first, so nothing expensive ever runs on
a candidate a cheap check would already have rejected. Across the sandbox's
16 instruments and however many strategies/parameter combinations get tried,
this keeps the tweak-and-refine loop fast, while `RunStore` stays a clean
record of things that survived real scrutiny - not backtest-attempt noise.

## What exists vs. what doesn't yet

**Exists, tested:**
- Every individual tool for every tier (`metrics.py`, `sensitivity.py`,
  `walk_forward.py`, `bootstrap.py`) - built and verified in earlier sessions
- `RunStore` (`data/run_store.py`) - can persist a run tagged with whichever
  `validation_stage` it reached; doesn't enforce the tiering itself
- `FxStore.get_candles_multi()` - multi-instrument query helper, ready for
  whatever the cartesian/rotation comparator ends up needing

**Doesn't exist yet:**
- The actual orchestrator that runs a candidate set through Tiers 0-4 in
  order and calls `record_run()` only for survivors. Natural next build once
  real sandbox data is in and the SMA strategy is actually being tuned
  against it - not built now, since it depends on decisions (exact
  thresholds, what "the candidate space" actually contains once more
  strategies exist) that are premature before real data is in hand.
