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

# Rank the sendable verdicts in SENDABLE's order, so a digest fills with strong
# listings and only pads from review once those run out. Built from the tuple
# above rather than written out, so the two cannot drift apart.
TIER_ORDER = (
    "CASE fit_decision "
    + " ".join(
        f"WHEN '{decision.value}' THEN {rank}"
        for rank, decision in enumerate(SENDABLE)
    )
    + " END"
)


def unsent_listings(limit: int) -> list[JobListing]:
    """Return up to ``limit`` unsent listings, strong ones first.

    Ordered by verdict tier and then newest-first within a tier, so a partial
    digest is padded out of the ``review`` pool only once every ``strong``
    listing has taken a slot. ``published_at DESC`` is the tiebreak rather than
    anything ranked: after the initial sweep this runs daily, so the pool on
    any given morning is small enough that within-tier order barely matters.

    ``published_at`` is a board-supplied string of inconsistent format across
    adapters, so this is a lexical sort, not a date sort -- good enough for a
    tiebreak, and ``added_at`` (which the table writes itself) breaks ties
    under it for listings whose board gave no date at all.
    """
    decisions = [decision.value for decision in SENDABLE]
    placeholders = ", ".join("?" for _ in decisions)
    rows = db.execute(
        f"""
        SELECT {COLUMNS}
        FROM job_listings
        WHERE sent = 0 AND fit_decision IN ({placeholders})
        ORDER BY
            {TIER_ORDER},
            published_at DESC,
            added_at DESC
        LIMIT ?
        """,
        (*decisions, limit),
    )
    return [JobListing.load(dict(row)) for row in rows]


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
