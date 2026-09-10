# CONTEXT HANDOFF — FX/Crypto Trading Analysis Project

**Written by:** Claude (outgoing instance), end of session, [conversation covering initial architecture through bootstrap resampling]
**Purpose:** Let a fresh Claude instance — with zero memory of this conversation — pick up this project exactly where it left off, with no re-litigating of settled decisions and no loss of context.
**How to use this document:** Read it fully before touching the code. It's organized so KEY DECISIONS explains *why* things are built the way they are (so you don't propose re-architecting something already deliberately chosen), VERIFICATION STATUS tells you exactly what to trust and what to still treat as unproven, and IMMEDIATE NEXT STEP tells you where to actually start.

**Jurisdictional note (read this early):** The entity building this is **UK-registered; UK law governs.** Regulatory and compliance considerations from here on should be framed specifically around UK requirements (FCA, financial promotions rules, UK GDPR), not generic multi-jurisdiction language. Full detail in RISKS.

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

**Near-term scope (not yet built, but architected for):** the largest practical number of fiat currencies, interchangeable with each other, plus a solid portfolio of crypto assets — not just one FX pair and one crypto pair. Currently the codebase only *demonstrates* EUR/USD (synthetic) and BTC/USDT (synthetic). The `BrokerAdapter` abstraction (see KEY DECISIONS) was specifically designed so extending to many currency pairs and many crypto assets is an additive change (new adapter instances, not new architecture) — but that extension has **not been done yet**. This is very likely the highest-leverage next body of work; see IMMEDIATE NEXT STEP.

**Longer-term (explicitly deferred, not started):** mapping out idealised **user profiles/personas** once the core analytical engine is proven — i.e., defining who this is actually for and tailoring the product around them. Do not start this until the single-user pipeline is validated against real data and genuinely spans multiple currencies/assets.

---

## 3. KEY DECISIONS

Organised by area. Each of these was deliberated and settled — treat re-opening any of them as requiring a genuinely new reason, not just habit.

### Language & runtime
- **Python throughout, not Rust.** The bottleneck at every stage is network I/O (broker/exchange API calls), not CPU — strategy logic itself runs in microseconds regardless of language. Rewriting in Rust now would cost real development velocity for negligible benefit. **Explicit revisit condition:** only reconsider if the project ever pursues genuine tick-level cross-exchange arbitrage or market-making, where colocation and microsecond execution genuinely matter — a fundamentally different, much harder strategy category than what's built today. Also noted: Python 3.14 (Oct 2025) made a free-threaded, no-GIL build officially supported for the first time — a further "if we ever hit a real CPU bottleneck" option, not something to build on speculatively. Multi-user scaling concerns don't change this conclusion either — per-user strategy loops are I/O-bound (waiting on broker APIs), so async Python handles concurrency without needing a systems language.

### Data layer
- **SQLite (local/test) + raw psycopg2 (cloud Postgres), not SQLAlchemy.** SQLAlchemy was tried first for the cloud migration but abandoned because it could not be executed at all in this build sandbox (no network access to install it, no way to verify the code path). Switched to a leaner approach where SQLite and Postgres share nearly all their SQL verbatim (both support `ON CONFLICT ... DO UPDATE`, both accept plain `TEXT`/`REAL`/`INTEGER` types identically) — only the connection method and parameter placeholder (`?` vs `%s`) differ by backend. This let at least the SQLite half be genuinely run and verified rather than shipping entirely-untested code.
- **Snowflake Postgres is the chosen cloud database**, not Neon (the original default recommendation) and not a self-hosted Postgres instance. Reasoning: the person has deep existing Snowflake/SQL expertise, and Snowflake launched "Snowflake Postgres" in public preview (Nov 2025) — a genuinely wire-compatible standard Postgres service. Because `FxStore` was already built to speak plain `postgresql://` connection strings, **zero code changes were needed** to support it — confirmed against Snowflake's own connection documentation before implementing. A defensive startup warning was added if a Postgres `DATABASE_URL` is missing `sslmode`, since Snowflake's docs mandate SSL. Neon remains documented as the cheaper/simpler fallback if ever needed.

### Broker & exchange integration
- **`BrokerAdapter` abstract interface** (`brokers/base.py`) — every broker/exchange implements `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`. Chosen specifically so the future multi-currency/multi-exchange breadth (see PRODUCT FOCUS) is additive: one new adapter file per broker/exchange, no changes needed to strategy, backtest, or router code.
- **OANDA is the first FX broker**, chosen over Interactive Brokers (much steeper API/local-gateway learning curve) and FXCM (no distinct advantage for a beginner) for its clean, well-documented REST API (v20) and free practice environment. IC Markets/cTrader was noted as a broker whose Open API is implemented across multiple brokers at once — worth revisiting specifically for the multi-broker breadth goal.
- **ccxt is the crypto integration layer** rather than bespoke per-exchange clients — one consistent API across 100+ exchanges, actively maintained, directly serves the "choose the best exchange" goal.
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

### Testing philosophy (applied consistently, not just for one module)
Every new module got: (a) hand-verified unit tests with manually computed expected values wherever feasible, (b) an integration/smoke test against realistic synthetic data, (c) explicit, written acknowledgment of what could **not** be tested (anything needing real network access) rather than silently presenting untested code as verified. The bootstrap module went further — its Monte Carlo estimate was checked against **exhaustive enumeration** of every possible ordering for a small trade set, not just random-sampling confidence.

### Regulatory framing
Consistently flagged throughout (originally in generic terms, **now specifically UK-focused per the most recent instruction** — see RISKS) that offering an automated trading product to other people is very likely to trigger financial services regulation, and that professional legal consultation is needed before the multi-user phase begins. This has not blocked any building so far because the multi-user phase hasn't started.

---

## 4. CURRENT STATE

**As of the end of this session, the single-user analytical pipeline is functionally complete and internally consistent, but has never touched anything real.** Concretely:

- **Data layer**: `FxStore` works against SQLite (verified, run repeatedly) or Postgres/Snowflake Postgres via `DATABASE_URL` (written and reasoned through carefully, but never connected to a real server).
- **Broker/exchange layer**: `OandaBroker` (FX) and `CcxtBroker` (100+ crypto exchanges via ccxt) both implement `BrokerAdapter`; `BrokerRouter` can hold multiple adapters and picks the best price. Only ever tested against fakes/synthetic data — never a real OANDA account or real exchange.
- **Backtest engine**: cost-aware (pip- and percentage-based), with the mark-to-market fix applied. Fully verified via hand-computed tests.
- **Four analysis layers**, all built, all tested, all demoed end-to-end on synthetic data:
  1. `backtest/metrics.py` — return, drawdown, Sharpe, win rate, profit factor.
  2. `backtest/walk_forward.py` — out-of-sample validation.
  3. `backtest/sensitivity.py` — parameter-grid robustness.
  4. `backtest/bootstrap.py` — resample/shuffle Monte Carlo (**the most recent piece of work this session**).

**Real proof points from the last full regression run** (all from actual executed code, synthetic data, seeded/reproducible):
- FX demo (SMA 10/30, EUR/USD-shaped synthetic data, 120 trades): total return **-0.51%**, win rate 28.3%, profit factor 0.68, max drawdown 0.75%, Sharpe -2.22.
- Crypto demo (same strategy, BTC/USDT-shaped synthetic data, 121 trades): total return **-15.49%**, max drawdown 16.06%, Sharpe -7.80 — percentage fees compounding across more, more volatile trades hit far harder than FX's fixed pip costs.
- Walk-forward demo (16 windows, 4-way parameter grid): combined out-of-sample return **≈+0.12%** — notably *better* than the fixed-parameter single backtest, with the 20/60 period pair selected most often.
- Sensitivity demo (6×6 grid, 35 valid combinations): only **one** combination was profitable (fast=20/slow=60, +0.085%), and it was correctly flagged as a likely overfit "spike" — its neighbours averaged ~0.24% worse, a gap wider than the grid's own standard deviation.
- Bootstrap demo (same 120-trade FX result): resample-mode 90% CI for return **[-1.04%, +0.03%]**, 94.1% probability of loss; shuffle-mode confirmed the order-invariance proof held, and the actual historical drawdown sat at the **81st percentile** of all possible reorderings of those same trades — the real path was itself a somewhat unlucky sequencing of an already-unprofitable trade set.

**The honest bottom line**: every validation method tried, independently, agrees this example strategy has no real edge after realistic costs. That is the correct, expected outcome for a deliberately simple placeholder strategy on synthetic random-walk-like data — it proves the pipeline is working and is not lying to you, not that trading is hopeless.

Full regression suite (7 test modules + 5 end-to-end demo scripts) was green at handoff time.

---

## 5. REPOSITORY MAP

```
fx_trader/
├── README.md                          — primary technical reference; kept in sync after every change; read this alongside this handoff doc
├── requirements.txt                   — requests, psycopg2-binary, ccxt
├── CONTEXT_HANDOFF.md                 — this document
├── data/
│   └── store.py                       — FxStore: SQLite (local/test) or Postgres/Snowflake Postgres (cloud) via DATABASE_URL
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
│   ├── test_ccxt_broker.py            — pagination, quotes, orders, balance vs. the fake exchange
│   ├── test_router_multi_exchange.py  — best-price selection + graceful failure handling
│   ├── test_walk_forward.py           — selection + state/position continuity, no-lookahead structural check
│   ├── test_sensitivity.py            — neighbor-gap/heatmap/marginal-effect math, invalid-combo handling
│   └── test_bootstrap.py              — percentile/CI math, order-invariance proof, MC vs. exhaustive enumeration
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
- `CcxtBroker`'s internal logic (OHLCV-to-candle mapping, pagination cursor advancement, quote fallback, order/balance handling) — verified against a **fake** exchange object built for this purpose.
- `BrokerRouter` — verified with two independent fake exchanges (correct price selection, graceful handling of one failing).
- Walk-forward selection logic and the state/position-continuity fix — hand-verified against a deterministic scenario.
- Sensitivity analysis's `neighbor_gap`, `heatmap_grid`, `marginal_effect` — hand-verified against a constructed grid with a known, deliberate spike.
- Bootstrap's percentile/CI math, shuffle-mode order-invariance, and Monte Carlo accuracy — hand-verified, the last one against exhaustive enumeration.
- All 5 demo scripts run end-to-end on synthetic data without error.

### ⚠️ Written to spec, NEVER executed against anything real — must be validated before trusting
- **`OandaBroker.fetch_candles` / `get_quote` / `place_market_order`** — has never made a real network call. Written carefully against OANDA's v20 documentation, but unverified.
- **`CcxtBroker` against a real exchange** — only ever tested against the fake. Real ccxt exchange behaviour (rate limits, response shape quirks per exchange, sandbox mode support/absence) is unconfirmed.
- **`FxStore`'s Postgres/Snowflake Postgres code path** — never connected to any real Postgres server, Snowflake or otherwise. Only the SQLite half of the shared SQL has actually run.

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

**An important, non-obvious calibration point for whoever picks this up**: there is a real tension between the above (minimal, check-in-first) and how this session actually ran (very thorough, fully-tested builds delivered without a pre-check-in, across many large features). The resolution that worked: **default to the minimal/check-in-first behaviour**, but when the person's own phrasing signals they want depth — "to the highest ability and usability," "best in class," "highest ability" — that phrasing itself is the go-ahead; build fully and rigorously without first offering a lighter alternative. Watch for that signal specifically rather than assuming every request wants the maximal treatment.

**Working norms established through direct instruction and consistent follow-through (not through being corrected — nothing in this session involved being told something was done wrong):**
- Discuss and compare options *before* implementing, when a genuine architectural choice exists (this is how Snowflake vs. Postgres, Rust vs. Python, and the broker comparisons all played out — presented, discussed, then built once direction was clear).
- Every chart/visualisation shown must be built from **actually-executed, real output** — never fabricated or illustrative example data standing in for real computation. This was upheld consistently (equity curves, heatmaps, histograms all pulled from real runs).
- State plainly when something looks like overfitting/a spike/noise rather than hedging it — bluntness about honest findings has been the tone throughout, not just a one-off request.
- The person has strong SQL/Snowflake expertise and basic Python knowledge; is new to trading concepts specifically. Explanations of trading mechanics (crossover, equity curve, drawdown) have been given plainly and in some depth when asked; SQL/data-layer discussion can assume real fluency.

---

## 8. OPEN THREADS

Everything below is unresolved, deferred, or started-but-not-finished. None of it is forgotten — it's a deliberate backlog.

- **Multi-currency, multi-crypto breadth** (the current stated core focus — see PRODUCT FOCUS) — architecture supports it, nothing beyond one FX pair and one crypto pair has actually been built or demoed. No portfolio logic, no cross-currency conversion-path logic yet.
- Real OANDA account not connected or validated.
- Real Snowflake Postgres connection not established or validated.
- Real ccxt/crypto exchange connection not established or validated.
- User-profile/persona modelling — explicitly "eventually," not started.
- Multi-user SaaS build (auth, subscription billing, secrets management for other users' broker credentials, tenant data isolation) — explicitly deferred until the single-user pipeline is proven; nothing built.
- UK regulatory/compliance research — not done; flagged as needing a UK-qualified fintech/financial-services solicitor before the multi-user phase (see RISKS).
- Position sizing is still a fixed constant (`units_per_trade`) — no volatility-adjusted or risk-based sizing.
- No portfolio-level / cross-strategy / cross-asset analysis.
- No standalone diagnostic visualisation tools yet (trade entry/exit overlays on a price chart, a reusable P&L distribution histogram, an underwater/drawdown-duration chart) — only the one-off bootstrap histogram/heatmap built for specific demos so far.
- Additional metrics discussed but not implemented: Sortino ratio, Calmar ratio, drawdown duration, trade expectancy, rolling-window metrics.
- No parallelisation in sensitivity/bootstrap analysis — deliberately deferred as not yet necessary; loop structure was left easy to parallelise later if grid/iteration sizes grow large.
- Live/paper trading execution loop (the actual always-on process) — discussed conceptually only, no code.
- Deployment infrastructure — Vultr was discussed as the likely compute host (small VPS running the bot continuously) alongside Snowflake Postgres for data, but no VPS setup, systemd service, or deployment config exists yet.

---

## 9. IMMEDIATE NEXT STEP

**Recommendation, not a mandate — confirm with the person before building, per their own stated preference on checking in first.**

Given the reframed focus (PRODUCT FOCUS, section 2) — breadth across the largest practical number of fiat currencies plus a solid crypto portfolio — the highest-leverage next step is **not** necessarily connecting a real OANDA/ccxt account first. It's generalising the existing single-pair demo scripts and analysis tools into a **configurable, multi-instrument runner**: proving the backtest engine, walk-forward, sensitivity, and bootstrap tooling all generalise cleanly across a list of FX pairs and crypto pairs — not just the one of each currently demoed — while still on synthetic (or eventually real) data. This directly serves "refine the model and allow for flexibility of parameters and processes" and validates the `BrokerAdapter`/`CostModel`/`Strategy` abstractions actually hold up under the breadth they were designed for, before either (a) wiring up real accounts or (b) building portfolio-level aggregation on top.

**Legitimate alternative ordering**: connect one real data source first (OANDA practice account is the lowest-friction option, already has a demo account and clean docs) to close the biggest verification gap (see VERIFICATION STATUS) before generalising further. Worth raising both orderings with the person and letting them choose — this is exactly the kind of fork their "ask questions, don't assume" preference calls for.

---

## 10. RISKS

### Regulatory — UK-specific, treat as highest priority
**The building entity is UK-registered; UK law governs.** This changes the regulatory lens from the generic "consult a lawyer in your jurisdiction" language used earlier in this project to something specific:
- Once this becomes a paid, multi-user product that executes trades on people's behalf (or even gives them signals to act on), **FCA regulation of investment business and financial promotions is very likely to apply** — the exact boundary depends on how much discretion the product exercises and how it's marketed, and that boundary determination needs a UK-qualified fintech/financial-services solicitor, not an AI's best guess.
- The UK's **Consumer Duty** regime is notably strict about consumer-facing financial products and services — worth specific attention given the subscription/multi-user direction.
- **UK GDPR** and data protection law govern any storage of other users' personal and financial data, including broker API credentials — proper encryption-at-rest and a real secrets management approach (KMS/vault, never plaintext DB columns or `.env` files in a multi-user context) is a hard requirement before that phase, not a nice-to-have.
- **Gate condition, explicitly**: do not begin the multi-user build (see OPEN THREADS) without UK-qualified legal sign-off first. This has been said to the person multiple times through this project and should keep being said.

### Technical / correctness
- Everything marked ⚠️ in VERIFICATION STATUS is a real risk if assumed to work — none of it has touched a real server.
- The example strategy (SMA crossover) has been repeatedly, rigorously shown **unprofitable** after realistic costs, across every validation method tried. It is a deliberately honest placeholder proving the pipeline works, not a trading edge. Treating it as one, or skipping the validation-tooling step before trusting a new strategy, is a real risk once real money is anywhere near this system.
- Nothing in this project constitutes financial advice, and that framing should be preserved in any user-facing output the product eventually produces.

### Security posture
- Broker/exchange credentials are currently handled via plain environment variables throughout (`OANDA_API_TOKEN`, `{EXCHANGE}_API_KEY`, etc.) — entirely appropriate for a single-user local/dev setup, but this pattern **must not** carry into any multi-user version. No security hardening work has been done yet; it's explicitly deferred (see OPEN THREADS), not overlooked.

### Scope-creep risk
- The analytical toolkit is already substantial (four `backtest/` modules). As multi-currency/multi-asset breadth is added, watch that `BrokerAdapter`, `CostModel`, and `Strategy` continue to generalise cleanly rather than accumulating per-instrument special-casing — that clean generalisation is the entire point of the abstractions chosen (see KEY DECISIONS).

### Minor — tooling/operational note, not a project risk
- The file-creation tool used to build this project intermittently threw an `Input validation errors occurred: path: Field required` error mid-session, and in a few cases left a stray, incomplete file at the target path despite reporting failure. Workaround used throughout: check for and delete the stray file, then retry the same `create_file` call cleanly. Worth knowing if it recurs.

---

## 11. KEY CONCEPTS GLOSSARY

| Concept | Definition | Where implemented |
|---|---|---|
| Crossover | The moment a fast SMA flips from below to above (or vice versa) a slow SMA — the only signal `SmaCrossoverStrategy` acts on. Only the *change* triggers a signal, not simply being above/below. | `strategy/sma_crossover.py` |
| Equity curve | `balance + unrealised P&L of any open position`, recorded every candle. Flat between trades, moves with mark-to-market while a position is open. | `backtest/engine.py`, `BacktestEngine.run()` |
| Mark-to-market | What a position would net if closed *right now* — uses the same slippage/commission model a real close uses (fixed bug, see KEY DECISIONS). | `BacktestEngine._mark_to_market_pnl` |
| Walk-forward testing | Select parameters on an in-sample window, test them *unchanged* on the following out-of-sample window, slide forward, repeat. Tests generalisation over time. | `backtest/walk_forward.py` |
| Neighbor-gap | How much better a parameter combination scores than its immediate grid-neighbours. Large gap = suspected overfit spike; small gap = plateau, more trustworthy. | `backtest/sensitivity.py` |
| Bootstrap resample | Draws trades *with replacement* many times — tests the plausible range of outcomes if the same process repeated. | `backtest/bootstrap.py`, `method="resample"` |
| Bootstrap shuffle | Reorders the *same* trades without replacement — isolates path/sequencing risk in drawdown, since total return is mathematically order-invariant. | `backtest/bootstrap.py`, `method="shuffle"` |
| BrokerAdapter | The common interface (`fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`) every broker/exchange integration implements. | `brokers/base.py` |
| BrokerRouter | Holds multiple `BrokerAdapter`s, queries live quotes from all of them, routes to whichever offers the best price; skips (doesn't crash on) a failing one. | `brokers/router.py` |

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
python -m tests.test_ccxt_broker
python -m tests.test_router_multi_exchange
python -m tests.test_walk_forward
python -m tests.test_sensitivity
python -m tests.test_bootstrap
```

**To connect real services** (all currently unverified — see VERIFICATION STATUS):
- OANDA: `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` env vars (practice account — see README for setup link).
- Cloud Postgres/Snowflake Postgres: `DATABASE_URL` env var, e.g. `postgresql://user:pass@host:5432/postgres?sslmode=require`.
- A ccxt exchange: `{EXCHANGE_ID}_API_KEY`, `{EXCHANGE_ID}_API_SECRET` env vars, e.g. `BINANCE_API_KEY`.

---

## 13. HANDOFF COMPLETENESS CHECKLIST

Self-audit performed before finalising this document — every major thread from the source conversation, confirmed present below:

- [x] Initial architecture (data layer, brokers, strategy, backtest engine, tests) — KEY DECISIONS, REPOSITORY MAP
- [x] Cloud DB discussion and Neon → Snowflake Postgres decision — KEY DECISIONS
- [x] Broker comparison (OANDA/IB/FXCM/IC Markets-cTrader) — KEY DECISIONS
- [x] Vultr/compute-host discussion — OPEN THREADS
- [x] Rust vs. Python decision, including the crypto/multi-user follow-up question — KEY DECISIONS
- [x] Crypto integration via ccxt — KEY DECISIONS, REPOSITORY MAP
- [x] Crossover/equity-curve educational explanation — KEY CONCEPTS GLOSSARY
- [x] Mark-to-market bugfix — KEY DECISIONS, CURRENT STATE
- [x] Walk-forward testing + the position-continuity bugfix — KEY DECISIONS, CURRENT STATE
- [x] Sensitivity analysis + the real overfitting finding — CURRENT STATE
- [x] Bootstrap resampling (resample + shuffle) — KEY DECISIONS, CURRENT STATE
- [x] Multi-user SaaS / regulatory considerations, now UK-specific — RISKS, OPEN THREADS
- [x] Testing philosophy — KEY DECISIONS
- [x] Real numeric results from every demo — CURRENT STATE
- [x] userPreferences quoted verbatim — CONSTRAINTS & PREFERENCES
- [x] New product-focus reframe (multi-currency/multi-crypto breadth) — PRODUCT FOCUS, OPEN THREADS, IMMEDIATE NEXT STEP
- [x] UK jurisdiction note — flagged prominently at top, detailed in RISKS
- [x] Verification status (real vs. written-to-spec) preserved with full fidelity — VERIFICATION STATUS

**Not carried into this document because it doesn't need to be**: the turn-by-turn conversational back-and-forth itself (which questions were asked in which order) — what matters is the decisions and reasoning that resulted, which are all captured above.

**File packaging note**: this document is included inside the `fx_trader/` project folder and packaged into `fx_trader.zip` alongside the full codebase.

**Getting this into the Project (file upload of a folder does NOT work in Claude Projects — confirmed, not a one-off glitch):** Claude Projects' knowledge area is flat with no folder/subfolder support at all, so uploading a folder directly will always error, and uploading its contents flattens everything into one undifferentiated file list. Two working options:
1. **Preferred: connect via the GitHub integration** (Project knowledge panel → "+" → GitHub). This is the only option that preserves real folder structure in a way Claude can browse, and gives a one-click "Sync now" for updates. Push this codebase to a private GitHub repo, connect it once, re-sync whenever it changes.
2. **Fallback: upload `fx_trader.zip` as a single knowledge file.** Claude can't browse inside it from the Project's file list, but any Claude instance with code execution enabled can extract it into a working sandbox at the start of a session and recover the full structure. Update by deleting the old zip and uploading a fresh one — there's no versioning, so don't leave stale copies sitting alongside the current one.

Either way, a fresh instance should extract/sync the codebase and read this document in full **before** writing or changing anything — ideally enforced via a Project custom instruction rather than repeated by hand each session.
