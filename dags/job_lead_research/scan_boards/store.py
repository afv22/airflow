"""Persistence for the job listings pulled off company boards.

One table, ``job_listings``: the accumulated record of every posting a board
scan has ever handed back. Unlike the watchlist mirror in
:mod:`..sync_watchlist.store`, nothing here is rewritten from an upstream
source -- a board that drops a posting should not erase what we saw, since the
listing having existed is the fact downstream tasks reason about.

Rows are keyed by ``(company_name, id)`` rather than ``id`` alone. Board ids are
only unique within the board that issued them, so two providers can and do hand
back the same string; scoping by company keeps them apart without inventing a
surrogate key.
"""

import sqlite3

from common import db
from ..types import JobListing

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS job_listings (
        id              TEXT    NOT NULL,
        company_name    TEXT    NOT NULL,
        location        TEXT,
        title           TEXT,
        description     TEXT,
        listing_url     TEXT,
        published_at    TEXT,
        added_at        TEXT    NOT NULL DEFAULT (datetime('now')),

        -- Stage one of the decision pipeline. Later stages add their own
        -- <stage>_decision / <stage>_rejection pair alongside these.
        relevance_decision  TEXT    NOT NULL DEFAULT 'pending',
        relevance_rejection TEXT    NOT NULL DEFAULT '',

        -- Stage two: the careful judgement against the job-criteria Variable,
        -- run only on listings stage one passed. 'skipped' is written for the
        -- rest, by the relevance stage itself.
        fit_decision        TEXT    NOT NULL DEFAULT 'pending',
        fit_reasoning       TEXT    NOT NULL DEFAULT '',

        -- Stage three: whether this listing has gone out in a digest email.
        -- Write-once, and only after Resend has accepted the message, so a
        -- send that fails leaves the listing eligible for tomorrow's digest.
        sent                INTEGER NOT NULL DEFAULT 0,

        PRIMARY KEY (company_name, id)
    )
    """,
]

# Columns added to job_listings after rows already existed. CREATE TABLE IF NOT
# EXISTS is a no-op against a table that is already there, so a column added to
# SCHEMA above reaches a fresh database and no other -- every existing row would
# be missing it. ADD COLUMN raises rather than no-oping when the column is
# already present, so these are applied individually and that specific error
# swallowed, which is what makes re-running the DAG safe.
#
# A new column here also needs its default backfilled for existing rows: see
# BACKFILLS below.
MIGRATIONS = [
    "ALTER TABLE job_listings ADD COLUMN fit_decision TEXT NOT NULL DEFAULT 'pending'",
    "ALTER TABLE job_listings ADD COLUMN fit_reasoning TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE job_listings ADD COLUMN sent INTEGER NOT NULL DEFAULT 0",
]

# One-time corrections to rows that predate a column's meaning. Each must be
# idempotent -- they run on every scan, not once.
#
# Listings the relevance filter already rejected were never eligible for a fit
# judgement, so they take 'skipped' rather than sitting in the fit queue
# forever. Scoped to rows still at the column default so a real verdict is
# never overwritten.
BACKFILLS = [
    """
    UPDATE job_listings
    SET fit_decision = 'skipped'
    WHERE fit_decision = 'pending' AND relevance_decision != 'pass'
    """,
]


def init_schema() -> None:
    """Create the table if it is not already there, and bring it up to date."""
    db.init_schema(SCHEMA)
    _apply_migrations()
    db.init_schema(BACKFILLS)


def _apply_migrations() -> None:
    """Apply each ADD COLUMN, treating "already exists" as success.

    Applied one statement per transaction rather than as one batch: SQLite has
    no IF NOT EXISTS for ADD COLUMN, so the already-applied case arrives as an
    OperationalError, and sharing a transaction would roll the whole batch back
    on the first column that was already there.
    """
    for statement in MIGRATIONS:
        try:
            db.execute(statement)
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error).lower():
                raise


def insert_listings(listings: list[JobListing]) -> int:
    """Insert listings we have not seen before, returning how many were new.

    A scan re-reads the whole board every run, so the great majority of what
    arrives here is already stored. ``ON CONFLICT DO NOTHING`` makes that the
    cheap, silent case and keeps the first-seen ``added_at`` intact -- which is
    what makes the column mean "when we first saw this", not "when we last
    scanned". Re-inserting instead would reset that on every run and hide which
    postings are genuinely new.

    ``added_at`` and the ``relevance_*`` decision columns are left to their
    column defaults rather than taken from the dataclass: a freshly scraped
    listing carries no timestamp yet, and has not been judged, so writing the
    scraper's placeholder verdict here would let a scrape overwrite a real
    decision. Only the scraped fields are named in the INSERT.

    All listings go in one transaction: a scan either lands whole or not at all,
    so a mid-batch failure cannot leave a company half-recorded.
    """
    if not listings:
        return 0

    with db.session() as conn:
        before = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO job_listings (
                id, company_name, location, title, description,
                listing_url, published_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_name, id) DO NOTHING
            """,
            [
                (
                    listing.id,
                    listing.company_name,
                    listing.location,
                    listing.title,
                    listing.description,
                    listing.listing_url,
                    listing.published_at,
                )
                for listing in listings
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]

    return after - before
