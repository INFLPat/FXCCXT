# FX Trader

A backtestable, cost-aware FX trading pipeline. Built to go: historical data → strategy → backtest → paper trading → (eventually, carefully) live trading, using the *same* strategy code at every stage, against a cloud database and a broker-agnostic execution layer.

**The business is UK-based: GBP and USD are the core currencies for all analysis (leaning UK/EU), with EUR also represented.** Don't default to EUR/USD-only examples — see `CONTEXT_HANDOFF.md` Section 15.

## Working across Claude chats (GitHub sync workflow)

This project is developed across multiple Claude.ai chats inside one Claude Project, with this GitHub repo (`INFLPat/FXCCXT`, public) as the source of truth for code between sessions.

**Read this before assuming otherwise: Claude.ai's GitHub integration is pull-only.** There is no automatic push from a Claude chat back to this repo, on any plan — the Project's "Context" panel only reads repo content into a chat's context.

The real workflow, every session:
1. New chat → pulls the current GitHub state into context via the Project's Context panel (hit its refresh/sync control if content looks stale).
2. Claude edits or produces files inside that chat's own sandbox.
3. The user downloads those files.
4. The user pushes them to GitHub — via `git`, or by manually re-uploading/editing the changed file(s) on github.com.
5. The user refreshes/syncs the Context panel so the *next* chat picks up the change.

**Never push a stale bulk copy of the whole project without first confirming the chat's GitHub context is current.** Doing so can silently overwrite work committed by a different, more recent chat. Always sync from GitHub at the start of a session, and push only the specific files that actually changed that session.

A fully automated push (no manual step 3/4) would require **Claude Code** instead of this chat interface — a separate Anthropic product with real local git access. Not currently in use for this project.

## Project layout

```
fx_trader/
├── VALIDATION_HIERARCHY.md   # Tiered validation/persistence gate - which checks run before a result is allowed to persist
├── fetch_sandbox_data.py     # Pulls a real 6-month FX+crypto dataset via OandaBroker/CcxtBroker - written, not yet run
├── data/
│   ├── store.py               # Candle storage - SQLite (local) or Postgres (cloud), same code. Includes get_candles_multi() for cross-instrument queries.
│   └── run_store.py           # RunStore: persists validated backtest results (trades, params, metrics), tagged with the validation tier reached
├── brokers/
│   ├── base.py               # BrokerAdapter interface every broker plugs into
│   ├── oanda.py               # OANDA implementation (data + live quotes + order placement)
│   ├── ccxt_broker.py         # Crypto exchanges via ccxt (100+ exchanges, one adapter)
│   └── router.py              # Picks the best connected broker for a trade
├── strategy/
│   ├── base.py               # Strategy interface every strategy implements
│   └── sma_crossover.py      # Example strategy (NOT proven-profitable - see below)
├── backtest/
│   ├── engine.py              # Simulates a strategy against historical data with real costs
│   ├── metrics.py             # Return, drawdown, Sharpe, win rate, profit factor
│   ├── walk_forward.py        # Rolling out-of-sample validation - tests whether backtest results are real
│   ├── sensitivity.py         # Parameter grid analysis - tests whether results are a spike or a plateau
│   └── bootstrap.py           # Resample/shuffle Monte Carlo - tests how much of a result is luck
├── tests/
│   ├── synthetic_data.py            # Fake FX + crypto data generators, no API key needed
│   ├── fake_ccxt_exchange.py         # Minimal ccxt-shaped fake, for offline adapter testing
│   ├── test_engine.py                # Hand-verified cost-math sanity tests (pip-based + %-based)
│   ├── test_store.py                 # Storage round-trip/upsert test (SQLite by default, or real Postgres)
│   ├── test_multi_query.py           # get_candles_multi() - multi-instrument query correctness
│   ├── test_run_store.py             # RunStore round-trip against a real BacktestResult
│   ├── test_ccxt_broker.py           # CcxtBroker logic tested against the fake exchange
│   ├── test_router_multi_exchange.py # Proves BrokerRouter actually picks the better price
│   ├── test_walk_forward.py          # Hand-verified windowing math + a fully hand-computable end-to-end run
│   ├── test_sensitivity.py           # Hand-verified neighbor-gap/heatmap math + invalid-combo handling
│   └── test_bootstrap.py             # Hand-verified percentile math + Monte Carlo vs. exhaustive enumeration
├── run_backtest_demo.py              # End-to-end FX demo using synthetic data
├── run_crypto_backtest_demo.py       # End-to-end crypto demo using synthetic data
├── run_walk_forward_demo.py          # SMA crossover walk-forward validation on synthetic FX data
├── run_sensitivity_demo.py           # SMA crossover parameter sensitivity analysis on synthetic FX data
├── run_bootstrap_demo.py             # SMA crossover bootstrap resampling on synthetic FX data
└── requirements.txt
```

## Real-data sandbox, run persistence & validation hierarchy

Everything above this point in the project's history ran on synthetic data. `fetch_sandbox_data.py` pulls a **fixed, real 6-month window** (2026-01-01 to 2026-06-30, H1 granularity) across 16 real instruments, chosen for the business's actual GBP/USD/EUR focus:

- FX (OANDA): `GBP_USD, EUR_GBP, GBP_JPY, GBP_CHF, EUR_USD, USD_JPY, USD_CHF, USD_CAD`
- Crypto vs USD (Binance): `BTC/USDT, ETH/USDT, XRP/USDT, LTC/USDT`
- Crypto vs GBP (Kraken): `BTC/GBP, ETH/GBP, XRP/GBP, LTC/GBP`

Same 4 crypto assets on both quote currencies deliberately, so any USD-vs-GBP behavioural difference a strategy shows is a real quote-currency effect, not a different-asset artifact. Run it locally (this project's own build sandbox has no network access) and upload the resulting `data/sandbox_2026h1.db` - see the script's own docstring for exact setup steps. **Status: written, not yet run** - see the verification table below.

Backtest **results** (as opposed to raw market data) now have somewhere to live: `RunStore` (`data/run_store.py`) persists a run's trades, parameters, cost model, and summary metrics, tagged with which tier of validation it cleared. `BacktestEngine.run()` deliberately stays a pure function with no side effects - persistence is always an explicit, separate call, never automatic, specifically so `sensitivity.py`'s grid searches and `bootstrap.py`'s resampling (which each run many backtests internally) don't flood the store with noise.

Which results are even allowed to reach `RunStore` is governed by `VALIDATION_HIERARCHY.md` - a cost-ordered gate (cheap single-run metrics first, applied to the whole candidate space, escalating through neighbor-gap plateau checks, walk-forward, and finally bootstrap resampling only for the handful of finalists that survive everything before it). Read that file for the full tier breakdown and reasoning. **The orchestrator that actually runs candidates through the tiers isn't built yet** - deliberately deferred until real sandbox data exists to set its thresholds against.

`FxStore.get_candles_multi()` fetches several instruments' candles in one query, grouped by instrument - groundwork for the planned cross-pair/cartesian rotation comparator (explicitly a separate, future, dedicated session - not designed yet).

## What's verified vs. what isn't

I can run Python in a sandbox while building this, but that sandbox has **no network access** - so anything that needs to reach OANDA's servers, a real exchange, or a real cloud database is written carefully but genuinely untested by me. Here's the honest breakdown:

| Component | Status |
|---|---|
| Backtest engine, cost math, metrics | ✅ Run + hand-verified against manually computed numbers |
| `FxStore` on SQLite | ✅ Run - round-trip, upsert-updates-not-duplicates, and range filtering all verified |
| `FxStore.get_candles_multi()` | ✅ Run - matches single-instrument `get_candles()` output exactly, empty-instrument and empty-list edge cases verified |
| `RunStore` on SQLite | ✅ Run against a real `BacktestEngine` result (not a hand-built fake) - a run and all its trades round-tripped exactly, `validation_stage` filtering verified |
| `FxStore` / `RunStore` on Postgres (cloud) | ⚠️ Same SQL as the verified SQLite paths, never executed against a real Postgres server |
| `OandaBroker.fetch_candles` / `get_quote` / `place_market_order` | ⚠️ Follows documented v20 API, not run against OANDA's servers |
| CostModel percentage-based costs (`commission_pct`/`slippage_pct`) | ✅ Run + hand-verified, same rigor as the pip-based costs |
| `CcxtBroker`'s own logic (OHLCV mapping, pagination, quote/order/balance handling) | ✅ Run against a fake exchange object - genuinely verified, no ccxt install or network needed for this |
| `CcxtBroker` against a real exchange | ⚠️ Never run against real ccxt or a live exchange |
| `fetch_sandbox_data.py` (OANDA + Binance + Kraken, 16 instruments, one run) | ⚠️ **Never run.** First time OandaBroker and CcxtBroker are exercised together, first time two ccxt exchanges are used in one run, first time any GBP-quoted crypto pair is touched. Higher-risk than earlier single-instrument tests for exactly that reason - run it and report full output back |
| `BrokerRouter` routing logic (best bid/ask selection, graceful failure) | ✅ Run + verified with two independent fake exchanges quoting different prices |
| Walk-forward selection + state/position continuity (`warm_start`, `initial_trade`) | ✅ Run + hand-verified |
| Sensitivity analysis (`neighbor_gap`, `heatmap_grid`, `marginal_effect`, invalid-combo handling) | ✅ Run + hand-verified against a constructed grid with a known, deliberate spike |
| Bootstrap resampling (`resample`/`shuffle`, percentile/CI math, order-invariance) | ✅ Run + hand-verified, including against exhaustive enumeration of a small trade set |

Run what you can locally first:
```bash
pip install -r requirements.txt
python run_backtest_demo.py
python run_crypto_backtest_demo.py
python run_walk_forward_demo.py
python run_sensitivity_demo.py
python run_bootstrap_demo.py
python -m tests.test_engine
python -m tests.test_store
python -m tests.test_multi_query
python -m tests.test_run_store
python -m tests.test_ccxt_broker
python -m tests.test_router_multi_exchange
python -m tests.test_walk_forward
python -m tests.test_sensitivity
python -m tests.test_bootstrap

# real sandbox data - needs network + a free OANDA practice token
python fetch_sandbox_data.py
```

## Cloud database

`FxStore` supports any Postgres via `DATABASE_URL`, falling back to a local SQLite file if that's unset.

**Using Snowflake Postgres**, per your choice. It's a fully managed Postgres instance running on Snowflake's infrastructure (public preview as of writing), and it connects over the plain libpq/Postgres wire protocol — confirmed against Snowflake's own connection docs — so no special driver or Snowflake-specific code was needed. It's just another `postgresql://` URL, handled by the exact same `psycopg2` path already written for any Postgres host.

To connect:
1. In Snowsight, create a Postgres instance. Snowflake gives you a connection string in this format:
   `postgresql://<username>:<password>@<hostname>:5432/<database_name>`
2. **Append `?sslmode=require`** — Snowflake's docs state SSL is required to connect. `FxStore` will now warn you at startup if `sslmode` is missing from a Postgres URL, specifically because of this.
3. `export DATABASE_URL="postgresql://snowflake_admin:yourpassword@abcefg.snowflake.app:5432/postgres?sslmode=require"`
4. Run `python -m tests.test_store` — if it prints "All store assertions passed" against your Postgres backend, the connection is confirmed working.
5. From then on, `run_backtest_demo.py`, `run_crypto_backtest_demo.py`, and anything else using `FxStore()` will use Snowflake Postgres automatically.

You don't need the `snowflake-connector-python` package for any of this — that's Snowflake's native driver for its analytical warehouse (SQL dialect, warehouses, etc.), which is a different thing entirely from Snowflake Postgres's standard Postgres wire protocol. `psycopg2-binary`, already in `requirements.txt`, is all this needs.

**Testing status**: same honesty bar as everything broker/network-facing in this project — never touched a real Snowflake Postgres instance. What's verified: the sslmode warning fires correctly on a Postgres URL missing it, and the exact same SQL (schema, upsert, queries) has been fully round-trip tested via SQLite for both `FxStore` and `RunStore`. Run `test_store.py` against your real instance to close that last gap.

## Broker/API options

I compared the current landscape - here's where things stand:

- **OANDA** (what's built): clean REST API (`v20`), excellent docs, free practice account, easiest starting point. FX + CFDs only, no stocks.
- **Interactive Brokers**: much wider market access (FX, stocks, futures, options, 150+ markets) and very low costs at scale, but the TWS API has a steeper learning curve and requires running a local gateway process.
- **FXCM**: multiple API flavors (REST, FIX, Java, a dedicated ForexConnect SDK) - good if you want protocol flexibility.
- **IC Markets / other cTrader-based brokers**: worth knowing about for your multi-broker goal specifically - cTrader's Open API is implemented by *multiple* brokers, so one integration can work across several of them rather than needing bespoke code per broker.

**On "choose the best broker automatically":** for FX majors, live spread differences between reputable brokers are usually a fraction of a pip - true price arbitrage isn't where the value is. Where multi-broker genuinely helps is redundancy (broker A's API has an outage, route to broker B) and access (different brokers, different instruments or cost structures for different strategies). `BrokerRouter` is built to support this pattern.

## Crypto support

`brokers/ccxt_broker.py` adds crypto via [ccxt](https://github.com/ccxt/ccxt), which covers 100+ exchanges through one consistent API - `CcxtBroker(exchange_id="binance")`, `CcxtBroker(exchange_id="kraken")`, etc. Two real differences from OANDA worth understanding:

- **Historical data has no separate bid/ask.** Crypto exchanges' OHLCV history is a single trade-price series, not bid/ask candles - so historical candles here set bid == ask, and trading costs are modeled entirely through `CostModel`'s `commission_pct`/`slippage_pct` fields (percentage of notional) rather than a historical spread.
- **Costs are percentage-based, not pip-based.** `CostModel` supports both, additively - FX configs using the pip fields are completely unaffected.

**Multi-exchange routing is where `BrokerRouter` earns its keep**, unlike FX: `tests/test_router_multi_exchange.py` proves it with two independently-quoting fake exchanges. `fetch_sandbox_data.py` is the first time this project actually uses two real exchanges (Binance for USD pairs, Kraken for GBP pairs) in a single run, though not yet through the router itself - each instrument's exchange is currently chosen deliberately per-pair (GBP-quoted assets aren't listed on Binance), not selected dynamically.

To go live (after sandbox testing): `CcxtBroker(exchange_id="binance", sandbox=True)` defaults to the exchange's testnet where ccxt supports one. **Important**: historical data pulls need `sandbox=False` explicitly - a testnet has no meaningful trading history, so leaving the default would silently return near-empty results. `fetch_sandbox_data.py` handles this correctly; keep it in mind for any new script that calls `CcxtBroker`.

## Parameter sensitivity analysis

`backtest/sensitivity.py` answers a different question than walk-forward testing: not "does a selected parameter set hold up over time" but "is the parameter space itself smooth enough to trust the selection process at all." Core diagnostic: `neighbor_gap()` - how much better is a point than its immediate neighbors in parameter space? A real edge produces a plateau; an isolated spike is close to the textbook signature of fitting to this dataset's noise.

On the existing synthetic-data run: across a 6x6 grid of SMA periods (35 valid combinations), only **one** was profitable at all (`fast=20, slow=60` at +0.085%), and `neighbor_gap` correctly flagged it as a likely overfit spike. Not yet re-run against real sandbox data.

## Bootstrap resampling

`backtest/bootstrap.py` answers how much of a reported result is just the luck of how these specific trades happened to land - `method="resample"` (with replacement, a genuine confidence interval) and `method="shuffle"` (reorders the same trades, isolating path/sequencing risk in drawdown since total return is mathematically order-invariant).

On the existing synthetic-data run: -0.51% return sat inside a 90% resample CI of roughly [-1.04%, +0.03%]; the actual historical drawdown sat at the 81st percentile of all possible reorderings of the same trades. Not yet re-run against real sandbox data.

## How the broker abstraction works

Every broker implements `BrokerAdapter` (`brokers/base.py`): `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`. Nothing else in the codebase needs to know which broker it's talking to. `BrokerRouter` holds a list of connected adapters and, for a given trade, queries live quotes from all of them and routes to whichever offers the better price.

## How costs are modeled in the backtest

- **Spread**: candles store bid and ask separately; you buy at the ask, sell at the bid, always.
- **Commission**: per-unit and/or flat fee, on both entry and exit.
- **Slippage**: configurable pips (or %, for crypto) charged against you on every fill.
- **Equity curve mark-to-market**: while a position is open, the equity curve reflects what closing it *right now* would actually net you - same slippage/commission logic as a real close. Verified in `tests/test_engine.py::test_mark_to_market_matches_realized_when_price_unchanged`.

## Honest note on the SMA crossover example

It's there to prove the pipeline works end-to-end, not because it's a good strategy. On synthetic random-walk data with realistic costs, it loses a small amount of money - the expected, honest result for a simple crossover on noise. Whether that holds on real GBP/USD/EUR/crypto data (the sandbox dataset) is not yet known - that's exactly what `fetch_sandbox_data.py` and the validation hierarchy exist to find out.

## Walk-forward (out-of-sample) testing

`backtest/walk_forward.py` slides a window through time: on each in-sample slice, it grid-searches `param_grid` and keeps whichever parameters scored best, then runs *only* those parameters, unchanged, on the following out-of-sample slice.

**A real, fixed bug worth knowing about**: a position still open at a window boundary used to silently vanish, since `SmaCrossoverStrategy` only signals on the *moment* of a crossover, not "I'm currently bullish." Fixed via `BacktestEngine.run()`'s `warm_start` and `initial_trade` parameters, verified in `tests/test_walk_forward.py`.

On the existing synthetic-data run: close to breakeven out-of-sample (+0.12% combined, Sharpe 0.12), selecting between four fast/slow period combinations across 16 windows. Not yet re-run against real sandbox data.

## Suggested next steps, in order

1. **Run `fetch_sandbox_data.py` locally** and upload the resulting `data/sandbox_2026h1.db` - this is the actual next step, see `CONTEXT_HANDOFF.md` Section 9.
2. Run the SMA crossover strategy against all 16 sandbox instruments through `VALIDATION_HIERARCHY.md`'s tiers, cheapest first.
3. Only strategies/parameter sets that clear the full hierarchy get persisted via `RunStore`.
4. Once the sandbox dataset and persistence layer are proven against real data, build the actual Tier 0-4 orchestrator.
5. Only then, and only in a dedicated future session per standing instruction, start on the cross-pair/cartesian rotation comparator.

I'm not a financial advisor and this isn't financial advice - this project is about building a sound, honest testing and execution pipeline, not about telling you what will make money. FX and crypto trading carry real risk of loss, and automation doesn't remove that risk - it just executes your mistakes faster.
