"""Read/write access to ``job_listings`` for the fit filter stage.

The table itself is owned by :mod:`..scan_boards.store`; this module only adds
the queries this stage needs against the ``fit_*`` columns already defined
there.
"""

from common import db
from job_lead_research.types import FitDecision, FitResult, JobListing

COLUMNS = """
    id, company_name, location, title, description,
    listing_url, published_at, added_at,
    relevance_decision, relevance_rejection,
    fit_decision, fit_reasoning
"""


def pending_listings() -> list[JobListing]:
    """Return every listing still awaiting a fit judgement.

    No join back to ``relevance_decision``: a listing the relevance filter
    turned down is written straight to ``skipped`` by that stage, so
    ``pending`` already means "passed relevance, not yet judged for fit".
    """
    rows = db.execute(
        f"""
        SELECT {COLUMNS}
        FROM job_listings
        WHERE fit_decision = ?
        ORDER BY company_name, id
        """,
        (FitDecision.PENDING.value,),
    )
    return [JobListing.load(dict(row)) for row in rows]


def save_decisions(results: list[FitResult]) -> int:
    """Write each result's verdict back to its ``job_listings`` row.

    Matched on ``(company_name, id)`` -- the table's primary key -- since board
    ids alone repeat across providers. One transaction for the whole batch, so
    a mid-batch failure leaves every row still ``pending`` rather than half
    judged.

    Unlike the relevance stage, the reasoning is kept for every verdict, not
    only rejections: the digest prints it for a ``strong`` too, since "why is
    this worth reading first" is the part Andrew actually acts on.
    """
    if not results:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET fit_decision = ?, fit_reasoning = ?
            WHERE company_name = ? AND id = ?
            """,
            [
                (
                    FitDecision(result.decision).value,
                    result.reasoning,
                    result.company_name,
                    result.id,
                )
                for result in results
            ],
        )

    return len(results)


def mark_errored(listings: list[JobListing], error: str) -> int:
    """Mark listings as errored so a failed judgement is visible.

    Distinct from :func:`save_decisions`: this is for listings that never got a
    verdict at all (the LLM call itself failed), not for a real verdict.
    Error handling is deliberately minimal for now -- an errored row is not
    retried automatically, since ``pending_listings`` looks only at ``pending``.
    """
    if not listings:
        return 0

    with db.session() as conn:
        conn.executemany(
            """
            UPDATE job_listings
            SET fit_decision = ?, fit_reasoning = ?
            WHERE company_name = ? AND id = ?
            """,
            [
                (FitDecision.ERROR.value, error, listing.company_name, listing.id)
                for listing in listings
            ],
        )

    return len(listings)
