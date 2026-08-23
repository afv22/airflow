"""LLM-driven first pass over pending job listings.

This stage exists to cut out the obviously irrelevant, not to judge fit: a
listing only fails here if it clearly is not UK-remote/London and clearly is
not some kind of broad software engineering role. Later stages do the actual
fit judgement, so this one is deliberately biased to let borderline cases
through rather than reject them.

Pending listings are judged in small batches rather than one call per listing
or one call for the whole day: descriptions are often long and formatted, so
a handful per prompt keeps each call's context reasonable while still saving
the overhead of a separate call per listing. Each chunk becomes one mapped
task instance via ``.expand()``.
"""

from airflow.sdk import task

from job_lead_research.filter_relevance import store
from job_lead_research.filter_relevance.prompt import SYSTEM_PROMPT
from job_lead_research.types import RelevanceResult, RelevanceResults

# Connection of type "Pydantic AI" (conn_type: pydanticai) holding the
# OpenRouter API key -- same connection used in dags/test_email.py.
LLM_CONN_ID = "openrouter_default"
MODEL_ID = "openrouter:deepseek/deepseek-v4-flash-0731"

# How many listings go into one LLM call. Descriptions can be long and
# formatted, so this stays small rather than trying to fit the whole day's
# batch into one prompt.
CHUNK_SIZE = 5


def _escape_jinja(text: str) -> str:
    """Neutralize Jinja delimiters so scraped text can't be parsed as a template.

    ``LLMOperator.prompt`` is a ``template_field``, so the whole prompt this
    task builds gets rendered through Jinja before the LLM ever sees it.
    Scraped job descriptions are free text from other people's sites and have
    turned up literal ``{{...}}`` (an unrendered salary-range placeholder from
    the source page) that Jinja then fails to parse as its own syntax. Widen
    the delimiters with a zero-width space so Jinja no longer recognizes them,
    while leaving the text visually unchanged for the LLM.
    """
    zwsp = "​"
    return (
        text.replace("{{", f"{{{zwsp}{{")
        .replace("}}", f"}}{zwsp}}}")
        .replace("{%", f"{{{zwsp}%")
        .replace("%}", f"%{zwsp}}}")
        .replace("{#", f"{{{zwsp}#")
        .replace("#}", f"#{zwsp}}}")
    )


def _format_listing(listing: dict) -> str:
    """Format an already Jinja-escaped listing dict (see ``get_pending_chunks``)."""
    return (
        f"company_name: {listing['company_name']}\n"
        f"id: {listing['id']}\n"
        f"title: {listing['title']}\n"
        f"location: {listing['location']}\n"
        f"description: {listing['description']}\n"
    )


@task.llm(
    llm_conn_id=LLM_CONN_ID,
    model_id=MODEL_ID,
    system_prompt=SYSTEM_PROMPT,
    # A single top-level model rather than list[RelevanceResult]: LLMOperator's
    # serialize_output only dumps to a dict when the output itself is a
    # BaseModel instance, which a bare list of models never is. Wrapping the
    # list in one model gives serialize_output something to act on, so a
    # plain dict crosses XCom instead of a raw RelevanceResult that only the
    # producing task's process knows how to deserialize.
    output_type=RelevanceResults,
    serialize_output=True,
)
def judge_listings(chunk: list[dict]) -> str:
    """Return the prompt; the LLM's parsed reply becomes this task's XCom.

    ``chunk`` is already Jinja-escaped by :func:`get_pending_chunks` -- safe to
    pass straight through ``op_kwargs``, which Airflow always renders through
    Jinja before this callable runs.
    """
    listing_blocks = "\n---\n".join(_format_listing(listing) for listing in chunk)
    return f"Judge the following {len(chunk)} job listings:\n\n{listing_blocks}"


@task
def save_verdicts(chunks: list[list[dict]], results: list[dict]) -> int:
    """Persist every chunk's verdicts, and log anything left unjudged.

    A listing whose verdict never lands here -- because it was dropped from
    its chunk's reply -- is left ``pending`` and picked up again next run,
    rather than silently marked either way.
    """
    verdicts = [
        RelevanceResult(**result)
        for chunk_result in results
        for result in chunk_result["results"]
    ]
    saved = store.save_decisions(verdicts)
    listings = [listing for chunk in chunks for listing in chunk]
    passed = sum(1 for v in verdicts if v.relevant)
    print(
        f"Judged {saved} of {len(listings)} pending listings: "
        f"{passed} passed, {saved - passed} rejected."
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


@task
def get_pending_chunks() -> list[list[dict]]:
    """A one-time snapshot of pending listings, batched into ``CHUNK_SIZE``
    groups and Jinja-escaped, ready to hand straight to ``judge_listings``.

    Taken once here rather than re-queried inside each mapped task: a mapped
    ``judge_listings`` instance and ``save_verdicts`` used to each call
    ``store.pending_listings()`` independently, and since ``save_verdicts``
    writes verdicts back to the same ``pending`` rows this stage reads, a
    chunk count computed against one query could go stale by the time another
    task re-queried -- observed as an ``IndexError`` when a later chunk's
    listings had already been judged and removed from the pending set out
    from under it. One snapshot, threaded through XCom, removes the race
    instead of just narrowing it.

    Plain dicts rather than ``JobListing`` dataclasses: ``relevance_decision``
    is an enum, and Airflow's default XCom serde cannot round-trip an enum
    field on a dataclass.
    """
    listings = [
        {
            # company_name and id are exact-match keys used to write verdicts
            # back to the row -- left unescaped so the LLM echoes them back
            # byte-for-byte; only free text the LLM merely reads gets escaped.
            "company_name": listing.company_name,
            "id": listing.id,
            "title": _escape_jinja(listing.title),
            "location": _escape_jinja(listing.location),
            "description": _escape_jinja(listing.description),
        }
        for listing in store.pending_listings()
    ]
    return [listings[i : i + CHUNK_SIZE] for i in range(0, len(listings), CHUNK_SIZE)]


def filter_relevance(upstream=None):
    """Judge every pending listing, in chunks, and write verdicts back.

    ``upstream`` is the task (or XComArg) that should finish before this group
    starts reading ``job_listings`` -- e.g. ``scan_boards()``. Composing tasks
    with ``>>`` only wires an edge to whatever this function *returns*
    (``save_verdicts``), so without an explicit dependency here,
    ``get_pending_chunks`` would have no upstream at all and could run before
    the scan that populates the table it reads.
    """
    chunks = get_pending_chunks()
    if upstream is not None:
        upstream >> chunks  # type: ignore

    results = judge_listings.expand(chunk=chunks)  # type: ignore
    return save_verdicts(chunks, results)  # type: ignore
