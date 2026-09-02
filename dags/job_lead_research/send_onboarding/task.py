"""The opening report: one email per newly tracked company, its whole board.

When a company joins the watchlist, the first sweep scrapes its entire board at
once and the fit stage can pass a lot of it. The daily digest is capped and
company-blind, so that backlog would dribble out over a week, interleaved with
everyone else's leads and never seen as a set. Picking the two or three roles
actually worth applying to is a human judgement that needs the cluster in one
place, once.

Running upstream of ``send_digest`` and marking its listings sent is what keeps
the two from overlapping: by the time the digest queries its pool, this
company's roles are already spoken for, and the digest goes back to carrying
only deltas from companies already onboarded.

Deterministic, like the digest -- the per-listing prose was written by the fit
stage, so this stage only selects, groups, and mails.
"""

from datetime import date

from airflow.sdk import task
from airflow.sdk.exceptions import AirflowSkipException

from common.email import send_email
from job_lead_research.send_digest.store import mark_sent
from job_lead_research.send_onboarding import store, template
from job_lead_research.types import FitDecision, JobListing

RECIPIENT = "andrew.vagliano1@gmail.com"


def _subject(company_name: str, listings: list[JobListing]) -> str:
    """Subject naming the company and the split, e.g.
    ``Onboarding: Acme — 8 roles (3 strong) — 02 Sep``.

    Several of these can land in one morning, so the company name leads: the
    inbox list is the first place they need telling apart.
    """
    strong = sum(1 for listing in listings if listing.fit_decision is FitDecision.STRONG)
    roles = "role" if len(listings) == 1 else "roles"
    return (
        f"Onboarding: {company_name} — {len(listings)} {roles} "
        f"({strong} strong) — {date.today():%d %b}"
    )


def _report_on(company_name: str) -> bool:
    """Send one company's report and record it. True if an email went out.

    Deferral comes first: a company still holding ``pending`` fit verdicts had
    its sweep cut short, and reporting now would mail a board with holes in it.
    Leaving it owed costs a day and buys the complete cluster the report exists
    to be.

    A company with nothing sendable is marked onboarded without an email --
    "nothing here worth your time" is a normal outcome for a board, not a
    report worth sending, and re-asking every morning would leave it
    permanently owed.

    Writes follow the digest's send-first ordering, for the reason on
    :func:`..send_digest.store.mark_sent`: a send that raises leaves the
    company owed and its listings eligible, so tomorrow retries cleanly, while
    a send that lands and fails to mark costs at worst one duplicate report.
    """
    pending = store.pending_count(company_name)
    if pending:
        print(
            f"{company_name}: deferring, {pending} listings still awaiting a fit "
            f"verdict. Stays owed a report."
        )
        return False

    listings = store.sendable_listings(company_name)
    if not listings:
        store.mark_onboarded(company_name)
        print(f"{company_name}: nothing sendable on the board; onboarded, no email.")
        return False

    message_id = send_email(
        name="Job Leads",
        to=RECIPIENT,
        subject=_subject(company_name, listings),
        html=template.render(company_name, listings),
    )
    mark_sent(listings)
    store.mark_onboarded(company_name)

    print(
        f"{company_name}: {template.summary(listings)} -- sent as message {message_id}."
    )
    return True


@task
def send_onboarding_report() -> int:
    """Mail every owed company its opening report; return how many were sent.

    Raises ``AirflowSkipException`` when nothing is owed, which is most
    mornings -- consistent with ``send_digest``, and the reason the digest is
    wired with ``trigger_rule="none_failed"``: a skip here must not skip the
    digest behind it.

    One company's failure does not stop the others, the same stance
    ``scan_boards`` takes on a broken board. The companies are independent --
    one bad Resend call says nothing about the next company's email -- and the
    failed one stays owed, so it simply reports tomorrow. Failures are
    collected and raised at the end so the run is visibly red rather than
    quietly short.
    """
    owed = store.companies_owed_report()
    if not owed:
        raise AirflowSkipException("No companies owed an onboarding report.")

    sent = 0
    failed: list[str] = []
    for company_name in owed:
        try:
            if _report_on(company_name):
                sent += 1
        except Exception as error:
            failed.append(f"{company_name}: {error}")

    print(f"Reported on {sent} of {len(owed)} companies owed a report.")
    if failed:
        raise RuntimeError(
            f"Onboarding report failed for {len(failed)}: {'; '.join(failed)}"
        )

    return sent
