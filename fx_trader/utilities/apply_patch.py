#!/usr/bin/env python3
"""
fx_trader/utilities/apply_patch.py

Reusable, git-tracked session-handoff tool. Reads a JSON patch payload
(produced by Claude at the end of a chat session) and applies it to the
repo: creates new files, patches existing ones with an exact-match-count
safety check, runs the repo's test suite, and stages + commits the result
- stopping short of `git push` by default (see --auto-push).

Repo root is resolved from this file's own location
(fx_trader/utilities/apply_patch.py -> repo root is two levels up), so it
behaves identically whether run from the repo root, from fx_trader/, by
double-clicking apply_patch.command, or with a file dropped onto it.

JSON payload shape:
{
  "commit_message": "one-line summary",
  "operations": [
    {"action": "create", "file": "fx_trader/foo.py", "content": "..."},
    {"action": "patch",  "file": "README.md", "old": "...", "new": "..."}
  ]
}
Every "file" path is relative to repo root. "create" overwrites if the
file already exists (logged, not silently). "patch" requires "old" to
match the target file's current content EXACTLY ONCE - any other count
is skipped and reported, never guessed.

Usage:
    python3 apply_patch.py path/to/patch.json               # apply
    python3 apply_patch.py path/to/patch.json --dry-run      # preview only, writes nothing
    python3 apply_patch.py path/to/patch.json --auto-push    # also run 'git push' after commit

See fx_trader/utilities/README.md for the full workflow this replaces.
"""

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FX_TRADER_DIR = REPO_ROOT / "fx_trader"
PREFLIGHT_MAX_ATTEMPTS = 5  # explicit ceiling on the interactive resolve-and-recheck loop


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    assert args, "_run_git requires at least one git subcommand"
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _print_git_state() -> tuple[bool, bool]:
    """Prints current git status and how far behind origin/main we are.
    Returns (is_dirty, is_behind)."""
    status = _run_git(["status", "--short"])
    print("--- git status --short ---")
    print(status.stdout or "(clean)")
    is_dirty = bool(status.stdout.strip())

    _run_git(["fetch", "origin"])
    behind = _run_git(["log", "--oneline", "HEAD..origin/main"])
    print("--- commits on origin/main not yet local ---")
    print(behind.stdout or "(none - up to date)")
    is_behind = bool(behind.stdout.strip())
    return is_dirty, is_behind


def _resolve_menu(kind: str) -> None:
    """Interactive selector for a dirty-tree or behind-origin state.
    `kind` is 'dirty' or 'behind'. Runs the chosen git action; does not
    re-check itself - preflight()'s loop re-checks after this returns."""
    assert kind in ("dirty", "behind"), f"kind must be 'dirty' or 'behind', got {kind}"
    if kind == "dirty":
        options = {
            "1": ("Abort - I'll handle it manually", None),
            "2": ("Show git status / git diff again", ["diff"]),
            "3": ("Run 'git stash' and continue", ["stash"]),
            "4": ("Type a custom git command", "custom"),
        }
        prompt = "Working tree is not clean."
    else:
        options = {
            "1": ("Abort - I'll handle it manually", None),
            "2": ("Show 'git log --oneline HEAD..origin/main' again", ["log", "--oneline", "HEAD..origin/main"]),
            "3": ("Run 'git pull --ff-only' and continue", ["pull", "--ff-only"]),
            "4": ("Type a custom git command", "custom"),
        }
        prompt = "Local branch is behind origin/main."

    print(f"\n{prompt} Choose how to proceed:")
    for key, (label, _action) in options.items():
        print(f"  {key}) {label}")
    choice = input("Enter 1-4: ").strip()

    if choice not in options:
        print("Unrecognized choice - treating as abort.")
        sys.exit(1)

    _label, action = options[choice]
    if action is None:
        print("Aborting - resolve manually, then re-run this script.")
        sys.exit(1)
    if action == "custom":
        custom = input("git ").strip()
        assert custom, "a custom command is required for option 4"
        action = custom.split()
    result = _run_git(action)
    print(result.stdout)
    if result.returncode != 0:
        print(f"Command failed:\n{result.stderr}")


def preflight() -> None:
    """Loops resolve-and-recheck until the repo is clean and up to date,
    or PREFLIGHT_MAX_ATTEMPTS is hit."""
    for _attempt in range(PREFLIGHT_MAX_ATTEMPTS):
        is_dirty, is_behind = _print_git_state()
        if not is_dirty and not is_behind:
            print("Repo is clean and up to date - proceeding.\n")
            return
        _resolve_menu("dirty" if is_dirty else "behind")
    print(f"Repo still not ready after {PREFLIGHT_MAX_ATTEMPTS} attempts - stopping. Resolve manually.")
    sys.exit(1)


def load_payload(path: Path) -> dict:
    assert path.exists(), f"patch payload not found: {path}"
    data = json.loads(path.read_text())
    assert "operations" in data and data["operations"], "payload must have a non-empty 'operations' list"
    return data


def apply_create(op: dict, dry_run: bool) -> str | None:
    """Writes full content to a file, creating parent dirs and overwriting
    if the file already exists. Returns the repo-relative path touched,
    or None when dry_run (nothing is written)."""
    assert "file" in op and "content" in op, "create op requires 'file' and 'content'"
    target = REPO_ROOT / op["file"]
    if dry_run:
        if target.exists():
            old_lines = target.read_text().splitlines(keepends=True)
            new_lines = op["content"].splitlines(keepends=True)
            diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile="current", tofile="new"))
            print(f"[DRY RUN] {op['file']} would be OVERWRITTEN:\n{diff or '(identical - no-op)'}")
        else:
            print(f"[DRY RUN] {op['file']} would be CREATED (new file, {len(op['content'])} bytes)")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(op["content"])
    print(f"{'OVERWROTE' if existed else 'CREATED  '} {op['file']}")
    return op["file"]


def apply_patch_op(op: dict, dry_run: bool) -> str | None:
    """Exact-match-count patch. Skips (never guesses) on anything other
    than exactly one match of 'old' in the current file content."""
    assert "file" in op and "old" in op and "new" in op, "patch op requires 'file', 'old', 'new'"
    target = REPO_ROOT / op["file"]
    if not target.exists():
        print(f"SKIP {op['file']}: file not found")
        return None
    text = target.read_text()
    count = text.count(op["old"])
    if count != 1:
        print(f"SKIP {op['file']}: expected old text exactly once, found {count} - check manually")
        return None
    if dry_run:
        diff = "".join(difflib.unified_diff(
            op["old"].splitlines(keepends=True), op["new"].splitlines(keepends=True),
            fromfile="old", tofile="new",
        ))
        print(f"[DRY RUN] {op['file']} would be PATCHED:\n{diff}")
        return None
    target.write_text(text.replace(op["old"], op["new"], 1))
    print(f"PATCHED  {op['file']}")
    return op["file"]


def apply_operations(operations: list[dict], dry_run: bool) -> list[str]:
    assert operations, "apply_operations requires at least one operation"
    touched: list[str] = []
    for op in operations:
        action = op.get("action")
        if action == "create":
            result = apply_create(op, dry_run)
        elif action == "patch":
            result = apply_patch_op(op, dry_run)
        else:
            print(f"SKIP unknown action {action!r} for {op.get('file', '?')}")
            result = None
        if result:
            touched.append(result)
    return touched


def run_tests() -> bool:
    """Runs every fx_trader/tests/test_*.py module, returns True iff all
    of them pass. Always run - see module docstring."""
    test_dir = FX_TRADER_DIR / "tests"
    assert test_dir.exists(), f"tests directory not found: {test_dir}"
    modules = sorted(p.stem for p in test_dir.glob("test_*.py"))
    assert modules, "no test_*.py modules found"
    all_passed = True
    for name in modules:
        result = subprocess.run(
            [sys.executable, "-m", f"tests.{name}"], cwd=FX_TRADER_DIR,
            capture_output=True, text=True,
        )
        ok = result.returncode == 0
        all_passed = all_passed and ok
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        if not ok:
            print(result.stdout[-2000:])
            print(result.stderr[-2000:])
    return all_passed


def commit_changes(touched: list[str], suggested_message: str) -> None:
    assert touched, "commit_changes requires at least one touched file"
    print(f"\nSuggested commit message: {suggested_message}")
    typed = input("Press Enter to accept, or type a replacement: ").strip()
    message = typed or suggested_message

    add_result = _run_git(["add", *touched])
    if add_result.returncode != 0:
        print(f"git add failed:\n{add_result.stderr}")
        sys.exit(1)
    print(_run_git(["status", "--short"]).stdout)

    commit_result = _run_git(["commit", "-m", message])
    print(commit_result.stdout)
    if commit_result.returncode != 0:
        print(f"git commit failed:\n{commit_result.stderr}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a Claude session's file changes to this repo.")
    parser.add_argument("payload", type=Path, help="path to the session's patch JSON file")
    parser.add_argument("--dry-run", action="store_true", help="preview changes, write nothing")
    parser.add_argument("--auto-push", action="store_true", help="run 'git push' automatically after commit")
    args = parser.parse_args()

    payload = load_payload(args.payload)

    if not args.dry_run:
        preflight()

    touched = apply_operations(payload["operations"], args.dry_run)

    if args.dry_run:
        print("\nDry run complete - nothing was written.")
        return

    if not touched:
        print("\nNothing applied (every operation was skipped) - nothing to test or commit.")
        return

    print("\n--- Running test suite ---")
    if not run_tests():
        print("\nAt least one test FAILED - not committing. Files were still written to disk; "
              "fix, or 'git restore <file>' to undo, then re-run once tests pass.")
        sys.exit(1)

    commit_changes(touched, payload.get("commit_message", "Session changes"))

    if args.auto_push:
        print("\n--- Pushing ---")
        push_result = _run_git(["push"])
        print(push_result.stdout)
        if push_result.returncode != 0:
            print(f"git push failed - do not force it:\n{push_result.stderr}")
            sys.exit(1)
    else:
        print("\nNot pushed. Run:\n  cd ~/FXCCXT && git push")


if __name__ == "__main__":
    main()
