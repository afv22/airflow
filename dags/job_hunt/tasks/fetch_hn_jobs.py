import requests
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from airflow.sdk import task

from job_hunt.types import HackerNewsJob

HACKERNEWS_BASE_URL = "https://hacker-news.firebaseio.com/v0"

MAX_JOB_AGE = timedelta(days=14)

# Ceiling on fan-out, so an unusually busy day can't spawn unbounded LLM calls.
MAX_JOBS = 25


@task
def fetch_hn_jobs() -> list[dict]:
    """Fetch recent job listings from HackerNews."""
    r = requests.get(f"{HACKERNEWS_BASE_URL}/jobstories.json")
    r.raise_for_status()

    cutoff = datetime.now(timezone.utc) - MAX_JOB_AGE
    jobs = []
    for job_id in r.json():
        r_job = requests.get(f"{HACKERNEWS_BASE_URL}/item/{job_id}.json")
        r_job.raise_for_status()
        payload = r_job.json()
        if not payload or payload.get("url") is None:
            # Text-only "who is hiring" style posts have nothing to research.
            continue

        job = HackerNewsJob(**payload)
        # jobstories is not strictly time-ordered, so filter rather than break.
        if datetime.fromtimestamp(job.time, tz=timezone.utc) < cutoff:
            continue
        jobs.append(asdict(job))

    jobs.sort(key=lambda j: j["time"], reverse=True)
    return jobs[:MAX_JOBS]
