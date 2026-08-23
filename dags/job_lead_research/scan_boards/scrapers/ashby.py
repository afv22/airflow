"""Ashby board scraper.

    https://api.ashbyhq.com/posting-api/job-board/{slug}

The endpoint is public, unauthenticated, and returns the whole board in one
response -- there is no pagination and no server-side filtering, so a scan is
exactly one request per company.

Two things about it are worth knowing before changing this:

*Filter parameters are ignored.* ``?location=London`` returns the full board
unchanged. The only parameter that does anything is ``includeCompensation``, so
any narrowing has to happen after the fetch, on our side.

*Descriptions come in both forms.* ``descriptionPlain`` is preferred over
``descriptionHtml`` because everything downstream reads the description as text
for the research agent, and the HTML is markup-heavy enough to bury the content.
"""

import requests

from job_lead_research.types import JobListing, RelevanceDecision
from .base import ATSScraper

API_ROOT = "https://api.ashbyhq.com/posting-api/job-board"

# Free with the request and otherwise something the research stage would have to
# open the listing to find.
PARAMS = {"includeCompensation": "true"}

REQUEST_TIMEOUT_SECONDS = 30


class AshbyScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every publicly listed posting on the board.

        ``isListed`` false means the role is not on the public board at all, so
        it is dropped here rather than downstream -- nothing later in the
        pipeline should ever see a posting a visitor could not.
        """
        if not self.company.board_slug:
            raise ValueError(f"{self.company.name} has no board_slug.")

        response = requests.get(
            f"{API_ROOT}/{self.company.board_slug}",
            params=PARAMS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        jobs = response.json().get("jobs", [])
        return [posting for posting in jobs if posting.get("isListed", True)]

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one Ashby posting onto the common listing shape.

        ``added_at`` is left empty: it means "when we first saw this", which
        only the insert knows, so the store fills it from the column default.
        The ``relevance_*`` fields are likewise unset here -- a scraper reports
        what a board says, it does not judge it -- and are filled by the
        decision stage that runs after this one.
        """
        return JobListing(
            id=blob["id"],
            company_name=self.company.name,
            location=self.location(blob),
            title=(blob.get("title") or "").strip(),
            description=blob.get("descriptionPlain")
            or blob.get("descriptionHtml")
            or "",
            listing_url=blob.get("jobUrl") or "",
            published_at=blob.get("publishedAt") or "",
            added_at="",
            relevance_decision=RelevanceDecision.PENDING,
            relevance_rejection="",
        )

    @staticmethod
    def location(blob: dict) -> str:
        """The posting's locations, primary first, as one comma-joined string.

        Secondary locations are kept rather than dropped: a role whose primary
        location is San Francisco but which also lists London is a London role,
        and keeping only the primary would hide that from anything filtering on
        location downstream.
        """
        locations = [blob.get("location")]
        locations.extend(
            secondary.get("location")
            for secondary in blob.get("secondaryLocations") or ()
        )
        return ", ".join(location for location in locations if location)
