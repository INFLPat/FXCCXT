<!-- version: 261005 -->
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
- **Run monitor** (built chat 12): `fx_trader/run_monitor.py` - one module, JSON Lines log `fx_trader/logs/run_log_YYMMDD.jsonl` (London date of run start; gitignored, local only, never the cloud DB). Records UTC and Europe/London times, duration, CPU, peak memory, git commit, notes/counts/per-item timings; start/notes/items are streamed so a killed run keeps its data. Log-write failure never crashes a run: queue, retry, fallback file + stderr, `python3 run_monitor.py recover`. `python3 run_monitor.py summary <log>` prints runs. API, record schema, recovery: its docstring. **Every new long-running script adopts it from creation; unadopted scripts and the chat that adopts each are listed in `ROADMAP.md` Section 4 (Run-monitor adoption). Adopted so far: `run_full_sweep.py`.**
- **Run scripts take CLI args, not edited constants (settled 261001, chat 13)**: windows, chat number and similar per-run inputs are `argparse` args, recorded in the run_monitor start record (no file edit per run, so `git_dirty` stays false). New `run_*.py` follow this; existing ones convert when next touched. Generated per-run outputs are named `<name>_<window>_chat<N>_<YYMMDD run date, London>[_<tag>]`, e.g. `data/sweep_runs_250701to251231_chat13_261001.db`; scripts refuse to reuse an existing output DB. First adopter: `run_full_sweep.py`.
- **Tests are hermetic (settled 261001, chat 13)**: a test never reads, writes or names a real output (runs DBs, sandbox DBs, logs, run-date-named files); it uses temp files and injected fake dates/paths and cleans up after itself. Origin: `test_sweep_config.py` first used a real run name and failed once the real run DB existed (patch 13b). Before shipping any test ask: would it behave identically with real outputs present and absent? Claude runs every new test in its own sandbox (stubbing heavy imports where the repo is not on disk) before shipping, and says so if it could not.
- **Commands use `python3` (settled 261001, chat 13)**: new or edited docs/docstrings say `python3`; existing `python` usage lines convert when their file is next edited. Not a functional problem today (nothing executes a bare `python`; `apply_patch.py` uses `sys.executable`) but pasted commands fail on this Mac. Exception: `run_monitor.py`'s printed 'Recover: python run_monitor.py recover' hint and its assertion in `test_run_monitor.py` change together or not at all.
- **Changed-files record (settled 261001, chat 13)**: every reply that delivers or supersedes a patch payload lists EVERY touched file by full repo path with action and op count (e.g. `fx_trader/ROADMAP.md - patch x5`); the end-of-chat handoff repeats the full list across all payloads applied that chat, naming any failed or superseded payload and what replaced it. The user keeps external file-history records from this list.
- **Attach lists mark `(in context)` or `(upload)` (settled 261001, chat 13)**: the Context panel repo sync already supplies every committed repo file; only local/gitignored items (DBs, logs, historical copies, pasted terminal output) are ever uploaded.
- **Patch and chat naming (settled 261003, chat 14)**: several patches in ONE chat take letters - `patch_261003_chat14.json`, `patch_261003_chat14b.json`, `..._chat14c.json`. A topic split ACROSS chats takes `p1`, `p2` on the roadmap chat ID (`14p1`, `16p1`, `30p2`); no existing ID is renumbered. `CHAT_LOG.md` is the append-only record of finished chats; `ROADMAP.md` is forward-looking.
- **Changed-files record with folders, and interim versions (settled 261003)**: every payload delivery lists each touched file as full repo path, containing folder, action (created/patched), payload, and the new version stamp; a file touched by TWO payloads in one chat is called out so the interim version can be backed up before the second is applied.
- **Dry run is terminal-only (settled 261003)**: `apply_patch.command` passes only the payload path, so `--dry-run` cannot be entered through it. Preview with `cd ~/FXCCXT && python3 fx_trader/utilities/apply_patch.py <payload.json> --dry-run` (no git preflight, writes nothing). Adding a dry-run option to the launcher is folded into chat 29.
- **Data manifest gate (settled 261003)**: `audit_sandbox_alignment.py --verify-manifest <file>` checks the (gitignored) sandbox against a saved manifest. It is a DATA gate, not part of `apply_patch` (patch tests stay hermetic). Run it before any sweep or analysis that reads the sandbox (chats 15, 16, 16p1, 25p1, 27, 28, 33p1) and after any re-ingest (then write a new manifest and back it up with the DB).
- **Evidence labels (settled 261003)**: MEASURED / READ / INFERRED / UNVERIFIED (research notes add MEMORY, UNREAD). Findings without a label are not recorded as fact.
- **Time policy (settled 261003)**: Section 4f.5; all time handling goes through `time_policy.py`.
- **Research cadence (settled 261003)**: a small "what did we miss" research chat before each major phase; findings fold into `RESEARCH_NOTES.md` and this file as decision + evidence (ROADMAP 17p1, 17p2, 19p2, 29p1).
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
| `run_monitor.py` | Unit-tested in Claude's Linux sandbox (success/failure/interrupt/killed-run/write-failure/recovery paths). Run on the user's machine in two real full sweeps (chat 13, both ok; start/notes/items/end records and meta as expected); macOS peak RSS recorded as 40 MB in both chat-13 sweeps (units consistent with bytes->MB, not independently verified); real disk-full not verified. |
| `time_policy.py`, `FxStore` bound normalisation, `periods.py` fixes (chat 14) | Regression tests proven to FAIL on the unpatched code (reproduced in Claude's sandbox) and PASS patched; full suite passed on the user's machine (Python 3.14.7). |
| `audit_sandbox_alignment.py` (chat 14) | Synthetic world with injected faults found exactly (tests passed on the user's machine); run read-only on the real sandbox in Claude's sandbox (Python 3.12, stubbed run_monitor/instrument_config). `main()` with the real run_monitor run by the user 261005 (ok in 3.9 s; manifest verified; figures reproduced; Python 3.14.7). |
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

## 4f. CHAT 14 - CROSS-RATE / ALIGNMENT / TIMEZONE AUDIT (261003): FINDINGS AND DECISIONS

Evidence labels: MEASURED (computed on the sandbox; reproduce with `python3 audit_sandbox_alignment.py --chat N`), READ (from code), INFERRED, UNVERIFIED. Sources, definitions, the pipeline, design brief and the open-investigations register (INV-n): `RESEARCH_NOTES.md`. Chat 14 delivered in two payloads: `patch_261003_chat14.json` (code; applied, all 26 test modules passed, commit 8dd6c71, pushed) and `patch_261003_chat14b.json` (documentation, commit 3a2fc4b), plus a close-out `patch_261005_chat14c.json` (audit run result and status).

**4f.1 Built (payload 1).** `time_policy.py` (stored-timestamp contract, `normalize_bound`, `local_view`, `fx_weekly_session`); `FxStore.get_candles` / `get_candles_multi` normalise bounds; `backtest/periods.py` month-clamp and post-side fixes; `audit_sandbox_alignment.py` (checks: format, gaps, cycles, basis, lag, ppy, manifest; argparse; run_monitor adopted; read-only DB); tests `test_time_policy.py`, `test_audit_sandbox_alignment.py`, extended `test_periods.py`; `requirements-dev.txt` (coverage, hypothesis); `.gitignore` adds `fx_trader/reports/*.json|txt`. Verified: new regression tests FAIL on the unpatched code (month-clamp ValueError; end candle dropped) and PASS patched; the audit finds injected faults exactly (+50 bps triangle, +100 bps basis, gap classes, venue hour); full suite passed on the user's machine. RUN by the user 261005 (Python 3.14.7): `main()` with the real `run_monitor` ok in 3.9 s (`logs/run_log_261005.jsonl`; report `reports/audit_220101to260331_chat14_261005.json`); `--verify-manifest` verified_ok (local sandbox identical to the uploaded copy: 16 instruments, 508,413 rows); every figure in 4f.2-4f.4 reproduced exactly.

**4f.2 Sandbox facts (MEASURED).** Manifest: 16 instruments, 508,413 rows (file `sandbox_manifest_22Q1to26Q1_261003.json`, computed on the uploaded copy; user verifies the local copy matches with `--verify-manifest`). Every timestamp is stored-format and on the hour; no ask<bid, no OHLC inconsistency; FX has zero zero-spread rows; every crypto bar has bid = ask by construction. Stamps are true UTC: the FX week follows New York 17:00 (US summer: last bar Fri 20:00, reopen Sun 21:00 UTC; US winter: 21:00 / 22:00; change weeks differ), and 215-216 scheduled weekends per FX pair match that rule exactly (215 for GBP_USD, EUR_USD, USD_JPY because of the 2022-06-24 event). Binance vs Kraken hourly returns correlate 0.972 / 0.973 / 0.971 / 0.938 (BTC/ETH/XRP/LTC) at lag 0 and ~0 at +/-1, +/-2: shared stamp convention (absolute open-vs-close convention UNVERIFIED, INV-11). FX-vs-crypto timing: loading of the basis change on GBP_USD returns is 0.05-0.08 at lag 0 and ~-0.01 to -0.04 at +/-1 (a one-hour offset would give ~1): aligned to the hour; sub-hour skew not excluded (INV-12).

**4f.3 Gap catalogue (MEASURED; UTC; classes are candidates until INV-9/10).**

| Class | Event | Detail |
|---|---|---|
| PAIR_SPECIFIC | 2022-05-04 21:00 | USD_CHF only, 6h; frozen closes preceded it |
| PAIR_SPECIFIC | 2022-05-08 21:00 | EUR_GBP only, 4h; thin Sunday-open bar |
| PAIR_SPECIFIC | 2022-06-24 20:00 -> 06-26 22:00 | EUR_USD, GBP_USD, USD_JPY only: reopen 1h late (50h) |
| FX_WIDE_UNSCHEDULED | 2022-05-12 05:00 -> 08:00 | all 8 pairs, 3h; crypto traded through |
| FX_WIDE_UNSCHEDULED | 2022-08-26 20:00 -> 08-28 23:00 | all 8, reopen 2h late (51h) |
| HOLIDAY_CANDIDATE | 2022-12-23 21:00 -> 12-26 22:00 | 73h |
| HOLIDAY_CANDIDATE | 2022-12-30 21:00 -> 2023-01-01 23:00 | 50h, reopen 1h late |
| HOLIDAY_CANDIDATE | 2023-12-22 -> 12-25 and 2023-12-29 -> 2024-01-01 | 73h each |
| HOLIDAY_CANDIDATE | 24 and 31 Dec 2024 and 2025 (last bar 21:00 UTC, next 22:00 next day) | 25h each (4 events) |

No gap at Good Friday / Easter Monday. Crypto: Binance missing exactly one hour on all four pairs, 2023-03-24 13:00 UTC. Kraken missing hours BTC 21 / ETH 36 / XRP 85 / LTC 406 (union 467; in exactly 1 pair 430, 2 pairs 14, 3 pairs 2, all 4 pairs 21); weekend share 86% / 61% / 56% / 36%; longest runs 5-6 h (~2024-04-14 03:00 in all four; ~2025-11-01 15:00; others). Crypto traded during every FX gap.

**4f.4 Cross-rate results and threshold decisions (MEASURED, bar-close data).**

| Triangle (cycle enumerated from the pair list) | abs residual bps p50 / p99 / p99.9 / max | summed 3-leg spread p50 | suspect bars | exec-band violation share |
|---|---|---|---|---|
| GBP_JPY vs GBP_USD x USD_JPY | 0.10 / 2.59 / 7.35 / 11.7 | 4.34 bps | 90 | 0.89% |
| GBP_CHF vs GBP_USD x USD_CHF | 0.14 / 2.72 / 7.59 / 130.8 | 5.24 bps | 15 | 0.04% |
| EUR_USD vs EUR_GBP x GBP_USD | 0.13 / 1.43 / 3.36 / 114.1 | 4.54 bps | 1 | 0.01% |

Direct mid sits 0.05-0.08 bps below the synthetic mid in all three (INV-5). Residuals concentrate at 20-22 UTC (GBP_JPY also 17 UTC); no year-on-year drift. Basis (BTC/ETH/XRP/LTC via USDT/GBP, USDT~USD alias): abs p50 5.5 / 6.6 / 8.2 / 11.0 bps, p99 35-80, max 234 / 290 / 249 / 261 - NOT tiered or executable-tested (crypto bars have bid = ask). USDT/venue stress dates in INV-7. **Settled:** (1) data-validity tiers are FIXED, in cost-ratio units: clean <= 0.25, noisy 0.25-1.0, suspect > 1.0 or > 10 bps (provisional edges from this distribution), plus a "bad" tier for stale/leg-attributed bars (not yet implemented); (2) the risk-appetite slider adjusts ONLY the confidence discount applied to noisy bars, never data validity (user agreed: loosening validity with appetite is wrong); (3) all three cross-rate roles are built over time: detector (executable residuals - done), estimator (least-squares strength, leave-one-out attribution - 14p1), diagnostics (dynamics, stale detection, orientation - 14p1); (4) not a strategy on this data (cost ratio p99 0.17-0.44); post-MVP edge only with ticks/latency/depth, likely not Python; (5) USD_CAD has no cycle and cannot be cross-checked; JPY and CHF triangles cannot separate a bad direct pair from a bad USD leg without stale/own-history checks.

**4f.5 Time policy (settled; code: `time_policy.py`).** T1 storage = UTC text in one fixed format (`STORED_TS_FORMAT`); T2 aware datetimes only, naive input rejected; T3 bar stamp = bar OPEN time; T4 conversion to a user's IANA zone is display-only (`local_view`; multi-user: zone chosen per user); T5 analysis periods stay UTC-defined for reproducibility (London quarter boundaries are BST, i.e. one H1 candle off UTC at 3 of 4 boundaries; a per-user `tz` on period generators is a later option, still resolved to UTC); T6 the FX week/day follows New York 17:00 (US DST, not UK): in weeks where US and UK clocks differ the London-time display of the open flips, and DST test matrices are mandatory; T7 local-time daily aggregation must handle 23/25-hour days; T8 intervals should become half-open `[start, end)` for the tick/live era (the current `end = next_start - 1s` with inclusive filtering is correct only for second-resolution bars); T9 all new code uses `time_policy`, no ad-hoc `isoformat()`/`strftime` for DB bounds. Stage 2 (after the source inventory): canonical integer `ts_utc` for ordering/joins with raw timestamps kept as provenance and an explicit per-source stamp convention (30p2).

**4f.6 Bugs found and fixed (both reproduced on unpatched code, regression-tested).** BUG-1: `isoformat()` bounds end in `+00:00`; the stored text has `.000000000Z`; at character 20 `+` (ASCII 43) sorts before `.` (46), so an inclusive `<= end` silently excluded the candle stamped exactly at the bound (`GBP_USD`: 26,396 of 26,397 rows returned). Start bounds worked by luck. Affected `compare_periods`, the partial last-year bar in `visualize_period_comparison`, and `run_out_of_time_validation`. Earlier validated results were NOT affected (`run_full_sweep.py` builds bounds in stored format; the hierarchy script passes none) - READ. Fix: `FxStore` normalises bounds. BUG-2: `_month_add` used `replace(month=...)`, raising on day 29-31; the post-training side started at `exclude_end` (2025-12-31 23:59:59), so `run_out_of_time_validation.py` would have raised before any output (blocking chat 16); `test_periods` never reached the post side. Fix: clamp the day; post side starts at `exclude_end + 1s`. **Bug classes seen:** text-format contracts, boundary off-by-one, calendar arithmetic, branches no test reaches, silently assumed constants, convention drift between modules, docstring drift. **Methods adopted:** boundary-triplet tests (bound and +/-1 unit); day-of-month x month-offset matrices and DST-date matrices; real-data invariants as tests (e.g. `get_candles(coverage start, end)` returns exactly the coverage count); differential tests (two generators that must agree); test-the-test (inject a known bug, confirm a test fails - done for both bugs); every bug gets a regression test named with its ID; metamorphic tests (inverting a pair leaves the residual unchanged); warnings-as-errors runs; assumption register. **Candidates:** `coverage.py` branch reports (run occasionally by the user, dev-only), `hypothesis` property tests (installable, dev-only), golden-file regression, docstring-claim tests, mutation spot checks. **Assumption register:** AR-1 FX_PPY = 252*24 (MC-1); AR-2 fills at the signal bar's close (same-bar fills); AR-3 crypto bars have bid = ask so cost is commission/slippage only; AR-4 24 bars per FX day; AR-5 bar stamp = open; AR-6 USDT~USD alias; AR-7 all sources UTC; AR-8 sandbox FX is OANDA only.

**4f.7 Metric caveat MC-1 (annualisation).** `FX_PPY = 252*24 = 6,048` vs observed 6,224 bars/year (x1.029; Sharpe-family scale sqrt = 1.0145, ~1.4%). Crypto USDT 8,766 vs 8,760 (ok); LTC/GBP 8,671 (x0.990, trips the 1% warning). Annualisation-DEPENDENT: `sharpe_ratio`, `sortino_ratio`, `calmar_ratio` (CAGR years), `rolling_*` Sharpe/Sortino, `portfolio` equal-weight Sharpe. NOT dependent: total return, drawdown metrics, profit factor, expectancy, payoff, Omega, VaR/CVaR, tail ratio, skew/kurtosis, Kelly, exposure, bootstrap CIs, and all four base-metric candidates (`ci_lower_bound`, its cost-adjusted form, `expectancy_cost_adjusted`, `effect_size`). All Tier 0-4 gates are independent of it. The design's leaning candidate (Sortino x trust discount) IS affected. **Decision:** compute all variants and compare, do not pick blindly: (a) flag (now: this entry, a comment in `instrument_config.py`, the audit warning), (b) change (observed bars/year, and a time-based annualisation), (c) deliberate re-baseline before a Sharpe-family metric becomes load-bearing. Rank stability across variants becomes an axis of the chat-28 base-metric test (`compute_metrics` already takes `periods_per_year`). Do not change the constant until then (it would shift every FX Sharpe-family value and break byte-identical regression checks).

**4f.8 Decisions settled in chat 14 (do not relitigate without a new reason).** Holidays vs outages learning and handling (RESEARCH_NOTES Section 6); two separate data-source threads (sandbox/research vs live feed); free/research licences during R&D, redistribution licence started at MVP / single-user live (P15b); multiple live sources mandatory; raw sandbox data stays untouched - QC findings go in a side table (instrument, timestamp, class, evidence) and validation runs both raw and masked (the difference is a robustness metric); Epps guard folded into confidence weighting; sandbox universe grows in stages (as-is -> augmented incl. stablecoin pairs -> MVP suite -> later additions), registry designed for future-proof flexibility; strength definitions kept as views; two-speed architecture; executability is venue-scoped; stdlib-first with optional numpy path; coverage/hypothesis dev-only; bounds normalised inside `FxStore`; patches: letters for multiple patches in one chat, `p1/p2` for sub-topic chats split across chats; scope split S1 (chat 14) / S2 (14p1); research chats added to the plan.

**4f.9 Parked/deferred, with owners.** All INV-1..INV-20 (RESEARCH_NOTES Section 10) and the ROADMAP deferred-capabilities table (Section 9). `apply_patch.command` cannot pass `--dry-run` (it passes only the payload path; use the terminal command in `utilities/README.md`): add a dry-run option to the launcher and README - folded into chat 29 (de-bloat #2, which reviews utilities). 

**4f.10 Chat 14 coverage ledger (full review of this chat).**

| Item discussed | Recorded in |
|---|---|
| macOS RSS 40 MB (scope item 1) | Section 3 run_monitor row |
| Triangles, executable bands, thresholds, basis, tiers | 4f.4; RESEARCH_NOTES 2-3; ROADMAP 14p1 |
| Alignment, gaps, venue outages, Kraken stats | 4f.3; RESEARCH_NOTES 6; INV-9/10/13/14/19 |
| Timezone audit, time policy, per-user zones, DST, NY session | 4f.5; `time_policy.py` |
| Bugs 1-2, methodology, assumption register | 4f.6 |
| MC-1 annualisation and variants | 4f.7; ROADMAP chat 28; CONFIDENCE_SIZING_DESIGN Section 5; VALIDATION_HIERARCHY; `instrument_config.py` |
| Holidays vs outages; fault-injection replay | RESEARCH_NOTES 6; ROADMAP P7 |
| Two data-source threads, free vs paid, licensing | RESEARCH_NOTES 5; ROADMAP risk register, 16p1, 30p1, P15b |
| Storage: source column vs tables, canonical timestamp | RESEARCH_NOTES 5; ROADMAP 30p2 |
| Epps effect, confidence input | RESEARCH_NOTES 7; CONFIDENCE_SIZING_DESIGN Section 6 |
| Crypto safe-house H1/H2 | RESEARCH_NOTES 4; ROADMAP 21p1 |
| Can't-do-now (IRP, depth, swaps) | RESEARCH_NOTES 8; ROADMAP Section 9; P12 aims |
| Executability, venue edges, crypto nodes, stablecoin basis | RESEARCH_NOTES 2, 4; ROADMAP 14p1, 25p1 |
| Universe: hard-coded 16, staged growth | RESEARCH_NOTES 4; ROADMAP 19p1, 25p1, P11b |
| Same-bar-close fills, integrity manifest, licensing, single source, MC-1, point-in-time universe | ROADMAP risk register; AR-2 |
| Manifest gate (when to run) | Working Conventions; ROADMAP chats 15, 16, 16p1, 25p1, 27, 28, 33p1 |
| Research chats; reading list; literature leads | ROADMAP 17p1, 17p2, 19p2, 29p1; RESEARCH_NOTES 1, 9 |
| Residual/stress-day causes | RESEARCH_NOTES 10 (INV-1..7) |
| numpy/coverage/hypothesis dependency decisions | RESEARCH_NOTES 4; `requirements-dev.txt` |
| Reproducibility metadata; preflight gate | RESEARCH_NOTES 4; ROADMAP 33p1 |
| Roadmap Status/Date columns, CHAT_LOG, p-suffix IDs | ROADMAP; `CHAT_LOG.md` |
| Patch naming (letters vs p-suffix); dry-run is terminal-only; backup list rule | Working Conventions; `utilities/README.md` |
| User-confirmable unknowns (Kraken stablecoin CSVs, Python 3.14 wheels) | INV-16, INV-17 |

## 5. HOLZMANN REVIEW

Applied project-wide. New files should be written to the same standard:
linear control flow, explicit ceilings where relevant, assertions on grid
size, no bare `except: pass`.

## 6. OPEN THREADS

- Hosting: the live runner needs an always-on host, distinct from the database host (Snowflake Postgres/Neon). User recalls a Vultr vs Neon discussion in another chat that is not in the repo - capture it in chat 30.
- Post-MVP, with a lawyer and an accountant: compliance work they specify; ISA/tax-wrapper investigation (individual-vs-company and eligible-holdings questions flagged in `ROADMAP.md` Section 5).
- Investor pack (process explainer, flow chart, market/competitor comparison) runs in parallel; a lawyer reviews return language before external use.


- [Chat 14: audited; findings and settled policy are in Section 4f.5 - the original note below is kept for history] Timezone policy (chat 14 audit): user is London-based and wants everything aligned to London time. Run-monitor stores UTC + Europe/London. Stored market-data timestamps are UTC (OANDA `Z`, Binance/ccxt UTC, Kraken epoch, `sandbox_config.py`) and `periods.py` year/quarter boundaries are UTC. SETTLED 260930 (chat 12): keep storage UTC, convert for display only (London time); chat 14 audits conformance. Unknown: OANDA candle alignment timezone (`fetch_candles` sets none).
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

**Updated 261003 (chat 14): superseded by `ROADMAP.md` Section 4 (Status column). Chat 14 closed 261005. Next: 14p1 (currency-graph engine), 15, 16, 16p1, 17.**

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
