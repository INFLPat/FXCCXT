# FX Trader

Backtestable, cost-aware FX + crypto trading pipeline: historical data ->
strategy -> backtest -> validation hierarchy -> (eventually) live trading,
same strategy code at every stage. UK-based: GBP/USD core, EUR represented.

Current state, verification status, and the standing coding rules
(Holzmann's Power of Ten) live in [`CONTEXT_HANDOFF.md`](CONTEXT_HANDOFF.md)
- read that first.

## Layout

```
fx_trader/
├── VALIDATION_HIERARCHY.md         # Tier 0-4 gate spec
├── CONTEXT_HANDOFF.md              # current state, read first
├── fetch_sandbox_data.py           # real FX (OANDA) + USD-crypto (Binance) puller
├── ingest_kraken_gbp_csv.py        # Kraken bulk CSV loader for GBP-crypto
├── run_validation_hierarchy_real_data.py  # drives all 16 sandbox instruments through Tiers 0-4
├── data/
│   ├── store.py                    # FxStore - candle storage, SQLite or Postgres
│   └── run_store.py                # RunStore - persists validated backtest results
├── brokers/
│   ├── base.py, router.py, oanda.py, ccxt_broker.py
├── strategy/
│   ├── base.py, sma_crossover.py
├── backtest/
│   ├── engine.py                   # event-driven backtest + cost modeling
│   ├── metrics.py                  # return/drawdown/Sharpe/win-rate/profit-factor
│   ├── walk_forward.py             # rolling out-of-sample validation
│   ├── sensitivity.py              # parameter-grid plateau-vs-spike analysis
│   ├── bootstrap.py                # resample/shuffle Monte Carlo
│   └── validation_orchestrator.py  # Tier 0-4 gate, wires the above together
├── tests/                          # one test module per backtest/data/broker component
└── run_*.py                        # single-instrument synthetic-data demos
```

## Running it

```bash
pip install -r requirements.txt
python run_backtest_demo.py            # synthetic FX, SMA crossover, full cost model
python run_crypto_backtest_demo.py     # synthetic crypto, percentage-based costs
python run_walk_forward_demo.py
python run_sensitivity_demo.py
python run_bootstrap_demo.py
python -m tests.test_engine            # ... and the rest of tests/*.py

# Real sandbox data (needs data/sandbox_2025h2.db - see CONTEXT_HANDOFF.md)
python run_validation_hierarchy_real_data.py
```

## Cloud database

`FxStore`/`RunStore` accept any `postgresql://` URL via `DATABASE_URL` /
`RUNS_DATABASE_URL`, falling back to local SQLite if unset. Snowflake
Postgres works with zero code changes - it's a standard libpq connection,
just append `?sslmode=require` (a missing `sslmode` triggers a startup
warning). Verify with `python -m tests.test_store` against your real
instance before trusting the Postgres path - see `CONTEXT_HANDOFF.md`
Section 3 for what's actually been run vs. just written-to-spec.

## Broker/exchange notes

- **OANDA** (FX): clean v20 REST API, free practice account. Two real bugs
  were found and fixed in `fetch_candles` on first live run - see
  `CONTEXT_HANDOFF.md`. `get_quote`/`place_market_order` are still
  completely untested; treat with elevated suspicion for the same reason.
- **ccxt** (crypto): one adapter covers 100+ exchanges. Historical OHLCV has
  no separate bid/ask (bid=ask=trade price) - costs are modeled via
  `CostModel.commission_pct`/`slippage_pct` instead of a historical spread.
  Kraken's *live* OHLC API cannot serve data older than a rolling recent
  window - use `ingest_kraken_gbp_csv.py`'s bulk CSV path for historical
  Kraken data instead.
- **`BrokerRouter`** picks the best live quote across connected adapters.
  For FX majors, live spread differences between brokers are usually
  sub-pip - routing helps more with redundancy/access than price arbitrage.
  For crypto, cross-exchange price gaps are often real and larger -
  `tests/test_router_multi_exchange.py` proves the routing logic works.

## How costs are modeled

- **Spread**: buy at ask, sell at bid, always - drawn from each candle's own
  bid/ask (FX), or absent entirely for crypto (bid=ask, cost lives in
  `commission_pct`/`slippage_pct` instead).
- **Commission**: flat and/or per-unit (FX) or percentage-of-notional
  (crypto), on both entry and exit.
- **Slippage**: configurable pips or percentage, charged against you on
  every fill - deterministic, not stochastic.
- **Equity curve mark-to-market**: while a position is open, the equity
  curve reflects what closing it *right now* would net - same cost logic a
  real close uses. Verified in
  `tests/test_engine.py::test_mark_to_market_matches_realized_when_price_unchanged`.

## Validation tools

- **`walk_forward.py`**: does a SELECTED parameter set hold up on unseen
  data over time? Slides a window through history; scores + selects on
  in-sample, reports only out-of-sample.
- **`sensitivity.py`**: is the parameter SPACE smooth enough to trust the
  selection in the first place? `neighbor_gap()` distinguishes a real
  plateau from an isolated overfit spike.
- **`bootstrap.py`**: how much of a result is the luck of these specific
  trades' order/composition? `resample` gives a confidence interval;
  `shuffle` isolates path/sequencing risk in drawdown.
- **`validation_orchestrator.py`**: wires all three (plus a sanity floor)
  into the ordered Tier 0-4 gate from `VALIDATION_HIERARCHY.md`, persisting
  only genuine survivors via `RunStore`.

Not a lawyer or financial advisor - this project is a testing/execution
pipeline, not investment advice. Automation doesn't remove trading risk.
