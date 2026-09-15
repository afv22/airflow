"""LLM-driven first pass over pending job listings.

This stage exists to cut out the obviously irrelevant, not to judge fit: a
listing only fails here if it clearly is not UK-remote/London and clearly is
not some kind of broad software engineering role. Later stages do the actual
fit judgement, so this one is deliberately biased to let borderline cases
through rather than reject them.

Pending listings are judged in small batches rather than one call per listing
or one call for the whole day: descriptions are often long and formatted, so
a handful per prompt keeps each call's context reasonable while still saving
the overhead of a separate call per listing.
"""

import asyncio

from airflow.sdk import BaseHook, task
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from job_lead_research.filter_relevance import store
from job_lead_research.filter_relevance.prompt import SYSTEM_PROMPT
from job_lead_research.types import JobListing, RelevanceResults

# Connection of type "Pydantic AI" (conn_type: pydanticai) holding the
# OpenRouter API key -- same connection used in dags/test_email.py.
LLM_CONN_ID = "openrouter_default"
MODEL_ID = "deepseek/deepseek-v4-flash-0731"

# How many listings go into one LLM call. Descriptions can be long and
# formatted, so this stays small rather than trying to fit the whole day's
# batch into one prompt.
CHUNK_SIZE = 5

MAX_CONCURRENCY = 4


def format_listing(listing: JobListing) -> str:
    return (
        f"company_name: {listing.company_name}\n"
        f"id: {listing.id}\n"
        f"title: {listing.title}\n"
        f"location: {listing.location}\n"
        f"description: {listing.description}\n"
    )


def format_listings(listings: list[JobListing]) -> str:
    blocks = "\n---\n".join(format_listing(l) for l in listings)
    return f"Judge the following {len(listings)} job listings:\n\n{blocks}"


def _agent() -> Agent[None, RelevanceResults]:
    conn = BaseHook.get_connection(LLM_CONN_ID)
    model = OpenRouterModel(
        MODEL_ID, provider=OpenRouterProvider(api_key=conn.password)
    )
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        output_type=RelevanceResults,
        model_settings=ModelSettings(timeout=60),
    )


@task
def filter_relevance() -> dict[str, int]:
    listings = store.pending_listings()
    batches = [
        listings[i : i + CHUNK_SIZE] for i in range(0, len(listings), CHUNK_SIZE)
    ]
    agent = _agent()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def judge(batch):
        async with sem:
            return await agent.run(format_listings(batch))

    async def run_all() -> dict[str, int]:
        results = await asyncio.gather(
            *(judge(b) for b in batches), return_exceptions=True
        )

        passed = rejected = errored = 0
        for batch, result in zip(batches, results):
            try:
                if isinstance(result, BaseException):
                    raise result
                verdicts = result.output.results
                store.save_decisions(verdicts)
                passed += sum(1 for v in verdicts if v.relevant)
                rejected += sum(1 for v in verdicts if not v.relevant)
            except BaseException as exc:
                print(f"Batch of {len(batch)} listings failed: {exc!r}")
                try:
                    store.mark_errored(batch, str(exc))
                    errored += len(batch)
                except Exception as mark_exc:
                    print(f"Could not mark batch errored: {mark_exc!r}")

        unsettled = len(listings) - passed - rejected - errored
        return {
            "passed": passed,
            "rejected": rejected,
            "errored": errored,
            "unsettled": unsettled,
        }

    return asyncio.run(run_all())
