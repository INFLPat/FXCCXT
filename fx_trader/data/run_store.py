import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from backtest.engine import BacktestResult, Trade

DEFAULT_LOCAL_URL = "sqlite:///data/runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    strategy_name    TEXT NOT NULL,
    params           TEXT NOT NULL,
    instrument       TEXT NOT NULL,
    granularity      TEXT NOT NULL,
    cost_model       TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    ending_balance   REAL NOT NULL,
    period_start     TEXT,
    period_end       TEXT,
    validation_stage TEXT NOT NULL,
    metrics          TEXT,
    notes            TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    trade_id         TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    side             TEXT NOT NULL,
    units            REAL NOT NULL,
    entry_time       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    exit_time        TEXT,
    exit_price       REAL,
    commission_paid  REAL NOT NULL,
    reason_open      TEXT,
    reason_close     TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id          TEXT PRIMARY KEY,
    trade_id         TEXT NOT NULL,
    units            REAL NOT NULL,
    price            REAL NOT NULL,
    timestamp        TEXT NOT NULL,
    commission       REAL NOT NULL,
    reason           TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades (run_id);
CREATE INDEX IF NOT EXISTS idx_fills_trade ON fills (trade_id);
CREATE INDEX IF NOT EXISTS idx_runs_lookup ON runs (strategy_name, instrument, validation_stage);
"""


@dataclass
class RunRecord:
    run_id: str
    created_at: str
    strategy_name: str
    params: dict
    instrument: str
    granularity: str
    cost_model: dict
    starting_balance: float
    ending_balance: float
    period_start: str | None
    period_end: str | None
    validation_stage: str
    metrics: dict | None
    notes: str


class RunStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("RUNS_DATABASE_URL") or DEFAULT_LOCAL_URL
        scheme = urlparse(self.database_url).scheme
        self.backend = "postgres" if scheme.startswith("postgres") else "sqlite"
        self._ph = "%s" if self.backend == "postgres" else "?"

        if self.backend == "postgres" and psycopg2 is None:
            raise ImportError(
                "psycopg2 is required for a Postgres database_url. "
                "Install it with: pip install psycopg2-binary"
            )
        if self.backend == "sqlite":
            path = self.database_url.replace("sqlite:///", "")
            if path not in (":memory:", ""):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_path = path

        with self._connect() as conn:
            cur = conn.cursor()
            if self.backend == "sqlite":
                conn.executescript(SCHEMA)
            else:
                cur.execute(SCHEMA)

    @contextmanager
    def _connect(self):
        import sqlite3

        if self.backend == "sqlite":
            conn = sqlite3.connect(self._sqlite_path)
        else:
            conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_run(
        self,
        result: BacktestResult,
        strategy_name: str,
        params: dict,
        instrument: str,
        granularity: str,
        cost_model: dict,
        validation_stage: str,
        metrics: dict | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        notes: str = "",
    ) -> str:
        run_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        ph = self._ph

        run_sql = f"""
            INSERT INTO runs (
                run_id, created_at, strategy_name, params, instrument,
                granularity, cost_model, starting_balance, ending_balance,
                period_start, period_end, validation_stage, metrics, notes
            ) VALUES ({", ".join([ph] * 14)})
        """
        run_row = (
            run_id, created_at, strategy_name, json.dumps(params), instrument,
            granularity, json.dumps(cost_model), result.starting_balance,
            result.ending_balance, period_start, period_end, validation_stage,
            json.dumps(metrics) if metrics is not None else None, notes,
        )

        trade_sql = f"""
            INSERT INTO trades (
                trade_id, run_id, side, units, entry_time, entry_price,
                exit_time, exit_price, commission_paid, reason_open, reason_close
            ) VALUES ({", ".join([ph] * 11)})
        """
        trade_ids = [str(uuid.uuid4()) for _ in result.trades]
        trade_rows = [
            (
                trade_ids[i], run_id, t.side, t.units, t.entry_time, t.entry_price,
                t.exit_time, t.exit_price, t.commission_paid, t.reason_open, t.reason_close,
            )
            for i, t in enumerate(result.trades)
        ]

        # Additive (Phase 1 Step 1, CONFIDENCE_SIZING_DESIGN.md Section
        # 8.3): every fill belonging to each trade, if the trade has fill
        # history - empty for a hand-constructed Trade, which never
        # reaches record_run() in practice. Existing `trades` columns are
        # completely unchanged above.
        fill_sql = f"""
            INSERT INTO fills (
                fill_id, trade_id, units, price, timestamp, commission, reason
            ) VALUES ({", ".join([ph] * 7)})
        """
        fill_rows = [
            (str(uuid.uuid4()), trade_ids[i], f.units, f.price, f.timestamp, f.commission, f.reason)
            for i, t in enumerate(result.trades)
            for f in t.fills
        ]

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(run_sql, run_row)
            if trade_rows:
                cur.executemany(trade_sql, trade_rows)
            if fill_rows:
                cur.executemany(fill_sql, fill_rows)

        return run_id

    def _row_to_record(self, row) -> RunRecord:
        return RunRecord(
            run_id=row[0], created_at=row[1], strategy_name=row[2], params=json.loads(row[3]),
            instrument=row[4], granularity=row[5], cost_model=json.loads(row[6]),
            starting_balance=row[7], ending_balance=row[8], period_start=row[9],
            period_end=row[10], validation_stage=row[11],
            metrics=json.loads(row[12]) if row[12] else None, notes=row[13],
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        ph = self._ph
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT run_id, created_at, strategy_name, params, instrument, granularity, "
                "cost_model, starting_balance, ending_balance, period_start, period_end, "
                f"validation_stage, metrics, notes FROM runs WHERE run_id = {ph}",
                (run_id,),
            )
            row = cur.fetchone()
            return self._row_to_record(row) if row else None

    def list_runs(
        self,
        strategy_name: str | None = None,
        instrument: str | None = None,
        validation_stage: str | None = None,
    ) -> list[RunRecord]:
        ph = self._ph
        query = (
            "SELECT run_id, created_at, strategy_name, params, instrument, granularity, "
            "cost_model, starting_balance, ending_balance, period_start, period_end, "
            "validation_stage, metrics, notes FROM runs WHERE 1=1"
        )
        params: list = []
        if strategy_name:
            query += f" AND strategy_name = {ph}"
            params.append(strategy_name)
        if instrument:
            query += f" AND instrument = {ph}"
            params.append(instrument)
        if validation_stage:
            query += f" AND validation_stage = {ph}"
            params.append(validation_stage)
        query += " ORDER BY created_at DESC"

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_trades(self, run_id: str) -> list[Trade]:
        ph = self._ph
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT side, units, entry_time, entry_price, exit_time, exit_price, "
                f"commission_paid, reason_open, reason_close FROM trades WHERE run_id = {ph} "
                "ORDER BY entry_time ASC",
                (run_id,),
            )
            rows = cur.fetchall()
        return [
            Trade(
                side=r[0], units=r[1], entry_time=r[2], entry_price=r[3],
                exit_time=r[4], exit_price=r[5], commission_paid=r[6],
                reason_open=r[7] or "", reason_close=r[8] or "",
            )
            for r in rows
        ]
