"""Greenhouse board adapter.

    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Verified against a live board (Mozilla, 82 roles) on 2026-08-22:

*The board API honours no filter parameters.* ``?department=Ads`` returns the
full board unchanged, exactly like Ashby -- so filtering is client-side here
too, and the shared scanning harness applies it.

*``content=true`` is what makes the payload useful.* Without it a job carries
neither ``departments`` nor ``offices`` nor a description; with it the whole
Mozilla board is ~1.4MB in one unpaginated response (``meta.total`` matched the
jobs returned), which is fine for one request per company.

*``content`` is HTML-entity-escaped HTML* (``&lt;div&gt;...``), so it is
unescaped here to real HTML -- the analogue of Ashby's ``descriptionHtml``.

*Greenhouse has no secondary-locations field; ``offices`` is the multi-location
axis.* ``location.name`` is a single string ("Remote Germany"), and the
``offices`` list names every office the role is open in, usually repeating the
primary. Extra offices map onto ``secondary_locations``. Employment type,
workplace type, and remote-ness have no structured field at all -- remote-ness
lives inside the location string -- so those stay ``None`` rather than being
guessed at.
"""

import html

import requests

from ..types import BoardListing

API_ROOT = "https://boards-api.greenhouse.io/v1/boards"

PARAMS = {"content": "true"}

REQUEST_TIMEOUT_SECONDS = 30

# Sheet-facing filter fields. Narrower than Ashby's because Greenhouse exposes
# less structure per job; an unsupported field in the sheet is rejected at
# parse time with this list in the error.
FILTERABLE_FIELDS = (
    "location",
    "department",
    "title",
)


def parse_board_url(board_url: str) -> tuple[str, list[str]]:
    """Pull the board token out of a careers URL, warning about dropped params.

    Greenhouse tokens appear in three shapes in the wild:
    ``job-boards.greenhouse.io/{token}``, ``boards.greenhouse.io/{token}``, and
    embedded on a company's own careers page as
    ``boards.greenhouse.io/embed/job_board?for={token}``. The ``for`` parameter
    is therefore the one query parameter that is meaningful rather than
    droppable, which is exactly why URL parsing lives in the adapter and not
    the harness.
    """
    warnings: list[str] = []
    url = board_url.strip()
    if not url:
        raise ValueError("Company has no board_url.")

    remainder, _, query = url.partition("?")
    token = ""
    dropped = []
    for parameter in query.split("&"):
        if not parameter:
            continue
        name, _, value = parameter.partition("=")
        if name == "for" and value:
            token = value
        else:
            dropped.append(parameter)

    if dropped:
        warnings.append(
            f"board_url carries frontend parameters ({'&'.join(dropped)}) which "
            f"the Greenhouse board API ignores; they were dropped. Put the "
            f"filter in the sheet's filters column instead, e.g. "
            f"'department: Engineering'."
        )

    if not token:
        token = remainder.rstrip("/").rsplit("/", 1)[-1]
    if not token:
        raise ValueError(f"Could not read a Greenhouse board token from {board_url!r}.")

    return token, warnings


def fetch(token: str) -> list[dict]:
    """Return every posting on a board. Raises on any transport or HTTP error.

    Greenhouse only serves live postings, so there is no listed/unlisted split
    to apply here.
    """
    response = requests.get(
        f"{API_ROOT}/{token}/jobs", params=PARAMS, timeout=REQUEST_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.json().get("jobs", [])


def locations_of(job: dict) -> list[str]:
    """Every location a posting is open in, primary first.

    The primary is ``location.name``; the ``offices`` list usually repeats it
    and sometimes adds more, so it is deduplicated in order behind the primary.
    An office a role is open in counts as fully as the primary location, same
    as Ashby's secondary locations.
    """
    names = [(job.get("location") or {}).get("name")]
    names.extend(office.get("name") for office in job.get("offices") or ())
    return list(dict.fromkeys(name for name in names if name))


def departments_of(job: dict) -> list[str]:
    """Every department a posting belongs to.

    A list because the API's ``departments`` is one; storage keeps only the
    first as the display value, while filtering sees them all.
    """
    return [
        department.get("name")
        for department in job.get("departments") or ()
        if department.get("name")
    ]


def candidates(job: dict) -> dict[str, list[str]]:
    """The values each filterable field can match for one posting."""
    return {
        "location": locations_of(job),
        "department": departments_of(job),
        "title": [job.get("title") or ""],
    }


def as_listing(company: str, job: dict) -> BoardListing:
    """Map a Greenhouse job onto the shape every adapter returns."""
    locations = locations_of(job)
    departments = departments_of(job)
    content = job.get("content")
    return BoardListing(
        company=company,
        title=(job.get("title") or "").strip(),
        url=job.get("absolute_url") or "",
        location=locations[0] if locations else None,
        secondary_locations=locations[1:],
        department=departments[0] if departments else None,
        published_at=job.get("first_published"),
        updated_at=job.get("updated_at"),
        # Entity-escaped in the payload; unescaping yields real HTML.
        description=html.unescape(content) if content else None,
    )
