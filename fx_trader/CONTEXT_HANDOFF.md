# CONTEXT HANDOFF - FX/Crypto Trading Analysis Project

Read this before touching the code. Trimmed to current state only - prior
sessions' blow-by-blow narrative has been cut; only decision-relevant facts
kept. GitHub sync is pull-only - see root `README.md`.

**Jurisdiction**: UK-registered entity, UK law (FCA, financial promotions,
UK GDPR). Core currencies: GBP (primary), USD, EUR represented.

---

## 1. OBJECTIVE

Python system analysing historical/live FX + crypto price data to find the
optimum moment to trade, via multiple complementary analytical processes
(not one signal). Near-term: a thoroughly-tested single-user pipeline.
Long-term: multi-user subscription product, UK compliance as a first-order
constraint throughout.

## 2. KEY DECISIONS (settled - re-open only with a genuinely new reason)

- **Python throughout**, not Rust - bottleneck is network I/O, not CPU.
- **SQLite (local/test) + raw psycopg2 (cloud Postgres)**, not SQLAlchemy.
  Snowflake Postgres is the chosen cloud DB - plain `postgresql://` URL,
  zero code changes needed.
- **The frozen sandbox dataset stays out of any cloud DB, forever** - static
  reference data, no need for cloud infra.
- **`BrokerAdapter` interface** - every broker/exchange implements
  `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`.
  OANDA (FX) + ccxt (crypto, currently Binance/Kraken).
- **`CostModel`** supports pip-based (FX) and percentage-based (crypto)
  costs, additively.
- **Validation hierarchy** (`VALIDATION_HIERARCHY.md`) is an explicit,
  ordered, cost-tiered gate on what gets persisted - built and run this
  session, see Section 4.
- **Holzmann's "Power of Ten" coding rules apply to all code in this
  project (and are a standing rule across all chats, not project-specific):**
  linear control flow (max 2 nesting levels); every loop has an explicit
  ceiling; close every resource, including on error paths; one function
  does one job, fits on one screen; >=2 assertions per function; never
  swallow an error (no bare `except: pass`); zero warnings tolerated.
  Applied repo-wide this session - see Section 5.

## 3. VERIFIED VS. NOT

Build sandbox has no network access - anything needing OANDA/an exchange/a
real cloud DB is written carefully but untested by Claude directly; the
person re-runs and reports back.

| Component | Status |
|---|---|
| Backtest engine, cost math, metrics, walk-forward, sensitivity, bootstrap | Run + hand-verified against manually computed numbers |
| `FxStore` / `RunStore` on SQLite | Run - round-trip, upsert-not-duplicate, range filtering verified |
| `validation_orchestrator.py` (Tier 0-4) | Run against all 16 real sandbox instruments this session - see Section 4 |
| `OandaBroker.fetch_candles` | Run against a real practice account - 2 bugs found and fixed (timestamp format needs literal `Z`, not `+00:00`; `count` must not be sent alongside both `from`+`to`) |
| `CcxtBroker.fetch_candles` vs Binance | Run - exact expected candle counts, real prices |
| `CcxtBroker.fetch_candles` vs Kraken (live API) | Run - confirmed real limitation: only serves a rolling recent window, not arbitrary history. Worked around via `ingest_kraken_gbp_csv.py` (Kraken's quarterly bulk CSV export) |
| `FxStore` / `RunStore` on Postgres/Snowflake Postgres | Never connected to a real server |
| `OandaBroker.get_quote` / `place_market_order` | **Never run - elevated suspicion**: `fetch_candles` looked equally reasonable before running and had 2 real bugs |
| `CcxtBroker` against a real exchange (not fake) | Never run |

## 4. CURRENT STATE

**Sandbox dataset**: `data/sandbox_2025h2.db`, 2025-07-01 to 2025-12-31,
16/16 instruments confirmed real and cross-validated (GBP-crypto checked
via cross-rate arithmetic against independently-sourced USD-crypto + FX
data - three independent sources agreeing). 8 FX (OANDA, 3,141 candles
each), 4 USD-crypto (Binance, 4,416 each), 4 GBP-crypto (Kraken CSV,
4,409-4,410 each - Kraken only records hours with an actual trade).

**Validation hierarchy - built and run this session**
(`backtest/validation_orchestrator.py`, driven by
`run_validation_hierarchy_real_data.py`): `SmaCrossoverStrategy` across a
36-point fast/slow SMA grid, run through Tiers 0-4 for all 16 real
instruments. Position sizing: ~10% of starting balance notional per trade,
consistent across instruments (a sizing choice made explicit in the script
- wasn't specified anywhere in the earlier single-instrument demos).

**Result: 0/16 instruments produced a Tier 4 (persisted) survivor.**
- 8 instruments (EUR_GBP, GBP_JPY, GBP_CHF, USD_CHF, BTC/USDT, LTC/USDT,
  BTC/GBP, LTC/GBP) had **zero profitable parameter combinations at all**
  across the full 36-point grid after realistic costs.
- 8 instruments had some Tier 1-3 survivors (up to 30/36 for USD_JPY), but
  every finalist failed Tier 4's bootstrap gate - the 90% CI for total
  return didn't clear zero, or loss probability was too high. In plain
  terms: whatever apparent edge existed in-sample wasn't statistically
  distinguishable from luck once resampled.
- This is consistent with the codebase's standing honest note that SMA
  crossover isn't presented as a profitable strategy - it now holds on real
  data across all three asset/currency groupings, not just synthetic data.
- **Implication for next steps**: don't tune SMA crossover's parameters
  further looking for a winner among this grid - the hierarchy exists
  precisely to stop that. A different strategy family, not a different
  parameter, is the next lever.

## 5. HOLZMANN REVIEW (this session)

Applied to the full repo. Genuine findings, fixed:
- **3+ levels of control-flow nesting** in `OandaBroker.fetch_candles`,
  `CcxtBroker.fetch_candles`, `run_walk_forward`, `sensitivity.py::neighbors`,
  `BacktestEngine.run`, `BootstrapResult.summary`, and
  `ingest_kraken_gbp_csv.py::main` - all refactored into smaller, named
  helper functions, each with >=2 assertions. Re-verified against the full
  test suite after each change (all pass).
- **One `except Exception: pass`** in `test_store.py` - changed to log the
  exception rather than silently discard it.
- One narrower fix: `ingest_kraken_gbp_csv.py`'s CSV-parse exception
  handler now catches `(OSError, ValueError, IndexError)` specifically
  instead of bare `Exception`.
- No genuine unclosed-resource issues found - `FxStore`/`RunStore` already
  use context managers throughout.
- `except Exception as exc:` blocks that log or structurally record the
  exception (`brokers/router.py`, `fetch_sandbox_data.py`,
  `sensitivity.py`'s invalid-combo handling) were judged compliant with the
  no-swallowing rule as written - the exception is captured and surfaced,
  never silently dropped - and left as-is.
- **Not mechanically enforced**: flat `if/elif/else` chains that an AST
  depth-counter flags as "3 levels" (because Python represents `elif` as a
  nested `If` in the previous branch's `orelse`) were left alone where the
  real, human-readable nesting is 2 levels or fewer - e.g.
  `SmaCrossoverStrategy.on_candle`, `SensitivityResult.summary`. Flattening
  these further would add indirection without reducing real complexity.
- **Note on applicability**: several Holzmann rules were written for
  safety-critical embedded C (the original ten also include "no dynamic
  memory allocation after init" and "no recursion" - not requested here,
  and not meaningfully applicable to Python). The 7 rules requested
  translate reasonably to this codebase; trivial one-line property getters
  and dataclasses were not padded with assertions for the sake of a count -
  doing so would itself be noise, not safety.

## 6. OPEN THREADS

- **Validation hierarchy result above** is the most important open item:
  SMA crossover doesn't clear the bar on real data. Next strategy idea
  needed, not more SMA tuning.
- **Cross-pair/cartesian comparator + statistical pairs trading** -
  deferred to its own future session. Reasoned (not yet validated)
  expectation: needs finer-than-hourly granularity, since mean-reversion
  signals decay faster than trend signals. Two things to have ready first:
  multi-instrument timestamp/candle-boundary alignment across OANDA/
  Binance/Kraken's differing conventions, and Kraken's live-API depth limit
  will likely resurface at finer granularity too.
- **Tiered subscription refresh rates** (~5min/~30sec-1min/~1sec) - not
  designed. Caveat: refresh rate (infra) and signal granularity (strategy
  design) are different axes - faster polling alone doesn't change a
  strategy's actual trades unless the strategy itself is redesigned for
  that granularity, and live granularity must match what was validated.
- `OandaBroker.get_quote` / `place_market_order` - still never run, test
  explicitly before trusting either.
- `ccxt_broker.py` volume truncation (`int(vol)`) - low-priority, pre-existing
  precision loss on Binance rows (harmless there; Kraken rows use the true
  float via `ingest_kraken_gbp_csv.py` instead).
- Position sizing, multi-user entitlements, portfolio aggregation, live
  feed, deployment infra - all still open, unchanged from earlier sessions.

## 7. IMMEDIATE NEXT STEP

1. Decide the next strategy family to test through the hierarchy now that
   SMA crossover is a confirmed dead end on real data (mean-reversion?
   breakout? something using the cross-instrument data already in the
   sandbox?).
2. Test `OandaBroker.get_quote`/`place_market_order` before trusting them.
3. Only after that, in a dedicated session, start the cross-pair/pairs-
   trading work.

## 8. RISKS

Two real bugs found and fixed in `OandaBroker.fetch_candles` (Section 3) -
concrete evidence that "written to spec" isn't "correct". Apply the same
suspicion to anything still unverified in Section 3, especially
`get_quote`/`place_market_order`. Not a lawyer or financial advisor - this
project is about a sound, honest testing/execution pipeline, not investment
advice. Automation doesn't remove trading risk, it executes decisions faster.
