"""Manual-only smoke test that the Airflow runner is alive.

Does no real work and touches nothing outside itself: it exists so that
triggering one run answers the question "is the scheduler picking up DAGs and
is the worker executing tasks?" without waiting on a real pipeline.

Unscheduled on purpose -- schedule=None means it only ever runs when someone
triggers it from the UI or the CLI.
"""

import os
import platform
import sys
from datetime import datetime, timezone

import pendulum

from airflow.sdk import DAG, task

DAG_ARGS = {
    "default_args": {
        "owner": "andrew",
        "retries": 0,
        # A failure here is something you went looking for, so don't also mail it.
        "email_on_failure": False,
        "email_on_retry": False,
    },
    "schedule": None,
    "start_date": pendulum.datetime(2026, 8, 20, tz="Europe/London"),
    "catchup": False,
    "max_active_runs": 1,
    "tags": ["ops", "health-check"],
}


@task
def report_environment() -> dict:
    """Log what the worker looks like and hand it back as the task's XCom."""
    info = {
        "utc_now": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "python": sys.version.split()[0],
        "executor_pid": os.getpid(),
        "cwd": os.getcwd(),
    }
    for key, value in info.items():
        print(f"{key}: {value}")
    return info


with DAG("health_check", **DAG_ARGS) as dag:
    report_environment()
