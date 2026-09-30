# version: 260930
"""
tests/test_run_monitor.py

Method: every test writes to a temp dir (never the real logs/), drives
run_monitor through success / failure / interrupt / killed-run / write-
failure paths, and reads the log back with load_runs(). Write failures are
simulated by replacing run_monitor._append_lines. Git tests build a
throwaway repo. Not covered: real macOS ru_maxrss units (only the unit
conversion is tested), real disk-full.

Run: python -m tests.test_run_monitor
"""

import contextlib
import io
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import run_monitor
from run_monitor import count, item, load_records, load_runs, monitored_run, note, recover_records


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="run_monitor_test_"))


def _only_log(d: Path) -> Path:
    logs = list(d.glob("run_log_*.jsonl"))
    assert len(logs) == 1, f"expected exactly one log file, got {logs}"
    return logs[0]


@contextlib.contextmanager
def _quiet():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


@contextlib.contextmanager
def _failing_appends(should_fail):
    """Replace run_monitor._append_lines; should_fail(path) -> bool."""
    original = run_monitor._append_lines

    def fake(path, lines, sync):
        if should_fail(Path(path)):
            raise OSError(28, "No space left on device (simulated)")
        return original(path, lines, sync)

    run_monitor._append_lines = fake
    try:
        yield
    finally:
        run_monitor._append_lines = original


def test_successful_run_records_everything():
    d = _dir()
    with _quiet():
        with monitored_run("demo", log_dir=d, chat=12):
            note("sandbox_db", "x.db")
            count("candles", 100)
            count("candles", 50)
            item("RsiStrategy/GBP_JPY", 1.5, tier4=2)
            item("MacdStrategy/GBP_JPY", 0.5)
    runs, corrupt = load_runs(_only_log(d))
    assert not corrupt and len(runs) == 1
    run = next(iter(runs.values()))
    assert run["status"] == "ok"
    assert run["start"]["meta"] == {"chat": 12} and run["start"]["script"] == "demo"
    assert run["notes"] == {"sandbox_db": "x.db"}
    assert run["end"]["counts"] == {"candles": 150}
    assert len(run["items"]) == 2 and run["items"][0]["data"] == {"tier4": 2}
    summary = run["end"]["item_summary"]
    assert summary["n"] == 2 and abs(summary["total_s"] - 2.0) < 1e-9
    assert summary["min_s"] == 0.5 and summary["max_s"] == 1.5 and summary["lines_dropped"] == 0
    assert run["end"]["duration_s"] >= 0 and run["end"]["cpu_s"] >= 0
    records, _ = load_records(_only_log(d))
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1)), "seq must be contiguous from 1"
    assert {r["run_id"] for r in records} == {run["start"]["run_id"]}
    assert not run_monitor._STACK, "stack must be empty after a run"
    print("Successful-run assertions passed.")


def test_utc_and_london_stamps_agree():
    if run_monitor.LONDON is None:
        print(f"SKIPPED london stamp test: {run_monitor.LONDON_ERROR}")
        return
    d = _dir()
    with _quiet():
        with monitored_run("tz", log_dir=d):
            pass
    start = next(iter(load_runs(_only_log(d))[0].values()))["start"]
    utc = datetime.fromisoformat(start["start_utc"])
    london = datetime.fromisoformat(start["start_london"])
    assert utc == london, "same instant expected"
    assert london.utcoffset().total_seconds() in (0, 3600), "London is GMT or BST"
    print("UTC/London stamp agreement assertion passed.")


def test_london_date_handles_bst_and_gmt():
    if run_monitor.LONDON is None:
        print("SKIPPED london date test (no tz database)")
        return
    summer = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)   # 00:30 BST on 2 Jul
    winter = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)  # 23:30 GMT on 31 Dec
    assert run_monitor._london_date(summer) == "260702", run_monitor._london_date(summer)
    assert run_monitor._london_date(winter) == "261231", run_monitor._london_date(winter)
    print("London-date BST/GMT assertions passed.")


def test_failure_is_recorded_and_reraised():
    d = _dir()
    raised = None
    with _quiet():
        try:
            with monitored_run("boom", log_dir=d):
                item("a", 1.0)
                raise ValueError("bad thing")
        except ValueError as exc:
            raised = exc
    assert raised is not None and str(raised) == "bad thing", "original exception must propagate"
    run = next(iter(load_runs(_only_log(d))[0].values()))
    assert run["status"] == "failed" and run["end"]["error_type"] == "ValueError"
    assert run["end"]["error_msg"] == "bad thing" and len(run["items"]) == 1
    print("Failure-recorded-and-reraised assertions passed.")


def test_interrupt_and_systemexit_statuses():
    d = _dir()
    cases = [(KeyboardInterrupt, "interrupted"), (lambda: SystemExit(0), "ok"), (lambda: SystemExit(2), "failed")]
    with _quiet():
        for make, _expected in cases:
            exc = make() if callable(make) and make is not KeyboardInterrupt else make()
            try:
                with monitored_run("c", log_dir=d):
                    raise exc
            except (KeyboardInterrupt, SystemExit):
                pass
    runs, _ = load_runs(_only_log(d))
    statuses = sorted(r["status"] for r in runs.values())
    assert statuses == sorted(e for _, e in cases), statuses
    print(f"Interrupt/SystemExit statuses: {statuses}")
    print("Interrupt/SystemExit assertions passed.")


def test_decorator_passes_return_value_and_args():
    d = _dir()

    @monitored_run("decorated", log_dir=d)
    def work(a, b=1):
        note("seen", a + b)
        return a * 10

    with _quiet():
        assert work(2, b=3) == 20
    run = next(iter(load_runs(_only_log(d))[0].values()))
    assert run["notes"]["seen"] == 5 and run["status"] == "ok"
    print("Decorator return-value assertion passed.")


def test_killed_run_keeps_everything_up_to_the_crash():
    d = _dir()
    run = monitored_run("killed", log_dir=d)
    with _quiet():
        run.__enter__()
        note("window", "22Q1-26Q1")
        item("step1", 2.0)
        runs, _ = load_runs(run.path)          # read BEFORE any exit: simulates a kill
        r = next(iter(runs.values()))
        assert r["status"] == "incomplete" and r["end"] is None
        assert r["notes"] == {"window": "22Q1-26Q1"} and len(r["items"]) == 1
        run.__exit__(None, None, None)
    print("Killed-run retention assertions passed.")


def test_truncated_last_line_is_reported_not_hidden():
    d = _dir()
    with _quiet():
        with monitored_run("t", log_dir=d):
            pass
    log = _only_log(d)
    with open(log, "a", encoding="utf-8") as f:
        f.write('{"record_type":"item","run_id":"x","se')   # cut mid-write
    runs, corrupt = load_runs(log)
    assert corrupt == [3] and len(runs) == 1, (corrupt, len(runs))
    print("Truncated-line reporting assertion passed.")


def test_transient_write_failure_queues_then_flushes_in_order():
    d = _dir()
    state = {"fail": False}
    with _quiet() as (_out, err), _failing_appends(lambda p: state["fail"] and p.parent == d):
        with monitored_run("flaky", log_dir=d):
            item("ok1", 1.0)
            state["fail"] = True
            item("lost_for_now", 1.0)
            item("lost_for_now_2", 1.0)
            state["fail"] = False
            item("ok2", 1.0)
    assert "RUN MONITOR: cannot write" in err.getvalue(), "failure must be loud"
    records, corrupt = load_records(_only_log(d))
    assert not corrupt
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1)), "no gaps, in order"
    labels = [r["label"] for r in records if r["record_type"] == "item"]
    assert labels == ["ok1", "lost_for_now", "lost_for_now_2", "ok2"], labels
    end = records[-1]
    assert end["record_type"] == "end" and end["log_write_failures"] == 2
    print("Transient write-failure queue-and-flush assertions passed.")


def test_permanent_write_failure_falls_back_then_recovers():
    d, fb = _dir(), _dir()
    original_fb = run_monitor.FALLBACK_DIR
    run_monitor.FALLBACK_DIR = fb
    try:
        with _quiet() as (_out, err), _failing_appends(lambda p: p.parent == d):
            with monitored_run("diskfull", log_dir=d) as run:
                log_path = run.path
                item("a", 1.0)
                item("b", 2.0)
    finally:
        run_monitor.FALLBACK_DIR = original_fb
    assert not log_path.exists(), "log itself must have received nothing"
    fallbacks = list(fb.glob("run_monitor_unsaved_*.jsonl"))
    assert len(fallbacks) == 1
    text = err.getvalue()
    assert "NOT in" in text and "Recover: python run_monitor.py recover" in text and run_monitor.RECORD_MARKER in text

    appended, _skipped = recover_records(fallbacks[0], log_path)
    assert appended == 4, f"start + 2 items + end expected, got {appended}"
    again, _ = recover_records(fallbacks[0], log_path)
    assert again == 0, "recovery must be idempotent"
    run_rec = next(iter(load_runs(log_path)[0].values()))
    assert run_rec["status"] == "ok" and len(run_rec["items"]) == 2
    print("Permanent failure -> fallback file -> recover assertions passed.")


def test_fallback_also_failing_prints_records_and_recovers_from_terminal_text():
    d = _dir()
    with _quiet() as (_out, err), _failing_appends(lambda p: True):
        with monitored_run("everything_fails", log_dir=d) as run:
            log_path = run.path
            item("a", 1.0)
    text = err.getvalue()
    assert text.count(run_monitor.RECORD_MARKER) >= 3, "all records must be printed when the fallback fails"
    transcript = _dir() / "terminal.txt"
    transcript.write_text("some shell noise\n" + text + "\n$ prompt\n", encoding="utf-8")
    appended, skipped = recover_records(transcript, log_path)
    assert appended == 3 and skipped > 0, (appended, skipped)   # start, item, end (end copy is a duplicate)
    run_rec = next(iter(load_runs(log_path)[0].values()))
    assert run_rec["status"] == "ok" and len(run_rec["items"]) == 1
    print("Terminal-transcript recovery assertions passed.")


def test_load_runs_dedupes_partial_write_duplicates():
    d = _dir()
    with _quiet():
        with monitored_run("dup", log_dir=d):
            item("a", 1.0)
    log = _only_log(d)
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(lines + [lines[1]]) + "\n", encoding="utf-8")   # duplicate the item line
    run = next(iter(load_runs(log)[0].values()))
    assert len(run["items"]) == 1, "duplicate (run_id, seq) must be collapsed"
    print("Duplicate-line dedupe assertion passed.")


def test_nesting_links_parent_and_misuse_is_loud():
    d = _dir()
    with _quiet():
        with monitored_run("outer", log_dir=d):
            with monitored_run("inner", log_dir=d):
                item("x", 0.1)
    runs, _ = load_runs(_only_log(d))
    by_script = {r["start"]["script"]: r for r in runs.values()}
    assert by_script["inner"]["start"]["parent_run_id"] == by_script["outer"]["start"]["run_id"]
    assert by_script["outer"]["start"]["parent_run_id"] is None
    try:
        note("k", "v")
        raise AssertionError("note() outside a run must raise")
    except RuntimeError:
        pass
    with _quiet():
        with monitored_run("neg", log_dir=d):
            try:
                item("bad", -1.0)
                raise AssertionError("negative seconds must be rejected")
            except AssertionError as exc:
                assert "negative" in str(exc)
    print("Nesting / misuse assertions passed.")


def test_item_line_ceiling_keeps_summary_exact():
    d = _dir()
    original = run_monitor.MAX_ITEMS_PER_RUN
    run_monitor.MAX_ITEMS_PER_RUN = 3
    try:
        with _quiet():
            with monitored_run("many", log_dir=d):
                for i in range(5):
                    item(f"i{i}", 1.0)
    finally:
        run_monitor.MAX_ITEMS_PER_RUN = original
    run = next(iter(load_runs(_only_log(d))[0].values()))
    assert len(run["items"]) == 3
    summary = run["end"]["item_summary"]
    assert summary["n"] == 5 and abs(summary["total_s"] - 5.0) < 1e-9 and summary["lines_dropped"] == 2
    print("Item-line ceiling assertions passed.")


def test_rss_unit_conversion():
    assert abs(run_monitor._rss_to_mb(1024 * 1024, "darwin") - 1.0) < 1e-9, "macOS reports bytes"
    assert abs(run_monitor._rss_to_mb(1024, "linux") - 1.0) < 1e-9, "Linux reports KiB"
    print("ru_maxrss unit-conversion assertions passed (real macOS value NOT verified).")


def test_git_info_repo_and_non_repo():
    if shutil.which("git") is None:
        print("SKIPPED git test: git not installed")
        return
    original = run_monitor.REPO_ROOT
    repo, plain = _dir(), _dir()
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.t", "commit", "-q", "--allow-empty", "-m", "x"],
                       cwd=repo, check=True)
        run_monitor.REPO_ROOT = repo
        clean = run_monitor._git_info()
        assert clean["git_commit"] and clean["git_dirty"] is False and clean["git_error"] is None, clean
        (repo / "new.txt").write_text("x")
        assert run_monitor._git_info()["git_dirty"] is True
        run_monitor.REPO_ROOT = plain
        broken = run_monitor._git_info()
        assert broken["git_commit"] is None and broken["git_error"], broken
    finally:
        run_monitor.REPO_ROOT = original
    print("Git info (repo / dirty / non-repo) assertions passed.")


if __name__ == "__main__":
    test_successful_run_records_everything()
    test_utc_and_london_stamps_agree()
    test_london_date_handles_bst_and_gmt()
    test_failure_is_recorded_and_reraised()
    test_interrupt_and_systemexit_statuses()
    test_decorator_passes_return_value_and_args()
    test_killed_run_keeps_everything_up_to_the_crash()
    test_truncated_last_line_is_reported_not_hidden()
    test_transient_write_failure_queues_then_flushes_in_order()
    test_permanent_write_failure_falls_back_then_recovers()
    test_fallback_also_failing_prints_records_and_recovers_from_terminal_text()
    test_load_runs_dedupes_partial_write_duplicates()
    test_nesting_links_parent_and_misuse_is_loud()
    test_item_line_ceiling_keeps_summary_exact()
    test_rss_unit_conversion()
    test_git_info_repo_and_non_repo()
    print("\nAll run_monitor tests passed.")
