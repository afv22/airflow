import requests
from datetime import datetime, timedelta, timezone

from airflow.sdk import task

from job_hunt import store
from job_hunt.types import HackerNewsJob

HACKERNEWS_BASE_URL = "https://hacker-news.firebaseio.com/v0"

MAX_JOB_AGE = timedelta(days=10)

# Ceiling on fan-out, so an unusually busy day can't spawn unbounded LLM calls.
MAX_JOBS = 25


def fetch_recent_listings() -> list[HackerNewsJob]:
    """Return every current HackerNews job listing newer than MAX_JOB_AGE."""
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
        jobs.append(job)

    return jobs


def as_listing_row(job: HackerNewsJob) -> dict:
    """Map the HackerNews payload onto the columns of ``job_listings``."""
    return {
        "source": store.SOURCE_HACKERNEWS,
        "source_id": str(job.id),
        "title": job.title,
        "url": job.url,
        "posted_at": store.utc_timestamp(job.time),
        "body": job.text,
    }


@task
def fetch_hn_jobs() -> list[dict]:
    """Record current HackerNews listings and return the ones still to research.

    Listings stay on the HackerNews board for days, so most of what we fetch has
    been seen before. Everything gets written to the table, but only listings
    without a verdict are handed downstream, which keeps the agent from paying
    to research the same posting on consecutive runs.
    """
    store.init_schema()

    listings = [as_listing_row(job) for job in fetch_recent_listings()]
    store.upsert_listings(listings)

    return store.unresearched(store.SOURCE_HACKERNEWS, limit=MAX_JOBS)
