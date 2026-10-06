# version: 261005
"""
fx_trader/utilities/chat_preflight.py

Builds ONE gitignored file, fx_trader/local/chat_start.txt, to attach (or
paste) at the start of a chat, so common facts are never re-asked
(CONTEXT_HANDOFF Working Conventions, chat-start block).

  STATIC part   fx_trader/local/machine_profile.md - hand-edited, gitignored,
                holds what no command can discover (account tiers, local data
                paths, decisions). Created from a template if missing; never
                overwritten. Edit it when something changes.
  DYNAMIC part  generated each run: UTC/London time, git log -3, status, HEAD
                vs origin/main, Python/OS, optional-library versions, and the
                sandbox manifest check (read-only).

This script contains no personal data and is safe in the public repo.
Short-lived (seconds): deliberately NOT a run_monitor adopter. Failures in a
section are printed in that section, never swallowed.

Run (from anywhere):  python3 fx_trader/utilities/chat_preflight.py
"""

import argparse
import importlib
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FX_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = FX_DIR.parent
LOCAL_DIR = FX_DIR / "local"
PROFILE = LOCAL_DIR / "machine_profile.md"
OUT = LOCAL_DIR / "chat_start.txt"
LIBS = ("psycopg2", "ccxt", "requests", "numpy", "matplotlib", "coverage", "hypothesis")
GIT_TIMEOUT_S = 10
MAX_PROFILE_BYTES = 20_000

PROFILE_TEMPLATE = """# machine_profile.md (gitignored; edit by hand - never committed)
- OS / machine: <fill in>
- Python: <fill in>
- OANDA account type: <fill in, e.g. practice/basic>
- Kraken fee tier: <fill in>
- Paid data/feed subscriptions: <none yet / list>
- Kraken CSV folder path: <fill in>
- Other standing facts / decisions: <fill in>
"""


def run_git(args: list) -> str:
    assert args, "git needs a subcommand"
    assert GIT_TIMEOUT_S > 0, "timeout must be positive"
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"GIT FAILED: {type(exc).__name__}: {exc}"
    return result.stdout.strip() if result.returncode == 0 else f"GIT ERROR {result.returncode}: {result.stderr.strip()}"


def git_block() -> str:
    head, remote = run_git(["rev-parse", "HEAD"]), run_git(["rev-parse", "origin/main"])
    same = "HEAD == origin/main" if head == remote else "HEAD DIFFERS FROM origin/main (or git failed)"
    return "\n".join([
        "git log --oneline -3:", run_git(["log", "--oneline", "-3"]),
        "git status --short: " + (run_git(["status", "--short"]) or "(clean)"), same,
    ])


def library_versions(names: tuple) -> list:
    assert names, "no libraries listed"
    lines = []
    for name in names:
        try:
            module = importlib.import_module(name)
            lines.append(f"{name} {getattr(module, '__version__', '(imports; no __version__)')}")
        except ImportError:
            lines.append(f"{name} NOT INSTALLED")
    return lines


def manifest_block(fx_dir: Path) -> str:
    """Verify the single local sandbox DB against the newest manifest file."""
    manifests = sorted((fx_dir / "reports").glob("sandbox_manifest_*.json"))
    dbs = sorted((fx_dir / "data").glob("sandbox_*.db"))
    if not manifests or len(dbs) != 1:
        return f"manifest check SKIPPED: {len(manifests)} manifest file(s), {len(dbs)} sandbox DB(s) (need >=1 and exactly 1)"
    sys.path.insert(0, str(fx_dir))
    try:
        import os
        os.chdir(fx_dir)
        import audit_sandbox_alignment as au
        con = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
        try:
            diffs = au.diff_manifests(au.load_manifest_file(str(manifests[-1])), au.build_manifest(con))
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - reported in the output, not swallowed
        return f"MANIFEST CHECK FAILED: {type(exc).__name__}: {exc}"
    verdict = "verified_ok: True" if not diffs else "MISMATCH:\n  " + "\n  ".join(diffs)
    return f"manifest {manifests[-1].name} vs {dbs[0].name}: {verdict}"


def profile_text(path: Path) -> str:
    """Contents of the static profile; writes the template if absent (never overwrites)."""
    assert MAX_PROFILE_BYTES > 0, "ceiling must be positive"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        return PROFILE_TEMPLATE + "\n(TEMPLATE CREATED - edit it, then re-run)"
    text = path.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= MAX_PROFILE_BYTES, f"{path} is larger than {MAX_PROFILE_BYTES} bytes"
    return text


def render(profile: str, git: str, libs: list, manifest: str, now: datetime) -> str:
    assert now.tzinfo is not None, "now must be timezone-aware"
    try:
        from zoneinfo import ZoneInfo
        london = now.astimezone(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        london = f"unavailable ({type(exc).__name__})"
    return "\n".join([
        f"=== CHAT START BLOCK (generated {now.isoformat(timespec='seconds')} UTC / {london} London) ===",
        "--- machine profile (static, hand-edited) ---", profile.rstrip(),
        "--- git ---", git,
        f"--- environment ---\nPython {platform.python_version()} on {platform.platform()} ({platform.machine()})",
        "Libraries: " + "; ".join(libs), "--- sandbox manifest ---", manifest, "=== END ===", "",
    ])


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write fx_trader/local/chat_start.txt")
    parser.add_argument("--no-manifest", action="store_true", help="skip the sandbox manifest check")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    manifest = "manifest check SKIPPED (--no-manifest)" if args.no_manifest else manifest_block(FX_DIR)
    text = render(profile_text(PROFILE), git_block(), library_versions(LIBS), manifest, datetime.now(timezone.utc))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"Written: {OUT} (attach or paste this file at the start of the chat)")


if __name__ == "__main__":
    main()
