"""Shared SQLite client for state that needs to outlive a single DAG run.

Airflow's own metadata lives in Postgres; this is a separate, small database
that tasks use to remember things between runs (seen job ids, digest history,
and so on). Import :func:`connect` or :func:`session` rather than opening
``sqlite3`` directly, so every task gets the same file and pragmas.
"""

import sqlite3
from contextlib import contextmanager
from collections.abc import Generator
from os import environ
from pathlib import Path

AIRFLOW_HOME = Path(environ.get("AIRFLOW_HOME", Path(__file__).resolve().parents[2]))

# Kept outside dags/ so the DAG parser never walks it.
DB_PATH = AIRFLOW_HOME / "state" / "state.db"

# Mapped tasks run in parallel processes, so a writer can find the file locked.
# WAL lets readers work during a write; the timeout absorbs the rest.
BUSY_TIMEOUT_SECONDS = 30.0


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a configured connection, creating the database file if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        timeout=BUSY_TIMEOUT_SECONDS,
        # Let us manage transactions explicitly via the session() helper.
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path: Path = DB_PATH) -> Generator[sqlite3.Connection]:
    """Connection scoped to a transaction: commits on success, rolls back on error.

    >>> with session() as conn:
    ...     conn.execute("INSERT INTO seen_jobs (job_id) VALUES (?)", (job_id,))
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def execute(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Run a single statement in its own transaction and return any rows."""
    with session(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def executemany(sql: str, params: list[tuple], db_path: Path = DB_PATH) -> None:
    """Run one statement over many parameter sets in a single transaction."""
    with session(db_path) as conn:
        conn.executemany(sql, params)


def init_schema(statements: list[str], db_path: Path = DB_PATH) -> None:
    """Apply idempotent DDL (``CREATE TABLE IF NOT EXISTS`` and friends).

    Each DAG package owns its own tables and calls this with its own DDL,
    so adding a table never means editing this module.
    """
    with session(db_path) as conn:
        for statement in statements:
            conn.execute(statement)
