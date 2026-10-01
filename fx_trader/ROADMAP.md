<!-- version: 261001 -->
# Roadmap

Read after `CONTEXT_HANDOFF.md` and `CONFIDENCE_SIZING_DESIGN.md`. Rewritten 260929 (chat 10); housekeeping edits 260930 (chat 11). Contents: 0 baseline, 1 MVP, 2 sandbox and validation data, 3 phases, 4 chat plan, 5 parallel/post-MVP, 6 de-bloat, 7 risk register, 8 extras.

## 0. Honest baseline

7 of 80 tested (strategy, instrument) combinations cleared Tier 4, on a single 6-month window (2025 H2), several on wafer-thin CI lower bounds (GBP_JPY +0.14%, +0.13%). Not a demonstrated edge. Everything below is infrastructure to find out whether an edge exists. Pace so far: project started 260910, 10 chats in 19 days (about 1 per 2 days). Target: 3-4 tighter one-topic chats per week.

## 1. MVP

**Definition (agreed 260929):** everything needed to actually trade live, single user. Going live at MVP is possible, not automatic.
- Survivors validated out-of-time; multiple-testing controlled.
- Cartesian currency-pair comparison (relative-strength ranking of currencies, spread mean-reversion between related pairs, possibly more) - believed to be the core edge; ample dedicated time.
- Additional signal generators explored (built, or scoped for post-MVP).
- Shadow scoring engine, then sizing (hysteresis, debounce, rescaling), then cross-instrument allocation and confidence modules.
- User risk setting (risk-appetite slider), pulled forward from old Phase 3 into MVP.
- Live capability, single user, small real fund (about GBP 100-200) once signed off.
- Minimal dashboard; investor documentation, flow chart and presentation; multiple refine and de-bloat rounds.

**First live run success criteria (operational, NOT a return target):** live fills match backtest-implied fills within a pre-set divergence threshold (set BEFORE seeing data); no risk-limit breaches; reconciliation clean; realised costs match the cost model. A month of GBP 100-200 is only a handful of trades, so it tests plumbing and cost parity, not profitability. Return targets (e.g. 1.2x-2x monthly) are aspirations resting on the untested cartesian work, not on current evidence.

**Not in MVP:** multi-user, subscriptions, deployment at scale, regime detection (Option C).

**Live-trading checklist (L1-L16; all needed before real money):**
- L1 live feed (completed-candle detection, gap/stale detection, weekend/rollover) sharing one interface with a REPLAY feed (sandbox replayed as if live)
- L2 order manager (state machine, idempotent client order IDs, retries/backoff, partial fills, rejections)
- L3 startup reconciliation of positions/balance against the broker
- L4 stops (engine has none; broker-side protective stops for live)
- L5 kill switch and hard limits (max daily loss, exposure, order size, fat-finger check)
- L6 persistent audit log (signals, orders, fills; usable by an accountant)
- L7 alerts (email/Telegram)
- L8 supervision and hosting (always-on runner host, auto-restart, heartbeat) - NOTE database host (Snowflake Postgres/Neon) is not the runner host; hosting decision open (chat 30)
- L9 financing (swap/rollover) costs in CostModel (absent today)
- L10 small-account viability (min order sizes, fee floors; find the sweet spot for return % vs fees at GBP 100-200)
- L11 FX leverage/margin limits (UK retail) and margin close-out behaviour
- L12 UK access/legality of chosen broker and exchange
- L13 secrets handling (.env hygiene, no tokens in logs, pinned dependencies)
- L14 CI (GitHub Actions running the test suite)
- L15 go-live checklist and rollback plan, rehearsed on paper first
- L16 tax record-keeping design (not tax advice; accountant to confirm)

## 2. Sandbox and validation data

**Development sandbox (DECIDED 260929): the sandbox's current window is final.** Kraken 26Q2 is unpublished and may not appear; no further quarters are planned. All 16 instruments confirmed aligned on START/END (see `CONTEXT_HANDOFF.md` Section 4).

**Unseen-data caveat:** out-of-time replay, re-discovery and the base-metric test all consume the existing data. The only genuinely unseen data will be forward paper/live data. Chat 17 decides a holdout policy inside the sandbox (e.g. reserve 2026 Q1 untouched until the final pre-live check). Not decided yet.

**Still open:** (a) compute-budget dry run on the ~4.25-year window (original sweep 186.6s on 6 months, machine unknown; chat 13 re-runs 142.2s on the user's machine, 6 months, three runs; extrapolation of ~25+ min is a guess, run-monitor data will replace it); (b) cross-rate arithmetic validation (GBP_USD x USD_JPY vs GBP_JPY, EUR_USD vs EUR_GBP x GBP_USD) and cross-instrument timestamp alignment - never performed; do before any cross-instrument result is trusted.

**Reproducing the 7 survivors: DONE chat 13 (261001), result in `CONTEXT_HANDOFF.md` Section 4d; text below kept as background:** the current `run_full_sweep.py` targets the 22Q1-26Q1 DB with no date restriction, so it will NOT reproduce 2025 H2 numbers. Reproduction needs either the original 2025 H2 sandbox database plus the old script version (held by the user), or a re-windowed sweep (2025-07-01 to 2025-12-31). Survivor table: `CONTEXT_HANDOFF.md` Section 4d.

**Out-of-time methodology (two parts, both built):** (1) frozen-candidate replay (`run_out_of_time_validation.py`): the 17 persisted survivors, unchanged params, every window outside 2025 H2, same gate values, no loosening. (2) independent re-discovery (`run_full_sweep.py` on the wider window, restricted to non-2025-H2 windows first), compared by hand with (1). Open questions: does a strong new discovery replace the 17 as the reference set; `RunStore` output naming (`full_sweep_runs.db` vs `validated_runs.db`); whether `MAX_GRID_COMBINATIONS` (200) should rise.

## 3. Phases

**Ordering principle (agreed 260929):** cartesian pair work is believed to be the most profitable piece, so it is front-loaded: do the quick, necessary baseline work (P0-P1), then go straight into cartesian (P2). Nothing that cartesian would force redoing gets built first. Durations are low confidence. ~70 chats at ~3.5/week: cartesian go/no-go around early-mid Nov; MVP sign-off around Mar 2027, then the small live run.

| Phase | Content | Chats | Target |
|---|---|---|---|
| P0 | Baseline integrity and instrumentation (11-15) | 5 | 29 Sep-9 Oct |
| P1 | Minimal validation before cartesian: OOT replay, holdout policy (16-17) | 2 | 9-16 Oct |
| P2 | CARTESIAN: definition, architecture/interface design, multiple-testing control, alignment layer, multi-leg engine/interface, relative-strength and spread mean-reversion signals, hierarchy validation, go/no-go (18-25) | ~8 | mid Oct-mid Nov |
| P3 | Deferred single-instrument validation (trade frequency, re-discovery, base-metric test) and de-bloat #2 (26-29) | 4 | Nov |
| P4 | Broker/account/hosting choice, fees, OANDA and ccxt API tests, cost-model update (30-33) | 4-5 | Nov |
| P5 | Scoring engine (shadow), built for cartesian/multi-leg inputs from the start | 3 | Nov-Dec |
| P6 | Sizing wiring, hysteresis, debounce | 3 | Dec |
| P7 | Live infrastructure L1-L8 | ~9 | Dec-Jan |
| P8 | User risk setting (design and build) | 3 | Jan |
| P9 | Cross-instrument allocation and confidence modules | 4 | Jan |
| P10 | Additional signal generators beyond cartesian (scope, build 1-2) | 3 | parallel |
| P11 | Minimal dashboard and visualisation | 3 | Jan-Feb |
| P12 | Paper trading (4-8 weeks elapsed, weekly short check-ins) | ~6 short | Jan-Mar |
| P13 | Investor pack: process explainer, flow chart, market/competitor research, feedback loops | ~5 | staggered |
| P14 | Security and compliance basics, CI (L13, L14) | 3 | staggered |
| P15 | MVP hardening, refine/de-bloat rounds, go-live checklist (L15), sign-off | 3-4 | Mar |

**Rework-exposed modules (single-instrument assumptions; do NOT extend or restructure until chat 19 decides the cartesian architecture; deletions and housekeeping only):** `backtest/engine.py` (`Trade`, `BacktestResult`), `data/run_store.py` schema (instrument-keyed), `backtest/validation_orchestrator.py`, `backtest/portfolio.py`, `strategy/base.py`, and anything new that would key on one instrument: scoring engine, sizing wiring, order manager, audit-log schema, runner. **Safe before chat 19:** run monitor, alignment audit, dry run, holdout policy, OOT replay, broker/fee research.

**Dependency rules:**
- Timestamp alignment and cross-rate audit (chat 14) before any cartesian work.
- Cartesian definition (18), then architecture (19): `Strategy.on_candle` takes one instrument's candle and cannot express cross-instrument signals; cartesian may need an extended interface or a standalone module with a multi-leg engine.
- Multiple-testing control (20) is designed against the cartesian search space, since cartesian multiplies tested combinations.
- Scoring engine and everything instrument-keyed after cartesian architecture is settled.
- Base-metric test (28) after cartesian, so the ranking can include cartesian candidates.
- Cartesian conclusions are PROVISIONAL until real fees are applied (chat 33): pair trades pay costs on multiple legs.
- Replay feed (L1) before the live runner; kill switch, audit log and reconciliation (L3, L5, L6) before paper trading; paper trading before real money.
- User risk setting after scoring and sizing exist.
- Investor pack staggered through the build; its feedback can raise new work items.
- Fill-ins for waiting on local runs or a usage-window lockout: independent chats (26 trade frequency, 30 broker/hosting research) may be pulled forward.

## 4. Chat plan

Rules: one topic per chat, finished inside one 24h window. The Context panel repo sync already supplies every committed repo file, so the Attach column names repo files to LOOK AT (in context), not to upload; UPLOAD only what is not in the repo (gitignored DBs, logs, historical copies, pasted terminal output) and mark it (upload). A long attachment list means the scope is too big: split it (chat 19 is the known exception: a heavy architecture topic). All chats run on Sonnet (free tier; context limits rule out Haiku; Opus/Fable unavailable), so there is no per-chat model selection. Sizes (provisional, revisit as progress is monitored): S under half a 5-hour window, M about one, L one to two.

| # | Topic | Size | Attach (besides CONTEXT_HANDOFF.md) | After |
|---|---|---|---|---|
| 11 | Housekeeping and versioning rollout: dupes (apply_delete, sensitivity/bootstrap assertions, README lines), stale docstring refs, README layout, five-vs-three strategies wording. Deletions/doc fixes only in rework-exposed modules | S | utilities/apply_patch.py, utilities/README.md, backtest/sensitivity.py, backtest/bootstrap.py, README.md, fx_trader/README.md, run_validation_hierarchy_real_data.py, run_out_of_time_validation.py | - |
| 12 | Run-monitor utility (duration/metadata log) | S | run_full_sweep.py | 11 |
| 13 | DONE 261001. Reproduce 7 survivors on 2025 H2 (re-windowed sweep: add window-start/end `note()` calls via run_monitor) | M | old and current run_full_sweep.py, instrument_config.py, sandbox_config.py; user pastes terminal output | 12 |
| 14 | Cross-rate arithmetic, timestamp-alignment and TIMEZONE audit script (London-vs-UTC policy, OANDA candle alignment, `periods.py` boundaries); adopts run_monitor from creation; `fetch_sandbox_data.py` adopts it if touched | M | data/store.py, sandbox_config.py, instrument_config.py, fetch_sandbox_data.py (all in context); UPLOAD sandbox_22Q1to26Q1.db | 12 |
| 15 | Full-window compute dry run | S | run_full_sweep.py; user pastes `python3 run_monitor.py summary <log>` output or attaches the day's `logs/run_log_*.jsonl` | 13 |
| 16 | Out-of-time frozen-candidate replay: run and interpret | M | run_out_of_time_validation.py, backtest/periods.py, backtest/validation_orchestrator.py | 13, 14, 15 |
| 17 | Holdout policy inside the sandbox (matters most for cartesian) | S | none | 16 |
| 18 | Cartesian definition (relative strength, spread mean-reversion, others); no code | L | CONFIDENCE_SIZING_DESIGN.md, backtest/portfolio.py | 14, 17 |
| 19 | Cartesian architecture: Strategy interface vs standalone module, multi-leg Trade/BacktestResult, RunStore schema, orchestrator, PositionManager inputs; no code | L | CONFIDENCE_SIZING_DESIGN.md, strategy/base.py, backtest/engine.py, data/run_store.py, backtest/validation_orchestrator.py | 18 |
| 20 | Multiple-testing control sized for the cartesian search space (design plus implementation) | M | backtest/validation_orchestrator.py, backtest/sensitivity.py, backtest/bootstrap.py, VALIDATION_HIERARCHY.md | 19 |
| 21 | Alignment layer and multi-instrument data access (build) | M | data/store.py, backtest/portfolio.py; output of 14 | 19 |
| 22 | Multi-leg engine/interface extension per chat-19 design; regression: existing suite and sweep byte-identical | L | backtest/engine.py, strategy/base.py, tests/test_engine.py, tests/test_rescale.py | 19, 21 |
| 23 | Relative-strength currency ranking signal (build and first validation) | M | chat-19 design, chat-21 layer, chat-22 engine | 22 |
| 24 | Spread mean-reversion signal (build and first validation) | M | as 23 | 22 |
| 25 | Cartesian through the hierarchy with multiple-testing control; go/no-go review | L | outputs of 23, 24; validation_orchestrator.py | 20, 23, 24 |
| 26 | Trade-frequency extraction (feeds debounce) | S | data/run_store.py; user pastes output | fill-in |
| 27 | Independent re-discovery on non-training windows (single-instrument plus cartesian candidates) | M | run_full_sweep.py; outputs of 16, 25 | 25 |
| 28 | Base-metric re-ranking test (deciding test in Section 4e) | M | backtest/base_metrics.py; outputs of 16, 27 | 27 |
| 29 | Full de-bloat pass #2 | L | per process | 28 |
| 30 | Broker/account choice (OANDA Standard vs Core, UK crypto exchange access, min order sizes, small-account viability) and hosting (runner host vs database host) | M | CONFIDENCE_SIZING_DESIGN.md; user: budget and restrictions | fill-in |
| 31 | OANDA get_quote/place_market_order practice test | M | brokers/oanda.py, brokers/base.py | 30 |
| 32 | ccxt testnet order test | M | brokers/ccxt_broker.py | 30 |
| 33 | Cost-model update (real fees, financing, small-account floors) and re-run of cartesian conclusions | M | backtest/engine.py (CostModel), instrument_config.py | 30 |

**Run-monitor adoption (from chat 12; API in `run_monitor.py`'s docstring). Rule: every new long-running script adopts it from creation. Status of everything else:**
- ADOPTED: `run_full_sweep.py` (chat 12).
- Chat 13: DONE (CLI args, window/coverage notes) - re-windowed `run_full_sweep.py`. The old 2025 H2 script copy is unmonitored by definition.
- Chat 14: new audit script adopts from creation; `fetch_sandbox_data.py` adopts if touched, else at its next re-run (sandbox is final, so unlikely).
- Chat 16: `run_out_of_time_validation.py` adopts when run (one `item` per candidate x window; `count` windows/candles).
- Chats 21-25 (cartesian): every new script and sweep adopts from creation; one `item` per pair/signal/grid combination; `count` candles and legs. The 100,000 item-line ceiling may bite; the end-record summary stays exact.
- Chats 26, 30-33 (trade frequency, `fetch_real_cost_data.py`, OANDA/ccxt tests): adopt from creation; network latency via `item`.
- Chat 29 (de-bloat #2): first resolve `run_validation_hierarchy_real_data.py` vs `run_full_sweep.py`; adopt only in the survivor. `visualize_period_comparison.py` and `ingest_kraken_gbp_csv.py`: adopt if kept and run in batch.
- P7 / L8 (decided chat 30): the always-on live/paper runner is NOT a fit (needs heartbeats/events, not one run record). Keep separate from the L6 audit log.
- Deliberately NOT adopted: `run_*_demo.py` (synthetic, seconds-long).

Later (detail expands as they approach): P5 onward per Section 3.

## 5. Parallel tracks and post-MVP placeholders

**Parallel:** regular de-bloat (documentation folds in); investor pack (plain-language explainer, flow chart, potential and base of facts, competitor and market comparison, feedback loop). Return language stays scaled down to potential and the training-data facts, with risk and exclusion wording; a UK financial-promotion check by a lawyer before anything leaves the circle (not legal advice).

**Post-MVP dedicated blocks (placeholders, to be scoped in a dedicated session):**
- Efficiency (parallelised sweeps, faster feeds)
- Profitability (weight/parameter refinement, cost optimisation)
- Signal generation (more strategy families, regime detection / Option C)
- UX (dashboard depth, alerts, explainability)
- Security (vault, access control, dependency scanning)
- Compliance: retain a lawyer and an accountant; implement whatever they specify (FCA, UK GDPR, Consumer Duty, record-keeping)
- ISA/tax-efficient wrapper investigation with the accountant. Open flag (medium confidence, accountant to confirm): ISAs are for individuals, the project entity is a company, and ISA-eligible holdings may not include spot FX/crypto/leveraged products
- Multi-user and subscription tiers; deployment and monitoring; finer granularity and more instruments (multi-instrument alignment across OANDA/Binance/Kraken)

## 6. De-bloat schedule

Mini-pass in chat 11; full pass in chat 29 (after the cartesian go/no-go and deferred validation); then every 8 chats or at each phase boundary, whichever first; final full pass before MVP sign-off. Process: `CONTEXT_HANDOFF.md` Working Conventions.

## 7. Risk register

| Risk | Area | Severity | Status |
|---|---|---|---|
| Out-of-time validation not yet run | Accuracy | High | Chat 16 |
| Multiple-comparisons inflation (80 combos; cartesian multiplies it) | Accuracy | High | Chat 20 |
| Sandbox data consumed by validation; no unseen data except forward | Accuracy | Medium-High | Chat 17 |
| Cartesian edge is a hypothesis, not evidenced; conclusions provisional until real fees | Profitability | High | Chats 25, 33 |
| Work built on single-instrument assumptions may need redoing | Efficiency | Medium-High | Rework-exposed list, Section 3 |
| Thin margins on several survivors | Profitability | High | No capital before real fees are applied |
| Real fee schedule, financing and swap costs not modeled | Profitability | High | Chats 30, 33 |
| Small-account viability (min order sizes, fee floors) | Profitability | Medium-High | Chats 30, 33 |
| No stops in the engine | Operational | High before live | L4 |
| No kill switch, idempotency or reconciliation | Operational | High before live | L2, L3, L5 |
| `Strategy` interface cannot express cross-instrument signals | Design | Medium-High | Chat 19 |
| No live-vs-backtest parity test | Accuracy | Medium-High | P12 paper trading |
| `OandaBroker.get_quote`/`place_market_order` never run | Operational | Medium | Chat 31 |
| `CcxtBroker` never run against a real exchange | Operational | Medium | Chat 32 |
| Broker/exchange UK access and legality unchecked | Regulatory | Medium | Chat 30 |
| Runner hosting undecided | Operational | Medium | Chat 30 |
| Timezone policy (London vs UTC) unaudited across data and periods | Accuracy | Medium | Chat 14 |
| Secrets via plain env vars | Security | Low now, grows | L13 |
| No audit trail of live orders | Security/Regulatory | Medium | L6 (`fills` table is the foundation) |
| Unpinned `requirements.txt`; no CI | Security/Efficiency | Low-Medium | L13, L14 |
| Rebalance-order slippage not modeled distinctly | Accuracy | Medium | After sizing wiring |
| Chat context load (whole repo per chat) on a free plan | Efficiency | Medium | Attachment lists per chat (Section 4) |
| Investor return language could count as a financial promotion | Regulatory | Medium | Lawyer check |

## 8. Also worth doing (unscheduled, cheap)

Pin requirements (L13); calendar-quarter/custom/regime-labeled periods in `periods.py`; volatility-bucket colouring and instrument-by-era heatmap in period-comparison visualisations; minimal live dashboard once sizing exists (`period_comparison.py` is already split for reuse).
