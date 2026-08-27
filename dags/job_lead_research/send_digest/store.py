"""Read/write access to ``job_listings`` for the digest stage.

The table itself is owned by :mod:`..scan_boards.store`; this module only adds
the queries this stage needs against the ``sent`` column already defined there.
"""

from common import db
from job_lead_research.types import FitDecision, JobListing

COLUMNS = """
    id, company_name, location, title, description,
    listing_url, published_at, added_at,
    relevance_decision, relevance_rejection,
    fit_decision, fit_reasoning
"""

# The verdicts a listing can be sent on, best first. Anything else -- reject,
# skipped, error, or a listing still pending a verdict -- is never digested.
SENDABLE = (FitDecision.STRONG, FitDecision.REVIEW)

# Newest-first within a verdict. ``published_at`` is a board-supplied string of
# inconsistent format across adapters, so this is a lexical sort, not a date
# sort -- good enough for a tiebreak, and ``added_at`` (which the table writes
# itself) breaks ties under it for listings whose board gave no date at all.
# After the initial sweep this runs daily, so the pool on any given morning is
# small enough that within-verdict order barely matters.
RECENCY = "published_at DESC, added_at DESC"


def unsent_count(decision: FitDecision) -> int:
    """How many unsent listings hold ``decision``.

    The digest sizes itself off the strong pool before it selects (see
    :func:`..task._quota`), so it needs the count separately from the rows.
    """
    rows = db.execute(
        """
        SELECT COUNT(*) AS n
        FROM job_listings
        WHERE sent = 0 AND fit_decision = ?
        """,
        (decision.value,),
    )
    return rows[0]["n"]


def _unsent_by_decision(decision: FitDecision, limit: int) -> list[JobListing]:
    """The ``limit`` newest unsent listings holding ``decision``."""
    if limit <= 0:
        return []

    rows = db.execute(
        f"""
        SELECT {COLUMNS}
        FROM job_listings
        WHERE sent = 0 AND fit_decision = ?
        ORDER BY {RECENCY}
        LIMIT ?
        """,
        (decision.value, limit),
    )
    return [JobListing.load(dict(row)) for row in rows]


def unsent_listings(strong: int, review: int) -> list[JobListing]:
    """Return up to ``strong`` strong and ``review`` review unsent listings.

    The two quotas are drawn independently rather than as one ranked query with
    a single limit: the caller has already decided how many of each the digest
    should carry, and a short strong pool must not spill its unused slots into
    review. Strong listings lead the result, so the email reads best-first.
    """
    return _unsent_by_decision(FitDecision.STRONG, strong) + _unsent_by_decision(
        FitDecision.REVIEW, review
    )


def mark_sent(listings: list[JobListing]) -> int:
    """Flag each listing as digested, in one transaction.

    Called only after Resend has accepted the message: a send that raises
    leaves every row ``sent = 0``, so the listings return to tomorrow's pool
    rather than being silently swallowed by a failed email. The reverse risk --
    a send that succeeds and a mark that then fails -- costs a duplicate
    listing in one later digest, which is the cheaper of the two failures.
    """
    if not listings:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET sent = 1
            WHERE company_name = ? AND id = ?
            """,
            [(listing.company_name, listing.id) for listing in listings],
        )

    return len(listings)
