"""Queries for the onboarding report: whose report is owed, and what goes in it.

Two tables, neither owned here. ``company_scan_state`` belongs to
:mod:`..sync_watchlist.store` (which creates it) and ``job_listings`` to
:mod:`..scan_boards.store`; this module only adds the reads and the two writes
this stage needs. The listing flags are set through
:func:`..send_digest.store.mark_sent` rather than a second copy of that
UPDATE -- "included in an email" is one fact with one writer, and the report
and the digest differ in what they select, not in how they record a send.
"""

from common import db
from job_lead_research.send_digest.store import COLUMNS, RECENCY, SENDABLE
from job_lead_research.types import FitDecision, JobListing


def companies_owed_report() -> list[str]:
    """Companies whose board has been scanned but which never got a report.

    A NULL ``first_scanned_at`` is the reason a typo'd slug or an unsupported
    ATS type never triggers a report: nothing has been read off that board, so
    there is no cluster to send. Such a company simply waits, and onboards on
    whichever morning its board first scrapes clean.
    """
    rows = db.execute(
        """
        SELECT company_name
        FROM company_scan_state
        WHERE first_scanned_at IS NOT NULL AND onboarded_at IS NULL
        ORDER BY company_name
        """
    )
    return [row["company_name"] for row in rows]


def pending_count(company_name: str) -> int:
    """How many of a company's listings are still awaiting a fit verdict.

    Non-zero means the sweep did not finish judging this board -- a fit batch
    that errored mid-run, most likely. The report defers on that rather than
    mailing a board with holes in it: the whole value of the report is that it
    is the complete cluster, seen once, side by side.
    """
    rows = db.execute(
        """
        SELECT COUNT(*) AS n
        FROM job_listings
        WHERE company_name = ? AND fit_decision = ?
        """,
        (company_name, FitDecision.PENDING.value),
    )
    return rows[0]["n"]


def sendable_listings(company_name: str) -> list[JobListing]:
    """Every unsent strong-or-review listing for one company, strong first.

    No cap, deliberately: the digest's cap exists because its pool is every
    company at once, while this is one board read in one sitting, and a cap
    here would recreate the drip the report exists to replace. Ordering is the
    digest's -- verdict tier first, then :data:`..send_digest.store.RECENCY`
    within a tier -- so the two emails read the same way.
    """
    placeholders = ", ".join("?" for _ in SENDABLE)
    rows = db.execute(
        f"""
        SELECT {COLUMNS}
        FROM job_listings
        WHERE company_name = ?
            AND sent = 0
            AND fit_decision IN ({placeholders})
        ORDER BY
            CASE fit_decision {" ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(SENDABLE))} END,
            {RECENCY}
        """,
        (company_name, *(d.value for d in SENDABLE), *(d.value for d in SENDABLE)),
    )
    return [JobListing.load(dict(row)) for row in rows]


def mark_onboarded(company_name: str) -> None:
    """Record that this company's report has been dealt with.

    Written whether or not an email went out: a board that produced nothing
    sendable has been fully considered, and re-considering it every morning
    would leave it permanently owed. The row is inserted if missing so this is
    safe to call for a company the scanner has not stamped -- in practice the
    stamp is always there, since it is what put the company in the owed list.
    """
    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO company_scan_state (company_name, onboarded_at)
            VALUES (?, datetime('now'))
            ON CONFLICT (company_name) DO UPDATE
                SET onboarded_at = datetime('now')
            """,
            (company_name,),
        )
