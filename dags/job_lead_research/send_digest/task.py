"""The digest email: the stage the rest of the pipeline exists to feed.

Everything upstream narrows a day's board scrape down to listings worth
Andrew's attention; this stage picks the best few of those, mails them, and
records that it did so. A listing is sent exactly once -- the ``sent`` column
is what makes tomorrow's digest pick up where today's left off.

Deliberately not an agent task. The per-listing prose is already written by the
fit stage, so the body is a fixed template (see :mod:`.template`) and the whole
stage is deterministic.
"""

from datetime import date

from airflow.sdk import task
from airflow.sdk.exceptions import AirflowSkipException

from common.email import send_email
from job_lead_research.send_digest import store, template
from job_lead_research.types import FitDecision, JobListing

RECIPIENT = "andrew.vagliano1@gmail.com"

# Few enough that the digest gets read and acted on in a morning. The cap
# is the point of the stage as much as the selection is: the pool it draws from
# is everything the fit filter ever passed and never sent, which after the
# initial sweep could be far more than a useful email.
DIGEST_SIZE = 5


def _subject(listings: list[JobListing]) -> str:
    """Subject line naming the count, so the inbox preview is already useful."""
    lead = "lead" if len(listings) == 1 else "leads"
    return f"{len(listings)} new job {lead} — {date.today():%d %b}"


@task
def send_digest() -> str:
    """Mail the top unsent listings and mark them sent.

    Raises ``AirflowSkipException`` when the pool is empty, so a morning with
    nothing new shows as a skipped task rather than a success that quietly sent
    no mail -- the two look identical in the run history otherwise, and only
    one of them is worth investigating.

    The mark happens after Resend accepts the message, for the reason on
    :func:`store.mark_sent`: a failed send must leave its listings eligible for
    the next run.
    """
    listings = store.unsent_listings(DIGEST_SIZE)
    if not listings:
        raise AirflowSkipException("No unsent listings to digest.")

    message_id = send_email(
        to=RECIPIENT,
        subject=_subject(listings),
        html=template.render(listings),
    )
    store.mark_sent(listings)

    counts = {decision: 0 for decision in store.SENDABLE}
    for listing in listings:
        counts[listing.fit_decision] += 1
    print(
        f"Sent {len(listings)} listings "
        f"({counts[FitDecision.STRONG]} strong, "
        f"{counts[FitDecision.REVIEW]} review) as message {message_id}."
    )

    return message_id
