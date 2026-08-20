"""Job lead research pipeline.

Round 1 in progress: only ``sync_watchlist`` exists so far, so this DAG does
nothing but mirror the sheet. It is here to give that task a real task context
-- the Task SDK resolves Connections through the execution API, which means a
connection is only readable from inside a running task, not from a bare
``python -c`` in the container.

Run it on demand while building:

    airflow dags test job_lead_research

Next pieces slot in downstream of sync_watchlist: scan_board (fan-out over the
returned companies), the mechanical prefilter, then research and digest.
"""

import pendulum

from airflow.sdk import DAG

from job_lead_research.sync_watchlist import sync_watchlist

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
    sync_watchlist()
