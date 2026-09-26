<!-- fx_trader_utilities_readme (fx_trader/utilities/README.md) -->
# apply_patch.py - session handoff tool

Applies a Claude session's file changes to this repo: creates new files,
patches existing ones, runs the test suite, and commits - replacing the
old zip -> staging folder -> `rsync` process. `git push` stays manual by
default (see `--auto-push` below).

## One-time setup

```bash
chmod +x fx_trader/utilities/apply_patch.command
```

## Every session

1. At the end of a chat, Claude gives you a `patch.json` file (see shape
   below) instead of a full zip.
2. Save it anywhere (e.g. `~/Downloads/patch.json`).
3. Run it one of two ways:
   - **Double-click** `fx_trader/utilities/apply_patch.command` -> a
     native "choose file" dialog opens -> pick the JSON.
   - **Terminal**:
     ```bash
     cd ~/FXCCXT
     python3 fx_trader/utilities/apply_patch.py ~/Downloads/patch.json
     ```

Add `--dry-run` first if you want to preview the diff for every operation
without writing anything:
```bash
python3 fx_trader/utilities/apply_patch.py ~/Downloads/patch.json --dry-run
```

## What it does, in order

1. **Preflight**: checks `git status` is clean and the branch isn't
   behind `origin/main`. If either check fails, shows a numbered menu
   (abort / show more detail / a safe fixed action / type a custom `git`
   command) and re-checks after each choice, up to 5 attempts.
2. **Applies every operation** in the payload - `create` (full file,
   overwrites if present) or `patch` (exact old-text -> new-text, only if
   the old text matches the file's current content EXACTLY ONCE; anything
   else is skipped and reported, never guessed).
3. **Runs every `fx_trader/tests/test_*.py` module.** Any failure stops
   here - nothing is committed, but the files that were already written
   stay on disk (fix them, or `git restore <file>` to undo, then re-run).
4. **Commits** - shows the suggested message and `git status --short`,
   lets you accept it, type a replacement, and stages exactly the files
   the payload touched (never `git add .`).
5. **Stops.** Prints `git push` for you to run by hand - unless
   `--auto-push` was passed, in which case it runs `git push` itself and
   reports the result (never force-pushes; a rejected push fails loudly
   with the git error).

## Drag-and-drop, honestly

A plain script file isn't always a valid Finder drop target on macOS -
this depends on Finder/LaunchServices settings that vary by machine and
OS version, so it isn't guaranteed to work reliably. The `.command`
launcher's native file-picker dialog (double-click, then choose the file)
is the dependable default and needs no extra setup. If you specifically
want literal icon-drag-and-drop and it doesn't already work by dragging a
file onto `apply_patch.command` in Finder, the standard fix is wrapping it
in an Automator "Application" (New Document -> Application -> add a "Run
Shell Script" action calling
`python3 "$REPO_ROOT/fx_trader/utilities/apply_patch.py" "$1"` -> save as
an app) - a few clicks, no code, ask if you want the exact steps.

## Patch payload shape

```json
{
  "commit_message": "One-line summary of the session's changes",
  "operations": [
    {"action": "create", "file": "fx_trader/some_new_module.py", "content": "...full file text..."},
    {"action": "patch",  "file": "README.md", "old": "...exact existing text...", "new": "...replacement..."}
  ]
}
```

- `file` paths are always relative to the **repo root** (`~/FXCCXT`), not
  `fx_trader/` - e.g. `"fx_trader/backtest/engine.py"` or `"README.md"`.
- `create` always overwrites if the file already exists (logged as
  `OVERWROTE`, not silently).
- `patch`'s `old` must appear in the target file's CURRENT content exactly
  once. If the file has changed since Claude last saw it, this usually
  means 0 or 2+ matches - the operation is skipped and reported rather
  than applied incorrectly.
