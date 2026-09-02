"""Workable board scraper.

    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
    GET  https://apply.workable.com/api/v1/accounts/{slug}/jobs/{shortcode}

Public and unauthenticated like the others, but the only scraper here that
costs more than one request per company. Four things about it shaped this:

*The board endpoint is a POST.* A GET to it returns "Not Found", which reads
like a bad slug and is not -- the resource exists, the verb is wrong. A real
bad slug also 404s, on the POST, which is what ``raise_for_status`` reports.

*It is cursor-paginated, ten at a time.* The response carries ``nextPage``, an
opaque cursor echoed back as ``{"token": ...}`` in the next POST body, and a
page is fixed at ten postings -- ``?limit=100`` is accepted and ignored. Sixty
roles is therefore seven requests, and :meth:`fetch_jobs` walks until the
cursor runs out rather than trusting ``total``.

*The list carries no description.* Title, location and dates are all it has;
the body text lives only on the per-job endpoint, so a scan is one request per
posting on top of the page walk. There is no ``?content=true`` equivalent to
fold them into the board fetch the way Greenhouse's does. A posting whose
detail fetch fails keeps its list fields and loses only its description --
better a listing the research stage has to open itself than no listing.

*The body is split across three fields.* ``description`` is the pitch and the
duties, with the requirements in ``requirements`` and ``benefits`` separately,
so reading only ``description`` would hand the research stage a listing with no
requirements in it. The markup is raw ``<p>...`` with entities inside it, so
flattening takes tag-stripping and one unescape -- the Pinpoint case, not the
double unescape Greenhouse needs for its pre-escaped payload.

``v1`` and ``v2`` of the detail endpoint return byte-identical payloads; there
is no ``v3`` of it. ``v1`` is used here because it is the older and so the less
likely of the two to be retired first.
"""

import html
import re

import requests

from job_lead_research.types import JobListing
from .base import ATSScraper

API_ROOT = "https://apply.workable.com/api"

# The public posting, which is not in the payload -- both endpoints describe the
# job without ever linking to it -- but is fixed by the account slug and the
# posting's shortcode.
LISTING_URL = "https://apply.workable.com/{slug}/j/{shortcode}/"

REQUEST_TIMEOUT_SECONDS = 30

# The page walk is bounded so a cursor that never stops advancing costs a
# bounded number of requests instead of looping forever. Ten postings a page
# puts this well clear of any real board.
MAX_PAGES = 200

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")

# The body fields in the order they appear on the public posting, each with the
# heading to file it under. Workable names no headings of its own -- unlike
# Pinpoint, which ships the company's -- so these are ours, and exist to keep
# the stitched text readable as sections rather than one run-on block.
SECTIONS = [
    ("description", None),
    ("requirements", "Requirements"),
    ("benefits", "Benefits"),
]


def as_text(content: str) -> str:
    """Flatten one field of Workable's description markup down to plain text."""
    if not content:
        return ""

    return WHITESPACE.sub(" ", html.unescape(TAG.sub(" ", content))).strip()


class WorkableScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every posting on the board, each with its description merged in.

        Non-published and internal postings are dropped here rather than
        downstream -- nothing later in the pipeline should ever see a posting a
        visitor could not. Both fields have been observed only in their public
        state on live boards, so the checks are a guard against a board that
        does expose others, not a filter with known work to do.
        """
        if not self.company.board_slug:
            raise ValueError(f"{self.company.name} has no board_slug.")

        return [
            self.with_description(posting)
            for posting in self.fetch_board()
            if posting.get("state", "published") == "published"
            and not posting.get("isInternal")
        ]

    def fetch_board(self) -> list[dict]:
        """Walk the cursor-paginated board, returning the postings from every page.

        The cursor is what ends the walk: ``total`` is not counted against,
        since a board that changes mid-walk would leave the two disagreeing and
        the cursor is the side that knows when it is done.
        """
        postings: list[dict] = []
        token = None

        for _ in range(MAX_PAGES):
            response = requests.post(
                f"{API_ROOT}/v3/accounts/{self.company.board_slug}/jobs",
                json={"token": token} if token else {},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            page = response.json()
            postings.extend(page.get("results") or [])

            token = page.get("nextPage")
            if not token:
                break

        return postings

    def with_description(self, posting: dict) -> dict:
        """Return the posting with the detail endpoint's body fields merged in.

        A failed detail fetch costs this posting its description and nothing
        else: the list fields are already in hand, and dropping the listing
        outright over a body we could not read would hide the role entirely.
        The detail payload is the list blob plus the body fields, so merging it
        over the top is a superset and never loses anything.
        """
        shortcode = posting.get("shortcode")
        if not shortcode:
            return posting

        try:
            response = requests.get(
                f"{API_ROOT}/v2/accounts/{self.company.board_slug}/jobs/{shortcode}",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return posting | response.json()
        except (requests.RequestException, ValueError) as error:
            print(
                f"No description for {self.company.name} posting {shortcode}: {error}"
            )
            return posting

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one Workable posting onto the common listing shape.

        The id is the numeric ``id`` cast to string, not ``shortcode``: both are
        unique on the board, and ``id`` is what the payload leads with and what
        the other providers' ids are the analogue of. ``shortcode`` is still
        what addresses the posting publicly, so it is what builds the URL.
        """
        return JobListing(
            id=str(blob["id"]),
            company_name=self.company.name,
            location=self.location(blob),
            title=(blob.get("title") or "").strip(),
            description=self.description(blob),
            listing_url=LISTING_URL.format(
                slug=self.company.board_slug, shortcode=blob.get("shortcode", "")
            ),
            published_at=blob.get("published") or "",
        )

    @staticmethod
    def description(blob: dict) -> str:
        """The posting's body sections, stitched into one plain-text block.

        Sections the company left empty are skipped rather than contributing a
        bare heading with nothing under it.
        """
        parts = []
        for field, header in SECTIONS:
            body = as_text(blob.get(field) or "")
            if not body:
                continue

            parts.append(f"{header}\n{body}" if header else body)

        return "\n\n".join(parts)

    @classmethod
    def location(cls, blob: dict) -> str:
        """The posting's locations, primary first, with the workplace type.

        Every location is kept rather than only the primary, for the reason the
        Ashby scraper keeps its secondaries: a role listed in both London and
        Dubai is a London role, and keeping only the first would hide that from
        anything filtering on location downstream. Locations flagged ``hidden``
        are not shown on the public posting and so are not reported here.

        ``workplace`` is appended because remote/hybrid/onsite is the thing most
        worth filtering on downstream and it lives nowhere else in the payload.
        Fully remote postings are the case where it carries the whole meaning:
        they come back with country-only locations and no city at all.
        """
        locations = blob.get("locations") or [blob.get("location") or {}]

        places = dict.fromkeys(
            place
            for location in locations
            if not location.get("hidden") and (place := cls.place(location))
        )

        workplace = (blob.get("workplace") or "").replace("_", " ").strip()
        if places and workplace:
            return f"{', '.join(places)} ({workplace})"

        return ", ".join(places) or workplace

    @staticmethod
    def place(location: dict) -> str:
        """One location as a place name, at the finest granularity it carries.

        Country-only is normal rather than degraded: remote postings routinely
        arrive with an empty ``city`` and a null ``region``, and "United States"
        is the whole of what the board is claiming about them.
        """
        parts = (location.get("city"), location.get("region"), location.get("country"))
        return ", ".join(
            dict.fromkeys(part.strip() for part in parts if part and part.strip())
        )
