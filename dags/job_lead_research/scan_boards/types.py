"""The scanner seam: what every board adapter returns, however it got it.

One adapter per ATS, all of them ``scan(company) -> ScanResult``. Keeping the
output shape identical across adapters is what lets the harness stay ignorant of
which board it is talking to, and what makes adding an ATS additive rather than
structural.
"""

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class ScanStatus(StrEnum):
    """Outcome of one board scan.

    ``EMPTY`` exists to stay distinct from ``ERROR``: a board that loaded and
    genuinely has no open roles must never look like a board that failed to
    load, or a broken adapter would read as a company that stopped hiring.
    """

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


def normalize_url(url: str) -> str:
    """Strip query string and fragment, leaving the stable part of a listing URL.

    The listing URL is the dedupe key, so anything in it that varies between
    scans of an unchanged role invents a new listing. Query strings are where
    that variance lives -- tracking parameters, session ids, board-side view
    state -- while the path identifies the posting. Ashby's ``jobUrl`` is
    already clean, so this is close to a no-op there; it is applied uniformly
    anyway, because the first board that does mint per-session URLs should not
    also be the first to need a schema migration.
    """
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


@dataclass
class BoardListing:
    """One open role, as any adapter reports it.

    Fields beyond title and URL are optional because ATS platforms disagree on
    what they expose; an adapter fills what its board offers and leaves the rest
    ``None`` rather than inventing a value.
    """

    company: str
    title: str
    url: str
    location: str | None = None
    secondary_locations: list[str] = field(default_factory=list)
    department: str | None = None
    team: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    is_remote: bool | None = None
    published_at: str | None = None
    updated_at: str | None = None
    description: str | None = None

    @property
    def source_id(self) -> str:
        """Stable identity for this listing within its board."""
        return normalize_url(self.url)

    @property
    def content_hash(self) -> str:
        """Fingerprint of the fields that describe the role, not where it lives.

        Stored alongside the URL key so URL churn is measurable: if a board
        starts minting new URLs for unchanged roles, that shows up as a flood of
        new ``source_id`` values sharing a hash, which is the evidence needed to
        justify a smarter key. Until there is such evidence, the simple key
        stands.
        """
        parts = [self.title, self.location, self.department, self.team]
        joined = "|".join((part or "").strip().casefold() for part in parts)
        return hashlib.sha256(joined.encode()).hexdigest()


@dataclass
class ScanResult:
    """Listings from one board plus how the scan itself went.

    Health travels with the listings rather than being raised, because most scan
    problems are not exceptional: a board with no roles, a filter that matches
    nothing, a URL carrying parameters the API ignores. Exceptions stay reserved
    for genuine crashes, so the harness can record a useful row for everything
    short of that.
    """

    company: str
    listings: list[BoardListing] = field(default_factory=list)
    status: ScanStatus = ScanStatus.OK
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    fetched_count: int = 0
    capped: bool = False

    @property
    def kept_count(self) -> int:
        return len(self.listings)

    @property
    def warning(self) -> str | None:
        """Warnings as one string for storage, or ``None`` when the scan was clean."""
        return "; ".join(self.warnings) or None

    @property
    def trustworthy(self) -> bool:
        """Whether this scan saw the whole board and can be reasoned about.

        A capped or failed scan is a partial view, and treating a partial view as
        complete is what would let a truncated scan mark still-open roles as
        closed once close-detection exists.
        """
        return self.status is not ScanStatus.ERROR and not self.capped

    @classmethod
    def failed(cls, company: str, error: str) -> "ScanResult":
        """A scan that could not be completed. Never carries listings."""
        return cls(company=company, status=ScanStatus.ERROR, error=error)


@dataclass
class ScanContext:
    """Everything an adapter needs about the company it is scanning."""

    name: str
    board_url: str
    filters: str
    max_listings: int

    @staticmethod
    def from_company(company: Any, max_listings: int) -> "ScanContext":
        return ScanContext(
            name=company.name,
            board_url=company.board_url,
            filters=company.filters,
            max_listings=max_listings,
        )
