"""Persistence for the company watchlist mirrored from the Google Sheet.

Two tables with deliberately different ownership:

``companies``
    A dumb mirror of the sheet's Companies tab. Fully rewritten on every sync --
    the sheet always wins, and nothing the pipeline knows is kept here. That is
    what makes it replaceable: if the mirror is wrong, the next run fixes it.

``company_scan_state``
    Pipeline-owned scan bookkeeping, keyed by company name. Kept separate
    precisely so the rewrite above cannot destroy it. Rows are allowed to
    outlive their company: a name removed from the sheet and later re-added
    keeps its history, and a stale row costs one unused row.
"""

import sqlite3

from common import db
from ..types import Company

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS companies (
        name        TEXT    NOT NULL PRIMARY KEY,
        board_url   TEXT,
        board_type  TEXT,
        board_slug  TEXT,
        status      TEXT,
        notes       TEXT,
        synced_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_scan_state (
        company_name        TEXT    NOT NULL PRIMARY KEY,

        -- Written once, by scan_boards, the first time a company's board
        -- scrapes without raising. NULL means the board has never been read:
        -- a typo'd slug or an unsupported ATS type leaves it that way, which is
        -- what makes "not yet scanned" and "scanned, nothing found" distinct.
        first_scanned_at    TEXT,

        -- Written by send_onboarding_report once the company's opening report
        -- has been dealt with. NULL alongside a non-NULL first_scanned_at is
        -- exactly the set of companies still owed a report.
        onboarded_at        TEXT,

        -- Moved forward by scan_boards on every successful scrape, unlike the
        -- write-once stamp above. This is what the same-day skip reads, so it
        -- deliberately tracks the most recent read rather than the first.
        last_scanned_at     TEXT
    )
    """,
]

# init_schema only runs CREATE TABLE IF NOT EXISTS, which does nothing to a
# table that already exists, so columns added to the DDL above have to be
# backfilled onto deployed databases separately. SQLite has no ALTER TABLE IF
# NOT EXISTS; the duplicate-column error is the "already applied" signal.
MIGRATIONS = [
    "ALTER TABLE company_scan_state ADD COLUMN last_scanned_at TEXT",
]

# Only companies in this state are handed to the board scanner. Anything else
# in the column (paused, rejected, applied) is Andrew parking a row without
# deleting it, and must not cost a scan.
STATUS_ACTIVE = "active"


def init_schema() -> None:
    """Create the tables if they are not already there, and apply migrations."""
    db.init_schema(SCHEMA)

    for statement in MIGRATIONS:
        try:
            db.execute(statement)
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error):
                raise


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
            INSERT INTO companies (name, board_url, board_type, board_slug, status, notes, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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
        SELECT c.name, c.board_url, c.board_type, c.board_slug, c.status, c.notes, c.synced_at
        FROM companies c
        WHERE c.board_type <> ''
            AND (TRIM(LOWER(c.status)) = ? OR TRIM(c.status) = '')
        ORDER BY c.name
        """,
        (STATUS_ACTIVE,),
    )
    return [Company.load(dict(row)) for row in rows]


def mark_scanned(company_name: str) -> None:
    """Record a successful scrape of a company's board.

    Writes two stamps with deliberately different rules. ``first_scanned_at``
    is write-once -- the update only fills a value that is still NULL, so it
    stays the first successful scan rather than the most recent one. That is
    what the onboarding report keys off, and a stamp that moved with every scan
    would say nothing about when the company entered the pipeline.
    ``last_scanned_at`` moves forward every time, and is what the same-day skip
    in :func:`scanned_since` reads.

    Called after a scrape returns without raising, including one that returned
    zero listings: an empty board is a board we successfully read.
    """
    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO company_scan_state (company_name)
            VALUES (?)
            ON CONFLICT (company_name) DO NOTHING
            """,
            (company_name,),
        )
        conn.execute(
            """
            UPDATE company_scan_state
            SET first_scanned_at = COALESCE(first_scanned_at, datetime('now')),
                last_scanned_at = datetime('now')
            WHERE company_name = ?
            """,
            (company_name,),
        )


def scanned_since(cutoff_hours: float) -> set[str]:
    """Return the companies whose boards scraped cleanly within ``cutoff_hours``.

    A set of names rather than a per-company check so the scan does one query
    instead of one per company. Companies with no row, or a row predating the
    cutoff, are simply absent -- the caller treats absence as "scan it".
    """
    rows = db.execute(
        """
        SELECT company_name
        FROM company_scan_state
        WHERE last_scanned_at IS NOT NULL
            AND last_scanned_at > datetime('now', ?)
        """,
        (f"-{cutoff_hours} hours",),
    )
    return {row["company_name"] for row in rows}
