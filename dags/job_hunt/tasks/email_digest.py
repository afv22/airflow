from airflow.sdk import task

from common.email import send_email
from job_hunt.types import Verdict
from job_hunt.utils import format_job

RECIPIENT = "andrew.vagliano1@gmail.com"


@task
def email_digest(approved: list[dict]) -> str | None:
    if not approved:
        return None

    # Strong matches first; within a band the upstream recency order holds.
    approved.sort(key=lambda job: job["verdict"] != Verdict.STRONG)

    sections = []
    for band in (Verdict.STRONG, Verdict.REVIEW, Verdict.REJECT):
        jobs = [job for job in approved if job["verdict"] == band]
        if not jobs:
            continue
        items = "".join(format_job(job) for job in jobs)
        sections.append(f"<h2>{band.capitalize()} ({len(jobs)})</h2><ul>{items}</ul>")

    strong = sum(job["verdict"] == Verdict.STRONG for job in approved)
    return send_email(
        to=RECIPIENT,
        subject=f"{len(approved)} job(s) worth a look ({strong} strong)",
        html="".join(sections),
    )
