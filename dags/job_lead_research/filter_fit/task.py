"""LLM-driven fit judgement over listings that cleared the relevance filter.

Where ``filter_relevance`` asks only "is this plausibly a UK software
engineering role", this stage asks the expensive question: is it a role worth
Andrew's time, judged against the ``job-criteria`` Airflow Variable. That means
a smarter model, a much larger system prompt (the criteria document travels with
every call), and a three-way verdict -- ``strong`` / ``review`` / ``reject`` --
so the digest can lead with what deserves attention first.

The task shape mirrors the relevance stage: one snapshot of the pending queue,
chunked, with each chunk getting processed individually.
"""

import asyncio

from airflow.sdk import task
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel

from common.openrouter import provider
from job_lead_research.filter_fit import store
from job_lead_research.filter_fit.prompt import system_prompt
from job_lead_research.types import FitResults, JobListing

# A stronger model than the relevance filter's: this stage is the one making
# the judgement call the whole pipeline exists to make, and it is reading a
# full criteria document rather than two bullet points.
MODEL_ID = "z-ai/glm-5.3-flash"

# Smaller than the relevance filter's chunk. Every call carries the whole
# criteria document (~7KB) on top of the descriptions, and the judgement is more
# careful per listing, so the batch stays small enough that no one listing gets
# skimmed.
CHUNK_SIZE = 3

MAX_CONCURRENCY = 4


def _agent() -> Agent[None, FitResults]:
    model = OpenRouterModel(MODEL_ID, provider=provider())
    return Agent(
        model=model,
        system_prompt=system_prompt(),
        output_type=FitResults,
        model_settings=ModelSettings(timeout=120),
    )


def format_listing(listing: JobListing) -> str:
    return (
        f"company_name: {listing.company_name}\n"
        f"id: {listing.id}\n"
        f"title: {listing.title}\n"
        f"location: {listing.location}\n"
        f"published_at: {listing.published_at}\n"
        f"description: {listing.description}\n"
    )


def format_listings(listings: list[JobListing]) -> str:
    blocks = "\n---\n".join(format_listing(l) for l in listings)
    return f"Judge the following {len(listings)} job listings:\n\n{blocks}"


@task
def filter_fit() -> dict[str, int]:
    listings = store.pending_listings()
    batches = [
        listings[i : i + CHUNK_SIZE] for i in range(0, len(listings), CHUNK_SIZE)
    ]
    agent = _agent()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def judge(batch: list[JobListing]):
        async with sem:
            return await agent.run(format_listings(batch))

    async def run_all() -> dict[str, int]:
        results = await asyncio.gather(
            *(judge(b) for b in batches), return_exceptions=True
        )

        strong = review = rejected = errored = 0
        for batch, result in zip(batches, results):
            try:
                if isinstance(result, BaseException):
                    raise result
                verdicts = result.output.results
                store.save_decisions(verdicts)
                strong += sum(1 for v in verdicts if v.decision == "strong")
                review += sum(1 for v in verdicts if v.decision == "review")
                rejected += sum(1 for v in verdicts if v.decision == "pass")

            except BaseException as exc:
                print(f"Batch of {len(batch)} listings failed: {exc!r}")
                try:
                    store.mark_errored(batch, str(exc))
                    errored += len(batch)
                except Exception as mark_exc:
                    print(f"Could not mark batch errored: {mark_exc!r}")

        unsettled = len(listings) - strong - review - rejected - errored
        return {
            "strong": strong,
            "review": review,
            "rejected": rejected,
            "errored": errored,
            "unsettled": unsettled,
        }

    return asyncio.run(run_all())
