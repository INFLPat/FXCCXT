"""
data/store.py

Storage for historical FX candle data. Works against a cloud Postgres
database (Snowflake Postgres, Neon, Supabase, RDS, or any standard Postgres
host) or a local SQLite file, controlled entirely by the database_url/
DATABASE_URL you pass in:

    Snowflake Postgres:  postgresql://user:pass@host:5432/postgres?sslmode=require
    Other cloud Postgres: postgresql://user:pass@host/dbname?sslmode=require
    Local SQLite:         sqlite:///data/fx_history.db     (offline dev/tests)

Snowflake Postgres (public preview as of writing) connects over the standard
libpq/Postgres wire protocol - confirmed against Snowflake's own connection
docs - so it needs no special driver or code path here: it's just another
postgresql:// URL, handled by the same psycopg2 code as any other Postgres
host. That's the point of building against a standard connection string
rather than a bespoke client.

Why sqlite3 + psycopg2 rather than an ORM: SQLite and Postgres agree closely
enough on basic types (TEXT/REAL/INTEGER) and upsert syntax
(ON CONFLICT ... DO UPDATE) that almost all the SQL below is shared verbatim
between both backends - the only real differences are the connection itself
and the parameter placeholder ('?' vs '%s'). That's a thin enough gap to
handle directly, without pulling in a bigger dependency to abstract it.

Testing status: I've run the SQLite path end-to-end in the environment I
built this in (no network access there), so that half is verified. The
Postgres path (including against Snowflake Postgres specifically) uses the
same SQL, executed through the standard psycopg2 driver, but I have not been
able to run it against a real Postgres server of any kind - run
`python -m tests.test_store` once you've set DATABASE_URL to a real
connection string, to confirm before relying on it.
"""

import os
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    import psycopg2
except ImportError:
    psycopg2 = None

DEFAULT_LOCAL_URL = "sqlite:///data/fx_history.db"

# Shared between SQLite and Postgres - both accept this exact syntax.
SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    instrument   TEXT    NOT NULL,
    granularity  TEXT    NOT NULL,
    timestamp    TEXT    NOT NULL,
    bid_open     REAL    NOT NULL,
    bid_high     REAL    NOT NULL,
    bid_low      REAL    NOT NULL,
    bid_close    REAL    NOT NULL,
    ask_open     REAL    NOT NULL,
    ask_high     REAL    NOT NULL,
    ask_low      REAL    NOT NULL,
    ask_close    REAL    NOT NULL,
    volume       INTEGER,
    PRIMARY KEY (instrument, granularity, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (instrument, granularity, timestamp);
"""


@dataclass
class Candle:
    instrument: str
    granularity: str
    timestamp: str
    bid_open: float
    bid_high: float
    bid_low: float
    bid_close: float
    ask_open: float
    ask_high: float
    ask_low: float
    ask_close: float
    volume: int | None = None

    @property
    def mid_close(self) -> float:
        return (self.bid_close + self.ask_close) / 2

    @property
    def spread_close(self) -> float:
        return self.ask_close - self.bid_close


class FxStore:
    """
    Storage for FX candles, backed by SQLite (local files, offline dev/test)
    or Postgres (cloud - Neon, Supabase, RDS, or any standard Postgres host).

    database_url defaults to the DATABASE_URL env var, then falls back to a
    local SQLite file if that's unset.
    """

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL") or DEFAULT_LOCAL_URL
        scheme = urlparse(self.database_url).scheme
        self.backend = "postgres" if scheme.startswith("postgres") else "sqlite"
        self._ph = "%s" if self.backend == "postgres" else "?"  # param placeholder

        if self.backend == "postgres" and "sslmode" not in self.database_url:
            # Snowflake Postgres (and most cloud Postgres providers) require
            # or strongly expect SSL. A missing sslmode tends to surface as a
            # confusing connection error rather than a clear message, so flag
            # it up front instead.
            warnings.warn(
                "DATABASE_URL has no sslmode parameter. Cloud Postgres providers "
                "(including Snowflake Postgres) generally require SSL - if the "
                "connection fails, try appending '?sslmode=require' to the URL.",
                stacklevel=2,
            )
        if self.backend == "postgres" and psycopg2 is None:
            raise ImportError(
                "psycopg2 is required for a Postgres DATABASE_URL. "
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

    def upsert_candles(self, candles: list[Candle]) -> int:
        """Insert candles, updating any that already exist for the same
        (instrument, granularity, timestamp). Returns rows written."""
        if not candles:
            return 0

        ph = self._ph
        placeholders = ", ".join([ph] * 12)
        sql = f"""
            INSERT INTO candles (
                instrument, granularity, timestamp,
                bid_open, bid_high, bid_low, bid_close,
                ask_open, ask_high, ask_low, ask_close,
                volume
            ) VALUES ({placeholders})
            ON CONFLICT (instrument, granularity, timestamp) DO UPDATE SET
                bid_open=excluded.bid_open, bid_high=excluded.bid_high,
                bid_low=excluded.bid_low, bid_close=excluded.bid_close,
                ask_open=excluded.ask_open, ask_high=excluded.ask_high,
                ask_low=excluded.ask_low, ask_close=excluded.ask_close,
                volume=excluded.volume
        """
        rows = [
            (
                c.instrument, c.granularity, c.timestamp,
                c.bid_open, c.bid_high, c.bid_low, c.bid_close,
                c.ask_open, c.ask_high, c.ask_low, c.ask_close,
                c.volume,
            )
            for c in candles
        ]
        with self._connect() as conn:
            cur = conn.cursor()
            cur.executemany(sql, rows)
        return len(rows)

    def get_candles(
        self,
        instrument: str,
        granularity: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[Candle]:
        """Fetch candles ordered by time. start/end are ISO8601 strings, inclusive."""
        ph = self._ph
        query = (
            "SELECT instrument, granularity, timestamp, "
            "bid_open, bid_high, bid_low, bid_close, "
            "ask_open, ask_high, ask_low, ask_close, volume "
            f"FROM candles WHERE instrument = {ph} AND granularity = {ph}"
        )
        params: list[str] = [instrument, granularity]
        if start:
            query += f" AND timestamp >= {ph}"
            params.append(start)
        if end:
            query += f" AND timestamp <= {ph}"
            params.append(end)
        query += " ORDER BY timestamp ASC"

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [Candle(*row) for row in cur.fetchall()]

    def get_candles_multi(
        self,
        instruments: list[str],
        granularity: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, list[Candle]]:
        """
        Fetch candles for several instruments in one query, grouped by
        instrument - added for the cross-instrument comparison work (the
        planned cartesian/rotation layer) that needs many instruments'
        history at once, rather than issuing one round-trip per instrument
        via get_candles(). Every requested instrument is present as a key
        in the result, even if it has no data (empty list) - a caller
        shouldn't need to distinguish "no data" from "wasn't asked for" by
        checking dict membership.
        """
        if not instruments:
            return {}
        ph = self._ph
        placeholders = ", ".join([ph] * len(instruments))
        query = (
            "SELECT instrument, granularity, timestamp, "
            "bid_open, bid_high, bid_low, bid_close, "
            "ask_open, ask_high, ask_low, ask_close, volume "
            f"FROM candles WHERE instrument IN ({placeholders}) AND granularity = {ph}"
        )
        params: list[str] = list(instruments) + [granularity]
        if start:
            query += f" AND timestamp >= {ph}"
            params.append(start)
        if end:
            query += f" AND timestamp <= {ph}"
            params.append(end)
        query += " ORDER BY instrument ASC, timestamp ASC"

        result: dict[str, list[Candle]] = {instrument: [] for instrument in instruments}
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            for row in cur.fetchall():
                candle = Candle(*row)
                result[candle.instrument].append(candle)
        return result

    def coverage(self, instrument: str, granularity: str) -> tuple[str, str] | None:
        """Return (earliest, latest) timestamp available, or None if no data."""
        ph = self._ph
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT MIN(timestamp), MAX(timestamp) FROM candles "
                f"WHERE instrument = {ph} AND granularity = {ph}",
                (instrument, granularity),
            )
            row = cur.fetchone()
            return tuple(row) if row and row[0] else None
