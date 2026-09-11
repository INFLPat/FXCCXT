# FXCCXT

A backtestable, cost-aware FX and crypto trading analysis pipeline: historical data → strategy → backtest → validation (walk-forward / sensitivity / bootstrap) → (eventually, carefully) live trading.

- **Full documentation:** [`fx_trader/README.md`](fx_trader/README.md)
- **Project history, key decisions, current state:** [`fx_trader/CONTEXT_HANDOFF.md`](fx_trader/CONTEXT_HANDOFF.md) — read this before making changes in a new Claude session.

## Working across Claude chats

This repo is the source of truth for code between Claude.ai chat sessions in the FX Project. Claude.ai's GitHub integration is **pull-only** — there's no automatic push back to this repo, on any plan.

Every session: new chat pulls the current repo state into context → Claude edits files in its own sandbox → you download them → you push to GitHub (`git`, or manual re-upload) → you refresh/sync the Project's Context panel so the next chat sees it. Never push a stale bulk copy without confirming the chat's context is current first — see the full note in [`fx_trader/README.md`](fx_trader/README.md#working-across-claude-chats-github-sync-workflow).
