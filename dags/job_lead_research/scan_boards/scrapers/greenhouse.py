"""Greenhouse board scraper.

    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs

Public and unauthenticated like Ashby's, and likewise returns the whole board in
one response -- ``meta.total`` matches the length of ``jobs`` even on large
boards (verified against Stripe, 575 roles), so there is no pagination to walk.

*Descriptions need ``?content=true``.* Without it the list endpoint omits
``content`` entirely, and the per-job endpoint would mean one request per
posting -- 575 for Stripe. The flag folds them all into the single board fetch.

*``content`` is HTML-escaped HTML.* It arrives as ``&lt;div&gt;...``, i.e. entity
-encoded markup, so reading it as text takes an unescape, then tag-stripping,
then a second unescape for entities inside the markup itself. That is what
:func:`as_text` does. Ashby hands back a ready-made ``descriptionPlain`` and
needs none of this; the difference is in the providers, not in our handling.
"""

import html
import re

import requests

from job_lead_research.types import JobListing
from .base import ATSScraper

API_ROOT = "https://boards-api.greenhouse.io/v1/boards"

# Without this the postings come back with no description at all.
PARAMS = {"content": "true"}

REQUEST_TIMEOUT_SECONDS = 30

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")


def as_text(content: str) -> str:
    """Flatten Greenhouse's escaped description markup down to plain text."""
    if not content:
        return ""

    text = TAG.sub(" ", html.unescape(content))
    return WHITESPACE.sub(" ", html.unescape(text)).strip()


class GreenhouseScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every posting on the board, descriptions included."""
        if not self.company.board_slug:
            raise ValueError(f"{self.company.name} has no board_slug.")

        response = requests.get(
            f"{API_ROOT}/{self.company.board_slug}/jobs",
            params=PARAMS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return response.json().get("jobs", [])

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one Greenhouse posting onto the common listing shape.

        ``first_published`` is preferred over ``updated_at`` for
        ``published_at``: an edit to a months-old posting bumps ``updated_at``,
        which would make a stale role look new every time someone fixes a typo.

        Ids come back as integers here and as UUID strings on Ashby, so this
        casts to match the column and the rest of the pipeline.
        """
        return JobListing(
            id=str(blob["id"]),
            company_name=self.company.name,
            location=(blob.get("location") or {}).get("name", "").strip(),
            title=(blob.get("title") or "").strip(),
            description=as_text(blob.get("content") or ""),
            listing_url=blob.get("absolute_url") or "",
            published_at=blob.get("first_published") or blob.get("updated_at") or "",
        )
