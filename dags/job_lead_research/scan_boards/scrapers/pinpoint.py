"""Pinpoint board scraper.

    https://{slug}.pinpointhq.com/postings.json

Public and unauthenticated like the others. Pinpoint gives each company its own
subdomain rather than a path segment, so ``board_slug`` is the subdomain here
(``ravelin``), not a trailing path component.

Postings arrive under a top-level ``data`` key, and the whole board comes back
in one response: ``?page=2`` returns byte-identical content to page one, so
there is no pagination to walk and a scan is one request per company.

Three things about the payload are worth knowing before changing this:

*There is no publish date.* No posting carries a created/published timestamp --
``deadline_at`` is the only date-shaped field and it is null across the board.
``published_at`` is therefore left empty rather than filled from a stand-in;
"when we first saw this" is what ``added_at`` already records, and putting the
scan time in ``published_at`` would claim a fact the board never stated.

*The description is split across four fields.* ``description`` carries only the
opening pitch, with the actual duties in ``key_responsibilities``, the
requirements in ``skills_knowledge_expertise``, and ``benefits`` separately.
Reading only ``description`` would hand the research stage a listing with no
requirements in it, so :meth:`description` stitches the sections together under
the board's own headers.

*The HTML is raw, not entity-escaped.* It arrives as ``<div>...`` with entities
(``&amp;``, ``&nbsp;``) inside the markup, so flattening it takes tag-stripping
and one unescape -- not the double unescape Greenhouse needs, whose payload is
itself escaped before it is read.
"""

import html
import re

import requests

from job_lead_research.types import JobListing
from .base import ATSScraper

REQUEST_TIMEOUT_SECONDS = 30

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")

# The body fields, each paired with the field holding its display heading. The
# opening pitch has no header field of its own and needs none; the rest are
# section headings on the public posting and are what make the stitched text
# readable as sections rather than as one run-on block.
SECTIONS = [
    ("description", None),
    ("key_responsibilities", "key_responsibilities_header"),
    ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
    ("benefits", "benefits_header"),
]


def as_text(content: str) -> str:
    """Flatten one field of Pinpoint's description markup down to plain text."""
    if not content:
        return ""

    return WHITESPACE.sub(" ", html.unescape(TAG.sub(" ", content))).strip()


class PinpointScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every posting on the board."""
        if not self.company.board_slug:
            raise ValueError(f"{self.company.name} has no board_slug.")

        response = requests.get(
            f"https://{self.company.board_slug}.pinpointhq.com/postings.json",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return response.json().get("data", [])

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one Pinpoint posting onto the common listing shape.

        Titles come back with stray leading whitespace often enough to be worth
        stripping here rather than letting it reach storage.
        """
        return JobListing(
            id=str(blob["id"]),
            company_name=self.company.name,
            location=self.location(blob),
            title=(blob.get("title") or "").strip(),
            description=self.description(blob),
            listing_url=blob.get("url") or "",
            published_at="",
        )

    @staticmethod
    def description(blob: dict) -> str:
        """The posting's body sections, stitched into one plain-text block.

        Each section is prefixed with the board's own header text so the
        stitched result keeps the structure a reader of the public posting
        sees. Sections the company left empty are skipped rather than
        contributing a bare heading with nothing under it.
        """
        parts = []
        for field, header_field in SECTIONS:
            body = as_text(blob.get(field) or "")
            if not body:
                continue

            header = as_text(blob.get(header_field) or "") if header_field else ""
            parts.append(f"{header}\n{body}" if header else body)

        return "\n\n".join(parts)

    @staticmethod
    def location(blob: dict) -> str:
        """The posting's location, as city and region with the workplace type.

        ``location.name`` is the company's own label for the location and is not
        reliably a place -- boards use it for "Remote" while the same record
        gives a real city -- so the geographic fields are what get read, and the
        name is only used when they are empty. ``workplace_type_text`` is
        appended because remote/hybrid/onsite is the thing most worth filtering
        on downstream and it lives nowhere else in the payload.
        """
        location = blob.get("location") or {}

        place = ", ".join(
            part
            for part in dict.fromkeys(
                filter(None, (location.get("city"), location.get("province")))
            )
        )
        place = place or (location.get("name") or "").strip()

        workplace = (blob.get("workplace_type_text") or "").strip()
        if workplace and place:
            return f"{place} ({workplace})"

        return place or workplace
