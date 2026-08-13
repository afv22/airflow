from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field


@dataclass
class HackerNewsJob:
    id: int
    time: int
    title: str
    by: str
    type: str
    url: str
    text: str | None = None
    score: int = 0


class Verdict(StrEnum):
    STRONG = "strong"
    REVIEW = "review"
    REJECT = "reject"


class ExtractedDetails(BaseModel):
    """Facts pulled straight from the listing, ``None`` where not stated."""

    seniority_band: str | None = Field(description="Seniority band exactly as stated.")
    comp_range: str | None = Field(description="Compensation range if given.")
    location: str | None = Field(
        description="Location and hybrid policy, with days in office if given."
    )
    posting_date: str | None = Field(description="Date the listing was posted.")


class JobVerdict(BaseModel):
    """Structured decision the research agent returns for a single listing."""

    verdict: Verdict = Field(
        description=(
            "'strong' for a clear match, 'review' when uncertain, 'reject' only "
            "when a hard-reject trigger is hit. Bias toward 'review'."
        )
    )
    reasons: list[str] = Field(
        description="One to three short bullets citing specific JD language.",
        min_length=1,
        max_length=3,
    )
    disqualifiers: list[str] = Field(
        description="Hard-reject triggers hit, empty if none.",
        default_factory=list,
    )
    # extracted: ExtractedDetails
