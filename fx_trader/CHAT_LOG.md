<!-- version: 261005 -->
# Chat Log

Append-only record of completed (and in-progress) chats. `ROADMAP.md` stays forward-looking; finished work lives here. Add a row when a chat's last patch is applied. Date = the last day the chat was worked on / its final patch date (user convention, 261003). Titles marked (T) were supplied by the user; (R) were taken from ROADMAP.md because no title was supplied.

| Chat | Date | Topic | Outcome / pointer |
|---|---|---|---|
| C1 | 260911 | FX & crypto trading bot with backtesting (T) | Initial pipeline: data, strategy, backtest engine |
| C2 | 260911 | Updating files from GitHub repository (T) | Repo sync workflow |
| C3 | 260914 | FX sandbox dataset & run persistence (T) | `FxStore`, `RunStore`, first sandbox |
| C4 | 260915 | Testing model against real-world data with code standards (T) | Holzmann rules adopted; first real-data runs |
| C5 | 260918 | Adding performance and risk metrics to trading model (T) | `metrics.py` extended metrics, `rolling.py`, `portfolio.py` |
| C6 | 260924 | Git workflow setup for local to remote pushing (T) | Pull-only GitHub integration; local push workflow |
| C7 | 260925 | Testing strategies against sandbox with analysis (T) | Strategy sweeps; first survivors (see CONTEXT_HANDOFF 4d) |
| C8 | 260927 | Expanding sandbox data to 23Q1-26Q1 with multi-window validation (T) | Wider sandbox; multi-window validation |
| C9 | 260929 | Project documentation and code cleanup (T) | Docs consolidation; `sandbox_config.py` / `instrument_config.py` |
| C10 | 260930 | Roadmap rewrite, MVP definition, versioning rules (R) | `ROADMAP.md` rewritten 260929 (written overnight; final patch 260930 per user); MVP and cartesian front-loading settled |
| C11 | 260930 | Housekeeping and versioning rollout (R) | Version stamps, duplicate removal, layout fixes |
| C12 | 261001 | Run-monitor utility (R) | `run_monitor.py`; patch names carry chat number |
| C13 | 261001 | Reproduce the 7 survivors on 2025 H2; CLI-args conventions (R) | Same 7 pairs / 17 survivors reproduced; `run_full_sweep.py` argparse; patches 261001_chat13 to 13d (CONTEXT_HANDOFF 4d) |
| C14 | 261005 | Cross-rate arithmetic, timestamp-alignment and timezone audit (ROADMAP 4) | DONE (261005). Payload 1 (code) `patch_261003_chat14.json` applied + pushed, commit 8dd6c71. Payload 2 (docs) `patch_261003_chat14b.json`, commit 3a2fc4b. Close-out `patch_261005_chat14c.json`: audit `main()` run on the user's machine (ok, 3.9 s), manifest verified, figures reproduced. Findings and decisions: CONTEXT_HANDOFF 4f; research: RESEARCH_NOTES.md |
| C14p1 | 261005 | Currency-graph engine core (S2a) (R) | DONE on apply. Package currency_graph (model, graph projections, cycles, evaluate), 12 tests, preflight tool, docs; INV-17 closed, INV-16 partly answered. Remaining S2 scope split into 14p2-14p4 (ROADMAP). CONTEXT_HANDOFF 4g |

## Patch and commit record

| Patch | Chat | Commit | Notes |
|---|---|---|---|
| patch_261003_chat14.json | C14 | 8dd6c71 | Code: time_policy, FxStore bound normalisation, periods fixes, audit script, tests |
| patch_261003_chat14b.json | C14 | 3a2fc4b | Docs: CONTEXT_HANDOFF, ROADMAP, RESEARCH_NOTES, CHAT_LOG and others |
| patch_261005_chat14c.json | C14 | 1955222 | Close-out: audit run result, DONE status, commit hashes (commit 1955222) |
| patch_261005_chat14p1.json | C14p1 | (pending - record in the next chat's log update) | Code: currency_graph package + tests; docs; requirements.txt |
| patch_261005_chat14p1b.json | C14p1 | (pending - record in the next chat's log update) | chat_preflight.py + test, .gitignore, README notes |
