# Roadmap

Read after `CONTEXT_HANDOFF.md` and `CONFIDENCE_SIZING_DESIGN.md`.
Sequenced build+test plan plus a risk register spanning accuracy,
profitability, security, and efficiency.

## 0. Honest baseline

7 of 80 tested (strategy, instrument) combinations cleared Tier 4, on a
single 6-month window (2025 H2), several on wafer-thin CI lower bounds
(GBP_JPY: +0.14%, +0.13%). Not yet a demonstrated edge - the first
candidates that didn't immediately fail. Treat everything below as
infrastructure to find out whether a real edge exists, not as building on
top of one already confirmed.

## 1. Sandbox dataset - DONE

Multi-year sandbox (`sandbox_22Q1to26Q1.db`, 2022-01-01 through the latest
Kraken quarter, auto-detected) built and confirmed aligned across all 16
instruments - see `CONTEXT_HANDOFF.md` Section 4 for the verification
detail. `sandbox_config.py` is the single shared source of truth for the
window; add a new Kraken quarterly folder and every downstream script
picks up the new end date automatically.

**Still open**: (a) a compute-budget dry run before committing to a full
re-sweep on the ~4.25-year window (original 80-combination sweep was
186.6s on 6 months; Tier 0/1/3 scale with candle count, Tier 4 scales per-
finalist so should scale more mildly - worth a one-instrument dry run
first, not assumed); (b) **cross-rate arithmetic validation of the
sandbox** (e.g. GBP_USD x USD_JPY vs GBP_JPY, EUR_USD vs EUR_GBP x
GBP_USD) - never performed; alignment so far verified only via
START/END, continuity and row counts. Do next session, before trusting
cross-instrument results.

## 2. The most important gap: out-of-time validation doesn't exist yet

Walk-forward (Tier 3) and bootstrap (Tier 4) both operate entirely inside
the same 6-month sample - neither tells you whether a survivor's edge is
real vs. specific to that window's regime. With 80 combinations tested and
7 survivors, several thin, multiple-comparisons risk is real even after
three filtering tiers, since all three share the same sample.

**Two-part methodology, both scripts built and sandbox-ready**:
1. **Frozen-candidate replay** (`run_out_of_time_validation.py`): the 17
   persisted Tier 4 survivors, unchanged params, replayed against every
   window outside the original training period, judged against the same
   gate values - no loosening.
2. **Independent re-discovery** (`run_full_sweep.py`, pointed at the wider
   sandbox): re-run the full sweep blind, compare survivors to (1)'s PASS
   list by hand. Consider restricting a first pass to the non-2025H2
   portion only (genuine held-out discovery), before a second pass on the
   full combined window.

This is the **single highest-leverage accuracy action available** - more
valuable than further scoring-engine work, since the scoring engine's
whole premise is "trust these weights," and right now those weights rest
on one sample.

**Open questions**: does a strong new discovery from the wider window
replace the original 17 as the reference survivor set for Phase 1 Step 2,
or do the 17 stay canonical with new data only used to validate them?
`RunStore` output naming (`full_sweep_runs.db` vs `validated_runs.db`) -
window-aware/dynamic, or a static running log? Does having more data per
grid point now make it worth raising `validation_orchestrator.
MAX_GRID_COMBINATIONS` (currently 200) for a finer discovery-sweep grid -
a separate question from the confidence-sizing combination layer's own,
larger, future grid (Section 3, Phase 1 Step 2 below). Does the larger
backtest (more trades, more years) make sourcing the real fee schedule
(Section 9.1 of the sizing design, still blocked on OANDA account type/
credentials) more urgent, or does it stay unchanged?

## 3. Build phases

### Phase 1 Step 2 - Scoring engine (shadow-computed)

Build `PositionManager`: per-candle, per-instrument shadow stance per
strategy, weighted raw/shaped scores across all three `k` shape values in
parallel, using pairwise-discount correlation weighting (decided,
`CONTEXT_HANDOFF.md` Section 4e) - base metric candidate still open
(`CONTEXT_HANDOFF.md` Section 4e), don't hardcode one until the out-of-
time re-ranking test (Section 2 above) has run. Logged, not acted on.

Test plan: unit-test the shadow-stance state machine against
`BacktestEngine._apply_signal` directly; unit-test the score formula in
isolation (known stances/weights -> hand-computed scores, incl. zero-
weight and all-flat edge cases); integration run over the real sandbox
with out-of-time-validated survivor weights, human-sanity-checked; confirm
RSI/Confluence correlation doesn't get silently double-weighted (mechanism
chosen, wiring is this step's job).

### Phase 1 Step 3 - Wire scoring -> sizing

Build hysteresis thresholds, continuous rescaling via `rescale_trade()`
(built, unused until now), debounce guards (magnitude AND time-based -
actual numbers blocked on real trade-frequency data, Section 4 below).

Test plan: integration test with real rescaling on a hand-designed score
sequence, verifying weighted-average entry and P&L through several
adds/reduces; regression - re-run the 4 single-strategy demos through the
new sizing-aware path at weight=1 (no ensemble), confirm identical results
to pre-Phase-1 numbers.

### Phase 1 Step 4 - Cross-instrument allocation

Wire `portfolio.py`'s correlation matrix into real capital splitting.

Test plan: hand-verified allocation on a small synthetic 2-3 instrument
portfolio with known correlations.

### Phase 2 - Pluggable confidence modules

Build order, cheapest-to-verify first: cost-drag discount -> CI-width
discount -> instrument-class discount (build the lookup table from real
Tier 4 results, not assumption - the real survivor set didn't split
cleanly fiat/crypto) -> open-position correlation discount (needs Phase 1
Step 4 first; must include the cross-strategy case per Section 4e above).

### Phase 3 - Hard caps, risk appetite, house-money guardrails

Test plan should include adversarial scenarios: a runaway score breaching
the cap without the gate, a loss streak triggering the default-on downward
lifecycle adjustment, an upward adjustment checked against its hard
1.5-2x ceiling.

### Phase 4 / Option C - Regime detection

Explicitly deferred, own future project.

## 4. Parallel track - live-readiness

`OandaBroker.get_quote`/`place_market_order` are pure technical-
feasibility questions, independent of the sizing design - run this in
parallel with Phase 1 Step 2, not after Phase 3.

Also missing entirely so far: a genuine paper-trading validation phase
before any real capital. Once `get_quote`/`place_market_order` are
verified on a practice account, run the out-of-time-validated survivors
live-but-paper for 4-8 weeks, logging every live signal alongside what the
backtest pipeline would say for the same candle. Define an explicit
divergence threshold up front (e.g. live fills differing from backtest-
implied fills by more than X pips/bps) that would block moving to real
capital - decide the number before seeing the data.

## 5. Risk register

| Risk | Area | Severity | Mitigation / status |
|---|---|---|---|
| Out-of-time validation doesn't exist | Accuracy | High | Section 2 above, sequenced before Phase 1 Step 2 |
| RSI/Confluence not independent (avg +0.65 corr) | Accuracy | Medium | Resolved - pairwise discount, `CONTEXT_HANDOFF.md` Section 4e |
| Thin margins on several survivors (GBP_JPY CI lower bound +0.14%) | Profitability | High | Don't commit capital until real fee schedule is sourced and re-run against survivors specifically |
| Real fee schedule still not sourced | Profitability | High | Blocked on your input (OANDA account type, credentials) |
| No live-vs-backtest parity test | Accuracy/Profitability | Medium-High | Section 4's paper-trading phase |
| `OandaBroker.get_quote`/`place_market_order` never run | Accuracy/Operational | Medium | Sequence in parallel with Phase 1 Step 2 |
| API credentials via plain env vars, no secrets-management story | Security | Low now, grows with scale | Fine for single-user/practice now; needs a real vault before multi-user/live |
| No kill-switch / circuit breaker | Security/Operational | High before any live capital | Add to Phase 3 alongside cap architecture |
| No idempotency/duplicate-order protection on restart | Security/Operational | Medium | Relevant once Phase 1 Step 3's live rescaling exists |
| No audit trail of live orders (FCA record-keeping) | Security/Regulatory | Medium, grows for multi-user | `RunStore`'s `fills` table is a reasonable foundation to extend |
| `requirements.txt` uses unpinned `>=` ranges | Security/Efficiency | Low-Medium | Pin exact versions once the dependency set stabilizes |
| No CI - tests run manually | Efficiency/Accuracy | Medium | Section 6 |
| Combination layer's own grid will exceed `MAX_GRID_COMBINATIONS=200` | Efficiency | Low now | Revisit when Phase 1 Step 2's grid is actually built |
| Rebalance-order slippage not modeled distinctly from single-entry slippage | Accuracy | Medium | Unsolved, relevant once Phase 1 Step 3 ships |

## 6. Beneficial additions not currently planned

- **GitHub Actions CI**: run the full test suite on every push - the one
  automated regression check that doesn't depend on remembering to run
  tests manually.
- **Trade-frequency extraction from `data/full_sweep_runs.db`** - cheap,
  already-collected data, blocks Section 8.1's debounce thresholds.
- **Kill-switch/circuit breaker** - build alongside Phase 3's cap
  architecture, same hard-gate code shape.
- **Minimal live dashboard** once Phase 1 Step 3 exists -
  `period_comparison.py`'s execution/rendering split is specifically
  designed so a dashboard can reuse the same data layer.
- **Pin `requirements.txt`** once Phase 1's dependency set stabilizes.
- **Calendar-quarter / custom / regime-labeled periods** in `periods.py` -
  trivial extensions once `calendar_year_periods` is proven (regime
  detection itself stays deferred).
- **Cross-instrument volatility-bucket coloring** and an
  instrument-x-era profitability heatmap in the period-comparison
  visualizations, now that multi-year data exists.

## 7. Recommended sequencing

1. Compute-budget dry run on the multi-year sandbox (Section 1).
2. Extract real trade-frequency data from `data/full_sweep_runs.db`.
3. Out-of-time validation of the 7 survivors (Section 2) - highest
   leverage single action available.
4. In parallel: test `OandaBroker.get_quote`/`place_market_order` against
   a practice account.
5. Phase 1 Step 2 (scoring engine, shadow-computed), incorporating the
   cross-strategy correlation fix, using only out-of-time-validated
   survivors.
6. Phase 1 Step 3 + start the paper-trading validation clock.
7. Phase 1 Step 4, Phase 2, Phase 3 (kill-switch included).
8. Real fee schedule sourcing - lands before any capital-commitment
   decision, timed against your OANDA account/credentials situation, not
   gating the build work above.
