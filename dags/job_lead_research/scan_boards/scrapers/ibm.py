"""IBM board scraper.

    https://www-api.ibm.com/search/api/v2

IBM does not run an ATS board of the kind the other scrapers read. This endpoint
is a thin public proxy in front of the Elasticsearch index behind
``careers.ibm.com``, so the request body is an ES query and the response is an ES
result envelope -- ``hits.hits[]._source`` is where the postings actually live.
It is unauthenticated like the others, but almost nothing else about it is the
same, and four things are worth knowing before changing this:

*The board is far too big to take whole.* IBM posts globally, into the
thousands. Every other provider here hands back one company's board in one
response and any narrowing happens on our side; this one must be filtered
server-side or a scan would walk the entire global careers site. ``board_slug``
carries the country to filter to -- see :data:`COUNTRY_FIELD`.

*``description`` is a teaser, ``body`` is the posting.* ``description`` is
truncated to ~255 characters and ends in an ellipsis mid-sentence, which would
hand the research stage the opening paragraph of IBM boilerplate and none of the
requirements. ``body`` carries the full text, and unlike Greenhouse and Pinpoint
it arrives as plain text already -- no tags to strip, only HTML entities to
unescape.

*Paging needs a stable sort.* ``size`` is capped at 100 by the proxy (150 is a
400), so a country of any size takes several requests walked with ``from``. The
example request sorts by ``_score`` then ``pageviews``, which for a filter-only
query leaves every hit tied at score 0 -- the order is then unstable between
requests and a walk both repeats and misses postings. Sorting by ``_id`` gives a
total order and makes the walk exact; a walk of 395 postings under the example's
sort returned 392 unique, and under this one returns all 395.

*The ``_source`` list is a validated allowlist.* Omitting it returns hits with
no ``_source`` at all, and ``["*"]`` is rejected as a 400, so every field wanted
has to be named explicitly.

The opaque ``_id`` hash is the listing id rather than the ``jobId`` in the URL:
it is what the index keys on, and the only id the payload states as a field.
"""

import html

import requests

from job_lead_research.types import JobListing
from .base import ATSScraper

API_URL = "https://www-api.ibm.com/search/api/v2"

REQUEST_TIMEOUT_SECONDS = 30

# The proxy rejects anything above 100 with a 400.
PAGE_SIZE = 100

# A walk that somehow never stops should not run forever against a public
# endpoint. Comfortably above any single country's posting count.
MAX_PAGES = 100

# The indexed fields, under the names the payload uses for them. IBM's schema is
# positional -- the numbers carry no meaning on their own -- so they are named
# here once and read through these constants everywhere else.
COUNTRY_FIELD = "field_keyword_05"
CATEGORY_FIELD = "field_keyword_08"
WORKPLACE_FIELD = "field_keyword_17"
CITY_FIELD = "field_keyword_19"

# What a company with no slug recorded is scanned as. IBM's board is global and
# must be narrowed server-side, so a scan needs *some* country; this matches the
# scope of the other boards on the watchlist rather than pulling every region.
DEFAULT_COUNTRY = "United Kingdom"

SOURCE_FIELDS = [
    "_id",
    "title",
    "url",
    "body",
    "language",
    COUNTRY_FIELD,
    CATEGORY_FIELD,
    WORKPLACE_FIELD,
    CITY_FIELD,
]


def as_text(content: str) -> str:
    """Unescape one field of IBM's text, which arrives without markup."""
    if not content:
        return ""

    return html.unescape(content).strip()


class IBMScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every posting in the configured country, walking the pages.

        The walk stops on an empty page rather than trusting ``hits.total``
        alone: the total is reported against the index at the time of the first
        request, and a board that changes underneath a multi-request walk would
        otherwise leave the loop reading past the end or stopping short.
        """
        postings: list[dict] = []
        seen: set[str] = set()

        for page in range(MAX_PAGES):
            hits = self.fetch_page(page * PAGE_SIZE)
            if not hits:
                break

            for hit in hits:
                blob = hit.get("_source") or {}
                # The id lives on the envelope; _source carries it only because
                # SOURCE_FIELDS asks for it, and not on every index.
                blob.setdefault("_id", hit.get("_id", ""))

                # Belt and braces against a posting shifting between pages mid
                # walk. The sort makes this unlikely, not impossible.
                if blob["_id"] in seen:
                    continue

                seen.add(blob["_id"])
                postings.append(blob)

            if len(hits) < PAGE_SIZE:
                break

        return postings

    def fetch_page(self, offset: int) -> list[dict]:
        """Return one page of raw hits, starting at ``offset``."""
        response = requests.post(
            API_URL,
            json=self.query(offset),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return response.json().get("hits", {}).get("hits", [])

    def query(self, offset: int) -> dict:
        """The Elasticsearch request body for one page of the country's board.

        The aggregations in the browser's own request drive the facet counts on
        the careers page and are dropped here: they are a second filtered pass
        over the index for numbers nothing downstream reads.
        """

        return {
            "appId": "careers",
            "scopes": ["careers2"],
            "query": {"bool": {"must": []}},
            "post_filter": {
                "bool": {"must": [{"term": {COUNTRY_FIELD: DEFAULT_COUNTRY}}]}
            },
            "size": PAGE_SIZE,
            "from": offset,
            # A total order, so the walk neither repeats nor skips. See module
            # docstring.
            "sort": [{"_id": "asc"}],
            "lang": "zz",
            "localeSelector": {},
            "sm": {"query": "", "lang": "zz"},
            "_source": SOURCE_FIELDS,
        }

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one IBM posting onto the common listing shape.

        ``published_at`` is left empty: the index exposes no created or posted
        date for a listing -- the date fields in the schema come back empty
        across the board -- and ``added_at`` already records when we first saw
        it. Filling it with the scan time would claim a fact IBM never stated.
        """
        return JobListing(
            id=blob["_id"],
            company_name=self.company.name,
            location=self.location(blob),
            title=as_text(blob.get("title") or ""),
            description=as_text(blob.get("body") or ""),
            listing_url=blob.get("url") or "",
            published_at="",
        )

    @staticmethod
    def location(blob: dict) -> str:
        """The posting's city and country, with the workplace type appended.

        The city field is often IBM's own label rather than a place --
        "Multiple Cities" is common -- but it is the only geographic detail
        below country level in the payload, and is more use downstream than the
        country alone. Remote/hybrid/onsite is appended for the same reason
        Pinpoint's is: it is the thing most worth filtering on and lives nowhere
        else.
        """
        parts = (
            (blob.get(CITY_FIELD) or "").strip(),
            (blob.get(COUNTRY_FIELD) or "").strip(),
        )
        place = ", ".join(dict.fromkeys(part for part in parts if part))

        workplace = (blob.get(WORKPLACE_FIELD) or "").strip()
        if workplace and place:
            return f"{place} ({workplace})"

        return place or workplace
