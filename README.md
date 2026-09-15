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
