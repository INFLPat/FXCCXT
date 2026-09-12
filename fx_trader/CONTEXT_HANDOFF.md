# CONTEXT HANDOFF — FX/Crypto Trading Analysis Project

**Written by:** Claude (outgoing instance), end of session, [conversation covering initial architecture through bootstrap resampling]
**Purpose:** Let a fresh Claude instance — with zero memory of this conversation — pick up this project exactly where it left off, with no re-litigating of settled decisions and no loss of context.
**How to use this document:** Read it fully before touching the code. It's organized so KEY DECISIONS explains *why* things are built the way they are (so you don't propose re-architecting something already deliberately chosen), VERIFICATION STATUS tells you exactly what to trust and what to still treat as unproven, and IMMEDIATE NEXT STEP tells you where to actually start. **Read Section 15 too — it's the most recent session and changes what "immediate next step" means.**

**Jurisdictional note (read this early):** The entity building this is **UK-registered; UK law governs.** Regulatory and compliance considerations from here on should be framed specifically around UK requirements (FCA, financial promotions rules, UK GDPR), not generic multi-jurisdiction language. Full detail in RISKS. **The business also leans GBP/USD as its core currencies (GBP as the primary anchor, USD close behind, EUR represented for the UK/EU angle) — see Section 15 for how this shaped the real-data instrument list.**

---

## 1. OBJECTIVE

Build and rigorously validate a Python-based system that analyses historical and live FX and crypto price data to identify and visualise **the most reliable and flexible way to determine the optimum moment to execute a trade or currency conversion** — approached through multiple, complementary analytical processes rather than a single method. The near-term focus is a thoroughly-tested, single-user analytical pipeline capable of trading seamlessly across the largest practical range of interchangeable fiat currencies plus a solid portfolio of crypto assets. The long-term goal is a multi-user subscription product, with UK regulatory compliance treated as a first-order design constraint from the outset, not an afterthought.

---

## 2. PRODUCT FOCUS & SCOPE

**This is the central pillar — get this right before anything else.** The project is not "a bot that trades EUR/USD." It's a research and decision-support system whose core question is: *what is the most reliable, flexible way to know when to trade or convert currency* — and the answer is being built as **multiple independent analytical streams**, each interrogating that question from a different angle:

- **Signal generation** (`strategy/`) — what does "a good moment to trade" even look like, mechanically (currently: SMA crossover, deliberately simple/example-grade — see KEY DECISIONS).
- **Realistic simulation** (`backtest/engine.py`) — if you'd acted on a signal, what would actually have happened, net of real trading costs.
- **Validity/trust interrogation** — three distinct, complementary tools that each ask "can this result actually be trusted, and in what way":
  - Walk-forward testing — does it hold up over *time*?
  - Sensitivity analysis — is the parameter *space* smooth, or is this a lucky one-off?
  - Bootstrap resampling — how much of this *specific result* is just the luck of how these trades happened to land?
- **A planned fourth stream, not yet designed** — cross-pair/cartesian comparison (does switching from currency/asset A to B, right now, have a good risk-adjusted case behind it). Explicitly deferred to its own dedicated session — see Section 15 for what's been prepared for it and what hasn't.

**Near-term scope (in progress — see Section 15):** the largest practical number of fiat currencies, interchangeable with each other, plus a solid portfolio of crypto assets — not just one FX pair and one crypto pair. As of Section 14's handoff this was still only demonstrated on synthetic EUR/USD and BTC/USDT. This session began the real-data version of that: a fixed 6-month, 16-instrument sandbox dataset, GBP/USD/EUR-leaning per the business's actual jurisdiction. The `BrokerAdapter` abstraction was specifically designed so this breadth is additive — that design assumption is being exercised for real now, not just asserted.

**Longer-term (explicitly deferred, not started):** mapping out idealised **user profiles/personas** once the core analytical engine is proven. Do not start this until the single-user pipeline is validated against real data and genuinely spans multiple currencies/assets.

---

## 3. KEY DECISIONS

Organised by area. Each of these was deliberated and settled — treat re-opening any of them as requiring a genuinely new reason, not just habit. **Section 15 adds new decisions on top of these; it doesn't revisit any of them.**

### Language & runtime
- **Python throughout, not Rust.** The bottleneck at every stage is network I/O (broker/exchange API calls), not CPU — strategy logic itself runs in microseconds regardless of language. Rewriting in Rust now would cost real development velocity for negligible benefit. **Explicit revisit condition:** only reconsider if the project ever pursues genuine tick-level cross-exchange arbitrage or market-making, where colocation and microsecond execution genuinely matter — a fundamentally different, much harder strategy category than what's built today. Also noted: Python 3.14 (Oct 2025) made a free-threaded, no-GIL build officially supported for the first time — a further "if we ever hit a real CPU bottleneck" option, not something to build on speculatively. Multi-user scaling concerns don't change this conclusion either — per-user strategy loops are I/O-bound (waiting on broker APIs), so async Python handles concurrency without needing a systems language.

### Data layer
- **SQLite (local/test) + raw psycopg2 (cloud Postgres), not SQLAlchemy.** SQLAlchemy was tried first for the cloud migration but abandoned because it could not be executed at all in this build sandbox (no network access to install it, no way to verify the code path). Switched to a leaner approach where SQLite and Postgres share nearly all their SQL verbatim (both support `ON CONFLICT ... DO UPDATE`, both accept plain `TEXT`/`REAL`/`INTEGER` types identically) — only the connection method and parameter placeholder (`?` vs `%s`) differ by backend. This let at least the SQLite half be genuinely run and verified rather than shipping entirely-untested code.
- **Snowflake Postgres is the chosen cloud database**, not Neon (the original default recommendation) and not a self-hosted Postgres instance. Reasoning: the person has deep existing Snowflake/SQL expertise, and Snowflake launched "Snowflake Postgres" in public preview (Nov 2025) — a genuinely wire-compatible standard Postgres service. Because `FxStore` was already built to speak plain `postgresql://` connection strings, **zero code changes were needed** to support it — confirmed against Snowflake's own connection documentation before implementing. A defensive startup warning was added if a Postgres `DATABASE_URL` is missing `sslmode`, since Snowflake's docs mandate SSL. Neon remains documented as the cheaper/simpler fallback if ever needed.

### Broker & exchange integration
- **`BrokerAdapter` abstract interface** (`brokers/base.py`) — every broker/exchange implements `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`. Chosen specifically so the future multi-currency/multi-exchange breadth (see PRODUCT FOCUS) is additive: one new adapter file per broker/exchange, no changes needed to strategy, backtest, or router code.
- **OANDA is the first FX broker**, chosen over Interactive Brokers (much steeper API/local-gateway learning curve) and FXCM (no distinct advantage for a beginner) for its clean, well-documented REST API (v20) and free practice environment. IC Markets/cTrader was noted as a broker whose Open API is implemented across multiple brokers at once — worth revisiting specifically for the multi-broker breadth goal.
- **ccxt is the crypto integration layer** rather than bespoke per-exchange clients — one consistent API across 100+ exchanges, actively maintained, directly serves the "choose the best exchange" goal. **As of Section 15, this is exercised against two exchanges (Binance, Kraken) in one run for the first time — previously only ever one at a time.**
- **`BrokerRouter`** holds multiple adapters and picks the best live quote per trade. Explicit, tested finding: for FX majors, cross-broker price differences are typically a fraction of a pip — routing's real value there is redundancy/access, not price arbitrage. For crypto, cross-exchange price gaps are usually larger, so routing has genuine "best price" value — proven with a real two-fake-exchange test (correctly picked the cheaper ask/better bid, and survived one exchange's API failing without crashing).

### Cost modelling
- **`CostModel` supports both pip-based (FX convention) and percentage-based (crypto convention) costs, additively**, in the same dataclass. Extending it for crypto was verified not to change a single digit of the existing FX demo's output — the FX code path is untouched when the new percentage fields are left at their zero defaults.

### Correctness fixes found and made during this build (both are subtle, worth knowing about specifically)
- **Mark-to-market bug**: the engine's "unrealised P&L while a position is open" originally ignored the commission/slippage a real close would incur — this created an artificial cliff in the equity curve exactly at the instant a trade closed. Fixed via `BacktestEngine._mark_to_market_pnl()`, which applies the *same* slippage/commission logic `_close_trade` uses, hypothetically, without mutating the trade. Hand-verified: with price held flat, equity while a trade is open now matches equity right after close exactly (previously off by a full round-trip commission).
- **Walk-forward continuity bug**: when a walk-forward window boundary is crossed, a position that was still logically open (per the strategy's internal state) would silently vanish — because `SmaCrossoverStrategy` only signals on the *moment* of a crossover, not "I am currently bullish," and the engine used to force-close and fully reset at each window boundary. Fixed with two new, additive, backward-compatible `BacktestEngine.run()` parameters: `warm_start=True` (carries a strategy's internal state across a `run()` boundary) and `initial_trade=` (carries a still-open position across the same boundary, sourced from a new `BacktestResult.open_trade_at_end` snapshot captured *before* the normal end-of-run force-close). Hand-verified with a deterministic scenario matching a manual calculation exactly.
- **Deliberate non-fix**: `Strategy.on_candle()` still only returns a signal on a state *change*, not a "current bias" query. The bug above was fixed at the engine layer specifically so this contract didn't need to change for every future strategy.

### Validation tooling design
- **Walk-forward** (`backtest/walk_forward.py`): rolling (not anchored) window; out-of-sample periods are non-overlapping by default (`step` defaults to `out_of_sample_size`); in-sample periods *do* commonly overlap between consecutive windows (normal for a trailing lookback); parameter selection via a pluggable `selection_metric` (default `total_return_pct`); the combined out-of-sample equity curve is built by **compounding** each window's return onto a running balance, not just concatenating raw curves — representing "if you'd actually traded through all these consecutive periods."
- **Sensitivity analysis** (`backtest/sensitivity.py`): parameter grid expressed as `{name: [values]}` (Cartesian product computed internally), not walk-forward's list-of-dicts — more natural for the primary 2D-heatmap use case. Core diagnostic: `neighbor_gap()`, comparing a grid point's score to the average of its immediate axis-aligned neighbours (von Neumann adjacency, no diagonals); flagged as a likely overfit "spike" when the gap exceeds the whole grid's standard deviation. `marginal_effect()` works regardless of how many parameters vary (averages out everything else), specifically for usability beyond the 2-parameter case. Invalid combinations (e.g. `fast_period >= slow_period`) are caught and recorded, never allowed to crash the whole grid run.
- **Bootstrap resampling** (`backtest/bootstrap.py`): two standard, complementary methods — `resample` (with replacement — plausible-outcome range) and `shuffle` (permutation, no replacement — isolates path/sequencing risk in drawdown specifically, since total return is mathematically order-invariant and shuffle mode proves this as an explicit assertion, not just a claim). Deliberately does **not** report an annualised Sharpe ratio per iteration, since the trade-resolution synthetic equity curve has no fixed time cadence between points, making `periods_per_year` meaningless there — `total_return_pct` and `max_drawdown_pct` are the two metrics trusted at trade resolution.
- **How these three combine into a decision, and what's allowed to persist as a result** — this used to be implicit (run all three, eyeball the outcome). **As of Section 15 it's an explicit, ordered, cost-tiered hierarchy — see `VALIDATION_HIERARCHY.md` and Section 15.** This is additive to the three tools above, not a change to any of them.

### Testing philosophy (applied consistently, not just for one module)
Every new module got: (a) hand-verified unit tests with manually computed expected values wherever feasible, (b) an integration/smoke test against realistic synthetic data, (c) explicit, written acknowledgment of what could **not** be tested (anything needing real network access) rather than silently presenting untested code as verified. The bootstrap module went further — its Monte Carlo estimate was checked against **exhaustive enumeration** of every possible ordering for a small trade set, not just random-sampling confidence.

### Regulatory framing
Consistently flagged throughout (originally in generic terms, **now specifically UK-focused per the most recent instruction** — see RISKS) that offering an automated trading product to other people is very likely to trigger financial services regulation, and that professional legal consultation is needed before the multi-user phase begins. This has not blocked any building so far because the multi-user phase hasn't started.

---

## 4. CURRENT STATE

**As of Section 14's handoff, the single-user analytical pipeline was functionally complete and internally consistent on synthetic data, but had never touched anything real.** That description is now half-superseded — see below and Section 15 for what changed.

- **Data layer**: `FxStore` works against SQLite (verified, run repeatedly) or Postgres/Snowflake Postgres via `DATABASE_URL` (written and reasoned through carefully, but never connected to a real server). **New this session: `FxStore.get_candles_multi()` — verified — and a separate `RunStore` (`data/run_store.py`) for persisting validated backtest results — also verified on SQLite. See Section 15.**
- **Broker/exchange layer**: `OandaBroker` (FX) and `CcxtBroker` (100+ crypto exchanges via ccxt) both implement `BrokerAdapter`; `BrokerRouter` can hold multiple adapters and picks the best price. Only ever tested against fakes/synthetic data — never a real OANDA account or real exchange. **`fetch_sandbox_data.py` (Section 15) is the first attempt to change that — written, not yet run.**
- **Backtest engine**: cost-aware (pip- and percentage-based), with the mark-to-market fix applied. Fully verified via hand-computed tests.
- **Four analysis layers**, all built, all tested, all demoed end-to-end on synthetic data:
  1. `backtest/metrics.py` — return, drawdown, Sharpe, win rate, profit factor.
  2. `backtest/walk_forward.py` — out-of-sample validation.
  3. `backtest/sensitivity.py` — parameter-grid robustness.
  4. `backtest/bootstrap.py` — resample/shuffle Monte Carlo.

**Real proof points from the last full synthetic-data regression run** (all from actual executed code, seeded/reproducible — still accurate, nothing here has been re-run against real data yet):
- FX demo (SMA 10/30, EUR/USD-shaped synthetic data, 120 trades): total return **-0.51%**, win rate 28.3%, profit factor 0.68, max drawdown 0.75%, Sharpe -2.22.
- Crypto demo (same strategy, BTC/USDT-shaped synthetic data, 121 trades): total return **-15.49%**, max drawdown 16.06%, Sharpe -7.80.
- Walk-forward demo (16 windows, 4-way parameter grid): combined out-of-sample return **≈+0.12%**.
- Sensitivity demo (6×6 grid, 35 valid combinations): only **one** combination profitable (fast=20/slow=60, +0.085%), correctly flagged as a likely overfit spike.
- Bootstrap demo (same 120-trade FX result): resample-mode 90% CI **[-1.04%, +0.03%]**, 94.1% probability of loss; shuffle-mode drawdown sat at the **81st percentile** of all reorderings.

**Sandbox dataset initiative (this session, in progress — see Section 15 for full detail):** the pipeline above has only ever run on synthetic data. This session began replacing that with a fixed, real 6-month window (2026-01-01 to 2026-06-30, H1 granularity) across 16 real instruments — 8 FX pairs leaning GBP/USD/EUR (the business is UK-based) and 8 crypto pairs (4 assets × USD and GBP quote currencies, matched assets on both sides for clean comparison). `fetch_sandbox_data.py` is written and extends `OandaBroker`/`CcxtBroker` with no core adapter changes needed — **but it has not been run.** This sandbox has no network access, so running it and uploading the resulting SQLite file is a manual step still outstanding. **No real-data backtest results exist yet.** A persistence layer (`RunStore`) and a tiered validation hierarchy (`VALIDATION_HIERARCHY.md`) were also built this session, ready to receive results once the data lands, but the actual multi-tier orchestrator that would run candidates through that hierarchy is not built yet — deliberately, since its thresholds need real data to set sensibly.

**The honest bottom line, unchanged from Section 14**: every validation method tried, independently, agrees the example strategy has no real edge after realistic costs *on synthetic data*. Whether that holds on real GBP/USD/EUR/crypto data is exactly what the sandbox dataset is for finding out — it hasn't been tested yet.

Full regression suite (7 test modules + 5 end-to-end demo scripts) was green at the Section 14 handoff. **This session added 2 more test modules (`test_run_store.py`, `test_multi_query.py`), both run and green — see Section 15 — but the pre-existing suite was not re-run this session** (only the files needed to exercise the new code were reconstructed in this session's own sandbox). Re-run the full suite before assuming nothing regressed.

---

## 5. REPOSITORY MAP

```
fx_trader/
├── README.md                          — primary technical reference; kept in sync after every change; read this alongside this handoff doc
├── VALIDATION_HIERARCHY.md            — NEW (Section 15): tiered validation/persistence gate design doc
├── requirements.txt                   — requests, psycopg2-binary, ccxt
├── CONTEXT_HANDOFF.md                 — this document
├── fetch_sandbox_data.py              — NEW (Section 15): pulls real 6-month FX+crypto data via OandaBroker/CcxtBroker; written, not yet run
├── data/
│   ├── store.py                       — FxStore: SQLite (local/test) or Postgres/Snowflake Postgres (cloud) via DATABASE_URL. Extended this session with get_candles_multi().
│   └── run_store.py                   — NEW (Section 15): RunStore — persists validated backtest results (runs + trades tables), tagged with validation_stage
├── brokers/
│   ├── base.py                        — BrokerAdapter interface; Quote/OrderResult dataclasses
│   ├── oanda.py                       — OandaBroker: FX via OANDA v20 REST API
│   ├── ccxt_broker.py                 — CcxtBroker: crypto via ccxt, exchange_id-parameterised
│   └── router.py                      — BrokerRouter: best-price selection across connected adapters
├── strategy/
│   ├── base.py                        — Strategy interface, Signal enum, StrategyDecision
│   └── sma_crossover.py               — SmaCrossoverStrategy (example-grade, not a working edge — see below)
├── backtest/
│   ├── engine.py                      — BacktestEngine, CostModel, Trade, BacktestResult
│   ├── metrics.py                     — compute_metrics(): return, drawdown, Sharpe, win rate, profit factor
│   ├── walk_forward.py                — run_walk_forward(): rolling out-of-sample validation
│   ├── sensitivity.py                 — run_sensitivity_analysis(): parameter grid / neighbor-gap / heatmap
│   └── bootstrap.py                   — run_bootstrap(): resample/shuffle Monte Carlo
├── tests/
│   ├── synthetic_data.py              — generate_synthetic_candles (FX), generate_synthetic_crypto_candles
│   ├── fake_ccxt_exchange.py          — FakeCcxtExchange: offline stand-in for a real ccxt exchange
│   ├── test_engine.py                 — cost math (pip- + %-based), mark-to-market continuity fix
│   ├── test_store.py                  — round-trip/upsert + sslmode-warning
│   ├── test_multi_query.py            — NEW (Section 15): FxStore.get_candles_multi()
│   ├── test_run_store.py              — NEW (Section 15): RunStore round-trip against a real BacktestResult
│   ├── test_ccxt_broker.py            — pagination, quotes, orders, balance vs. the fake exchange
│   ├── test_router_multi_exchange.py  — best-price selection + graceful failure handling
│   ├── test_walk_forward.py           — selection + state/position continuity, no-lookahead structural check
│   ├── test_sensitivity.py            — neighbor-gap/heatmap/marginal-effect math, invalid-combo handling
│   └── test_bootstrap.py              — percentile math, order-invariance proof, MC vs. exhaustive enumeration
├── run_backtest_demo.py               — end-to-end FX demo (synthetic data)
├── run_crypto_backtest_demo.py        — end-to-end crypto demo (synthetic data)
├── run_walk_forward_demo.py           — walk-forward demo (synthetic FX data)
├── run_sensitivity_demo.py            — sensitivity analysis demo (synthetic FX data)
└── run_bootstrap_demo.py              — bootstrap resampling demo (synthetic FX data)
```

---

## 6. VERIFICATION STATUS

**This is the single most important section not to lose fidelity on.** The whole project has operated under one hard rule: never claim something works when it's only been written to spec. Preserve this distinction exactly.

### ✅ Actually run and verified (safe to build on with confidence)
- Backtest engine cost math — pip-based, percentage-based, and the mark-to-market fix — all hand-verified against manually computed numbers.
- `FxStore` on **SQLite specifically** — round-trip, upsert-really-updates-not-duplicates, range filtering, sslmode-warning logic.
- **`FxStore.get_candles_multi()` (Section 15)** — verified: matches `get_candles()` output exactly for multiple instruments in one call, correctly returns an empty list (not a missing key) for an instrument with no data, handles an empty instrument list.
- **`RunStore` on SQLite (Section 15)** — verified against a *real* `BacktestEngine` result, not a hand-built fake: a run and all 18 of its trades round-tripped exactly (side, entry/exit price, realized P&L all matched to the original object), `validation_stage` filtering confirmed to isolate the right subset across multiple persisted runs. Postgres path shares the same SQL, untested against a real server — same caveat as `FxStore`.
- `CcxtBroker`'s own logic (OHLCV-to-candle mapping, pagination cursor advancement, quote fallback, order/balance handling) — verified against a **fake** exchange object built for this purpose.
- `BrokerRouter` — verified with two independent fake exchanges (correct price selection, graceful handling of one failing).
- Walk-forward selection logic and the state/position-continuity fix — hand-verified against a deterministic scenario.
- Sensitivity analysis's `neighbor_gap`, `heatmap_grid`, `marginal_effect` — hand-verified against a constructed grid with a known, deliberate spike.
- Bootstrap's percentile/CI math, shuffle-mode order-invariance, and Monte Carlo accuracy — hand-verified, the last one against exhaustive enumeration.
- All 5 demo scripts run end-to-end on synthetic data without error.

### ⚠️ Written to spec, NEVER executed against anything real — must be validated before trusting
- **`OandaBroker.fetch_candles` / `get_quote` / `place_market_order`** — has never made a real network call. Written carefully against OANDA's v20 documentation, but unverified.
- **`CcxtBroker` against a real exchange** — only ever tested against the fake. Real ccxt exchange behaviour (rate limits, response shape quirks per exchange, sandbox mode support/absence) is unconfirmed.
- **`FxStore`'s Postgres/Snowflake Postgres code path** — never connected to any real Postgres server, Snowflake or otherwise. Only the SQLite half of the shared SQL has actually run.
- **`fetch_sandbox_data.py` (Section 15)** — never run. This is the first time `OandaBroker` and `CcxtBroker` would be exercised together across a real multi-exchange (Binance *and* Kraken in one run), real multi-instrument (16 instruments, including GBP-quoted crypto pairs never touched before) pull. Genuinely higher risk of surfacing a real bug than any single-instrument test so far, precisely because it's new combined territory. Run it, and report the full terminal output back — success or failure — so this section can be updated honestly either way.
- **`RunStore` on Postgres** — same untested status as `FxStore`'s Postgres path.

**Why this gap exists**: the build sandbox this was developed in has no network access at all — this isn't a shortcut taken, it's a hard environment constraint. The person has been told this explicitly and repeatedly throughout.

**Action for whoever picks this up**: before trusting any of the ⚠️ items, run them against a real (practice/sandbox/testnet) environment and update this section and the README's own verification table accordingly.

---

## 7. CONSTRAINTS & PREFERENCES

These are the person's own words, taken directly from their standing preferences (account-level, should already be active in a new chat via the system prompt — restated here for a self-contained handoff regardless):

> "Try to limit the usage and tokens as much as possible. Before you begin a task or project that is going to take up more than a basic amount of tokens or bandwidth ALWAYS check in ahead of doing this task... present why you would normally do it this way, and what the alternative less processing heavy options are... ONLY go ahead and perform the heavy task if there are no reasonable other options."

> "AVOID ALL 'sycophantic' style language. Don't be mean, but be critical or constructive in all aspects. NEVER try to agree outright with me as the user and always recognise wherever you can add expertise or insight. NEVER assume that I am explicitly correct and assume I may be testing your accuracy by omitting information or laying out a red-herring to catch you out."

> "DROP ALL PLEASANTRIES AND SUMMARIES, BE AS CONCISE AS POSSIBLE"

> "I think tentatively, so ask questions and refine, rather than trying to get something perfect the first time. ESPECIALLY ASK QUESTIONS IF I HAVEN'T PROVIDED ENOUGH CONTEXT OR CLARIFICATION WOULD HELP"

> "Default to short, direct answers. Drop the opening pleasantries and the wrap-up summary. Lead with the answer, add detail only if it's needed, and stop when you're done."

**An important, non-obvious calibration point for whoever picks this up**: there is a real tension between the above (minimal, check-in-first) and how this project actually gets built (very thorough, fully-tested work delivered across many large features). The resolution that has worked across multiple sessions now: **default to the minimal/check-in-first behaviour**, but when the person's own phrasing signals they want depth — "to the highest ability and usability," "best in class," or (new example from Section 15) explicitly answering "yes, persist" and "let's set the structure up now" to a direct check-in question — that's the go-ahead to build fully and rigorously without re-litigating. Watch for that signal specifically rather than assuming every request wants the maximal treatment, and don't assume it lasts forever either — check in again at the next genuinely heavy decision point.

**Working norms established through direct instruction and consistent follow-through:**
- Discuss and compare options *before* implementing, when a genuine architectural choice exists.
- Every chart/visualisation shown must be built from **actually-executed, real output** — never fabricated or illustrative example data standing in for real computation.
- State plainly when something looks like overfitting/a spike/noise rather than hedging it.
- The person has strong SQL/Snowflake expertise and basic Python knowledge; is new to trading concepts specifically. Explanations of trading mechanics have been given plainly and in some depth when asked; SQL/data-layer discussion can assume real fluency.
- **The business is UK-based (Section 15 makes this concrete): GBP and USD are the core currencies for analysis, EUR is represented for the UK/EU angle. Don't default to USD-only or EUR/USD-centric examples going forward — check this section and Section 15 before picking instruments for a new demo or example.**

---

## 8. OPEN THREADS

Everything below is unresolved, deferred, or started-but-not-finished. None of it is forgotten — it's a deliberate backlog.

- **Sandbox dataset not yet fetched.** `fetch_sandbox_data.py` is written; running it locally and uploading `data/sandbox_2026h1.db` back is the single most concrete next action — see Section 9 and Section 15.
- **Tier 0–4 validation orchestrator not built.** The individual tools and the `RunStore` destination all exist; the function/script that actually runs candidates through the tiers in order and calls `record_run()` only for survivors does not. Deliberately deferred until real data is in hand — thresholds (drawdown ceiling, minimum trade count, etc.) need real data to set sensibly, not guesses. See `VALIDATION_HIERARCHY.md`.
- **Cross-pair/cartesian rotation comparator — explicitly deferred to its own dedicated future chat**, per the person's direct instruction. `FxStore.get_candles_multi()` is ready to support it (built specifically with this in mind), but the comparator's actual interface, and how "strong currency likely to weaken / weak currency likely to improve" gets defined (pure price momentum vs. something carry/interest-rate-differential-informed) is a genuinely open design question for that session — don't guess at it in the meantime.
- **Position sizing / confidence / volatility-adjustment** — still a fixed constant (`units_per_trade`). Will need a `PositionSizer`-style interface (same abstraction pattern as `BrokerAdapter`/`CostModel`) once the cartesian layer's confidence output needs to feed into it. Not designed yet.
- **Multi-currency, multi-crypto breadth beyond the 16-instrument sandbox** — architecture supports it; the sandbox is real progress but still a fixed, small set. No portfolio logic, no cross-currency conversion-path logic yet.
- Real OANDA account not connected or validated (fetch_sandbox_data.py is the first attempt).
- Real Snowflake Postgres connection not established or validated.
- Real ccxt/crypto exchange connection not established or validated (fetch_sandbox_data.py is the first attempt, and the first time against two exchanges in one run).
- User-profile/persona modelling — explicitly "eventually," not started.
- **Multi-user SaaS build, including subscription-tier entitlements gating which strategies/processes a user can access** — explicitly deferred; the working principle agreed this session is to keep entitlement checks *out* of the core analysis library entirely (a thin permission layer above it later), not scattered through strategy/backtest/metrics code. Nothing built.
- UK regulatory/compliance research — not done; flagged as needing a UK-qualified fintech/financial-services solicitor before the multi-user phase (see RISKS).
- No standalone diagnostic visualisation tools yet. Should consume the generic `RunStore`/`BacktestResult` layer, not any specific strategy, once built.
- Additional metrics discussed but not implemented: Sortino ratio, Calmar ratio, drawdown duration, trade expectancy, rolling-window metrics, portfolio-level aggregation across instruments/strategies. Low structural risk to add later — `Metrics`/`compute_metrics()` is a clean, additive extension point.
- No parallelisation in sensitivity/bootstrap analysis — deliberately deferred as not yet necessary.
- Live/paper trading execution loop — discussed conceptually only, no code.
- Deployment infrastructure — no VPS setup, systemd service, or deployment config exists yet.
- **`data/sandbox_2026h1.db` (once fetched) — should this actually be committed to the public GitHub repo?** Not decided. It's a binary SQLite file; committing real market data to a public repo works but bloats git history with every re-fetch, and a public repo means the exact dataset (and therefore any strategy tuned against it) is publicly visible. Worth a deliberate decision (`.gitignore` it and treat as a local/artifact-store item, use Git LFS, or just commit it) rather than defaulting to committing it without thinking about it — flagged, not resolved.

---

## 9. IMMEDIATE NEXT STEP

**The fork from the original Section 9 (multi-instrument generalisation vs. connect a real account first) has been resolved — this session is doing both together, which is why the sandbox dataset approach exists.** The immediate next step is now unambiguous, not a choice:

1. **Run `fetch_sandbox_data.py` locally** (needs `OANDA_API_TOKEN`/`OANDA_ACCOUNT_ID` from a free practice account; no credentials needed for the Binance/Kraken crypto pulls) and upload the resulting `data/sandbox_2026h1.db` back into the chat.
2. **Report the full terminal output**, not just "it worked" — this is genuinely new territory (first real OANDA call, first real ccxt call, first time two exchanges in one run, first GBP-quoted crypto pairs) and the honest verification-status table depends on knowing exactly what happened, including partial failures.
3. Once data is in: run the SMA crossover strategy against all 16 instruments through `VALIDATION_HIERARCHY.md`'s Tier 0–1 (cheap) first, before reaching for sensitivity/walk-forward/bootstrap on anything that doesn't clear that bar.
4. **Only after that**, and only in a dedicated new session per the person's own instruction, start on the cross-pair/cartesian rotation comparator.

---

## 10. RISKS

### Regulatory — UK-specific, treat as highest priority
**The building entity is UK-registered; UK law governs.** This changes the regulatory lens from the generic "consult a lawyer in your jurisdiction" language used earlier in this project to something specific:
- Once this becomes a paid, multi-user product that executes trades on people's behalf (or even gives them signals to act on), **FCA regulation of investment business and financial promotions is very likely to apply** — the exact boundary depends on how much discretion the product exercises and how it's marketed, and that boundary determination needs a UK-qualified fintech/financial-services solicitor, not an AI's best guess.
- The UK's **Consumer Duty** regime is notably strict about consumer-facing financial products and services — worth specific attention given the subscription/multi-user direction.
- **UK GDPR** and data protection law govern any storage of other users' personal and financial data, including broker API credentials — proper encryption-at-rest and a real secrets management approach (KMS/vault, never plaintext DB columns or `.env` files in a multi-user context) is a hard requirement before that phase, not a nice-to-have.
- **Gate condition, explicitly**: do not begin the multi-user build without UK-qualified legal sign-off first. This has been said to the person multiple times through this project and should keep being said.

### Technical / correctness
- Everything marked ⚠️ in VERIFICATION STATUS is a real risk if assumed to work — none of it has touched a real server, and the newest additions (`fetch_sandbox_data.py`) are genuinely higher-risk than earlier single-instrument, single-exchange code because of how much is combined into one run for the first time.
- The example strategy (SMA crossover) has been repeatedly, rigorously shown **unprofitable** after realistic costs, across every validation method tried — **on synthetic data only**. Whether that holds on the real sandbox data is not yet known. Don't assume either answer before the data's actually in and validated through the hierarchy.
- Nothing in this project constitutes financial advice, and that framing should be preserved in any user-facing output the product eventually produces.

### Security posture
- Broker/exchange credentials are currently handled via plain environment variables throughout — entirely appropriate for a single-user local/dev setup, but this pattern **must not** carry into any multi-user version. No security hardening work has been done yet.

### Scope-creep risk
- The analytical toolkit is already substantial. As multi-currency/multi-asset breadth and the persistence/validation layers grow, watch that `BrokerAdapter`, `CostModel`, `Strategy`, and now `RunStore`/the validation hierarchy continue to generalise cleanly rather than accumulating per-instrument or per-strategy special-casing.

### Minor — tooling/operational note, not a project risk
- The file-creation tool used to build this project intermittently threw an `Input validation errors occurred: path: Field required` error mid-session in earlier sessions, and in a few cases left a stray, incomplete file at the target path despite reporting failure. Workaround used throughout: check for and delete the stray file, then retry the same `create_file` call cleanly. Worth knowing if it recurs.

---

## 11. KEY CONCEPTS GLOSSARY

| Concept | Definition | Where implemented |
|---|---|---|
| Crossover | The moment a fast SMA flips from below to above (or vice versa) a slow SMA — the only signal `SmaCrossoverStrategy` acts on. | `strategy/sma_crossover.py` |
| Equity curve | `balance + unrealised P&L of any open position`, recorded every candle. | `backtest/engine.py`, `BacktestEngine.run()` |
| Mark-to-market | What a position would net if closed *right now* — uses the same slippage/commission model a real close uses. | `BacktestEngine._mark_to_market_pnl` |
| Walk-forward testing | Select parameters on an in-sample window, test them *unchanged* on the following out-of-sample window, slide forward, repeat. | `backtest/walk_forward.py` |
| Neighbor-gap | How much better a parameter combination scores than its immediate grid-neighbours. Large gap = suspected overfit spike; small gap = plateau. | `backtest/sensitivity.py` |
| Bootstrap resample | Draws trades *with replacement* many times — tests the plausible range of outcomes if the same process repeated. | `backtest/bootstrap.py`, `method="resample"` |
| Bootstrap shuffle | Reorders the *same* trades without replacement — isolates path/sequencing risk in drawdown. | `backtest/bootstrap.py`, `method="shuffle"` |
| BrokerAdapter | The common interface every broker/exchange integration implements. | `brokers/base.py` |
| BrokerRouter | Holds multiple `BrokerAdapter`s, routes to whichever offers the best price. | `brokers/router.py` |
| **Sandbox dataset** | **The fixed, real 6-month (2026-01-01 to 2026-06-30, H1) market data window used as ground truth for all strategy tuning going forward — frozen, never re-fetched or overwritten once populated.** | `fetch_sandbox_data.py`, `data/sandbox_2026h1.db` |
| **Validation tier** | **One stage of the cost-ordered gate a candidate must clear before its result is allowed to persist — Tier 0 (sanity) through Tier 4 (bootstrap), each more expensive and applied to fewer survivors than the last.** | `VALIDATION_HIERARCHY.md` |
| **RunStore** | **Persists a validated backtest's trades, params, cost model, and summary metrics, tagged with the validation tier it cleared — separate from raw market data storage (`FxStore`).** | `data/run_store.py` |

---

## 12. HOW TO GET SET UP

Full detail lives in `README.md` — this is the short version. From the `fx_trader/` directory:

```bash
pip install -r requirements.txt

# demos (all run on synthetic, reproducible data - no API keys needed)
python run_backtest_demo.py
python run_crypto_backtest_demo.py
python run_walk_forward_demo.py
python run_sensitivity_demo.py
python run_bootstrap_demo.py

# full test suite
python -m tests.test_engine
python -m tests.test_store
python -m tests.test_multi_query
python -m tests.test_run_store
python -m tests.test_ccxt_broker
python -m tests.test_router_multi_exchange
python -m tests.test_walk_forward
python -m tests.test_sensitivity
python -m tests.test_bootstrap

# real sandbox data (NEW - see Section 15; needs network + OANDA practice token)
python fetch_sandbox_data.py
```

**To connect real services** (all currently unverified — see VERIFICATION STATUS):
- OANDA: `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` env vars (practice account — see README for setup link).
- Cloud Postgres/Snowflake Postgres: `DATABASE_URL` env var, e.g. `postgresql://user:pass@host:5432/postgres?sslmode=require`.
- A ccxt exchange: `{EXCHANGE_ID}_API_KEY`, `{EXCHANGE_ID}_API_SECRET` env vars — **not needed for `fetch_sandbox_data.py`**, which only reads public historical OHLCV.

---

## 13. HANDOFF COMPLETENESS CHECKLIST

*(Unchanged from the original session — see Section 15's own note at its end for this session's equivalent.)*

- [x] Initial architecture, cloud DB, broker comparison, Rust vs Python, crypto/ccxt, crossover/equity-curve explanation, mark-to-market bugfix, walk-forward + continuity bugfix, sensitivity analysis, bootstrap resampling, multi-user/regulatory considerations, testing philosophy, real numeric results, userPreferences, product-focus reframe, UK jurisdiction note, verification status — all present, see relevant sections above.

**Not carried into this document because it doesn't need to be**: the turn-by-turn conversational back-and-forth itself — what matters is the decisions and reasoning that resulted, which are captured above and in Section 15.

**File packaging note — superseded, see Section 14**: don't zip/bulk-reupload the project. Section 14 documents the correct GitHub sync process.

---

## 14. GITHUB SYNC — CONFIRMED WORKING (added by a later session)

The earlier "immediate unresolved issue" around folder upload is **resolved**, not by flattening or zipping, but by connecting the repo directly.

**Setup, now live**: repo is `github.com/INFLPat/FXCCXT` (public). Connected via the Project's **Context** panel → `+` → GitHub → repo added.

**Repo structure**: `README.md` at repo root is a short pointer only. `fx_trader/README.md` remains the canonical, detailed technical reference. `fx_trader/CONTEXT_HANDOFF.md` (this file) remains the "read first" document for a new session.

**Critical limitation, confirmed directly, not assumed**: Claude.ai's GitHub integration is **pull-only**, on any plan. There is no automatic or built-in push from a Claude.ai chat back to this repo.

**The actual workflow, every session:**
1. New chat → pulls current GitHub state into context via the Context panel (hit its refresh/sync control if content looks stale).
2. Claude edits or produces files inside that chat's own sandbox.
3. The user downloads those files.
4. The user pushes them to GitHub — via `git`, or by manually re-uploading/editing the changed file(s) on github.com.
5. The user refreshes/syncs the Context panel so the *next* chat picks up the change.

**Guardrail**: never regenerate and hand back "the full project" as a bulk download-and-reupload. Confirm the chat's GitHub context was actually synced at the *start* of the session before pushing anything, and push only the specific files that changed in that session.

**If a fully automated push is ever wanted**, that requires **Claude Code** instead of the Claude.ai chat interface. Not in use for this project as of this note.

---

## 15. SANDBOX DATASET, RUN PERSISTENCE & VALIDATION HIERARCHY (added by a later session)

This session's work, in the order it happened. Read this alongside — not instead of — Sections 1–14; nothing here overturns a prior KEY DECISION, it builds on top.

### Business context clarified: UK-based, GBP/USD core
The person confirmed the business is UK-based and wants **GBP and USD as the core currencies for all analysis**, leaning UK/EU, with EUR represented. This isn't new regulatory information (Section 1/10 already had the UK-jurisdiction note) — it's the first time currency *selection* for actual demos/data has been pinned to that fact rather than defaulting to EUR/USD-centric FX convention. Stored in Claude's memory system for this project; also written here explicitly so it survives regardless of memory system behaviour. **Apply this going forward**: don't default to EUR/USD or USD-only examples without checking this.

### Real-data sandbox: what and why
Rather than keep validating only on synthetic data, the person wants a **fixed, real 6-month window, used repeatedly as ground truth** for tuning the SMA crossover strategy (and later, other strategies) — "sandbox" meaning frozen and reused, not re-fetched per session.

**Period: 2026-01-01 to 2026-06-30 (H1 granularity).** Reasoning: existing walk-forward tooling was calibrated against ~3000 H1 candles (`in_sample_size=500`, `out_of_sample_size=150`, giving ~16 windows). 6 months of FX H1 data comes to ~3,100 candles/instrument — close enough to reuse that calibration without rework. 3 months (~1,560 candles) was judged too thin for walk-forward to say much; 9 months was judged a reasonable *later* extension once something's worth stress-testing across more regime diversity, not a sensible starting point (roughly doubles compute across 16 instruments for no immediate benefit).

**Instruments — 8 FX + 8 crypto, chosen for GBP/USD/EUR relevance:**
- FX: `GBP_USD, EUR_GBP, GBP_JPY, GBP_CHF, EUR_USD, USD_JPY, USD_CHF, USD_CAD`
- Crypto vs USD (Binance): `BTC/USDT, ETH/USDT, XRP/USDT, LTC/USDT`
- Crypto vs GBP (Kraken): `BTC/GBP, ETH/GBP, XRP/GBP, LTC/GBP`

Deliberate choice: **the same 4 crypto assets on both quote currencies**, so any USD-vs-GBP behavioural difference the strategy shows is a real quote-currency effect, not a different-asset artifact. The originally-proposed 4th asset was SOL; it was swapped for **LTC** specifically because SOL/GBP's availability on Kraken wasn't confirmed at the time, while LTC/GBP's was (Kraken has supported GBP pairs, including major and several mid-cap assets, since a 2021 expansion). `fetch_sandbox_data.py` checks symbol existence via `exchange.load_markets()` at runtime and skips/warns rather than guessing, so swapping this back (or adding more) is a one-line change, not a rewrite.

### `fetch_sandbox_data.py` — written, NOT yet run
Extends nothing in `OandaBroker`/`CcxtBroker` themselves (both already support what's needed) — it's a driver script. Key points for whoever picks this up:
- **Gotcha handled deliberately**: `CcxtBroker` defaults to `sandbox=True` (testnet). A testnet has no meaningful historical data, so this would silently return near-empty results for a historical pull. The script explicitly passes `sandbox=False` on both Binance and Kraken instances, since it only ever calls `fetch_candles` (read-only), never places an order.
- Needs `OANDA_API_TOKEN`/`OANDA_ACCOUNT_ID` (free practice account, no funding) for the FX half. Needs **no credentials** for the crypto half — historical OHLCV is a public endpoint on both Binance and Kraken.
- Writes to `data/sandbox_2026h1.db` via the existing `FxStore` — one file, all 16 instruments.
- **This has never been run.** It's the first time `OandaBroker` and `CcxtBroker` get exercised together, the first time two ccxt exchanges are used in one run, and the first time any GBP-quoted crypto pair is touched at all. Treat it as higher-risk than earlier single-instrument tests for exactly that reason — see VERIFICATION STATUS.

### Persistence layer: `RunStore`
Built because the existing pipeline had nowhere for a backtest's trades/commission/params to live beyond the Python process that ran them — a real gap once "commission tracking" and "multiple strategies feeding a visualiser" are explicit goals (see PRODUCT FOCUS / Section 2's planned fourth stream).

**Key design decision, discussed explicitly and confirmed by the person**: `BacktestEngine.run()` stays a **pure function with no side effects**, unchanged. Persistence is a **separate, explicit call** (`RunStore.record_run()`), never automatic. Reasoning: `sensitivity.py` runs dozens-to-hundreds of backtests per analysis (grid search), `bootstrap.py` runs thousands (resampling) — if persistence were wired into `run()` itself, every one of those internal computation backtests would try to write a row, flooding the table with combinatorial noise and adding I/O to what are currently fast in-memory loops.

**Schema** (`data/run_store.py`): two tables, `runs` (run_id, created_at, strategy_name, params JSON, instrument, granularity, cost_model JSON, starting/ending balance, period start/end, **validation_stage**, **metrics JSON**, notes) and `trades` (trade_id, run_id FK, and the same fields as the existing `Trade` dataclass). Both tables use **UUID primary keys generated in Python**, not `AUTOINCREMENT`/`SERIAL` — deliberately, to avoid the SQLite/Postgres syntax divergence those would introduce, matching the same "share as much SQL as possible" principle `FxStore`'s candles table already follows via its natural composite key.

`validation_stage` and `metrics` exist specifically to support the hierarchy below — a run is tagged with which tier it cleared, and its key summary numbers are stored inline so listing/comparing runs doesn't require recomputing metrics from trades every time.

**Tested**: SQLite path run against a real `BacktestEngine` result (not a hand-built fake) — 18 trades round-tripped exactly, `validation_stage` filtering confirmed to isolate the right subset. Postgres path untested, same status as `FxStore`.

### `FxStore.get_candles_multi()`
Added because the future cross-pair/cartesian comparator (explicitly deferred to its own session — see Section 8) will need several instruments' history at once, not one at a time. One query, grouped by instrument, every requested instrument present as a key even with zero rows (so a caller never has to distinguish "no data" from "wasn't in the dict"). Tested: matches `get_candles()` output exactly, handles the empty-instrument and empty-list edge cases. **This is data-layer groundwork only** — the comparator's actual interface and "what does relative strength/weakness even mean here" (pure price momentum vs. carry/interest-rate-differential-informed) is explicitly NOT designed yet, per the person's own instruction to keep that as a dedicated future session.

### Validation hierarchy
Full detail and the flowchart are in `VALIDATION_HIERARCHY.md` at the repo root — read it directly rather than relying on this summary. In short: **Tier 0** (sanity: ≥20 closed trades, engine ran clean) → **Tier 1** (`compute_metrics()` on the full candidate grid — cheap, applied to everything) → **Tier 2** (`neighbor_gap()`, free, reuses Tier 1's grid — plateau survives, spike doesn't) → **Tier 3** (`walk_forward.py`, re-run but only on Tier 1+2 survivors — cost scales with survivor count, not original grid size) → **Tier 4** (`bootstrap.py`, most expensive, but by now only a handful of finalists remain). Only Tier 4 survivors get persisted via `RunStore.record_run()`. Ordered strictly cheapest-first specifically so nothing expensive runs on a candidate a cheap check would already have rejected.

**What's NOT built**: the actual orchestrator that runs a candidate set through these tiers in order. Deliberately not built this session — real thresholds (the Tier 0 trade-count floor, Tier 1's drawdown ceiling, etc.) need real sandbox data to set sensibly, and none exists yet.

### Session-end state
- `fetch_sandbox_data.py`: written, not run. **This is the actual next action.**
- `data/store.py`: extended with `get_candles_multi()`, tested.
- `data/run_store.py`: new, tested.
- `tests/test_run_store.py`, `tests/test_multi_query.py`: new, both pass.
- `VALIDATION_HIERARCHY.md`: new design doc, no code changes required by it yet.
- Full pre-existing test suite was **not** re-run this session (only the specific files needed to exercise new code were reconstructed in a scratch environment) — low risk since changes are additive, but re-run it, don't assume.
- Nothing in `brokers/`, `strategy/`, or the four `backtest/` analysis modules was touched.
