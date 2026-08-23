"""HTML rendering for the digest email.

Deliberately a fixed template rather than an LLM-written body: the per-listing
prose that matters is already written -- ``fit_reasoning`` is the fit stage's
answer to "why is this worth reading first" -- so a model here would only be
paraphrasing text the pipeline already paid for.

Styling is inline on every element. Mail clients strip <style> blocks and have
no meaningful cascade, so a stylesheet would survive in some inboxes and
silently vanish in others.
"""

from html import escape

from job_lead_research.types import FitDecision, JobListing

# The headings each tier is filed under. Andrew asked for the two emphases to
# be visually distinct, so a judgement call is never read as a strong match.
TIER_HEADINGS = {
    FitDecision.STRONG: ("Strong fit", "#1a7f37"),
    FitDecision.REVIEW: ("Worth a look", "#9a6700"),
}

BODY_STYLE = (
    "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, "
    "Arial, sans-serif; font-size: 15px; line-height: 1.5; color: #1f2328; "
    "max-width: 640px;"
)
HEADING_STYLE = (
    "font-size: 13px; font-weight: 600; text-transform: uppercase; "
    "letter-spacing: 0.05em; margin: 28px 0 12px;"
)
CARD_STYLE = (
    "border: 1px solid #d0d7de; border-radius: 6px; padding: 14px 16px; "
    "margin-bottom: 12px;"
)
TITLE_STYLE = "font-size: 16px; font-weight: 600; margin: 0 0 4px;"
META_STYLE = "color: #59636e; font-size: 13px; margin: 0 0 8px;"
REASONING_STYLE = "margin: 0 0 10px;"
LINK_STYLE = "color: #0969da; text-decoration: none; font-weight: 500;"


def _render_listing(listing: JobListing) -> str:
    """Render one listing card. Every field is board- or LLM-supplied text."""
    meta = " &middot; ".join(
        escape(part) for part in (listing.company_name, listing.location) if part
    )
    reasoning = (
        f'<p style="{REASONING_STYLE}">{escape(listing.fit_reasoning)}</p>'
        if listing.fit_reasoning
        else ""
    )
    return (
        f'<div style="{CARD_STYLE}">'
        f'<p style="{TITLE_STYLE}">{escape(listing.title)}</p>'
        f'<p style="{META_STYLE}">{meta}</p>'
        f"{reasoning}"
        f'<a href="{escape(listing.listing_url, quote=True)}" '
        f'style="{LINK_STYLE}">View listing &rarr;</a>'
        f"</div>"
    )


def render(listings: list[JobListing]) -> str:
    """Render the whole digest, grouped under a heading per verdict tier.

    Tiers are emitted in ``TIER_HEADINGS`` order and an empty one is dropped
    entirely, so a digest of four strong listings carries no lonely "Worth a
    look" heading with nothing under it.
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

    return f'<div style="{BODY_STYLE}">{"".join(sections)}</div>'
