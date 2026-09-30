# version: 260930
"""
run_monitor.py  (fx_trader/run_monitor.py)

Run monitor: long-running scripts log duration and metadata as JSON Lines,
so timelines and efficiency gains can be benchmarked (ROADMAP.md chat 15's
compute-budget dry run reads these logs).

ADOPTION (a few lines per script):
    from run_monitor import count, item, monitored_run, note

    @monitored_run("run_full_sweep")          # or: with monitored_run("x"):
    def main():
        note("sandbox_db", SANDBOX_DB)        # one-off metadata
        count("candles", len(candles))        # running totals
        item("RsiStrategy/GBP_JPY", seconds)  # one timed unit of work

LOG: fx_trader/logs/run_log_YYMMDD.jsonl (gitignored, local only - never the
cloud DB). YYMMDD is the Europe/London date the run STARTED. One file per
day; every record of one run goes to the same file.

RECORDS (one JSON object per line; every record has schema_version,
record_type, run_id, seq - seq is unique per run and is the dedupe key):
  start  script, argv, start_utc, start_london, meta, git_commit, git_dirty,
         git_error, python, platform, machine, cpu_count, node, pid, cwd,
         parent_run_id, tz_error
  note   key, value            (streamed immediately)
  item   label, seconds, elapsed_s, data   (streamed immediately)
  end    status (ok|failed|interrupted), error_type, error_msg, end_utc,
         end_london, duration_s, cpu_s, peak_rss_mb, notes, counts,
         item_summary {n, total_s, min_s, max_s, lines_dropped},
         log_write_failures
A run with a start record and no end record = killed/crashed/power loss
(status "incomplete" in load_runs). Start, notes and items are written as
they happen, so a killed run keeps everything up to the crash.

TIMES: stored twice - *_utc (canonical, sortable, what a SQL timestamptz
column should ingest) and *_london (Europe/London, with BST/GMT offset, for
reading). If the platform has no tz database, *_london is None and tz_error
says why (never silent).

SQL LATER: each line is flat scalars plus small dicts; load_runs() already
merges start/note/item/end by run_id, so a future loader is a mapping from
that structure to columns + JSONB, not a re-design.

NOT COVERED: an always-on live runner needs heartbeats/events, not one run
record (ROADMAP L8, chat 30 decides). The live audit log (L6) is a separate,
accountant-grade record and must not be this file.

LOG-WRITE FAILURE: the run is never crashed by it. Records queue in memory
and are retried on every write; at the end any still-unwritten records go to
a fallback file in the system temp dir and are printed to stderr (prefixed
RUN_MONITOR_RECORD). Recover with:
    python run_monitor.py recover <fallback-file-or-saved-terminal-text> <log-file>
Idempotent: records already in the log (same run_id+seq) are skipped.

CLI:
    python run_monitor.py summary <log-file>
    python run_monitor.py recover <source> <log-file>
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ContextDecorator
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
LOG_DIR = MODULE_DIR / "logs"
FALLBACK_DIR = Path(tempfile.gettempdir())
RECORD_MARKER = "RUN_MONITOR_RECORD "
MAX_ITEMS_PER_RUN = 100_000
MAX_NOTES = 200
MAX_PENDING = 20_000
MAX_DEPTH = 8
MAX_LOG_LINES = 2_000_000
ERROR_MSG_MAX = 500
GIT_TIMEOUT_S = 5
RECORD_TYPES = ("start", "note", "item", "end")

_STACK: list = []


def _load_london():
    """(tzinfo, None) or (None, reason). Reason is recorded, not hidden."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/London"), None
    except (ImportError, KeyError) as exc:  # ZoneInfoNotFoundError subclasses KeyError
        return None, f"{type(exc).__name__}: {exc}"


LONDON, LONDON_ERROR = _load_london()


def _stamp(dt_utc: datetime) -> tuple[str, str | None]:
    """(utc_iso, london_iso_or_None) for a UTC datetime."""
    assert dt_utc.tzinfo is not None, "timestamp must be timezone-aware"
    assert dt_utc.utcoffset().total_seconds() == 0, "expected a UTC datetime"
    london = dt_utc.astimezone(LONDON).isoformat(timespec="seconds") if LONDON is not None else None
    return dt_utc.isoformat(timespec="seconds"), london


def _london_date(dt_utc: datetime) -> str:
    """YYMMDD of the Europe/London calendar date (UTC date if no tz database)."""
    assert dt_utc.tzinfo is not None, "timestamp must be timezone-aware"
    assert dt_utc.utcoffset().total_seconds() == 0, "expected a UTC datetime"
    local = dt_utc.astimezone(LONDON) if LONDON is not None else dt_utc
    return local.strftime("%y%m%d")


def _rss_to_mb(raw: float, platform_name: str) -> float:
    """ru_maxrss is KiB on Linux, bytes on macOS."""
    assert raw >= 0, "ru_maxrss cannot be negative"
    assert isinstance(platform_name, str), "platform_name must be a string"
    divisor = 1024 * 1024 if platform_name == "darwin" else 1024
    return raw / divisor


def _peak_rss_mb() -> float | None:
    """Process-lifetime peak memory, or None where `resource` does not exist
    (Windows). Process-wide, not per nested run."""
    try:
        import resource
    except ImportError:
        return None
    return _rss_to_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sys.platform)


def _git(args: list[str]) -> str:
    assert args, "_git requires a subcommand"
    assert REPO_ROOT.exists(), f"repo root missing: {REPO_ROOT}"
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(f"git {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def _git_info() -> dict:
    """Short commit + dirty flag, or None values with the reason in git_error."""
    info = {"git_commit": None, "git_dirty": None, "git_error": None}
    try:
        commit = _git(["rev-parse", "--short", "HEAD"]).strip()
        dirty = bool(_git(["status", "--porcelain"]).strip())
    except (OSError, subprocess.SubprocessError) as exc:
        info["git_error"] = f"{type(exc).__name__}: {exc}"
        return info
    info["git_commit"], info["git_dirty"] = commit, dirty
    return info


def _append_lines(path: Path, lines: list[str], sync: bool) -> None:
    """Append lines to path (raises OSError on failure)."""
    assert lines, "nothing to write"
    assert len(lines) <= MAX_LOG_LINES, "too many lines in one write"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
        f.flush()
        if sync:
            os.fsync(f.fileno())


def _classify(exc_type, exc) -> tuple[str, str | None, str | None]:
    """(status, error_type, error_msg) from __exit__'s arguments."""
    assert exc_type is None or isinstance(exc_type, type), "exc_type must be a class or None"
    assert ERROR_MSG_MAX > 0, "ERROR_MSG_MAX must be positive"
    if exc_type is None:
        return "ok", None, None
    if issubclass(exc_type, KeyboardInterrupt):
        return "interrupted", exc_type.__name__, None
    if issubclass(exc_type, SystemExit):
        code = getattr(exc, "code", None)
        if code in (None, 0):
            return "ok", None, None
        return "failed", "SystemExit", f"exit code {code}"
    return "failed", exc_type.__name__, str(exc)[:ERROR_MSG_MAX]


class monitored_run(ContextDecorator):
    """Context manager / decorator: one monitored run. Never suppresses an
    exception. `log_dir` overrides LOG_DIR (used by tests); other keyword
    arguments are stored as `meta` in the start record."""

    def __init__(self, script: str, log_dir=None, **meta):
        assert script and isinstance(script, str), "script name must be a non-empty string"
        assert all(isinstance(k, str) for k in meta), "meta keys must be strings"
        self.script = script
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.meta = meta
        self._reset_state()

    def _reset_state(self) -> None:
        assert self.script, "script name lost"
        assert isinstance(self.meta, dict), "meta must be a dict"
        self.run_id = None
        self.parent_id = None
        self.path = None
        self.seq = 0
        self.notes: dict = {}
        self.counts: dict = {}
        self.item_n = 0
        self.item_total = 0.0
        self.item_min = None
        self.item_max = None
        self.lines_dropped = 0
        self.pending: list[str] = []
        self.write_failures = 0
        self.warned = False
        self._t0 = 0.0
        self._cpu0 = 0.0

    def __enter__(self):
        assert len(_STACK) < MAX_DEPTH, f"monitored_run nesting deeper than {MAX_DEPTH}"
        assert self not in _STACK, "this monitored_run instance is already active"
        self._reset_state()
        now = datetime.now(timezone.utc)
        self.run_id = uuid.uuid4().hex
        self.parent_id = _STACK[-1].run_id if _STACK else None
        base = self.log_dir if self.log_dir is not None else LOG_DIR
        self.path = base / f"run_log_{_london_date(now)}.jsonl"
        self._t0 = time.perf_counter()
        self._cpu0 = time.process_time()
        _STACK.append(self)
        utc, london = _stamp(now)
        self._emit("start", {
            "script": self.script, "parent_run_id": self.parent_id, "argv": sys.argv,
            "start_utc": utc, "start_london": london, "tz_error": LONDON_ERROR, "meta": self.meta,
            **_git_info(), "python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(), "node": platform.node(),
            "pid": os.getpid(), "cwd": os.getcwd(),
        }, sync=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        assert _STACK and _STACK[-1] is self, "monitored_run exited out of order"
        assert self.run_id is not None, "exit without enter"
        try:
            self._finish(exc_type, exc)
        finally:
            _STACK.pop()
        return False

    def _emit(self, record_type: str, fields: dict, sync: bool = False, droppable: bool = False) -> None:
        assert record_type in RECORD_TYPES, f"unknown record type {record_type}"
        assert self.run_id is not None, "_emit outside an active run"
        if droppable and len(self.pending) >= MAX_PENDING:
            self.lines_dropped += 1
            return
        self.seq += 1
        record = {"schema_version": SCHEMA_VERSION, "record_type": record_type,
                  "run_id": self.run_id, "seq": self.seq, **fields}
        self.pending.append(json.dumps(record, default=str, separators=(",", ":")))
        self._flush(sync)

    def _flush(self, sync: bool) -> bool:
        """Try to write every pending line. On failure keep them all (a
        partial write can duplicate lines - harmless, dedupe is by run_id+seq)."""
        assert self.path is not None, "no log path"
        assert len(self.pending) <= MAX_PENDING + MAX_NOTES + 10, "pending queue over its ceiling"
        if not self.pending:
            return True
        try:
            _append_lines(self.path, self.pending, sync)
        except OSError as exc:
            self.write_failures += 1
            self._warn_once(exc)
            return False
        self.pending = []
        return True

    def _warn_once(self, exc: Exception) -> None:
        assert exc is not None, "warning needs an exception"
        assert self.path is not None, "no log path"
        if self.warned:
            return
        self.warned = True
        print(
            f"\n!!! RUN MONITOR: cannot write {self.path}: {type(exc).__name__}: {exc}\n"
            "!!! The run continues. Records are queued in memory and retried on every write\n"
            "!!! and at the end; if still unwritable they go to a fallback file and stderr.",
            file=sys.stderr,
        )

    def _note(self, key: str, value) -> None:
        assert isinstance(key, str) and key, "note key must be a non-empty string"
        assert key in self.notes or len(self.notes) < MAX_NOTES, f"more than {MAX_NOTES} distinct notes"
        self.notes[key] = value
        self._emit("note", {"key": key, "value": value})

    def _count(self, key: str, n: float) -> None:
        assert isinstance(key, str) and key, "count key must be a non-empty string"
        assert key in self.counts or len(self.counts) < MAX_NOTES, f"more than {MAX_NOTES} distinct counts"
        self.counts[key] = self.counts.get(key, 0) + n

    def _item(self, label: str, seconds: float, data: dict) -> None:
        assert isinstance(label, str) and label, "item label must be a non-empty string"
        assert seconds >= 0, "item seconds cannot be negative"
        self.item_n += 1
        self.item_total += seconds
        self.item_min = seconds if self.item_min is None else min(self.item_min, seconds)
        self.item_max = seconds if self.item_max is None else max(self.item_max, seconds)
        if self.item_n > MAX_ITEMS_PER_RUN:
            self.lines_dropped += 1
            return
        self._emit("item", {
            "label": label, "seconds": round(seconds, 4),
            "elapsed_s": round(time.perf_counter() - self._t0, 3), "data": data,
        }, droppable=True)

    def _finish(self, exc_type, exc) -> None:
        status, err_type, err_msg = _classify(exc_type, exc)
        utc, london = _stamp(datetime.now(timezone.utc))
        duration = time.perf_counter() - self._t0
        self._emit("end", {
            "script": self.script, "status": status, "error_type": err_type, "error_msg": err_msg,
            "end_utc": utc, "end_london": london, "duration_s": round(duration, 3),
            "cpu_s": round(time.process_time() - self._cpu0, 3), "peak_rss_mb": _peak_rss_mb(),
            "notes": self.notes, "counts": self.counts,
            "item_summary": {"n": self.item_n, "total_s": round(self.item_total, 3),
                             "min_s": self.item_min, "max_s": self.item_max,
                             "lines_dropped": self.lines_dropped},
            "log_write_failures": self.write_failures,
        }, sync=True)
        if self.pending:
            self._rescue()
        print(f"[run_monitor] {self.script}: {status} in {duration:.1f}s -> {self.path}")

    def _rescue(self) -> None:
        """End-of-run: pending lines could not be written to the log."""
        assert self.pending, "nothing to rescue"
        assert self.path is not None, "no log path"
        fallback = FALLBACK_DIR / f"run_monitor_unsaved_{self.run_id}.jsonl"
        try:
            _append_lines(fallback, self.pending, sync=True)
            where = f"saved to {fallback}"
        except OSError as exc:
            where = f"fallback {fallback} ALSO failed ({exc}); every record is printed below"
            for line in self.pending:
                print(RECORD_MARKER + line, file=sys.stderr)
        print(
            f"\n!!! RUN MONITOR: {len(self.pending)} record(s) NOT in {self.path}: {where}\n"
            f"!!! Copy of the end record (save this terminal output if in doubt):\n"
            f"{RECORD_MARKER}{self.pending[-1]}\n"
            f"!!! Recover: python run_monitor.py recover <source> {self.path}",
            file=sys.stderr,
        )


def _current() -> "monitored_run":
    if not _STACK:
        raise RuntimeError("run_monitor note/count/item called outside a monitored_run")
    return _STACK[-1]


def note(key: str, value) -> None:
    """One-off metadata (sandbox window, db path, grid size...)."""
    _current()._note(key, value)


def count(key: str, n: float = 1) -> None:
    """Running total (candles processed, runs completed...)."""
    _current()._count(key, n)


def item(label: str, seconds: float, **data) -> None:
    """One timed unit of work, streamed to the log immediately."""
    _current()._item(label, seconds, data)


def load_records(path) -> tuple[list[dict], list[int]]:
    """(records, corrupt_line_numbers). A truncated last line from a killed
    run is reported in the second list, never dropped silently."""
    path = Path(path)
    assert path.exists(), f"log not found: {path}"
    assert path.is_file(), f"not a file: {path}"
    records: list[dict] = []
    corrupt: list[int] = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            assert n <= MAX_LOG_LINES, f"log has more than {MAX_LOG_LINES} lines"
            text = line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError:
                corrupt.append(n)
    return records, corrupt


def _dedupe(records: list[dict]) -> list[dict]:
    assert isinstance(records, list), "records must be a list"
    assert len(records) <= MAX_LOG_LINES, "too many records"
    seen: set = set()
    unique = []
    for rec in records:
        key = (rec.get("run_id"), rec.get("seq"))
        if key not in seen:
            seen.add(key)
            unique.append(rec)
    return unique


def load_runs(path) -> tuple[dict, list[int]]:
    """({run_id: {start, notes, items, end, status}}, corrupt_lines). status
    is the end record's status, or 'incomplete' (killed/crashed)."""
    records, corrupt = load_records(path)
    runs: dict = {}
    for rec in _dedupe(records):
        run = runs.setdefault(rec["run_id"], {"start": None, "notes": {}, "items": [], "end": None})
        kind = rec["record_type"]
        if kind == "start":
            run["start"] = rec
        elif kind == "note":
            run["notes"][rec["key"]] = rec["value"]
        elif kind == "item":
            run["items"].append(rec)
        elif kind == "end":
            run["end"] = rec
        else:
            raise ValueError(f"unknown record_type {kind!r} in {path}")
    for run in runs.values():
        run["status"] = run["end"]["status"] if run["end"] else "incomplete"
    return runs, corrupt


def _parse_recovery_line(line: str) -> dict | None:
    """A record from a fallback-file line or a saved terminal line (marker
    anywhere in the line), else None (terminal noise, truncated line)."""
    assert isinstance(line, str), "line must be a string"
    assert RECORD_MARKER, "marker missing"
    idx = line.find(RECORD_MARKER)
    text = (line[idx + len(RECORD_MARKER):] if idx >= 0 else line).strip()
    if not text.startswith("{"):
        return None
    try:
        rec = json.loads(text)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) and "run_id" in rec and "seq" in rec else None


def recover_records(source, dest) -> tuple[int, int]:
    """Append records found in `source` (fallback .jsonl, or saved terminal
    text) to `dest` unless (run_id, seq) is already there. Returns
    (appended, skipped_lines). Idempotent."""
    source, dest = Path(source), Path(dest)
    assert source.exists(), f"source not found: {source}"
    lines = source.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX_LOG_LINES, "source has too many lines"
    found, skipped = [], 0
    for line in lines:
        rec = _parse_recovery_line(line)
        if rec is None:
            skipped += 1
        else:
            found.append(rec)
    existing = {(r.get("run_id"), r.get("seq")) for r in load_records(dest)[0]} if dest.exists() else set()
    new_lines = [
        json.dumps(r, default=str, separators=(",", ":")) for r in _dedupe(found)
        if (r.get("run_id"), r.get("seq")) not in existing
    ]
    if new_lines:
        _append_lines(dest, new_lines, sync=True)
    return len(new_lines), skipped


def _print_summary(path) -> None:
    runs, corrupt = load_runs(path)
    assert isinstance(runs, dict), "load_runs must return a dict"
    print(f"{len(runs)} run(s) in {path}")
    ordered = sorted(runs.values(), key=lambda r: (r["start"] or {}).get("start_utc", ""))
    for run in ordered:
        start, end = run["start"] or {}, run["end"] or {}
        counts = end.get("counts") or {}
        print(
            f"{start.get('start_london') or start.get('start_utc', '?'):<26} {start.get('script', '?'):<28} "
            f"{run['status']:<11} {end.get('duration_s', '?')}s  cpu={end.get('cpu_s', '?')}s  "
            f"items={len(run['items'])} counts={counts} commit={start.get('git_commit')}"
            f"{'+dirty' if start.get('git_dirty') else ''}"
        )
    if corrupt:
        print(f"WARNING: unreadable line(s) {corrupt[:20]} (truncated write from a killed run?)")


def main(argv: list[str]) -> int:
    assert isinstance(argv, list), "argv must be a list"
    usage = "usage: python run_monitor.py summary <log> | recover <source> <log>"
    if len(argv) == 2 and argv[0] == "summary":
        _print_summary(argv[1])
        return 0
    if len(argv) == 3 and argv[0] == "recover":
        appended, skipped = recover_records(argv[1], argv[2])
        print(f"appended {appended} record(s); skipped {skipped} non-record line(s)")
        return 0
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
