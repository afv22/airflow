"""Persistence for listings scraped off company job boards.

``board_listings`` is owned by this DAG rather than shared with ``job_hunt``'s
``job_listings``: the columns a structured ATS response offers (team, workplace
type, secondary locations) have no meaning for a HackerNews post, and inheriting
a schema shaped by HN would mean carrying dead columns in both directions.
Cross-source selection is a problem for the round where a second source exists.

Only listings that survived the adapter's filters are stored. That is the
accepted cost of filtering before storage: the table cannot tell you what was
rejected, so a misconfigured filter is caught by the scan warnings written into
``company_scan_state`` instead of by an audit of rejected rows.
"""

from common import db
from .types import BoardListing, ScanResult

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS board_listings (
        company             TEXT    NOT NULL,
        source_id           TEXT    NOT NULL,

        -- Written by the board adapters.
        title               TEXT    NOT NULL,
        url                 TEXT    NOT NULL,
        content_hash        TEXT    NOT NULL,
        location            TEXT,
        secondary_locations TEXT,
        department          TEXT,
        team                TEXT,
        employment_type     TEXT,
        workplace_type      TEXT,
        is_remote           INTEGER,
        published_at        TEXT,
        updated_at          TEXT,
        description         TEXT,

        -- Lifecycle. first_seen never moves; last_seen advances only on a scan
        -- that saw the whole board, so a truncated or failed scan can never be
        -- read as roles having closed.
        first_seen          TEXT    NOT NULL DEFAULT (datetime('now')),
        last_seen           TEXT    NOT NULL DEFAULT (datetime('now')),

        status              TEXT    NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'filtered', 'researched', 'sent')),
        filter_reason       TEXT,

        PRIMARY KEY (company, source_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_board_listings_new
        ON board_listings (status) WHERE status = 'new'
    """,
    # Lets a flood of new source_ids sharing one hash be spotted, which is the
    # evidence that a board has started minting per-session URLs and that the
    # URL is no longer a sound dedupe key.
    """
    CREATE INDEX IF NOT EXISTS idx_board_listings_content
        ON board_listings (company, content_hash)
    """,
]


def init_schema() -> None:
    """Create the listings table and add columns later rounds introduced."""
    db.init_schema(SCHEMA)
    _add_scan_warning_column()
    _add_updated_at_column()


def _add_scan_warning_column() -> None:
    """Add ``company_scan_state.last_warning`` to databases created before it.

    A scan can succeed and still be misconfigured -- a filter matching nothing, a
    board URL carrying parameters the API ignores. Those need to be visible
    without claiming the scan failed, so they get their own column rather than
    overloading ``last_error``.
    """
    columns = {row["name"] for row in db.execute("PRAGMA table_info(company_scan_state)")}
    if columns and "last_warning" not in columns:
        db.execute("ALTER TABLE company_scan_state ADD COLUMN last_warning TEXT")


def _add_updated_at_column() -> None:
    """Add ``board_listings.updated_at`` to databases created before it.

    The ATS's own last-modified timestamp, where the platform reports one
    (Greenhouse does, Ashby does not). Carried now because it is the cheapest
    signal of "role changed since last scan" once close-detection exists, and a
    nullable column today beats a backfill later.
    """
    columns = {row["name"] for row in db.execute("PRAGMA table_info(board_listings)")}
    if columns and "updated_at" not in columns:
        db.execute("ALTER TABLE board_listings ADD COLUMN updated_at TEXT")


def upsert_listings(listings: list[BoardListing], advance_last_seen: bool) -> int:
    """Store this scan's listings, refreshing the ones already known.

    ``advance_last_seen`` is the caller's assertion that the scan saw the whole
    board. When it is false -- a capped scan -- details are still refreshed but
    ``last_seen`` is left where it was, because a partial view is not evidence
    that anything is still open, and close-detection will later read exactly
    this column.

    ``first_seen`` and everything a later stage wrote (``status``,
    ``filter_reason``) are never overwritten, so re-seeing a role does not undo
    work already done on it.
    """
    if not listings:
        return 0

    last_seen = "datetime('now')" if advance_last_seen else "last_seen"
    db.executemany(
        f"""
        INSERT INTO board_listings (
            company, source_id, title, url, content_hash, location,
            secondary_locations, department, team, employment_type,
            workplace_type, is_remote, published_at, updated_at, description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (company, source_id) DO UPDATE SET
            title               = excluded.title,
            url                 = excluded.url,
            content_hash        = excluded.content_hash,
            location            = excluded.location,
            secondary_locations = excluded.secondary_locations,
            department          = excluded.department,
            team                = excluded.team,
            employment_type     = excluded.employment_type,
            workplace_type      = excluded.workplace_type,
            is_remote           = excluded.is_remote,
            published_at        = excluded.published_at,
            updated_at          = excluded.updated_at,
            description         = excluded.description,
            last_seen           = {last_seen}
        """,
        [
            (
                listing.company,
                listing.source_id,
                listing.title,
                listing.url,
                listing.content_hash,
                listing.location,
                "\n".join(listing.secondary_locations) or None,
                listing.department,
                listing.team,
                listing.employment_type,
                listing.workplace_type,
                listing.is_remote,
                listing.published_at,
                listing.updated_at,
                listing.description,
            )
            for listing in listings
        ],
    )
    return len(listings)


def record_scan(result: ScanResult) -> None:
    """Store the outcome of one board scan, successful or not.

    Written for every scan including failures: an errored scan that left no row
    would be indistinguishable from a company nobody has scanned yet.
    """
    db.execute(
        """
        INSERT INTO company_scan_state (
            company_name, last_scanned_at, last_status, last_error,
            last_warning, listing_count
        )
        VALUES (?, datetime('now'), ?, ?, ?, ?)
        ON CONFLICT (company_name) DO UPDATE SET
            last_scanned_at = datetime('now'),
            last_status     = excluded.last_status,
            last_error      = excluded.last_error,
            last_warning    = excluded.last_warning,
            listing_count   = excluded.listing_count
        """,
        (
            result.company,
            str(result.status),
            result.error,
            result.warning,
            result.kept_count,
        ),
    )


def unresearched(limit: int) -> list[dict]:
    """Return stored listings no verdict has been written for yet.

    Freshest first, so a capped run spends its budget on the newest roles and
    the rest come up on a later run. ``published_at`` can be null on boards that
    do not report it, hence the fallback to when we first saw the role.
    """
    rows = db.execute(
        """
        SELECT company, source_id, title, url, location, department, team,
               employment_type, workplace_type, is_remote, published_at,
               updated_at, description
        FROM board_listings
        WHERE status = 'new'
        ORDER BY COALESCE(published_at, first_seen) DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]
