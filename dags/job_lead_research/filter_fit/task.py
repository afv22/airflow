"""LLM-driven fit judgement over listings that cleared the relevance filter.

Where ``filter_relevance`` asks only "is this plausibly a UK software
engineering role", this stage asks the expensive question: is it a role worth
Andrew's time, judged against ``dags/criteria/job_search.md``. That means a
smarter model, a much larger system prompt (the criteria file travels with
every call), and a three-way verdict -- ``strong`` / ``review`` / ``reject`` --
so the digest can lead with what deserves attention first.

The task shape mirrors the relevance stage: one snapshot of the pending queue,
chunked, with each chunk becoming a mapped task instance via ``.expand()``.
The reasoning behind that shape is documented on ``get_pending_chunks`` there
and applies here unchanged.
"""

from airflow.sdk import task

from job_lead_research.filter_fit import store
from job_lead_research.filter_fit.prompt import system_prompt
from job_lead_research.prompting import escape_jinja
from job_lead_research.types import FitResult, FitResults

# Connection of type "Pydantic AI" (conn_type: pydanticai) holding the
# OpenRouter API key -- the same connection the relevance filter uses.
LLM_CONN_ID = "openrouter_default"

# A stronger model than the relevance filter's: this stage is the one making
# the judgement call the whole pipeline exists to make, and it is reading a
# full criteria document rather than two bullet points.
MODEL_ID = "openrouter:z-ai/glm-5.2"

# Smaller than the relevance filter's chunk. Every call carries the whole
# criteria file (~7KB) on top of the descriptions, and the judgement is more
# careful per listing, so the batch stays small enough that no one listing gets
# skimmed.
CHUNK_SIZE = 3


def _format_listing(listing: dict) -> str:
    """Format an already Jinja-escaped listing dict (see ``get_pending_chunks``)."""
    return (
        f"company_name: {listing['company_name']}\n"
        f"id: {listing['id']}\n"
        f"title: {listing['title']}\n"
        f"location: {listing['location']}\n"
        f"published_at: {listing['published_at']}\n"
        f"description: {listing['description']}\n"
    )


@task.llm(
    llm_conn_id=LLM_CONN_ID,
    model_id=MODEL_ID,
    # Built at parse time by reading the criteria file, so an edit to the
    # criteria is picked up whenever Airflow next reparses this DAG.
    system_prompt=system_prompt(),
    # One top-level model rather than list[FitResult], for the serialize_output
    # reason documented on FitResults.
    output_type=FitResults,
    serialize_output=True,
)
def judge_fit(chunk: list[dict]) -> str:
    """Return the prompt; the LLM's parsed reply becomes this task's XCom.

    ``chunk`` is already Jinja-escaped by :func:`get_pending_chunks` -- safe to
    pass straight through ``op_kwargs``, which Airflow always renders through
    Jinja before this callable runs.
    """
    listing_blocks = "\n---\n".join(_format_listing(listing) for listing in chunk)
    return f"Judge the following {len(chunk)} job listings:\n\n{listing_blocks}"


# Explicit task ids: the relevance stage defines tasks with these same
# function names, and two tasks named alike in one DAG get an auto-generated
# "__1" suffix whose assignment depends on import order. Naming them here
# keeps the graph readable and stable.
@task(task_id="save_fit_verdicts", trigger_rule="all_done")
def save_verdicts(chunks: list[list[dict]], results: list[dict]) -> int:
    """Persist every chunk's verdicts, and log anything left unjudged.

    A listing whose verdict never lands here -- because it was dropped from its
    chunk's reply, or because the whole chunk's LLM call failed -- is left
    ``pending`` and picked up again next run, rather than silently marked
    either way.

    ``trigger_rule="all_done"`` rather than the default ``all_success``: the
    upstream is a mapped task, and under ``all_success`` a single failing chunk
    skips this task entirely, discarding the verdicts every *other* chunk paid
    an LLM call to produce. Those results only ever live in XCom, so nothing
    would write them and the whole batch would be re-judged next run. Failed
    map instances are simply absent from ``results`` (verified: they are
    omitted, not passed as ``None``), so the batch saves whatever arrived and
    the rest stays pending.
    """
    verdicts = [
        FitResult(**result)
        for chunk_result in results
        for result in chunk_result["results"]
    ]
    saved = store.save_decisions(verdicts)
    listings = [listing for chunk in chunks for listing in chunk]

    counts = {decision: 0 for decision in ("strong", "review", "reject")}
    for verdict in verdicts:
        counts[verdict.decision] += 1
    print(
        f"Judged {saved} of {len(listings)} pending listings: "
        f"{counts['strong']} strong, {counts['review']} review, "
        f"{counts['reject']} reject."
    )

    judged_keys = {(v.company_name, v.id) for v in verdicts}
    missing = [
        listing
        for listing in listings
        if (listing["company_name"], listing["id"]) not in judged_keys
    ]
    if missing:
        print(f"{len(missing)} listings got no verdict and remain pending.")

    return saved


@task(task_id="get_fit_pending_chunks")
def get_pending_chunks() -> list[list[dict]]:
    """A one-time snapshot of fit-pending listings, batched and Jinja-escaped.

    Taken once here rather than re-queried inside each mapped task, for the
    race documented on the relevance stage's equivalent: ``save_verdicts``
    writes to the same rows this reads, so a chunk count computed against one
    query can go stale by the time another task re-queries it.

    Plain dicts rather than ``JobListing`` dataclasses: the decision fields are
    enums, and Airflow's default XCom serde cannot round-trip an enum field on
    a dataclass.
    """
    listings = [
        {
            # company_name and id are exact-match keys used to write verdicts
            # back to the row -- left unescaped so the LLM echoes them back
            # byte-for-byte; only free text the LLM merely reads gets escaped.
            "company_name": listing.company_name,
            "id": listing.id,
            "title": escape_jinja(listing.title),
            "location": escape_jinja(listing.location),
            # The criteria ask for listings older than ~30 days to be flagged,
            # so the judge needs the date the relevance filter never saw.
            "published_at": escape_jinja(listing.published_at),
            "description": escape_jinja(listing.description),
        }
        for listing in store.pending_listings()
    ]
    return [listings[i : i + CHUNK_SIZE] for i in range(0, len(listings), CHUNK_SIZE)]


def filter_fit(upstream=None):
    """Judge every fit-pending listing, in chunks, and write verdicts back.

    ``upstream`` is the task (or XComArg) that should finish before this group
    starts reading ``job_listings`` -- normally the relevance stage, which is
    what puts listings into the ``pending`` fit state. Composing with ``>>``
    only wires an edge to whatever this function *returns*, so without an
    explicit dependency here ``get_pending_chunks`` would have no upstream at
    all and could run before the filter that populates its queue.
    """
    chunks = get_pending_chunks()
    if upstream is not None:
        upstream >> chunks  # type: ignore

    results = judge_fit.expand(chunk=chunks)  # type: ignore
    return save_verdicts(chunks, results)  # type: ignore
