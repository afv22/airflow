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
        PRIMARY KEY (company_name, id)
    )
    """,
]


def init_schema() -> None:
    """Create the table if it is not already there."""
    db.init_schema(SCHEMA)


def insert_listings(listings: list[JobListing]) -> int:
    """Insert listings we have not seen before, returning how many were new.

    A scan re-reads the whole board every run, so the great majority of what
    arrives here is already stored. ``ON CONFLICT DO NOTHING`` makes that the
    cheap, silent case and keeps the first-seen ``added_at`` intact -- which is
    what makes the column mean "when we first saw this", not "when we last
    scanned". Re-inserting instead would reset that on every run and hide which
    postings are genuinely new.

    ``added_at`` is left to the column default rather than taken from the
    dataclass, so freshly scraped listings (which carry no value for it yet)
    get a consistent server-side timestamp.

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
            [listing.dump()[:-1] for listing in listings],
        )
        after = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]

    return after - before
