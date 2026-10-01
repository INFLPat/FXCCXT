# version: 261001
"""
tests/test_sweep_config.py

Method: parse_args/build_config from run_full_sweep called with a temp
sandbox file; asserts exact bound strings, runs-DB name, and the three
loud-rejection paths. Does not run a sweep.

Run: python -m tests.test_sweep_config
"""

import tempfile
from pathlib import Path

from run_full_sweep import build_config, parse_args


def _db() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _args(db, start="2025-07-01", end="2025-12-31", tag="B"):
    return parse_args(["--start", start, "--end", end, "--chat", "13", "--tag", tag, "--sandbox-db", db])


def test_bounds_and_runs_db_name():
    cfg = build_config(_args(_db()), "261001")
    assert cfg["start_ts"] == "2025-07-01T00:00:00.000000000Z", cfg["start_ts"]
    assert cfg["end_ts"] == "2025-12-31T23:59:59.000000000Z", cfg["end_ts"]
    assert cfg["runs_path"] == "data/sweep_runs_250701to251231_chat13_261001_B.db", cfg["runs_path"]
    assert build_config(_args(_db(), tag=""), "261001")["runs_path"] == "data/sweep_runs_250701to251231_chat13_261001.db"
    print("Bounds / runs-DB name assertions passed.")


def test_rejections():
    cases = [
        (lambda: build_config(_args(_db(), start="2025-12-31", end="2025-07-01"), "261001"), "after --end"),
        (lambda: build_config(_args("/nonexistent/x.db"), "261001"), "not found"),
        (lambda: build_config(_args(_db(), tag="bad tag!"), "261001"), "tag"),
    ]
    for call, expected in cases:
        try:
            call()
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    print("Rejection-path assertions passed.")


def test_existing_runs_db_rejected():
    cfg = build_config(_args(_db(), tag="EXISTS"), "261001")
    path = Path(cfg["runs_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    try:
        try:
            build_config(_args(_db(), tag="EXISTS"), "261001")
            raise RuntimeError("expected rejection of an existing runs DB")
        except AssertionError as exc:
            assert "already exists" in str(exc), str(exc)
    finally:
        path.unlink()
    print("Existing-runs-DB rejection assertion passed.")


if __name__ == "__main__":
    test_bounds_and_runs_db_name()
    test_rejections()
    test_existing_runs_db_rejected()
    print("\nAll sweep-config tests passed.")
