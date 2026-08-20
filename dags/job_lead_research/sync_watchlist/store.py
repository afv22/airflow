"""Persistence for the company watchlist mirrored from the Google Sheet.

Two tables with deliberately different ownership:

``companies``
    A dumb mirror of the sheet's Companies tab. Fully rewritten on every sync --
    the sheet always wins, and nothing the pipeline knows is kept here. That is
    what makes it replaceable: if the mirror is wrong, the next run fixes it.

``company_scan_state``
    Pipeline-owned scan bookkeeping (when a board was last scanned, whether the
    last scan succeeded), keyed by company name. Kept separate precisely so the
    rewrite above cannot destroy it. Rows are allowed to outlive their company:
    a name removed from the sheet and later re-added keeps its history, and a
    stale row costs one unused row.
"""

from common import db
from .types import Company

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS companies (
        name        TEXT    NOT NULL PRIMARY KEY,
        board_url   TEXT,
        board_type  TEXT,
        filters     TEXT,
        status      TEXT,
        notes       TEXT,
        synced_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_scan_state (
        company_name    TEXT    NOT NULL PRIMARY KEY,
        last_scanned_at TEXT,
        last_status     TEXT    CHECK (last_status IN ('ok', 'error', 'empty')),
        last_error      TEXT,
        listing_count   INTEGER
    )
    """,
]

# Only companies in this state are handed to the board scanner. Anything else
# in the column (paused, rejected, applied) is Andrew parking a row without
# deleting it, and must not cost a scan.
STATUS_ACTIVE = "active"


def init_schema() -> None:
    """Create the tables if they are not already there."""
    db.init_schema(SCHEMA)


def replace_companies(companies: list[Company]) -> int:
    """Rewrite the mirror to exactly match the sheet, returning the row count.

    Delete-then-insert inside one transaction, rather than an upsert plus a
    tombstone pass: the sheet is the whole truth, so reconciling row-by-row
    would be more code for the same result. :func:`common.db.session` rolls the
    delete back if the insert fails, so a bad sync leaves the previous mirror
    intact rather than an empty table.

    An empty sheet is refused. A Companies tab that reads as zero rows is far
    more likely to be a wrong range, a renamed tab, or a permissions change than
    a genuine decision to watch nothing -- and silently wiping the watchlist
    would turn that into a quiet no-op run instead of a visible failure.
    """
    if not companies:
        raise ValueError(
            "Refusing to sync an empty watchlist -- check the sheet range, tab "
            "name, and that the sheet is still shared with the service account."
        )

    with db.session() as conn:
        conn.execute("DELETE FROM companies")
        conn.executemany(
            """
            INSERT INTO companies (name, board_url, board_type, filters, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [company.dump() for company in companies],
        )

    return len(companies)


def active_companies() -> list[Company]:
    """Return the companies whose boards should be scanned this run.

    Status is compared case-insensitively so the sheet can hold "Active".
    A blank status counts as active: a freshly typed row with just a name and
    URL is the common way to add a company, and making it opt-in would mean
    new rows silently do nothing.
    """
    rows = db.execute(
        """
        SELECT c.name, c.board_url, c.board_type, c.filters, c.status, c.notes
        FROM companies c
        WHERE c.board_url <> ''
            AND (TRIM(LOWER(c.status)) = ? OR TRIM(c.status) = '')
        ORDER BY c.name
        """,
        (STATUS_ACTIVE,),
    )
    return [Company.load(dict(row)) for row in rows]


def record_scan(
    company_name: str,
    status: str,
    listing_count: int = 0,
    error: str | None = None,
) -> None:
    """Store the outcome of a board scan, whether it succeeded or not.

    Called by ``scan_board`` (round 1, piece 2). Recording failures is the point:
    an errored or empty scan must be visibly distinct from "this company has no
    open roles", so a flaky page never reads as every role having closed.
    """
    db.execute(
        """
        INSERT INTO company_scan_state (
            company_name, last_scanned_at, last_status, last_error, listing_count
        )
        VALUES (?, datetime('now'), ?, ?, ?)
        ON CONFLICT (company_name) DO UPDATE SET
            last_scanned_at = datetime('now'),
            last_status     = excluded.last_status,
            last_error      = excluded.last_error,
            listing_count   = excluded.listing_count
        """,
        (company_name, status, error, listing_count),
    )
