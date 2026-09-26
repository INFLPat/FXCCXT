<!-- root_readme (repo root: ~/FXCCXT/README.md) -->
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

Every session: new chat pulls the repo into context -> Claude edits in its
own sandbox -> you download the changed files -> you push to GitHub -> you
refresh the Project's Context panel so the next chat sees it. Never push a
stale bulk copy without confirming the chat's context is current first -
it can silently overwrite work committed by a more recent chat.

### Session handoff format (standard, every session)

At the end of any session that changed files:

1. A **list of every changed/created file, by full repo path** (e.g.
   `fx_trader/backtest/engine.py`), including anything given as a patch
   instruction rather than reproduced in full (see point 3).
2. A **single zip that preserves the repo's folder structure** -
   `fx_trader/...` paths inside the zip, never a flat dump - containing
   every file that was fully reproduced this session.
3. Where a change is a small, precise edit to a large file, Claude may
   give an exact old-text -> new-text patch instruction in the reply
   instead of reproducing the whole file - token cost scales with file
   size, and reproducing an 800-line file to change two lines is
   wasteful. Full reproduction (in the zip) is the default for new files
   or files with substantial changes; for a small patch to an otherwise-
   untouched large file, Claude states the trade-off and asks whether the
   full file should go in the zip anyway, rather than deciding
   unilaterally.
4. For that patch instruction, Claude gives a runnable Python patch
   script (old/new text pairs, applied with an exact-match-count check
   per file, skipping and reporting rather than guessing on a mismatch)
   instead of raw `sed` - portable across machines without worrying about
   BSD vs. GNU `sed` differences, and it fails loudly instead of silently
   mismatching on whitespace.

**When to package the zip**: zipping itself costs almost nothing - the
token cost is in authoring a file's full content, which happens once
whether the zip is built immediately or saved for the end. So: author
each file once (a new/heavily-changed file via full reproduction, any
further edit to an already-created file within the same session as a
small diff, not a full re-output), and only run the zip/present step
once, at the actual end of the session or whenever the person explicitly
wants a download - not after every file, and not speculatively for a
file that's still likely to change again this session.

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
suffix.
