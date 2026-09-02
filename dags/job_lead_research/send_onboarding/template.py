"""HTML rendering for the onboarding report email.

The digest's template with two changes: a count header, and section headings
that name what the reader is looking at. The digest arrives daily and needs no
introduction; this arrives once per company, carries a whole board, and is
meant to be sat down with -- so it says up front how much there is and how it
splits, giving the sift a shape before the scroll.

Card styling is imported from :mod:`..send_digest.template` rather than
restated. The two emails should look like the same system, and a divergence
here would be a silent one -- nothing renders both side by side.
"""

from html import escape

from job_lead_research.send_digest.template import (
    BODY_STYLE,
    HEADING_STYLE,
    _render_listing,
)
from job_lead_research.types import FitDecision, JobListing

# Ordered best-first, and this ordering is what the sections are emitted in.
# The wording differs from the digest's ("Strong fit" / "Worth a look"): these
# head a section of many rather than labelling one card.
TIER_HEADINGS = {
    FitDecision.STRONG: ("Strong fits", "#1a7f37"),
    FitDecision.REVIEW: ("Worth review", "#9a6700"),
}

INTRO_STYLE = "font-size: 15px; margin: 0 0 4px;"
COUNT_STYLE = "color: #59636e; font-size: 14px; margin: 0;"


def _counts(listings: list[JobListing]) -> dict[FitDecision, int]:
    """How many listings fall under each tier heading."""
    counts = {decision: 0 for decision in TIER_HEADINGS}
    for listing in listings:
        if listing.fit_decision in counts:
            counts[listing.fit_decision] += 1
    return counts


def summary(listings: list[JobListing]) -> str:
    """The one-line shape of the report: "8 roles passed filtering: 3 strong, 5 review".

    Shared with :mod:`.task`, which puts the same numbers in the subject line,
    so the inbox preview and the email agree without computing the split twice.
    """
    counts = _counts(listings)
    roles = "role" if len(listings) == 1 else "roles"
    return (
        f"{len(listings)} {roles} passed filtering: "
        f"{counts[FitDecision.STRONG]} strong, {counts[FitDecision.REVIEW]} review"
    )


def render(company_name: str, listings: list[JobListing]) -> str:
    """Render one company's whole sendable board, grouped by verdict tier.

    An empty tier is dropped rather than headed with nothing under it, matching
    the digest. The company is named in the intro because, unlike the digest,
    every card here carries the same company and the name would otherwise only
    appear in the subject.
    """
    sections = []
    for decision, (heading, colour) in TIER_HEADINGS.items():
        tier = [listing for listing in listings if listing.fit_decision is decision]
        if not tier:
            continue
        cards = "".join(_render_listing(listing) for listing in tier)
        sections.append(
            f'<p style="{HEADING_STYLE} color: {colour};">{heading}</p>{cards}'
        )

    return (
        f'<div style="{BODY_STYLE}">'
        f'<p style="{INTRO_STYLE}">'
        f"Now tracking <strong>{escape(company_name)}</strong>. "
        f"Here is their whole board."
        f"</p>"
        f'<p style="{COUNT_STYLE}">{escape(summary(listings))}</p>'
        f'{"".join(sections)}'
        f"</div>"
    )
