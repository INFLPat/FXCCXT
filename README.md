<!-- root_readme (repo root: ~/FXCCXT/README.md) -->
<!-- version: 261003 -->
# FXCCXT

Backtestable, cost-aware FX and crypto trading analysis pipeline: historical
data -> strategy -> backtest -> validation (walk-forward / sensitivity /
bootstrap / tiered hierarchy) -> (eventually, carefully) live trading.

- **Full documentation:** [`fx_trader/README.md`](fx_trader/README.md)
- **Current state, key decisions:** [`fx_trader/CONTEXT_HANDOFF.md`](fx_trader/CONTEXT_HANDOFF.md) - read this before making changes in a new Claude session.
- **Coding standard for this repo (and standing across all Claude chats):** Holzmann's "Power of Ten" rules - see `CONTEXT_HANDOFF.md` Section 2.

## Working across Claude chats

This repo is the source of truth for code between Claude.ai chat sessions.
**Claude.ai's GitHub integration is pull-only** - no automatic push, on any
plan.

Every session: new chat pulls the repo into context -> Claude prepares a
`patch_YYMMDD_chatNN.json` in its own sandbox -> you apply it with
`fx_trader/utilities/apply_patch.command` (tests run, then a commit is made)
-> you `git push` -> you refresh the Project's Context panel so the next chat
sees it. Never push a stale bulk copy without confirming the chat's context
is current first - it can silently overwrite work committed by a more recent
chat.

### Session handoff format (standard, every session)

At the end of any session that changed files, Claude delivers:

1. A **list of every changed/created/deleted file, by full repo path**.
2. A single **`patch_YYMMDD_chatNN.json`** (session date and chat number, e.g. `patch_260930_chat12.json`; a second or later patch in the same chat takes a letter, e.g. `patch_261003_chat14b.json`) - no zips. Operations:
   `create` (full file, overwrites), `patch` (exact old-text -> new-text, must
   match the current file EXACTLY ONCE or it is skipped and reported) and
   `delete`. `create` is the default for new files or substantial rewrites;
   `patch` for small edits to large files, since token cost scales with file
   size. Keep `old` strings short and single-line where possible; if one is
   skipped, shorten the anchor rather than lengthening it.
3. **How to apply**: double-click `fx_trader/utilities/apply_patch.command` in
   Finder and choose the JSON (preview first with `--dry-run` from a terminal).
   It checks git state, applies the operations, runs every `tests/test_*.py`,
   and commits; `git push` stays manual unless `--auto-push` is passed. Share
   the terminal output back with Claude. Full details:
   `fx_trader/utilities/README.md`.
4. **What to push and sync**: push only changed files, then refresh the
   Context panel.

### Turn-pacing convention: FNORD (standard, every session, by default)

For any reply, in any chat in this project: if the reply has **not**
finished everything asked in the most recent message (a large multi-part
request spanning several replies), end with an explicit status line -
what's done, what's next - so the person can tell "still working, say
continue" apart from a silent cutoff. **Never end a turn with no closing
text at all.** Only a reply that has genuinely finished EVERYTHING asked
ends with the literal word `FNORD` on its own line - borrowed from
walkie-talkie "over" conventions. `FNORD` and a status line are the only
two valid endings - never neither. One honest caveat: a hard length
cutoff mid-reply would also cut off the marker - its absence isn't proof
of failure, only its presence is proof of real completion. This is the
default across every chat in this project already, not something that
needs to be requested each time.

### File versioning (standard, from 260929)

Every file created or edited carries a `YYMMDD` version stamp; no stamp means legacy, stamped when next touched. Importable `.py`, `run_*.py`, tests: stable name + `# version: YYMMDD` on line 1 (line 2 under a shebang). Cross-referenced docs and READMEs: stable name + `<!-- version: YYMMDD -->` first line (READMEs: directly under the marker line). Generated or transient files (patch payloads, logs, charts, DB snapshots): dated filename, e.g. `patch_260930_chat12.json` (patch payloads also carry the chat number, from chat 12). `.gitignore` and other `#`-comment config files carry `# version: YYMMDD` on line 1. Full rule, rationale and the placement details settled 260930: `fx_trader/CONTEXT_HANDOFF.md` Working Conventions.

### Next-chat handoff (standard, every session)

Every session ends with: changed files by full repo path; a drafted opening message for the next one-topic chat; the exact files to attach to that chat (a long list means the scope is too big - split it); and what to apply, push and sync. One topic per chat, finished inside one 24-hour window.

### README marker lines (standard, every README.md in this repo)

The repo has several files all named `README.md` in different folders -
easy to lose track of which one a patch or edit is meant for. Every
`README.md` anywhere in this repo starts with an HTML-comment marker line
naming which one it is, before the title - invisible on GitHub's rendered
page, immediately visible in raw text, `cat`, or any editor:

```
<!-- root_readme (repo root: ~/FXCCXT/README.md) -->
<!-- fx_trader_readme (fx_trader/README.md) -->
<!-- fx_trader_utilities_readme (fx_trader/utilities/README.md) -->
```

Any new `README.md` added anywhere in this repo gets the same treatment -
named after its own path, underscores instead of slashes, `_readme`
suffix. The version line goes directly under the marker line.
