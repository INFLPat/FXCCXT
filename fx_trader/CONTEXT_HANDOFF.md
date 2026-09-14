# CONTEXT HANDOFF — FX/Crypto Trading Analysis Project

**Written by:** Claude (outgoing instance), end of session.
**Purpose:** Let a fresh Claude instance — with zero memory of this conversation — pick up this project exactly where it left off, with no re-litigating of settled decisions and no loss of context.
**How to use this document:** Read it fully before touching the code. **Read Section 16 first if you're short on time — it's the most recent session and changes what "immediate next step" means. Section 15 is still fully valid background, just superseded on current status.**

**Jurisdictional note (read this early):** The entity building this is **UK-registered; UK law governs.** Regulatory and compliance considerations from here on should be framed specifically around UK requirements (FCA, financial promotions rules, UK GDPR), not generic multi-jurisdiction language. Full detail in RISKS. **The business also leans GBP/USD as its core currencies (GBP as the primary anchor, USD close behind, EUR represented for the UK/EU angle) — see Section 15.**

---

## 1. OBJECTIVE

Build and rigorously validate a Python-based system that analyses historical and live FX and crypto price data to identify and visualise **the most reliable and flexible way to determine the optimum moment to execute a trade or currency conversion** — approached through multiple, complementary analytical processes rather than a single method. The near-term focus is a thoroughly-tested, single-user analytical pipeline capable of trading across the largest practical range of interchangeable fiat currencies plus a solid portfolio of crypto assets. The long-term goal is a multi-user subscription product, with UK regulatory compliance treated as a first-order design constraint from the outset.

---

## 2. PRODUCT FOCUS & SCOPE

**This is the central pillar.** The project is a research and decision-support system, not "a bot that trades EUR/USD." Built as **multiple independent analytical streams**:

- **Signal generation** (`strategy/`) — currently: SMA crossover, deliberately simple/example-grade.
- **Realistic simulation** (`backtest/engine.py`) — net of real trading costs.
- **Validity/trust interrogation** — walk-forward (holds up over time?), sensitivity (parameter space smooth, or lucky one-off?), bootstrap (how much is luck?).
- **A planned fourth stream, not yet designed** — cross-pair/cartesian comparison and statistical pairs trading. Explicitly deferred to its own dedicated session. **Section 16 adds a specific, reasoned expectation: this stream likely needs finer-than-hourly granularity, for a different reason than "crypto is fast" — see Section 16 before that session starts.**

**Near-term scope — now substantially real (Section 16):** As of this session, **12 of 16 planned real sandbox instruments have genuine, verified, real market data — the first real data this project has ever had.**

**Longer-term (deferred):** user personas, and a new product idea from Section 16 — **tiered subscription refresh rates** — with an important caveat attached (see Section 16). Don't start either until the single-user pipeline is validated against real data.

---

## 3. KEY DECISIONS

Treat re-opening any of these as requiring a genuinely new reason. **Sections 15/16 add to these; neither revisits any of them.**

### Language & runtime
- **Python throughout, not Rust.** Bottleneck is network I/O, not CPU. Revisit only for genuine tick-level arbitrage/market-making.

### Data layer
- **SQLite (local/test) + raw psycopg2 (cloud Postgres), not SQLAlchemy.**
- **Snowflake Postgres is the chosen cloud database.** `FxStore` speaks plain `postgresql://` — zero code changes needed to support it.
- **The frozen sandbox dataset stays out of any cloud database, forever, by design** (Section 15) — static reference data, no need for cloud infra. Only a future live feed needs Postgres. Don't let the two share a table if/when the live feed is built.

### Broker & exchange integration
- **`BrokerAdapter` abstract interface** — every broker/exchange implements `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`.
- **OANDA is the first FX broker.** **As of Section 16, `OandaBroker.fetch_candles` has been genuinely run against a real practice account for the first time — and had two real, confirmed bugs, both found and fixed this session.** Doesn't change the choice of OANDA; it's exactly the kind of thing "written to spec, never run" was always expected to eventually surface.
- **ccxt is the crypto integration layer.** Exercised against two real exchanges (Binance, Kraken) for the first time this session. Binance genuinely works as written. **Kraken's *live* OHLC API has a confirmed, real, external limitation — not a bug in our code.**
- **`BrokerRouter`** picks the best live quote per trade across connected adapters.

### Cost modelling
- **`CostModel`** supports both pip-based (FX) and percentage-based (crypto) costs, additively.

### Correctness fixes found and made during this build
- **Mark-to-market bug** (early session): fixed via `BacktestEngine._mark_to_market_pnl()`.
- **Walk-forward continuity bug** (early session): fixed via `warm_start`/`initial_trade` on `BacktestEngine.run()`.
- **OandaBroker timestamp format bug (Section 16)**: `fetch_candles` built request timestamps with Python's `.isoformat()`, producing `"...+00:00"`. OANDA's v20 API expects a literal `"Z"` suffix (confirmed against OANDA's own documented examples). Fixed via `_format_oanda_time()`. This alone did NOT fully resolve the observed 400 errors.
- **OandaBroker count/from/to combination bug (Section 16)**: `fetch_candles` sent `from`, `to`, AND `count` together on every request. OANDA's own OpenAPI spec states: *"Count should not be specified if both the start and end parameters are provided."* Fixed by never sending `to` to the API — paginating via `from`+`count` only, filtering against the caller's `end` client-side. **Neither fix could be verified by the Claude instance that made it — no network access in this project's build sandbox. Both were confirmed working only when the person re-ran the script against a real account.**

### Validation tooling design
- Walk-forward, sensitivity, bootstrap — unchanged, see README for detail.
- **Validation hierarchy (Section 15)** — explicit, ordered, cost-tiered gate, see `VALIDATION_HIERARCHY.md`. Not yet run against real data.

### Real-data sourcing discoveries (Section 16, new)
- **Kraken's live OHLC API only serves a rolling recent window**, confirmed via a real 0-candles-fetched result. Matches Kraken's own docs and multiple independent community reports. Not fixable in our code — worked around via Kraken's bulk historical CSV export (`ingest_kraken_gbp_csv.py`).
- **Kraken's bulk "Complete Data" archive is itself a point-in-time snapshot**, not continuously live — discovered when a verified-correct parser still returned zero 2026 rows. Kraken separately publishes quarterly "Incremental Updates" for exactly this reason. **Status at session end: unresolved — diagnosis in progress, see Section 16 OPEN ITEMS.**
- **Volume precision**: `ccxt_broker.py` truncates volume to `int()`. Harmless for Binance (volumes in the hundreds), would have been destructive for Kraken's GBP pairs (frequently sub-1 in the raw data, e.g. `0.06`, `0.2`, `0.405`). `ingest_kraken_gbp_csv.py` stores the true float instead. **Pre-existing Binance rows still have this precision loss — flagged, not fixed, low priority.**

### Testing philosophy
Unchanged. Section 16 is a clean live example: two real bugs were found specifically *because* the code was finally run for real, not because anyone reviewed it harder.

### Regulatory framing
Unchanged — see RISKS.

---

## 4. CURRENT STATE

**As of Section 16, this project has real, verified market data for the first time in its history.**

- **Data layer**: `FxStore` (SQLite verified, Postgres unverified) + `RunStore` (SQLite verified) + `get_candles_multi()` (verified) — all Section 15.
- **Broker/exchange layer**: `OandaBroker.fetch_candles` — **now genuinely run and verified**, after finding and fixing two real bugs. `get_quote`/`place_market_order` remain completely untested — treat with *more* suspicion than before this session, not less, since the same hand-built-request-params pattern produced two bugs in `fetch_candles`. `CcxtBroker` against Binance — **genuinely run and verified**. `CcxtBroker` against Kraken — run, and genuinely informative: confirmed a real limitation of the exchange, not a defect here.
- **Sandbox dataset (2026-01-01 to 2026-06-30, H1/1h)**: **12 of 16 instruments confirmed with real, verified, sane data** (8 FX via OANDA, 4 USD-crypto via Binance). **4 pending** (GBP-crypto via Kraken) — parser verified against real sample data, but actual ingestion found zero rows in the target window; likely cause is a stale base snapshot needing incremental files, unconfirmed at session end.
- **Everything from Section 15** (RunStore, get_candles_multi, VALIDATION_HIERARCHY.md) — built, tested, **still not exercised against real data**, since real data has only just started arriving.

The four synthetic-data analysis layers (metrics, walk-forward, sensitivity, bootstrap) remain exactly as documented previously — not yet re-run against any real data.

---

## 5. REPOSITORY MAP

```
fx_trader/
├── README.md
├── VALIDATION_HIERARCHY.md
├── requirements.txt
├── CONTEXT_HANDOFF.md
├── fetch_sandbox_data.py              — pulls FX (OANDA) + USD-crypto (Binance). WORKING after 2 bug fixes to brokers/oanda.py (Section 16).
├── ingest_kraken_gbp_csv.py           — Loads Kraken bulk CSVs for the 4 GBP-crypto pairs. Parser verified; full run pending diagnosis (Section 16).
├── data/
│   ├── store.py                       — FxStore, incl. get_candles_multi() (Section 15, verified)
│   └── run_store.py                   — RunStore (Section 15, verified on SQLite)
├── brokers/
│   ├── base.py
│   ├── oanda.py                       — MODIFIED TWICE this session - two real bugs fixed in fetch_candles. get_quote/place_market_order untouched, still fully untested.
│   ├── ccxt_broker.py                 — unmodified; volume-truncation issue (Section 16) lives here, flagged not fixed.
│   └── router.py
├── strategy/
│   ├── base.py
│   └── sma_crossover.py
├── backtest/
│   ├── engine.py
│   ├── metrics.py
│   ├── walk_forward.py
│   ├── sensitivity.py
│   └── bootstrap.py
├── tests/
│   ├── synthetic_data.py
│   ├── fake_ccxt_exchange.py
│   ├── test_engine.py
│   ├── test_store.py
│   ├── test_multi_query.py
│   ├── test_run_store.py
│   ├── test_ccxt_broker.py
│   ├── test_router_multi_exchange.py
│   ├── test_walk_forward.py
│   ├── test_sensitivity.py
│   └── test_bootstrap.py
├── run_backtest_demo.py
├── run_crypto_backtest_demo.py
├── run_walk_forward_demo.py
├── run_sensitivity_demo.py
└── run_bootstrap_demo.py

(repo root, alongside fx_trader/)
└── .gitignore                          — excludes data/*.db and credential files (Section 15)
```

**Not in git**: `data/sandbox_2026h1.db` — deliberately, per `.gitignore`. Lives only on the person's machine, uploaded directly to whichever chat needs it.

---

## 6. VERIFICATION STATUS

**The single most important section not to lose fidelity on.**

### ✅ Actually run and verified
- Backtest engine cost math, `FxStore`/`RunStore` on SQLite, `get_candles_multi()`, `CcxtBroker`'s logic against a fake exchange, `BrokerRouter`, walk-forward/sensitivity/bootstrap math — unchanged from earlier sessions.
- **`OandaBroker.fetch_candles`** — genuinely run against a real practice account. 8 FX instruments, 3,075 candles each, real GBP/EUR/USD/JPY/CHF/CAD prices and sane spreads. Two real bugs found and fixed getting here — took two rounds of real execution, not one.
- **`CcxtBroker.fetch_candles` against Binance** — genuinely run. 4 USD-crypto instruments, exactly 4,344 candles each (181 days × 24h — exact match), real BTC/ETH/XRP/LTC prices.
- **`ingest_kraken_gbp_csv.py`'s CSV parser** — verified against real sample data: correct column mapping, correct timestamp conversion, correct float volume preservation.

### ⚠️ Written to spec, NEVER executed against anything real
- **`OandaBroker.get_quote` / `place_market_order`** — **elevated concern as of this session**: `fetch_candles` looked equally reasonable before being run, and had two real bugs. Don't assume these are fine on the same basis.
- **`FxStore`/`RunStore` on Postgres/Snowflake Postgres** — never connected to a real server.
- **`CcxtBroker` against Kraken specifically** — technically "run," but the result (0 candles, always) is a confirmed real limitation of Kraken's live API, not a working verification. Treat Kraken's live OHLC endpoint as **known not to work** for historical pulls beyond a rolling recent window — an external constraint, not a "maybe fix later."
- **The full ingestion of Kraken's bulk CSV data** — parser verified, end-to-end run found 0 rows. Root cause suspected, not confirmed.

**Why this gap exists**: the build sandbox has no network access at all.

---

## 7. CONSTRAINTS & PREFERENCES

Unchanged — the person's standing preferences (quoted verbatim in earlier sections) all still apply. **Reinforced this session**: when the person asks a pointed technical question ("is this correct as expected?", "which files are needed?"), the right response is a direct, confident, verified answer — not hedging, not re-explaining context they already have.

---

## 8. OPEN THREADS

- **Kraken GBP-crypto ingestion — actively blocked, the most concrete open item.** `ingest_kraken_gbp_csv.py` found 0 rows for all 4 pairs. Leading hypothesis: the "Complete Data" base archive predates 2026, and Kraken's quarterly "Incremental Updates" (same support page, separate folder) need adding on top. The script now accepts a *list* of filenames per instrument specifically to make this easy once confirmed. **Next action: `tail -3` on one of the CSVs to see its actual last date, then get matching incremental file(s) if needed.**
- **Tier 0–4 validation orchestrator** — not built. Genuinely blocked on real data being fully ready (12/16 so far), not just deferred on principle.
- **Cross-pair/cartesian comparator + statistical pairs trading** — deferred to a dedicated future session. **New**: reasoned expectation this needs finer-than-hourly granularity (mean-reversion signals decay faster than trend signals — different reasoning from "crypto is fast," which was separately pushed back on). Two things to have ready before that session: (1) multi-instrument timestamp/candle-boundary alignment matters more at finer granularity across OANDA/Binance/Kraken's differing conventions; (2) Kraken's live-API depth limit will very likely resurface for finer-granularity historical data too.
- **New product idea: tiered subscription refresh/data-capture rates** — proposed tiers ~5min / ~30sec-1min / ~1sec. **Caveat, not yet resolved into a design**: refresh rate (infrastructure) and signal granularity (strategy design) are different axes — faster polling alone doesn't improve profitability unless the strategy itself is redesigned for that granularity, and live granularity must match what was validated or the backtest stops representing reality. Finer granularity also means more trades means more cost drag (already evidenced: crypto's worse synthetic result vs FX at H1) — untested assumption, not a given.
- **`ccxt_broker.py` volume truncation** — flagged, low priority, not fixed.
- Everything else from earlier Open Threads (position sizing, multi-user entitlements, portfolio aggregation, live feed, deployment infra) — unchanged, still open.

---

## 9. IMMEDIATE NEXT STEP

1. **Resolve the Kraken ingestion gap.** Check the base CSVs' actual last date (`tail -3`), get incremental files if needed, re-run `ingest_kraken_gbp_csv.py`, confirm all 16 instruments show real coverage.
2. **Only once genuinely 16/16**: run the SMA crossover strategy against the sandbox through `VALIDATION_HIERARCHY.md`'s tiers, cheapest first — don't skip to sensitivity/walk-forward/bootstrap before Tier 0/1 has filtered anything.
3. **Before trusting `get_quote`/`place_market_order`**: test them explicitly, given `fetch_candles`'s two-bug history in the same file.
4. Only after that, in a dedicated new session, start the cross-pair/cartesian/pairs-trading work — with the granularity expectation from this session in hand already.

---

## 10. RISKS

Unchanged in substance. One addition: **two real bugs found in `OandaBroker.fetch_candles` this session are a concrete demonstration that "written to spec, follows the documented API" isn't the same as "correct."** Apply the same suspicion to anything still in the ⚠️ column of Section 6, especially `get_quote`/`place_market_order`.

---

## 11. KEY CONCEPTS GLOSSARY

Earlier entries unchanged. Additions:

| Concept | Definition | Where implemented |
|---|---|---|
| Signal granularity vs. refresh rate | Two different things: signal granularity is the candle size driving actual trade decisions (strategy-design property); refresh rate is how often the system polls/updates a UI (product-tier property). Faster refresh alone doesn't change a strategy's behaviour. | Discussed Section 16, no code yet |
| Bulk historical CSV (Kraken) | Kraken's downloadable historical OHLCVT archive, separate from its live API. A point-in-time snapshot, not continuously live - hence Kraken's quarterly "Incremental Updates". | `ingest_kraken_gbp_csv.py` |

---

## 12. HOW TO GET SET UP

Unchanged command list, plus:
```bash
python3 ingest_kraken_gbp_csv.py
```

---

## 13–14. (Unchanged from earlier sessions)

Handoff completeness checklist and the GitHub sync process (pull-only integration, manual push workflow) — both still accurate as originally written.

---

## 15. SANDBOX DATASET, RUN PERSISTENCE & VALIDATION HIERARCHY

Built `RunStore` for persisting validated backtest results, with an explicit pure-function/opt-in-persistence split (`BacktestEngine.run()` stays side-effect-free; persistence is a separate, explicit call). Built `FxStore.get_candles_multi()` for the future cartesian work. Designed the tiered `VALIDATION_HIERARCHY.md` (Tier 0 sanity → Tier 1 light metrics on the full grid → Tier 2 neighbor-gap plateau check, free → Tier 3 walk-forward on survivors only → Tier 4 bootstrap on finalists only → persist). Wrote `fetch_sandbox_data.py` but had not yet run it. Established the UK/GBP-USD currency framing and the 16-instrument sandbox scope (8 FX leaning GBP/USD/EUR, 8 crypto split 4 USD/4 GBP across the same 4 assets - BTC/ETH/XRP/LTC, with LTC substituted for the originally-proposed SOL specifically because SOL/GBP's Kraken availability wasn't confirmed at the time). Decided the sandbox DB stays out of git (`.gitignore`) since the Claude/GitHub context sync can't usefully read binary files anyway, and real market data commonly carries redistribution restrictions.

---

## 16. REAL DATA ARRIVES: TWO OANDA BUGS FOUND, KRAKEN'S REAL LIMITATIONS, AND A PRODUCT DISCUSSION (added by a later session)

This is where synthetic-only testing ended and real data began.

### `fetch_sandbox_data.py` run for real — two bugs found and fixed in `OandaBroker.fetch_candles`

First run: all 8 FX instruments failed with `400 Bad Request`, immediately, every time. Diagnosed via OANDA's own documented request examples: the code built timestamps with `.isoformat()`, producing `"...+00:00"` where OANDA expects a literal `"Z"` suffix. Fixed via `_format_oanda_time()`.

Second run, after that fix: format confirmed correct in the outgoing request, but **still** `400 Bad Request` on every FX instrument. Important methodological point: **the first fix was real and necessary, but not sufficient** — changing one thing at a time, then re-running, rather than changing several things at once, is what made it possible to know a second, distinct bug remained. Diagnosed via OANDA's official OpenAPI specification directly: *"Count should not be specified if both the start and end parameters are provided."* The code sent `from`, `to`, and `count` together on every request. Fixed by never sending `to` at all — paginating via `from`+`count` only, filtering against the caller's `end` client-side.

Third run: all 8 FX instruments succeeded. 3,075 candles each, real GBP/EUR/USD/JPY/CHF/CAD prices and spreads, spot-checked as sane, not just trusted.

**Neither fix could be verified by the Claude instance that made it** — no network access in this project's build sandbox. Both were reasoned from primary sources (OANDA's own docs and OpenAPI spec) and confirmed working only when the person re-ran the script against their real account. The project's verification discipline working exactly as designed, across a real session boundary.

### Binance (USD-crypto) worked correctly on the first attempt

4 instruments, exactly 4,344 candles each — 181 days × 24 hours, matching the sandbox window exactly. No issues found. Real BTC/ETH/XRP/LTC prices, all plausible for the period.

### Kraken (GBP-crypto): a real external limitation, then a second one

First finding: Kraken's *live* OHLC API only serves a rolling recent window regardless of the `since` requested — confirmed by the fetch script genuinely returning 0 candles (not an error) for all 4 GBP pairs, corroborated independently by Kraken's own docs and multiple ccxt/freqtrade community reports describing the same limitation. Not fixable in this codebase. Worked around via Kraken's separate bulk historical CSV export — `ingest_kraken_gbp_csv.py` parses it, verified against a real sample of the person's actual downloaded data (correct columns: `unix_time,open,high,low,close,volume,trades`, no header; correct float volume preservation, since Kraken's GBP-pair volumes are frequently under 1 and `ccxt_broker.py`'s existing `int(vol)` truncation would have destroyed that data entirely).

Second finding, discovered when the verified parser still returned 0 rows for the real files: Kraken's bulk "Complete Data" archive is **itself** a point-in-time snapshot, not continuously live — exactly why Kraken separately publishes quarterly "Incremental Updates". If the base snapshot predates 2026-01-01, it simply doesn't contain any of the sandbox window. **Mid-diagnosis at session end** — checking the base files' actual last date (`tail -3`) should confirm this definitively. `ingest_kraken_gbp_csv.py` was refactored to accept a *list* of filenames per instrument specifically so incremental files can be added without further script changes once confirmed.

**A filename mixup was also caught and corrected**: 8 CSVs were initially prepared (4 GBP + 4 USD), but the 4 USD ones weren't needed — Binance already provides verified USD-quoted data. One filename (`XBTPYUSD_60.csv`) was flagged as likely Bitcoin priced in PYUSD (PayPal's stablecoin), not plain USD — worth knowing if Kraken's own USD data is ever wanted later; the correct file would be `XBTUSD_60.csv`.

### Product discussion: tiered refresh rates vs. signal granularity

The person proposed tiered subscription refresh rates for the eventual live feed (~5min lowest, ~30sec-1min mid, ~1sec top), reasoning that crypto's dynamism means faster refresh should mean better profitability. Pushed back on directly: **refresh rate (how often the system polls/updates a UI) and signal granularity (the candle size actually driving a strategy's trade decisions) are different axes.** A system polling every second but computing an SMA on hourly candles doesn't trade differently — it just learns about the same hourly decision sooner. Faster refresh can only improve the *user's awareness* of a decision the strategy already made at its real signal granularity, not the strategy's actual behaviour.

Two further reasons "faster = more profitable" isn't a safe default, even for crypto: shorter timeframes have a worse noise-to-signal ratio generally; and cost drag scales with trade count, with direct evidence already in hand — the synthetic-data crypto demo lost far more than the equivalent FX one (-15.49% vs -0.51%) purely from percentage fees compounding across more, more volatile trades, at H1 alone. Reacting many times more often without a strategy specifically redesigned to profit from sub-hourly moves would plausibly bleed faster, not win faster.

**The person's instinct that a sandbox-at-one-speed/live-at-another-speed setup felt suspicious was correct, with a concrete mechanism**: this project's entire validation methodology exists to prove what gets validated is what actually runs live. A live system polling/trading at a different effective granularity than what was backtested is running a different, unvalidated strategy that happens to share a name. **Resolution for now**: keep H1 for current work. Refresh-rate tiering remains a legitimate future feature, marketed honestly as "how quickly you're notified," not "trades better" — unless a strategy is later built and separately validated at finer granularity. **Not resolved into a design or implementation** — a discussion outcome to hold onto, not a spec.

### Product discussion: does cartesian/pairs-trading work need finer granularity?

Separately suggested, and affirmed as plausible rather than pushed back on — different mechanism from the refresh-rate discussion: **mean-reversion/relative-value signals typically decay faster than trend signals**, so an hourly candle can smooth away or entirely miss a divergence-and-reversion that resolved within that hour. Still flagged as a hypothesis to validate empirically once the hierarchy is running, not assumed correct because the reasoning sounds right — same discipline in both directions. Flagged for that future session: multi-instrument timestamp/candle-boundary alignment matters more at finer granularity across differing exchange conventions; and Kraken's live-API depth limit will likely resurface for finer-granularity historical data too.

### Session-end honest status

12 of 16 sandbox instruments confirmed with real, spot-checked data. 4 (Kraken GBP-crypto) blocked on an unresolved data-availability question, actively being diagnosed. Two real bugs found and fixed in `OandaBroker.fetch_candles`, both confirmed working by real re-execution. One data-fidelity issue found and not fixed (`ccxt_broker.py` volume truncation, low priority). Two product-design discussions had and recorded, neither resolved into an implementation. The validation hierarchy, `RunStore`, and every analysis tool from earlier sessions remain completely unexercised against real data — the actual next body of work, blocked only on closing the Kraken gap.
