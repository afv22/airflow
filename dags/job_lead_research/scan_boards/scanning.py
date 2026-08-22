"""The generic half of a board scan, shared by every adapter.

An adapter supplies five things -- a filterable-field list, a board-URL parser,
a fetcher, a per-posting filter-candidate map, and a mapper onto
:class:`BoardListing`. Everything else about scanning a board is protocol, not
platform: filter parsing, vocabulary tracking, typo warnings, the listing cap.
That protocol lives here exactly once, so a new ATS gets the whole behaviour --
including the misconfiguration warnings -- for the price of the five
platform-specific pieces, and the behaviours cannot drift apart between
adapters the way copy-pasted scan loops would.

Adapters are plain modules; a module whose top level defines the five names
satisfies :class:`BoardAdapter` structurally.
"""

from collections.abc import Callable
from typing import Protocol

from . import filters as board_filters
from .types import BoardListing, ScanContext, ScanResult, ScanStatus


class BoardAdapter(Protocol):
    """What a platform module must define to be scannable.

    Declared as callable attributes rather than methods so that a plain module
    -- the natural shape for an adapter -- typechecks as an implementation.
    """

    # Sheet-facing filter field names, in the ATS's own vocabulary.
    FILTERABLE_FIELDS: tuple[str, ...]

    # Board token out of a pasted careers URL, plus warnings about parts of the
    # URL that could not be honoured. Raises ValueError on an unusable URL.
    parse_board_url: Callable[[str], tuple[str, list[str]]]

    # Every live posting on the board, as raw ATS payloads. Raises on any
    # transport or HTTP error.
    fetch: Callable[[str], list[dict]]

    # The values each filterable field can match for one posting. Multi-valued
    # per field, because a role can legitimately have several (locations above
    # all); the filter layer treats the list as alternatives.
    candidates: Callable[[dict], dict[str, list[str]]]

    # One raw posting onto the shape every adapter returns.
    as_listing: Callable[[str, dict], BoardListing]


def scan(adapter: BoardAdapter, context: ScanContext) -> ScanResult:
    """Fetch one board and return the roles matching the company's filters.

    Filtering happens here, before storage, so only kept roles are ever
    written. That is what makes a misconfigured filter indistinguishable from
    an empty board by row count alone -- hence the vocabulary check below,
    which reports a filter value the board has never used as a warning naming
    what it does use.
    """
    token, warnings = adapter.parse_board_url(context.board_url)
    wanted = board_filters.parse(context.filters, adapter.FILTERABLE_FIELDS)

    postings = adapter.fetch(token)

    kept = []
    vocabulary: dict[str, set[str]] = {field: set() for field in adapter.FILTERABLE_FIELDS}
    for posting in postings:
        candidates = adapter.candidates(posting)
        for field, values in candidates.items():
            vocabulary[field].update(
                board_filters.normalize(value) for value in values if value
            )

        if wanted.matches(candidates):
            kept.append(adapter.as_listing(context.name, posting))

    if wanted:
        warnings.extend(describe_unmatched(wanted, vocabulary))

    capped = len(kept) > context.max_listings
    if capped:
        warnings.append(
            f"Board returned {len(kept)} matching roles, above the "
            f"{context.max_listings} cap; the rest were not stored."
        )
        kept = kept[: context.max_listings]

    return ScanResult(
        company=context.name,
        listings=kept,
        status=ScanStatus.OK if kept else ScanStatus.EMPTY,
        warnings=warnings,
        fetched_count=len(postings),
        capped=capped,
    )


def describe_unmatched(
    wanted: board_filters.BoardFilters, vocabulary: dict[str, set[str]]
) -> list[str]:
    """Warn about filter values this board has never used -- almost always typos.

    Reports the board's own vocabulary alongside, because the useful half of
    "'Enginering' matched nothing" is knowing the board says "Engineering".
    """
    messages = []
    for field, missing in wanted.unmatched_values(vocabulary).items():
        available = ", ".join(sorted(vocabulary.get(field, set()))) or "nothing"
        messages.append(
            f"filter {field}={', '.join(missing)!r} matched no role on this "
            f"board; it uses: {available}."
        )
    return messages
