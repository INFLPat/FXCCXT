<!-- version: 261001 -->
# CONTEXT HANDOFF - FX/Crypto Trading Analysis Project

Read before touching code. Conventions (FNORD, handoff, de-bloat) are in
WORKING CONVENTIONS below.

**Jurisdiction**: UK-registered entity, UK law (FCA, financial promotions,
UK GDPR). Core currencies: GBP (primary), USD, EUR.

## WORKING CONVENTIONS (binding in every chat; mirrored in Claude memory, repo wins on conflict)

- **FNORD**: every reply ends with the literal word `FNORD` on its own line if it fully answers the person's most recent message, else a short status line - this is per-reply completion, not whole-project completion. Length cutoffs can truncate it: absence isn't proof of failure, presence is proof of completion.

- **Ask, don't infer, when the answer is cheap for you to give and expensive for me to guess** (e.g. "has X been run yet?", "can you attach the current file?"). Default to a direct factual question over reasoning about probabilities.
- **"I don't know" is always preferred over confident-sounding reconstruction.** Flag a gap or an assumption explicitly rather than presenting inferred/remembered work as verified fact.
- **Ask for help on costly tasks.** You keep a full record of every chat and file version - a real ground-truth source I should draw on directly (an attached current file, pasted terminal output, chat history) rather than reconstructing or inferring the same thing myself. This is a partnership: your environment and records are a resource, not something to work around.
- **"Proved by running it" and "read the code" are different confidence levels - label which one applies, always.** A code change must be verified by actual execution wherever technically possible in this sandbox (synthetic data, reconstructed skeletons, unit-level checks) before being called confirmed; reasoning about magnitudes or textual equality alone is not enough. Where execution genuinely isn't possible here (live network, the real sandbox database, your machine), say so explicitly rather than presenting it with the same confidence as something actually run. Nothing ships as "verified" without one or the other being true and stated.
- **Repo is master.** A fresh chat with no memory must be able to continue from repo files alone. Memory mirrors the repo; update it as durable changes happen; on disagreement, fix memory.
- **Session handoff**: list changed files by full repo path; deliver as `patch_YYMMDD_chatNN.json` applied by double-clicking `fx_trader/utilities/apply_patch.command` in Finder (never a raw terminal command) against a `patch_YYMMDD_chatNN.json` payload (`create` / `patch` / `delete`; runs tests; commits; push is manual). Always share the terminal output after running it. Confirm the Context panel was synced at session start before pushing; push only changed files.
- **Check in before heavy tasks** (scripts, file generation, images): why, and the lighter alternatives. Proceed only if none is reasonable.
- **Correct, don't accumulate**: when evidence contradicts a doc, fix it in place. Keep worked examples only while informative.
- **One canonical script/module** over near-duplicates, unless real functionality differs. Shared constants live in `instrument_config.py` / `sandbox_config.py`.
- **File versioning (from 260929)**: every file created or edited carries a `YYMMDD` version stamp; a file with no stamp is legacy (pre-260929) and is stamped when next touched. By type: (a) importable `.py` modules, `run_*.py` and tests keep a stable name plus a first-line `# version: YYMMDD` comment, bumped on each edit (`.gitignore` and other `#`-comment config files follow the same rule, stamp on line 1); (b) cross-referenced docs (`CONTEXT_HANDOFF.md`, `ROADMAP.md`, `VALIDATION_HIERARCHY.md`, `CONFIDENCE_SIZING_DESIGN.md`) keep a stable name plus a first-line `<!-- version: YYMMDD -->`; READMEs keep the name and carry the version line directly under their marker line; (c) generated or transient files (patch payloads, run logs, charts, DB snapshots, exports, one-off snapshots) use a dated filename `<name>_YYMMDD.<ext>`. Why (a)/(b) are not renamed: a dated name breaks imports and GitHub's README landing page and forces every cross-reference to be re-patched on each edit (found 260929).
- **Settled 260930 (chat 11) - do not re-litigate**: (1) *Stamp placement*: `.py` files carry `# version: YYMMDD` on line 1, above the module docstring, except a file starting with a shebang (`apply_patch.py`), where it is line 2; READMEs: marker line, then `<!-- version: YYMMDD -->` directly under it, then the title; other docs: version comment on line 1. (2) *Stamp scope*: only files actually edited in a session are stamped, with that session's date; inspected-but-unchanged files (including rework-exposed modules) are left as they are. (3) *Payloads* are named `patch_YYMMDD_chatNN.json` (chat number added in chat 12 to prevent same-day collisions, e.g. `patch_260930_chat12.json`; applies to patch payload names only, no other file); zips are retired. (4) *Historical copies* attached to a chat are named `<name>_YYMMDD.<ext>` (date of that version; if unstamped, date of its last known change) and attached only when the chat cannot proceed without them; a pasted `git ls-files` is preferred to attaching files just to confirm layout. (5) *Sandbox references*: docs and scripts say "the sandbox"; its filename and window come from `sandbox_config.py` and are named only there, plus dated historical facts (e.g. the 2025 H2 training window). (6) *Models*: every chat runs on Sonnet; ROADMAP S/M/L sizes are provisional. (7) Decisions likely to recur are written into this section in the session they are made. (8) *Chat 12*: patch payload names carry the chat number (see (3)); `.gitignore` and other `#`-comment config files are stamped `# version: YYMMDD` on line 1. (9) *Chat 12*: stored market-data timestamps stay UTC, converted to London time for display only; user's Python is 3.14.7 (see Section 3).
- **End-of-chat handoff**: every session ends with (1) changed files by full repo path, (2) a drafted opening message for the next one-topic chat, (3) the exact list of files to attach to it (a long list means the scope is too big; split it), (4) what to apply, push and sync.
- **One topic per chat**, finished inside one 24-hour window; every chat runs on Sonnet (free tier; context limits rule out Haiku; Opus/Fable unavailable), so no per-chat model is recorded. Chat plan: `ROADMAP.md` Section 4.
- **Run monitor** (built chat 12): `fx_trader/run_monitor.py` - one module, JSON Lines log `fx_trader/logs/run_log_YYMMDD.jsonl` (London date of run start; gitignored, local only, never the cloud DB). Records UTC and Europe/London times, duration, CPU, peak memory, git commit, notes/counts/per-item timings; start/notes/items are streamed so a killed run keeps its data. Log-write failure never crashes a run: queue, retry, fallback file + stderr, `python run_monitor.py recover`. `python run_monitor.py summary <log>` prints runs. API, record schema, recovery: its docstring. **Every new long-running script adopts it from creation; unadopted scripts and the chat that adopts each are listed in `ROADMAP.md` Section 4 (Run-monitor adoption). Adopted so far: `run_full_sweep.py`.**
- **Run scripts take CLI args, not edited constants (settled 261001, chat 13)**: windows, chat number and similar per-run inputs are `argparse` args, recorded in the run_monitor start record (no file edit per run, so `git_dirty` stays false). New `run_*.py` follow this; existing ones convert when next touched. Generated per-run outputs are named `<name>_<window>_chat<N>_<YYMMDD run date, London>[_<tag>]`, e.g. `data/sweep_runs_250701to251231_chat13_261001.db`; scripts refuse to reuse an existing output DB. First adopter: `run_full_sweep.py`.
- **Loops self-protect**: every module's own loops carry an explicit ceiling; never rely on a caller.
- **"De-bloat the text and code of the project"** = run in order: (1) docs: fold resolved sub-task/discussion docs into this file as decision + evidence, keep settled decisions and open items, trim verbosity; (2) code: Power-of-Ten audit, structural redundancy pass, test docstrings state method only (rationale lives in the source file); (3) Claude-side: reconcile memory, Project Instructions and account preferences against the repo; (4) open resolutions (e.g. duplicate sweep scripts). Ask questions first. Acceptance test for every edit: would a chat seeing only the edited file lose any decision, requirement, open question or verified number? Compare old vs new like-for-like, and execute where possible (behaviour-preservation), before committing. Renumbering a section requires patching every reference to it. Keep patch "old" strings short and single-line where possible - a long multi-line span can silently fail to match if the real file wraps differently than assumed; verify against the actual current text before shipping, and fix a reported skip by shortening the anchor, not lengthening it.

## 1. OBJECTIVE

Python system analysing historical/live FX + crypto price data to find
optimum trade moments via multiple complementary strategies (not one
signal). Near-term: thoroughly-tested single-user pipeline. Long-term:
multi-user subscription product, UK compliance as a first-order
constraint throughout.

## 1a. MVP DEFINITION AND DECISIONS SETTLED 260929

- **MVP** = everything needed to trade live, single user, small real fund (about GBP 100-200): validated survivors, cartesian pair work, additional signal generators explored, shadow scoring plus sizing, user risk setting (pulled forward from old Phase 3), minimal dashboard, investor documentation. Full definition, success criteria (operational, not return targets) and the live-trading checklist L1-L16: `ROADMAP.md` Section 1.
- **The sandbox is final at its current window** (Kraken 26Q2 unpublished). Holdout policy inside it: chat 17.
- **Cartesian currency-pair comparison** (relative-strength ranking, spread mean-reversion, possibly more) is believed to be the core edge. Front-loaded: it starts right after the quick baseline work (chats 11-17), and nothing it would force redoing is built first. Its definition and architecture (chats 18-19) come BEFORE the scoring engine, because `Strategy.on_candle` cannot express cross-instrument signals.
- **Chat plan and attachment lists (all chats run on Sonnet):** `ROADMAP.md` Section 4.

## 2. KEY DECISIONS (settled - reopen only with a genuinely new reason)

- **Python throughout** - bottleneck is network I/O, not CPU.
- **SQLite (local/test) + raw psycopg2 (cloud Postgres)**, not SQLAlchemy.
  Snowflake Postgres is the chosen cloud DB - plain `postgresql://` URL.
- **Frozen sandbox dataset stays out of any cloud DB, forever.**
- **`BrokerAdapter` interface**: every broker/exchange implements
  `fetch_candles`, `get_quote`, `place_market_order`, `get_account_balance`.
  OANDA (FX) + ccxt (crypto, Binance/Kraken).
- **`CostModel`** supports pip-based (FX) and percentage-based (crypto)
  costs additively.
- **Validation hierarchy** (`VALIDATION_HIERARCHY.md`) is an explicit,
  ordered, cost-tiered gate on what gets persisted.
- **Cost-tier vs. service-tier are separate axes**, kept in separate files
  (`VALIDATION_HIERARCHY.md` vs `backtest/service_tiers.py`).
- **Holzmann's "Power of Ten" coding rules apply project-wide** (standing
  rule across all chats): linear control flow (max 2 nesting levels);
  every loop has an explicit ceiling; close every resource, including on
  error paths; one function does one job, fits on one screen; >=2
  assertions per function; never swallow an error (no bare `except: pass`);
  zero warnings tolerated.

## 3. VERIFIED VS. NOT

**User's environment (stated chat 12; do not re-ask):** Python 3.14.7 on macOS, `python3` only - give every command to the user as `python3`, never `python` (chat 13). Verified chat 13: the full test suite and a full 80-run sweep ran on 3.14.7 (stdlib/sqlite paths; psycopg2/ccxt/requests/matplotlib not exercised). Claude's build sandbox runs Python 3.12.3, so anything version-sensitive is unverified on 3.14. Not known: whether `psycopg2-binary`, `ccxt`, `requests` and `matplotlib` have working 3.14 builds (run_monitor.py uses stdlib only).

Build sandbox has no network access - anything needing OANDA/an exchange/a
real cloud DB is written carefully but unrun by Claude directly; the
person re-runs and reports back.

| Component | Status |
|---|---|
| Backtest engine, cost math, metrics, walk-forward, sensitivity, bootstrap | Hand-verified. `Trade`/`BacktestResult` support weighted-average-entry partial fills (`Fill` dataclass, `BacktestEngine.rescale_trade()`); full regression suite + entire 80-run real-data sweep re-verified byte-identical after that redesign. `rescale_trade()` unit-tested but not wired into any live/signal path yet. |
| Extended metrics, `rolling.py`, `portfolio.py`, extended `bootstrap.py`, `service_tiers.py`, `indicators.py`, `RunningSmoothedAverage` | Hand-verified against synthetic data and independent reference implementations. |
| `RsiStrategy` / `MacdStrategy` / `RsiMacdConfluenceStrategy` / `BollingerBandsStrategy` / `SmaCrossoverStrategy` | Hand-verified vs. synthetic data; all 5 run against all 16 real sandbox instruments through Tier 0-4 - see Section 4d. |
| `validation_orchestrator.py` Tier 0-4 gating | Exercised across 80 real (strategy, instrument) combinations, 186.6s total. |
| `FxStore` / `RunStore` on SQLite | Round-trip, upsert-not-duplicate, range filtering verified. |
| `FxStore` / `RunStore` on Postgres/Snowflake Postgres | Never connected to a real server. |
| `OandaBroker.fetch_candles` | Run against a real practice account - 2 bugs found and fixed (timestamp needs literal `Z`, not `+00:00`; `count` must not be sent alongside both `from`+`to`). |
| `CcxtBroker.fetch_candles` vs Binance | Run - exact candle counts, real prices confirmed. |
| `CcxtBroker.fetch_candles` vs Kraken (live API) | Confirmed real limitation: only serves a rolling recent window. Worked around via `ingest_kraken_gbp_csv.py`'s bulk CSV path. |
| `run_monitor.py` | Unit-tested in Claude's Linux sandbox (success/failure/interrupt/killed-run/write-failure/recovery paths). Run on the user's machine in two real full sweeps (chat 13, both ok; start/notes/items/end records and meta as expected); macOS peak-memory units and real disk-full not verified. |
| `OandaBroker.get_quote` / `place_market_order` | **Never run - elevated suspicion.** |
| `CcxtBroker` against a real (non-fake) exchange | Never run. |

## 4. CURRENT STATE

**Sandbox dataset**: window 22Q1-26Q1 (filename derived by `sandbox_config.py`;
`GLOBAL_START` 2022-01-01, `GLOBAL_END` auto-detected from the latest
`kraken_csv/YYQ#/` folder). Confirmed aligned: all 16 instruments end
2026-03-31T23:00:00Z; GBP-crypto and USD-crypto both start
2022-01-01T00:00:00Z; FX starts 2022-01-02T22:00:00Z (first Sunday open,
expected). The original 6-month window (2025-07-01 to 2025-12-31) is now
the training window for the 17 Tier 4 survivors below, not the live
sandbox - see `ROADMAP.md` for rollout status.

Extended metrics, rolling diagnostics, portfolio correlation, service-tier
filtering: built and verified - see `VALIDATION_HIERARCHY.md` for the full
list and `README.md` for module layout.

## 4b. STRATEGIES

Five: `SmaCrossoverStrategy`, `RsiStrategy`, `MacdStrategy`,
`RsiMacdConfluenceStrategy` (composes real standalone RSI+MACD instances,
fires only on independent agreement), `BollingerBandsStrategy`. Per-
strategy detail in `README.md`'s Strategies section.

## 4c. CONFIDENCE-WEIGHTED MULTI-STRATEGY SIZING - DESIGN ONLY, NOT BUILT

Full spec: [`CONFIDENCE_SIZING_DESIGN.md`](CONFIDENCE_SIZING_DESIGN.md).
Read it before touching Phase 1 Step 2+ - summary below is not a
substitute. Real fee schedules (Section 9.1) remain unresolved - blocked
on your OANDA account type / real API credentials, and needs to run
outside this sandbox regardless (no network access here).

## 4d. REAL-DATA VALIDATION HIERARCHY RUN

All 5 strategies x 16 real sandbox instruments through Tier 0-4 - 80
combinations, 186.6s, no network required. Param grids in
`run_full_sweep.py`.

**Result: 7 (strategy, instrument) pairs cleared Tier 4** (first survivors
this project has produced):

| Strategy | Instrument | Survivors | Best CI-90 lower bound |
|---|---|---|---|
| RsiStrategy | LTC/GBP | 2 | +5.62% |
| RsiMacdConfluenceStrategy | LTC/GBP | 1 | +4.31% |
| RsiMacdConfluenceStrategy | LTC/USDT | 3 | +3.39% |
| RsiStrategy | LTC/USDT | 2 | +1.12% |
| RsiStrategy | USD_JPY | 3 | +0.73% |
| RsiStrategy | GBP_JPY | 4 | +0.14% |
| RsiMacdConfluenceStrategy | GBP_JPY | 2 | +0.13% |

Only mean-reversion strategies (RSI, RSI+MACD confluence) survived, only
on JPY crosses and Litecoin pairs. `SmaCrossoverStrategy`: 0/16.
`MacdStrategy` / `BollingerBandsStrategy`: 0/16 each - both repeatedly
reached Tier 3, never cleared bootstrap. Persisted survivors:
`data/full_sweep_runs.db` was LOST (gitignored, never committed, not in git history). **Reproduced chat 13 (261001):** same 7 pairs / 17 survivors, same best CI-90 bounds, and 17 param sets identical to `SURVIVORS` in `run_out_of_time_validation.py`, from three runs: the 260922 script unmonitored on the old 2025 H2 sandbox; the current script monitored on the old sandbox; the current script on the 22Q1-26Q1 sandbox sliced to 2025-07-01..2025-12-31. The 2025 H2 slice of the 22Q1-26Q1 sandbox is row-for-row identical to the old sandbox (60,430 rows, 0 price/volume differences). Reproduction DBs (local, gitignored - back up): `data/sweep_runs_250701to251231_chat13_261001_{A-orig,A-mon,B}.db`. Re-run: `python3 run_full_sweep.py --start 2025-07-01 --end 2025-12-31 --chat <N>`. Original per-survivor CIs are not comparable (original DB lost); per-pair bests match the table above. Run time 142s on the user's machine (original 186.6s, machine unknown) - informational, not a gate.

**Checks against `CONFIDENCE_SIZING_DESIGN.md` Section 4.2:**
1. Real strategy correlation matches the synthetic-data numbers closely
   (SMA vs. Bollinger -0.59 real / -0.61 synthetic; RSI vs. Bollinger
   +0.39 / +0.34; MACD stays least-correlated with everything, 0.08-0.27).
   The trend-vs-reversion opposition is real, not a synthetic artifact.
2. **RSI vs. RsiMacdConfluenceStrategy correlate at +0.65 avg (up to
   +0.82 on LTC/GBP)** - not independent votes, since Confluence embeds
   RSI's own signal. Resolved in Section 4e below.
3. **Defect found and fixed in the Section 4 strawman weighting formula**:
   a rejected Tier-3 finalist (`BollingerBandsStrategy`/LTC_GBP, weight
   9.96 via its raw uncorrected Tier-1 return) could outscore an actual
   Tier 4 survivor (`RsiStrategy`/LTC_GBP, weight 5.62 via bootstrap CI).
   **Fix, applied**: use the bootstrap CI-90 lower bound as base_metric
   for every candidate reaching Tier 3+, computed for every finalist
   regardless of pass/fail - not just passing ones. Still needs to land
   in the real Phase 1 Step 2 scoring engine, not just the sanity-check
   script (`run_full_sweep.py`).
4. Real per-survivor trade frequency (needed for Section 8.1's debounce
   thresholds) - not yet extracted from `data/full_sweep_runs.db`. Cheap,
   still open.
5. No fiat-vs-crypto split in survivors (JPY crosses + Litecoin, not
   crypto-as-a-class) - Litecoin specifically stands out. Worth checking
   before assuming a fiat/crypto module split in Section 6.

## 4e. THREE DESIGN SUB-TASKS - RESOLVED

**Cross-strategy correlation** (`backtest/strategy_clustering.py`, 7
tests): two mechanisms built - cluster/group (single-linkage, merges
correlated strategies into one vote) vs. pairwise discount (every strategy
keeps its own vote, only its weight is discounted by max correlation with
a higher-trust strategy already counted). Compared on real GBP_JPY data
(RSI vs. Confluence, corr +0.6464): cluster/group nearly erases genuine
disagreement between a strong and a correlated-weaker strategy (0.0082 vs.
pairwise's 0.0979 in the disagreement case). **Decision: ship pairwise
discount as primary; keep cluster/group as a diagnostic/reporting view
only.** Dynamic/rolling correlation explicitly deferred (turns the
lookback window into an unvalidated hyperparameter; real new
infrastructure, not a formula change).

Real-data comparison (GBP_JPY, strawman weights, both Tier 4 survivors,
`RsiStrategy(period=10, oversold=25, overbought=80)` CI-90 lower bound
+0.1424%, `RsiMacdConfluenceStrategy(confirmation_window=10)` +0.1259%,
measured correlation 0.6464):

| Scenario | Naive (uncorrected) | Cluster/group | Pairwise discount |
|---|---|---|---|
| Both agree (RSI +1, Confl +1) | 0.2683 (simple sum) | 0.1341 (one term, stance +1.000) | 0.1869 (RSI 0.1424 + Confl discounted to 0.0445) |
| They disagree (RSI +1, Confl -1) | n/a (no way to express this as one number) | **0.0082** (0.061 stance x 0.1341 weight - disagreement nearly cancels) | **0.0979** (0.1424 - 0.0445 - RSI's confident vote still dominates) |

**Base-metric choice** (`backtest/base_metrics.py`, 5 tests): all ~25
`Metrics` fields grouped into 5 roles (magnitude, reliability,
drawdown-shape, cost-realism, sizing-informational-only). 4 candidate
scoring-weight blends built: `ci_lower_bound` (current default),
`ci_lower_bound_cost_adjusted`, `expectancy_cost_adjusted`, `effect_size`
(classical t-statistic, independent of bootstrap's resampling
assumption). Real comparison across all 17 survivors: cost-adjustment
barely moves rankings in this sample (cost drag too small/uniform to
matter yet - re-test once real fee data exists). **`expectancy_cost_
adjusted` and `effect_size` meaningfully DISAGREE with `ci_lower_bound` on
which survivor ranks best** - concrete examples: `RsiStrategy`/LTC_GBP is
the single best survivor by `ci_lower_bound` (rank #1, +5.62%) but drops
to rank #8 under `expectancy_cost_adjusted` (unremarkable per-trade
expectancy despite the widest/most favorable bootstrap CI);
`RsiStrategy`/USD_JPY is middling under `ci_lower_bound` (rank #8) but
jumps to rank #1 under `effect_size` (its 23 trades show unusually low
variance relative to their mean, which the classical t-statistic rewards
more than bootstrap resampling does). Real evidence for "don't commit to
one metric yet," not a-priori caution. **Decision: no winner picked.**
Deciding test specified: once out-of-time data exists, re-rank each
candidate's top picks against held-out windows and see which one's #1
pick actually holds up - do this before Phase 1 Step 2 commits to a base
metric.

**Period-comparison visualization** (`backtest/periods.py`,
`backtest/period_comparison.py`, `visualize_period_comparison.py`):
three-layer split (period definition / execution / rendering) so a future
live dashboard can reuse the data layer without pulling in
matplotlib/Chart.js. `calendar_year_periods()` built first; structured so
quarters/custom/regime-labeled periods slot in later without touching
downstream code. Static (PNG, bronze-tier) and interactive (HTML+Chart.js,
gold-tier) both built from the same data. Proven end-to-end against real
2025 H2 data. **A real boundary bug was caught by this work**: two period
generators disagreed on whether a period ends at `23:59:59` or the next
midnight, which could have double-counted a boundary candle - fixed via a
shared `_end_of_period()` helper, now used identically everywhere,
including `run_out_of_time_validation.py` (refactored onto this shared
module instead of its own private chunking logic).

All three explicitly deferred dynamic/rolling correlation, the full
metric cartesian product, and regime-labeled periods to a future session.

## 5. HOLZMANN REVIEW

Applied project-wide. New files should be written to the same standard:
linear control flow, explicit ceilings where relevant, assertions on grid
size, no bare `except: pass`.

## 6. OPEN THREADS

- Hosting: the live runner needs an always-on host, distinct from the database host (Snowflake Postgres/Neon). User recalls a Vultr vs Neon discussion in another chat that is not in the repo - capture it in chat 30.
- Post-MVP, with a lawyer and an accountant: compliance work they specify; ISA/tax-wrapper investigation (individual-vs-company and eligible-holdings questions flagged in `ROADMAP.md` Section 5).
- Investor pack (process explainer, flow chart, market/competitor comparison) runs in parallel; a lawyer reviews return language before external use.


- Timezone policy (chat 14 audit): user is London-based and wants everything aligned to London time. Run-monitor stores UTC + Europe/London. Stored market-data timestamps are UTC (OANDA `Z`, Binance/ccxt UTC, Kraken epoch, `sandbox_config.py`) and `periods.py` year/quarter boundaries are UTC. SETTLED 260930 (chat 12): keep storage UTC, convert for display only (London time); chat 14 audits conformance. Unknown: OANDA candle alignment timezone (`fetch_candles` sets none).
- `run_full_sweep.py` needs a permanent home / resolution against
  `run_validation_hierarchy_real_data.py` (does it replace or extend it -
  your call, not made here). The original `data/full_sweep_runs.db` was lost (Section 4d); reproduction DBs are local/gitignored - back up `fx_trader/data/*.db` manually, git cannot recover them.
- Section 4d point 3's weighting-formula fix needs to land in the real
  scoring engine, not just the sanity-check script.
- Real trade frequency per Tier 4 survivor (Section 4d point 4) - needed
  for Section 8.1's debounce thresholds.
- Service tiers are a starting allocation, not a decision - needs a
  dedicated product-design conversation.
- Cross-pair/cartesian comparator + statistical pairs trading - deferred;
  multi-instrument timestamp/candle-boundary alignment across
  OANDA/Binance/Kraken still unsolved.
- Tiered subscription refresh rates (~5min/~30sec-1min/~1sec) - not
  designed.
- `OandaBroker.get_quote` / `place_market_order` - never run, test before
  trusting either.
- Multi-user entitlements, live feed, deployment infra - open.
- Real fee schedule sourcing (Section 9.1 of the sizing design) - blocked
  on your input, can't run in this sandbox regardless.

## 7. IMMEDIATE NEXT STEP

**Updated 260929: superseded by `ROADMAP.md` Sections 3-4 (phases and chat-by-chat plan). The paragraph below is the pre-260929 summary; its item (2), acquiring a second out-of-time window, is dropped (26Q2 unpublished, 22Q1-26Q1 is the final sandbox).**


Per `CONFIDENCE_SIZING_DESIGN.md`'s phasing plan and `ROADMAP.md`'s
priority order - see `ROADMAP.md` for the full sequenced plan. Short
version: (1) extract real trade-frequency data from
`data/full_sweep_runs.db`; (2) acquire a second out-of-time window and
re-validate the 7 survivors against it - **highest-leverage single
action available**, since Tier 3/4 currently only ever tested inside the
same 6-month sample; (3) in parallel, test `OandaBroker.get_quote`/
`place_market_order` against a practice account; (4) Phase 1 Step 2
(scoring engine, shadow-computed), using the pairwise-discount correlation
fix and only out-of-time-validated survivors.

Phase 1 Step 1 (weighted-average-entry position accounting) is DONE -
see Section 3.

## 8. RISKS

Two real bugs found and fixed in `OandaBroker.fetch_candles` (Section 3) -
concrete evidence that "written to spec" isn't "correct." Apply the same
suspicion to anything still unverified, especially `get_quote`/
`place_market_order`. The Section 4d weighting-formula defect is the same
lesson at a different layer: always sanity-check a formula against real
results before it becomes load-bearing. Not a lawyer or financial advisor
- this project is a testing/execution pipeline, not investment advice.
Automation doesn't remove trading risk, it executes decisions faster.
