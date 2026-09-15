"""Job lead research pipeline.

Pulls job listings from a curated set of companies, determines whether
they are worth applying to, and mails them out: a newly tracked company's
whole board as a one-off onboarding report, everything after that as the
daily digest.

Stages hand off through the database, not through XCom: each one reads the
rows the stage before it wrote. So most of the arrows below are ordering
constraints rather than data flow, and the whole graph is written out in one
place at the bottom of this file.
"""

import pendulum

from airflow.sdk import DAG, Param, chain

from job_lead_research.filter_fit import filter_fit
from job_lead_research.filter_relevance import filter_relevance
from job_lead_research.scan_boards import scan_boards
from job_lead_research.send_digest import send_digest
from job_lead_research.send_onboarding import send_onboarding_report
from job_lead_research.sync_watchlist import sync_watchlist

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
    "params": {
        "force_rescan": Param(
            False,
            type="boolean",
            description="Scan every board even if it was scanned today.",
        ),
    },
}


with DAG("job_lead_research", **DAG_ARGS) as dag:
    chain(
        sync_watchlist(),
        scan_boards(),
        filter_relevance(),
        filter_fit(),
        # The onboarding report sits upstream of the digest rather than beside it:
        # it marks its listings sent, and the digest's pool is whatever is still
        # unsent, so side-by-side tasks would race over the same rows.
        send_onboarding_report(),
        send_digest(),
    )
