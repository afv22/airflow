"""Job lead research pipeline.

Pulls job listings from a curated set of companies, determines whether
they are worth applying to, and mails them out: a newly tracked company's
whole board as a one-off onboarding report, everything after that as the
daily digest.

"""

import pendulum

from airflow.sdk import DAG

from job_lead_research.sync_watchlist import sync_watchlist
from job_lead_research.scan_boards import scan_boards
from job_lead_research.filter_relevance import filter_relevance
from job_lead_research.filter_fit import filter_fit
from job_lead_research.send_onboarding import send_onboarding_report
from job_lead_research.send_digest import send_digest

DAG_ARGS = {
    "default_args": {
        "owner": "andrew",
        "retries": 0,
        "email_on_failure": True,
        "email_on_retry": False,
    },
    # Every weekday at 6am London time (Mon-Fri)
    "schedule": "0 6 * * 1-5",
    "start_date": pendulum.datetime(2026, 8, 20, tz="Europe/London"),
    "catchup": False,
    "max_active_runs": 1,
    "tags": ["jobs", "watchlist"],
}


with DAG("job_lead_research", **DAG_ARGS) as dag:  # type: ignore
    # scan_boards reads the companies out of the mirror rather than taking them
    # as an argument, so the dependency is ordering, not data.
    boards_scanned = scan_boards()
    sync_watchlist() >> boards_scanned  # type: ignore
    relevance_filtered = filter_relevance(upstream=boards_scanned)
    # The report runs before the digest rather than beside it: it marks its
    # listings sent, and the digest's pool is whatever is still unsent, so
    # parallel tasks would race over the same rows. Most mornings no company is
    # owed a report and the task skips, which is why the digest is
    # none_failed -- under the default all_success that skip would cascade and
    # the digest would never send.
    fit_done = filter_fit(upstream=relevance_filtered)
    fit_done >> send_onboarding_report() >> send_digest()  # type: ignore
