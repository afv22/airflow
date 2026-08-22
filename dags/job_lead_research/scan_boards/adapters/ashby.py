"""Ashby board adapter.

    https://api.ashbyhq.com/posting-api/job-board/{slug}

Verified against a live board (Watershed, 34 roles) on 2026-08-22:

*The posting API honours no filter parameters.* ``?location=London`` returns the
full board unchanged. The only parameter that does anything is
``?includeCompensation=true``, which adds a compensation field. So every filter
here is applied client-side, after fetching the whole board.

*A ``locationId`` from a board URL is unusable.* The UUID in
``jobs.ashbyhq.com/{slug}?locationId=9df2f056-...`` appears nowhere in the API
payload -- it is an internal entity id the frontend resolves separately. Pasting
a filtered board URL into the sheet therefore cannot filter anything, which is
why such a URL earns a warning rather than silent acceptance: believing a filter
is active when it is not is the failure worth surfacing.
"""

import requests

from ..types import BoardListing

API_ROOT = "https://api.ashbyhq.com/posting-api/job-board"

# Compensation is not filtered on, but it is free with the request and is the
# kind of thing the research agent would otherwise open the listing to find.
PARAMS = {"includeCompensation": "true"}

REQUEST_TIMEOUT_SECONDS = 30

# Sheet-facing filter fields, mapped to how this adapter reads them out of a
# posting. Names match the API's own vocabulary so what Andrew types in the sheet
# is what he sees on the board.
FILTERABLE_FIELDS = (
    "location",
    "department",
    "team",
    "employmentType",
    "workplaceType",
    "title",
)


def parse_board_url(board_url: str) -> tuple[str, list[str]]:
    """Pull the board slug out of a careers URL, warning about dropped params.

    Accepts the job-board URL Andrew would naturally copy out of the browser,
    with or without frontend filter parameters, and returns the slug plus any
    warnings the scan should carry.
    """
    warnings: list[str] = []
    url = board_url.strip()
    if not url:
        raise ValueError("Company has no board_url.")

    remainder, _, query = url.partition("?")
    if query:
        warnings.append(
            f"board_url carries frontend parameters ({query}) which the Ashby "
            f"posting API ignores; they were dropped. Put the filter in the "
            f"sheet's filters column instead, e.g. 'location: London'."
        )

    slug = remainder.rstrip("/").rsplit("/", 1)[-1]
    if not slug:
        raise ValueError(f"Could not read an Ashby board slug from {board_url!r}.")

    return slug, warnings


def fetch(slug: str) -> list[dict]:
    """Return every listed posting on a board. Raises on transport or HTTP error.

    Unlisted postings are dropped here rather than downstream: ``isListed``
    false means the role is not publicly on the board at all, so nothing later
    in the pipeline should ever see it.
    """
    response = requests.get(
        f"{API_ROOT}/{slug}", params=PARAMS, timeout=REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return [posting for posting in jobs if posting.get("isListed", True)]


def locations_of(posting: dict) -> list[str]:
    """Every location a posting is open in, primary first.

    Secondary locations count as fully as the primary one: a role whose primary
    location is San Francisco but which also lists London is a London role, and
    filtering on the primary alone would drop it without trace.
    """
    locations = [posting.get("location")]
    locations.extend(
        secondary.get("location")
        for secondary in posting.get("secondaryLocations") or ()
    )
    return [location for location in locations if location]


def candidates(posting: dict) -> dict[str, list[str]]:
    """The values each filterable field can match for one posting."""
    return {
        "location": locations_of(posting),
        "department": [posting.get("department") or ""],
        "team": [posting.get("team") or ""],
        "employmentType": [posting.get("employmentType") or ""],
        "workplaceType": [posting.get("workplaceType") or ""],
        "title": [posting.get("title") or ""],
    }


def as_listing(company: str, posting: dict) -> BoardListing:
    """Map an Ashby posting onto the shape every adapter returns."""
    locations = locations_of(posting)
    return BoardListing(
        company=company,
        title=(posting.get("title") or "").strip(),
        url=posting.get("jobUrl") or "",
        location=locations[0] if locations else None,
        secondary_locations=locations[1:],
        department=posting.get("department"),
        team=posting.get("team"),
        employment_type=posting.get("employmentType"),
        workplace_type=posting.get("workplaceType"),
        is_remote=posting.get("isRemote"),
        published_at=posting.get("publishedAt"),
        description=posting.get("descriptionPlain") or posting.get("descriptionHtml"),
    )
