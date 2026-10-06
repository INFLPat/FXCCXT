# version: 261005
"""
tests/test_chat_preflight.py

Method: utilities/ is not a package, so the script is loaded by path. Only
pure helpers are exercised, against temp dirs and injected strings (hermetic:
no git call, no real sandbox, no real local/ files).

Run: python3 -m tests.test_chat_preflight
"""

import importlib.util
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "utilities" / "chat_preflight.py"
spec = importlib.util.spec_from_file_location("chat_preflight", SCRIPT)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def test_library_versions_reports_installed_and_missing():
    lines = cp.library_versions(("json", "no_such_module_zz9"))
    assert lines[0].startswith("json ") and "NOT INSTALLED" not in lines[0]
    assert lines[1] == "no_such_module_zz9 NOT INSTALLED"
    print("library_versions assertions passed.")


def test_profile_template_created_once_and_never_overwritten():
    path = Path(tempfile.mkdtemp()) / "local" / "machine_profile.md"
    first = cp.profile_text(path)
    assert "TEMPLATE CREATED" in first and path.exists()
    path.write_text("- OANDA account type: practice/basic\n", encoding="utf-8")
    assert cp.profile_text(path) == "- OANDA account type: practice/basic\n", "existing profile must be returned untouched"
    print("profile template assertions passed.")


def test_render_contains_every_section_and_rejects_naive_time():
    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    text = cp.render("PROFILE-X", "GIT-X", ["numpy 2.5.3"], "MANIFEST-X", now)
    for token in ("PROFILE-X", "GIT-X", "numpy 2.5.3", "MANIFEST-X", "2026-10-05T12:00:00", "=== END ==="):
        assert token in text, token
    try:
        cp.render("p", "g", [], "m", datetime(2026, 10, 5))
        raise RuntimeError("expected rejection of a naive datetime")
    except AssertionError:
        pass
    print("render assertions passed.")


def test_manifest_block_skips_cleanly_when_files_are_missing():
    empty = Path(tempfile.mkdtemp())
    assert cp.manifest_block(empty).startswith("manifest check SKIPPED")
    print("manifest-skip assertion passed.")


if __name__ == "__main__":
    test_library_versions_reports_installed_and_missing()
    test_profile_template_created_once_and_never_overwritten()
    test_render_contains_every_section_and_rejects_naive_time()
    test_manifest_block_skips_cleanly_when_files_are_missing()
    print("\nAll chat_preflight tests passed.")
