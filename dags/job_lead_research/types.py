from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class ATSProvider(Enum):
    """The board platform a company's careers page is a skin over.

    Members exist for platforms we can name in the sheet, not only for ones with
    an adapter written: naming a board type is how Andrew records what he found,
    and an unsupported type should surface as "no adapter yet" in scan health
    rather than collapse into OTHER and lose the information.
    """

    ASHBY = "ashby"
    GREENHOUSE = "greenhouse"
    PINPOINT = "pinpoint"
    LEVER = "lever"
    WORKABLE = "workable"
    OTHER = "other"


@dataclass
class Company:
    name: str
    board_url: str
    board_type: ATSProvider
    board_slug: str
    status: str
    notes: str
    synced_at: str

    @staticmethod
    def load(record: Mapping[str, str]) -> "Company":
        try:
            board_type = ATSProvider(record.get("board_type", "").lower())
        except ValueError:
            board_type = ATSProvider.OTHER

        return Company(
            name=record["name"],
            board_url=record.get("board_url", ""),
            board_type=board_type,
            board_slug=record.get("board_slug", ""),
            status=record.get("status", ""),
            notes=record.get("notes", ""),
            synced_at=record.get("synced_at", ""),
        )

    def dump(self) -> tuple:
        return (
            self.name,
            self.board_url,
            self.board_type.value,
            self.board_slug,
            self.status,
            self.notes,
            self.synced_at,
        )


class RelevanceDecision(Enum):
    """Whether a listing is worth carrying into the next stage.

    ``PENDING`` is the state a freshly scraped listing is stored in, and is
    deliberately distinct from ``REJECT``: "not yet judged" and "judged and
    turned down" are different facts, and collapsing them into NULL would make a
    re-run unable to tell what still needs work. ``ERROR`` covers a judgement
    that was attempted and blew up, so a failing listing can be retried without
    being mistaken for a rejection.
    """

    PENDING = "pending"
    PASS = "pass"
    REJECT = "reject"
    ERROR = "error"


class FitDecision(Enum):
    """How well a listing matches the criteria in ``dags/criteria/job_search.md``.

    Three real verdicts rather than a boolean, so the digest can be ordered by
    how much attention a listing deserves: ``STRONG`` is worth reading first,
    ``REVIEW`` is a judgement call worth a look, ``REJECT`` is not.

    ``SKIPPED`` is the state of a listing the relevance filter turned down, and
    is a third fact distinct from both ``PENDING`` and ``REJECT``: it was never
    eligible for a fit judgement at all. Keeping it separate means the pending
    query needs no join back to ``relevance_decision``, and a count of fit
    rejections is not polluted by listings this stage never saw. It is written
    by the relevance stage, not this one -- see
    ``filter_relevance.store.save_decisions``.
    """

    PENDING = "pending"
    SKIPPED = "skipped"
    STRONG = "strong"
    REVIEW = "review"
    REJECT = "reject"
    ERROR = "error"


@dataclass
class JobListing:
    id: str
    company_name: str
    location: str
    title: str
    description: str
    listing_url: str
    published_at: str
    added_at: str = ""
    relevance_decision: RelevanceDecision = RelevanceDecision.PENDING
    relevance_rejection: str = ""
    fit_decision: FitDecision = FitDecision.PENDING
    fit_reasoning: str = ""

    @staticmethod
    def load(record: Mapping[str, str]) -> "JobListing":
        # An unreadable or absent verdict reads as unjudged rather than as a
        # rejection, so a bad value costs a re-judgement and not a dropped lead.
        try:
            relevance_decision = RelevanceDecision(
                record.get("relevance_decision", "").lower()
            )
        except ValueError:
            relevance_decision = RelevanceDecision.PENDING

        try:
            fit_decision = FitDecision(record.get("fit_decision", "").lower())
        except ValueError:
            fit_decision = FitDecision.PENDING

        return JobListing(
            id=record["id"],
            company_name=record.get("company_name", ""),
            location=record.get("location", ""),
            title=record.get("title", ""),
            description=record.get("description", ""),
            listing_url=record.get("listing_url", ""),
            published_at=record.get("published_at", ""),
            added_at=record.get("added_at", ""),
            relevance_decision=relevance_decision,
            relevance_rejection=record.get("relevance_rejection", ""),
            fit_decision=fit_decision,
            fit_reasoning=record.get("fit_reasoning", ""),
        )

    def dump(self) -> tuple:
        return (
            self.id,
            self.company_name,
            self.location,
            self.title,
            self.description,
            self.listing_url,
            self.published_at,
            self.added_at,
            self.relevance_decision.value,
            self.relevance_rejection,
            self.fit_decision.value,
            self.fit_reasoning,
        )


class RelevanceResult(BaseModel):
    """One listing's yes/no relevance verdict, as judged by the LLM filter.

    ``company_name`` and ``id`` together are how the result is matched back to
    its ``job_listings`` row -- board ids repeat across providers, so ``id``
    alone cannot identify a listing.
    """

    company_name: str
    id: str
    relevant: bool
    reasoning: str = ""


class RelevanceResults(BaseModel):
    """The whole batch of verdicts from one ``judge_listings`` call.

    ``LLMOperator``'s ``serialize_output=True`` only dumps its output to a
    dict when the output itself is a ``BaseModel`` instance -- a bare
    ``list[RelevanceResult]`` never satisfies that check, so a raw
    ``RelevanceResult`` list would cross XCom unserialized and fail
    deserialization downstream without a config change. Wrapping the list in
    one top-level model gives ``output_type`` a ``BaseModel`` to hand
    ``serialize_output`` and keeps the fix local to this type rather than
    Airflow config.
    """

    results: list[RelevanceResult]


class FitResult(BaseModel):
    """One listing's fit verdict, as judged against the criteria file.

    Matched back to its ``job_listings`` row on ``(company_name, id)``, for the
    same reason :class:`RelevanceResult` is: board ids repeat across providers.

    ``decision`` is the string form of a :class:`FitDecision` rather than the
    enum itself -- the LLM only ever returns the three real verdicts, and
    keeping the wire type a plain literal stops ``PENDING``/``SKIPPED``/
    ``ERROR`` from being offerable as model output.
    """

    company_name: str
    id: str
    decision: Literal["strong", "review", "reject"]
    reasoning: str = ""


class FitResults(BaseModel):
    """The whole batch of fit verdicts from one ``judge_fit`` call.

    Wrapped in a single top-level model for the same ``serialize_output``
    reason documented on :class:`RelevanceResults`.
    """

    results: list[FitResult]
