"""Read/write access to ``job_listings`` for the relevance filter stage.

The table itself is owned by :mod:`..scan_boards.store`; this module only adds
the queries this stage needs against the ``relevance_*`` columns already
defined there.
"""

from common import db
from job_lead_research.types import (
    FitDecision,
    JobListing,
    RelevanceDecision,
    RelevanceResult,
)


def pending_listings() -> list[JobListing]:
    """Return every listing still awaiting a relevance judgement."""
    rows = db.execute(
        """
        SELECT id, company_name, location, title, description,
               listing_url, published_at, added_at,
               relevance_decision, relevance_rejection
        FROM job_listings
        WHERE relevance_decision IN (?, ?)
        ORDER BY company_name, id
        """,
        (RelevanceDecision.PENDING.value, RelevanceDecision.ERROR.value),
    )
    return [JobListing.load(dict(row)) for row in rows]


def save_decisions(results: list[RelevanceResult]) -> int:
    """Write each result's verdict back to its ``job_listings`` row.

    Matched on ``(company_name, id)`` -- the table's primary key -- since board
    ids alone repeat across providers. One transaction for the whole batch, so
    a mid-batch failure leaves every row still ``pending`` rather than half
    judged.

    A rejection also closes out the downstream fit stage, writing
    ``fit_decision = 'skipped'`` in the same statement. This stage writing a
    later stage's column is deliberate: a listing rejected here is never
    eligible for a fit judgement, and settling that in the same transaction is
    what lets the fit stage's pending query be a plain
    ``WHERE fit_decision = 'pending'`` with no join back to this column. The
    alternative -- leaving rejects ``pending`` and filtering them out at read
    time -- leaves a row's meaning split across two stages, and strands them in
    the fit queue if that stage never runs.
    """
    if not results:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET relevance_decision = ?, relevance_rejection = ?, fit_decision = ?
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
                    (
                        FitDecision.PENDING if result.relevant else FitDecision.SKIPPED
                    ).value,
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
            SET relevance_decision = ?, relevance_rejection = ?, fit_decision = ?
            WHERE company_name = ? AND id = ?
            """,
            [
                (
                    RelevanceDecision.ERROR.value,
                    error,
                    # Not judged relevant, so not fit-eligible -- same reasoning
                    # as a reject in save_decisions.
                    FitDecision.SKIPPED.value,
                    listing.company_name,
                    listing.id,
                )
                for listing in listings
            ],
        )

    return len(listings)
