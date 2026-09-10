# FX Trader

A backtestable, cost-aware FX trading pipeline. Built to go: historical data → strategy → backtest → paper trading → (eventually, carefully) live trading, using the *same* strategy code at every stage, against a cloud database and a broker-agnostic execution layer.

## Project layout

```
fx_trader/
├── data/
│   └── store.py            # Candle storage - SQLite (local) or Postgres (cloud), same code
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

## What's verified vs. what isn't

I can run Python in a sandbox while building this, but that sandbox has **no network access** - so anything that needs to reach OANDA's servers or a real cloud database is written carefully but genuinely untested by me. Here's the honest breakdown:

| Component | Status |
|---|---|
| Backtest engine, cost math, metrics | ✅ Run + hand-verified against manually computed numbers |
| `FxStore` on SQLite | ✅ Run - round-trip, upsert-updates-not-duplicates, and range filtering all verified |
| `FxStore` on Postgres (cloud) | ⚠️ Same SQL as the verified SQLite path, but never executed against a real Postgres server |
| `OandaBroker.fetch_candles` | ⚠️ Follows documented v20 API, not run against OANDA's servers |
| `OandaBroker.get_quote` / `place_market_order` | ⚠️ Same - written to spec, unverified. Test extensively on a **practice** account before trusting these, and never point at `environment="live"` until they've proven themselves on practice |
| CostModel percentage-based costs (`commission_pct`/`slippage_pct`) | ✅ Run + hand-verified, same rigor as the pip-based costs |
| `CcxtBroker`'s own logic (OHLCV mapping, pagination, quote/order/balance handling) | ✅ Run against a fake exchange object - genuinely verified, no ccxt install or network needed for this |
| `CcxtBroker` against a real exchange | ⚠️ Never run against real ccxt or a live exchange - I don't have the library or network access here. Test on an exchange's sandbox/testnet before trusting it |
| `BrokerRouter` routing logic (best bid/ask selection, graceful failure) | ✅ Run + verified with two independent fake exchanges quoting different prices |
| Walk-forward selection + state/position continuity (`warm_start`, `initial_trade`) | ✅ Run + hand-verified - a position bought in-sample is proven to carry into out-of-sample at its original entry price, not dropped or re-opened |
| Sensitivity analysis (`neighbor_gap`, `heatmap_grid`, `marginal_effect`, invalid-combo handling) | ✅ Run + hand-verified against a constructed grid with a known, deliberate spike, plus an integration test proving a rejected parameter combination is skipped rather than crashing the run |
| Bootstrap resampling (`resample`/`shuffle`, percentile/CI math, order-invariance) | ✅ Run + hand-verified - percentile/CI math checked against known distributions, shuffle-mode order-invariance proven as an explicit assertion, and the Monte Carlo drawdown estimate validated against exhaustive enumeration of all possible orderings of a small trade set |

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
python -m tests.test_ccxt_broker
python -m tests.test_router_multi_exchange
python -m tests.test_walk_forward
python -m tests.test_sensitivity
python -m tests.test_bootstrap
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

**Testing status**: same honesty bar as everything broker/network-facing in this project — I don't have network access in the sandbox I build in, so this has never touched a real Snowflake Postgres instance. What I have verified: the sslmode warning fires correctly on a Postgres URL missing it, and the exact same SQL (schema, upsert, queries) has been fully round-trip tested via SQLite. Run `test_store.py` against your real instance to close that last gap.

**If you ever want Snowflake's actual analytical side too** (large-scale historical research across your own and, later, many users' data) rather than just Postgres-compatible storage, that's `Hybrid Tables`/standard Snowflake tables via the native `snowflake-connector-python` driver — a genuinely different code path from this file, worth treating as a separate addition if/when you want it, not a replacement for what's here.

## Broker/API options

I compared the current landscape - here's where things stand:

- **OANDA** (what's built): clean REST API (`v20`), excellent docs, free practice account, easiest starting point. FX + CFDs only, no stocks.
- **Interactive Brokers**: much wider market access (FX, stocks, futures, options, 150+ markets) and very low costs at scale, but the TWS API has a steeper learning curve and requires running a local gateway process.
- **FXCM**: multiple API flavors (REST, FIX, Java, a dedicated ForexConnect SDK) - good if you want protocol flexibility.
- **IC Markets / other cTrader-based brokers**: worth knowing about for your multi-broker goal specifically - cTrader's Open API is implemented by *multiple* brokers, so one integration can work across several of them rather than needing bespoke code per broker.

**On "choose the best broker automatically":** for FX majors, live spread differences between reputable brokers are usually a fraction of a pip - true price arbitrage isn't where the value is. Where multi-broker genuinely helps is redundancy (broker A's API has an outage, route to broker B) and access (different brokers, different instruments or cost structures for different strategies). `BrokerRouter` is built to support this pattern, but I'd treat it as a later-stage feature - prove out OANDA end-to-end first, then add a second adapter once you're ready to test the router for real.

## Crypto support

`brokers/ccxt_broker.py` adds crypto via [ccxt](https://github.com/ccxt/ccxt), which covers 100+ exchanges through one consistent API - `CcxtBroker(exchange_id="binance")`, `CcxtBroker(exchange_id="kraken")`, etc. Two real differences from OANDA worth understanding, not just implementation details:

- **Historical data has no separate bid/ask.** Crypto exchanges' OHLCV history is a single trade-price series, not bid/ask candles - so historical candles here set bid == ask, and trading costs are modeled entirely through `CostModel`'s new `commission_pct`/`slippage_pct` fields (percentage of notional) rather than a historical spread. This mirrors how most retail crypto backtesting tools work, but it means your backtest's realism depends on setting those percentages to your actual exchange's fee schedule - the defaults are a starting point, not gospel.
- **Costs are percentage-based, not pip-based.** `CostModel` now supports both, additively - FX configs using the pip fields are completely unaffected (`run_backtest_demo.py` produces byte-identical output to before). Run `run_crypto_backtest_demo.py` to see it - the same SMA crossover strategy loses noticeably more on crypto (-15.5% vs -0.5% on FX) purely from percentage fees compounding over more, more volatile trades. That's a real and useful result, not a bug: it's a concrete illustration of why cost modeling matters more, not less, once you're trading something volatile.

**Multi-exchange routing is where `BrokerRouter` earns its keep**, unlike FX: `tests/test_router_multi_exchange.py` proves it with two independently-quoting fake exchanges - it genuinely picks the cheaper ask when buying and the better bid when selling, and skips an exchange that's erroring without crashing. Real cross-exchange crypto price gaps are often bigger than the sub-pip differences between FX brokers, so this is the case where automatic routing has a real edge to capture, not just redundancy value.

To go live (after sandbox testing): `CcxtBroker(exchange_id="binance", sandbox=True)` defaults to the exchange's testnet where ccxt supports one - same safety-first pattern as `OandaBroker`'s `environment="practice"` default.

## Parameter sensitivity analysis

`backtest/sensitivity.py` answers a different question than walk-forward testing: not "does a selected parameter set hold up over time" but "is the parameter space itself smooth enough to trust the selection process at all." It runs a full backtest across every combination in a grid (e.g. every fast/slow SMA period pair) and analyzes the whole surface, not just the winner.

The core diagnostic is `neighbor_gap()`: how much better is a point than its immediate neighbors in parameter space (one grid step away, holding other parameters fixed)? A real, structural edge should produce a *plateau* - the neighborhood performs similarly. An isolated spike - great at exactly one combination, mediocre one step away in any direction - is close to the textbook signature of fitting to this dataset's specific noise. Hand-verified in `tests/test_sensitivity.py` against a constructed grid with a known, deliberate spike.

Run `run_sensitivity_demo.py` to see it on real data - and the real result is a good illustration of exactly why this matters. Across a 6x6 grid of SMA periods (35 valid combinations), only **one** was profitable at all: `fast=20, slow=60` at +0.085%. If you'd only run that single combination as a normal backtest, it would look like a working strategy. But `neighbor_gap` flags it immediately - its neighbors average roughly -0.24% below it, a gap wider than the entire grid's standard deviation. That's not a plateau, it's a spike, and the honest read is "this one combination got lucky on this dataset," not "this strategy works at 20/60." Two other decent-looking points (15/60, 20/50) *do* read as plateaus - worth more consideration, though still solidly unprofitable after costs.

Also included: `marginal_effect(param_name)`, which averages out every other parameter to show one parameter's effect on its own - works for the 2D heatmap case above, but also for any number of varying parameters where a full grid isn't practical to look at. On this same run, it surfaces a real, mild trend: larger `slow_period` values consistently did less badly (-0.55% at 20, improving to -0.27% at 60) - directionally interesting even though nothing in this grid actually turned a profit.

## Bootstrap resampling

`backtest/bootstrap.py` answers a third, different question from the other two tools: not "does this generalize over time" (walk-forward) or "is the parameter space smooth" (sensitivity), but **how much of this one reported number is just the luck of how these specific trades happened to land**. It operates directly on a real backtest's completed trades - `BacktestResult.trades` - via two complementary, standard methods:

- **`method="resample"`** (bootstrap with replacement): draws a fresh sample of trades, with repeats allowed, thousands of times - answering "if this same process generated a similar batch of trades, what range of outcomes would be plausible." Produces a genuine confidence interval around total return, not just the single point estimate every other tool in this project reports.
- **`method="shuffle"`** (permutation, no replacement): keeps the exact same trades but reorders them. Total return is a simple sum and is mathematically **order-invariant** - shuffling can't change it - but max drawdown absolutely is order-dependent, since the same wins and losses can arrive in a much friendlier or harsher sequence. This isolates pure path/sequencing risk from trade quality. `tests/test_bootstrap.py` proves the order-invariance as an explicit mathematical assertion, not just an assumption, and separately validates the Monte Carlo drawdown estimate against **exhaustive enumeration** of all 120 possible orderings of a small 5-trade set - the empirical min/max matched the true enumerated min/max exactly, and the mean matched to within 0.01 percentage points.

Run `run_bootstrap_demo.py` to see both on real data. The result is a good demonstration of why this matters: the backtest's single reported number (-0.51% return) sits inside a 90% resample confidence interval of roughly [-1.04%, +0.03%] - consistently negative, not just unluckily so once. More interesting: shuffle mode shows the *actual* historical drawdown (0.72%) sits at the **81st percentile** of all possible reorderings of those same trades - most alternate sequences of the identical wins and losses would have produced a smaller drawdown. The real historical path wasn't just unprofitable, it was also a somewhat unlucky ordering of an already-unprofitable set of trades - a distinction only this kind of analysis can draw.

## How the broker abstraction works

Every broker implements `BrokerAdapter` (`brokers/base.py`): `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`. Nothing else in the codebase - strategies, the backtest engine, `BrokerRouter` - needs to know which broker it's talking to. Adding Interactive Brokers or FXCM later means writing one new adapter file; everything downstream keeps working unchanged.

`BrokerRouter` holds a list of connected adapters and, for a given trade, queries live quotes from all of them and routes to whichever offers the better price - falling back gracefully (skipping, not crashing) if one broker's API call fails.

## How costs are modeled in the backtest

- **Spread**: candles store bid and ask separately; you buy at the ask, sell at the bid, always. Pulled from actual historical spread rather than a fixed assumption.
- **Commission**: per-unit and/or flat fee, on both entry and exit.
- **Slippage**: configurable pips charged against you on every fill - a simple deterministic model for now; tighten it up once you have real fill data from paper trading.
- **Equity curve mark-to-market**: while a position is open, the equity curve reflects what closing it *right now* would actually net you - same slippage and commission logic as a real close, not a friction-free price difference. This matters for `max_drawdown_pct` and the Sharpe ratio specifically, both computed from the full equity curve; `total_return_pct` (based only on starting/ending balance) was never affected either way, since that only depends on trades that actually closed. Verified in `tests/test_engine.py::test_mark_to_market_matches_realized_when_price_unchanged` - with price held flat between an open position and its later close, the "if I closed now" figure while open now matches the realized figure after close exactly, with no artificial jump at the close instant.

## Honest note on the SMA crossover example

It's there to prove the pipeline works end-to-end, not because it's a good strategy. On synthetic random-walk data with realistic costs, it loses a small amount of money, which is the expected, honest result for a simple crossover on noise. If you tune parameters until backtest returns look good, you're very likely fitting to historical noise rather than finding a real edge - always validate on out-of-sample data before trusting a result, which is exactly what the next section does.

## Walk-forward (out-of-sample) testing

`backtest/walk_forward.py` is the direct answer to the paragraph above. Instead of picking one set of strategy parameters and running the whole history once, it slides a window through time: on each in-sample slice, it grid-searches `param_grid` and keeps whichever parameters scored best, then runs *only* those parameters, completely unchanged, on the following out-of-sample slice - data they never influenced. Every reported metric comes from the out-of-sample side. Run `run_walk_forward_demo.py` to see it end to end.

**A real, fixed bug worth knowing about, not just a design note**: while building this I found that a position still open at a window boundary would silently vanish - `SmaCrossoverStrategy` only signals BUY/SELL on the *moment* of a crossover, not "I'm currently bullish," so if a trend was still running when in-sample ended, the strategy wouldn't re-signal to reopen it out-of-sample unless a fresh crossover happened to fire right there. That would have understated exactly the scenario walk-forward testing exists to check. Fixed via `BacktestEngine.run()`'s new `warm_start` (carries strategy state across a run() boundary) and `initial_trade` (carries a still-open position across the same boundary) parameters - both opt-in, both fully backward compatible, verified in `tests/test_walk_forward.py` with a hand-computed scenario: a position bought in-sample must carry into out-of-sample at its original entry price and close at the correct out-of-sample price, not get dropped or re-opened.

**On the actual result**: run the demo against synthetic FX data and the honest picture is close to breakeven out-of-sample (+0.12% combined, Sharpe 0.12 - not meaningfully different from zero), selecting between four fast/slow period combinations across 16 windows. Worth comparing against the earlier single fixed-parameter backtest (-0.51%, using only 10/30 the whole time) - walk-forward's re-selection did somewhat better here, and one parameter pair (20/60, the slowest/most conservative option in the grid) got chosen far more often than the others across windows. Neither of those is a claim that this strategy is good; it's the difference between an honest test and a single lucky (or unlucky) backtest run.

## Suggested next steps, in order

1. **Set up Neon** and confirm `test_store.py` passes against it.
2. **Set up an OANDA practice account** and confirm `OandaBroker.fetch_candles` actually works against real servers - this is the first genuinely untested piece to validate.
3. **Pull real historical data** for a couple of major pairs, store it in your cloud DB, re-run the backtest against real data instead of synthetic.
4. **Test `get_quote` and `place_market_order`** against the practice account thoroughly before trusting them.
5. **Paper trade for a meaningful stretch** (weeks, not days) before considering real money.
6. Only then, consider a second broker adapter and start actually using `BrokerRouter` for something real.

I'm not a financial advisor and this isn't financial advice - this project is about building a sound, honest testing and execution pipeline, not about telling you what will make money. FX trading carries real risk of loss, and automation doesn't remove that risk - it just executes your mistakes faster.
