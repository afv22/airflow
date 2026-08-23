"""Read/write access to ``job_listings`` for the relevance filter stage.

The table itself is owned by :mod:`..scan_boards.store`; this module only adds
the queries this stage needs against the ``relevance_*`` columns already
defined there.
"""

from common import db
from job_lead_research.types import JobListing, RelevanceDecision, RelevanceResult


def pending_listings() -> list[JobListing]:
    """Return every listing still awaiting a relevance judgement."""
    rows = db.execute(
        """
        SELECT id, company_name, location, title, description,
               listing_url, published_at, added_at,
               relevance_decision, relevance_rejection
        FROM job_listings
        WHERE relevance_decision = ?
        ORDER BY company_name, id
        """,
        (RelevanceDecision.PENDING.value,),
    )
    return [JobListing.load(dict(row)) for row in rows]


def save_decisions(results: list[RelevanceResult]) -> int:
    """Write each result's verdict back to its ``job_listings`` row.

    Matched on ``(company_name, id)`` -- the table's primary key -- since board
    ids alone repeat across providers. One transaction for the whole batch, so
    a mid-batch failure leaves every row still ``pending`` rather than half
    judged.
    """
    if not results:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET relevance_decision = ?, relevance_rejection = ?
            WHERE company_name = ? AND id = ?
            """,
            [
                (
                    (
                        RelevanceDecision.PASS
                        if result.relevant
                        else RelevanceDecision.REJECT
                    ).value,
                    "" if result.relevant else result.reasoning,
                    result.company_name,
                    result.id,
                )
                for result in results
            ],
        )

    return len(results)


def mark_errored(listings: list[JobListing], error: str) -> int:
    """Mark listings as errored so a failed judgement can be retried later.

    Distinct from :func:`save_decisions`: this is for listings that never got
    a verdict at all (the LLM call itself failed), not for a real pass/reject
    outcome.
    """
    if not listings:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET relevance_decision = ?, relevance_rejection = ?
            WHERE company_name = ? AND id = ?
            """,
            [
                (RelevanceDecision.ERROR.value, error, listing.company_name, listing.id)
                for listing in listings
            ],
        )

    return len(listings)
