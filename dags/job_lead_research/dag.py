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

from airflow.sdk import DAG, chain

from job_lead_research import filter_fit, filter_relevance
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
}


with DAG("job_lead_research", **DAG_ARGS) as dag:  # type: ignore
    watchlist_synced = sync_watchlist()
    boards_scanned = scan_boards()

    # Both filter stages work the same way: snapshot the pending listings into
    # chunks, judge the chunks in parallel, then write every verdict back in
    # one task. The snapshot is what the previous stage has to finish before,
    # and the save is what the next stage has to wait for.
    relevance_chunks = filter_relevance.get_pending_chunks()
    relevance_verdicts = filter_relevance.save_verdicts(
        relevance_chunks,  # type: ignore
        filter_relevance.judge_listings.expand(chunk=relevance_chunks),  # type: ignore
    )

    fit_chunks = filter_fit.get_pending_chunks()
    fit_verdicts = filter_fit.save_verdicts(
        fit_chunks,  # type: ignore
        filter_fit.judge_fit.expand(chunk=fit_chunks),  # type: ignore
    )

    onboarding_sent = send_onboarding_report()
    digest_sent = send_digest()

    # The onboarding report sits upstream of the digest rather than beside it:
    # it marks its listings sent, and the digest's pool is whatever is still
    # unsent, so side-by-side tasks would race over the same rows. Most
    # mornings no company is owed a report and that task skips, which is why
    # send_digest is declared none_failed -- under the default all_success the
    # skip would cascade and the digest would never send.
    chain(
        watchlist_synced,
        boards_scanned,
        relevance_chunks,
        relevance_verdicts,
        fit_chunks,
        fit_verdicts,
        onboarding_sent,
        digest_sent,
    )
