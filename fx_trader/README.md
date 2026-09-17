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
├── VALIDATION_HIERARCHY.md         # Tier 0-4 gate spec + extended-metrics/service-tier notes
├── CONFIDENCE_SIZING_DESIGN.md     # Phase 1-4 spec: multi-strategy confidence scoring & position sizing (design only, not built)
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
│   ├── indicators.py                # EMA/RSI/MACD - batch reference + RunningSmoothedAverage (streaming primitive)
│   ├── rsi_strategy.py              # RsiStrategy - Wilder RSI oversold/overbought reversal
│   ├── macd_strategy.py             # MacdStrategy - MACD crossover, optional RSI confirmation filter
│   ├── rsi_macd_confluence.py       # RsiMacdConfluenceStrategy - RSI and MACD fire independently, combined on agreement
│   └── bollinger_strategy.py        # BollingerBandsStrategy - band reversal mean-reversion
├── backtest/
│   ├── engine.py                   # event-driven backtest + cost modeling
│   ├── metrics.py                  # return/drawdown/Sharpe + extended risk-adjusted & tail metrics
│   ├── rolling.py                  # rolling Sharpe/Sortino/drawdown (Tier 3 finalists only)
│   ├── portfolio.py                # cross-instrument correlation & diversification (standalone, not gated)
│   ├── service_tiers.py            # bronze/silver/gold metric visibility - separate axis from cost-tier
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
python run_rsi_demo.py                 # synthetic FX, RSI oversold/overbought reversal
python run_macd_demo.py                # synthetic FX, MACD crossover - with vs without RSI filter
python run_confluence_demo.py          # synthetic FX, RSI+MACD independent confluence across window sizes
python run_bollinger_demo.py           # synthetic FX, Bollinger Band mean-reversion
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

## Strategies

- **`SmaCrossoverStrategy`**: simple moving-average crossover. Confirmed
  NOT to clear Tier 4 on real 2025 H2 sandbox data across all 16
  instruments - see `CONTEXT_HANDOFF.md`.
- **`RsiStrategy`**: Wilder RSI mean-reversion. Enters on a REVERSAL out of
  an oversold/overbought extreme (RSI crossing back through the threshold),
  not on "RSI is currently below 30" - avoids buying into a still-falling
  move.
- **`MacdStrategy`**: MACD line/signal-line crossover (trend-following,
  same shape as SMA crossover but with exponential rather than simple
  averages), with an optional RSI overextension filter on by default
  (`require_rsi_confirmation=True`) - skips a crossover signal if RSI
  already agrees the move is extended. Toggle the flag to test through the
  validation hierarchy whether the filter actually earns its keep on real
  data, rather than assuming a textbook combination works.
- **`RsiMacdConfluenceStrategy`**: a genuinely different combination from
  `MacdStrategy`'s filter - RSI and MACD each run as complete, independent
  signal generators (literally composing standalone `RsiStrategy`/
  `MacdStrategy` instances), and only agree-and-fire within
  `confirmation_window` candles of each other. Neither is "primary". At
  `confirmation_window=0` (exact same candle) this is extremely
  restrictive by construction - see `run_confluence_demo.py`'s printed
  trade-count-vs-window comparison for how fast that loosens up.
- **`BollingerBandsStrategy`**: mean-reversion on a band reversal - long
  when price closes back ABOVE the lower band after being below it, short/
  flat on the mirror case at the upper band. Same "wait for the reversal,
  don't catch a falling knife" philosophy as `RsiStrategy`, but using
  volatility-adjusted bands (SMA ± `num_std` population std dev, period
  20 by default) instead of RSI's fixed 30/70 thresholds. Worth knowing:
  the current candle is included in its own band's window (the standard
  definition), so a single sharp move partly widens the band around
  itself rather than always poking cleanly outside it - see the module's
  own docstring before assuming a short `period` behaves like a fixed
  threshold would.

All three share the same `Strategy` interface, so the same backtest engine,
metrics, and validation hierarchy apply unchanged - see
`VALIDATION_HIERARCHY.md`.

How these five strategies should work TOGETHER - continuous confidence
scoring, weighted by validation-hierarchy results, driving continuous
position sizing across instruments - is fully designed but not yet built.
See [`CONFIDENCE_SIZING_DESIGN.md`](CONFIDENCE_SIZING_DESIGN.md).

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

## Performance & risk metrics

`backtest/metrics.py`'s `compute_metrics()` returns a `Metrics` object with
~30 fields per backtest - return/drawdown/Sharpe plus Sortino, Calmar,
Omega, drawdown duration, Ulcer Index, trade expectancy, payoff ratio,
win/loss streaks, historical VaR/CVaR, tail ratio, skewness/kurtosis, Kelly
fraction (informational only), exposure %, cost drag %, and an optional
buy & hold benchmark/alpha. All are free relative to the backtest that
already ran - see `VALIDATION_HIERARCHY.md` for the full list, what's
deliberately excluded and why, and which validation tier each sits at.

Two things NOT in `Metrics` because they're a genuinely different cost/scope
shape - each has its own module and its own docstring explaining why:
- `backtest/rolling.py` - rolling-window Sharpe/Sortino/drawdown, restricted
  to Tier 3 finalists in the validation hierarchy, never the full grid.
- `backtest/portfolio.py` - cross-instrument correlation matrix and
  equal-weight portfolio diagnostics, kept outside the per-instrument gate
  entirely since it needs multiple instruments' aligned data together.

**Subscription tiers**: `backtest/service_tiers.py` maps each metric to a
minimum bronze/silver/gold tier, independent of how cheap/expensive it was
to compute - `Metrics.for_service_tier(tier)` filters accordingly. This is
a starting allocation pending a dedicated product-design conversation, not
a final decision - see `VALIDATION_HIERARCHY.md`.

## Validation tools

- **`walk_forward.py`**: does a SELECTED parameter set hold up on unseen
  data over time? Slides a window through history; scores + selects on
  in-sample, reports only out-of-sample.
- **`sensitivity.py`**: is the parameter SPACE smooth enough to trust the
  selection in the first place? `neighbor_gap()` distinguishes a real
  plateau from an isolated overfit spike.
- **`bootstrap.py`**: how much of a result is the luck of these specific
  trades' order/composition? `resample` gives a confidence interval (now
  across ~20 metrics, not just return/drawdown); `shuffle` isolates path/
  sequencing risk in drawdown and other order-dependent metrics.
- **`validation_orchestrator.py`**: wires all of the above (plus a sanity
  floor, plus rolling diagnostics for finalists) into the ordered Tier 0-4
  gate from `VALIDATION_HIERARCHY.md`, persisting only genuine survivors
  via `RunStore`.

Not a lawyer or financial advisor - this project is a testing/execution
pipeline, not investment advice. Automation doesn't remove trading risk.
