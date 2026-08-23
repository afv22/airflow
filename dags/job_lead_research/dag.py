"""Job lead research pipeline.

Round 1 in progress: only ``sync_watchlist`` exists so far, so this DAG does
nothing but mirror the sheet. It is here to give that task a real task context
-- the Task SDK resolves Connections through the execution API, which means a
connection is only readable from inside a running task, not from a bare
``python -c`` in the container.

Run it on demand while building:

    airflow dags test job_lead_research

sync_watchlist mirrors the sheet, scan_boards polls each active company's ATS
through its adapter, and filter_relevance runs the coarse LLM pass that clears
obviously irrelevant listings out of the pending queue. Research and digest
slot in downstream of that.
"""

import pendulum

from airflow.sdk import DAG

from job_lead_research.sync_watchlist import sync_watchlist
from job_lead_research.scan_boards import scan_boards
from job_lead_research.filter_relevance import filter_relevance

DAG_ARGS = {
    "default_args": {
        "owner": "andrew",
        "retries": 0,
        "email_on_failure": True,
        "email_on_retry": False,
    },
    # Unscheduled while the pipeline is a stub: it is triggered by hand during
    # development. Round 1 gives it the daily 5am slot alongside job_research.
    "schedule": None,
    "start_date": pendulum.datetime(2026, 8, 20, tz="UTC"),
    "catchup": False,
    "max_active_runs": 1,
    "tags": ["jobs", "watchlist"],
}


with DAG("job_lead_research", **DAG_ARGS) as dag:
    # scan_boards reads the companies out of the mirror rather than taking them
    # as an argument, so the dependency is ordering, not data.
    boards_scanned = scan_boards()
    sync_watchlist() >> boards_scanned
    filter_relevance(upstream=boards_scanned)
