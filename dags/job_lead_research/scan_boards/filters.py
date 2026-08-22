"""Per-company board filters, written by hand in the watchlist sheet.

The sheet's ``filters`` cell holds a compact DSL: field names on the left,
comma-separated values on the right, semicolons between clauses.

    location: London, Remote - UK; team: Engineering, Design

Values within a clause are OR'd, clauses are AND'd -- "a London or Remote-UK
role on the Engineering or Design team". That covers every filter the watchlist
has wanted so far, in one spreadsheet cell that is still readable at a glance.

Two deliberate limits, both revisitable without invalidating written rows:

*No negation.* ``exclude_title: Sales`` is the obvious next feature and ``title``
is the field that will want it first, since enumerating the titles you *do* want
is impractical. The ``exclude_`` prefix stays free until then.

*No cross-company normalization.* Matching is literal (case- and
whitespace-insensitive). Andrew writes what the board actually uses, because the
watchlist is hand-curated and glancing at a board once when adding it is cheaper
than a mapping layer that fails silently when a board renames a team.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CLAUSE_SEPARATOR = ";"
FIELD_SEPARATOR = ":"
VALUE_SEPARATOR = ","


class FilterError(ValueError):
    """A filter cell that cannot be parsed, or names a field we cannot filter on.

    Raised rather than skipped: a filter that does not do what it says is worse
    than no filter, because the run still looks successful while quietly
    scanning the wrong thing.
    """


def normalize(value: str) -> str:
    """Casefold and collapse whitespace, so sheet spacing never decides a match.

    ``"  London,  UK "`` and ``"london, uk"`` are the same location. Internal
    runs of whitespace collapse too, which is what makes a value copied out of a
    rendered web page match the same value typed by hand.
    """
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class BoardFilters:
    """Parsed filter clauses, keyed by field name, values already normalized.

    Empty means "keep everything" -- the common case for a newly added company,
    and the reason an absent filter cell must never be an error.
    """

    clauses: Mapping[str, tuple[str, ...]]

    def __bool__(self) -> bool:
        return bool(self.clauses)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(self.clauses)

    def matches(self, candidates: Mapping[str, Sequence[str]]) -> bool:
        """True when every clause is satisfied by at least one candidate value.

        ``candidates`` maps a field name to every value that counts as that
        field for one listing -- a list rather than a scalar because a role can
        legitimately have several. Location is the case that matters: an Ashby
        role carries a primary location plus any number of
        ``secondaryLocations``, and a role based in San Francisco that also
        lists London *is* a London role. Matching against only the primary would
        drop it silently, which is the expensive direction to be wrong in.

        A field named in the filters but absent from ``candidates`` fails the
        match. That only happens when an adapter does not offer the field at
        all, and quietly ignoring the clause would over-return rather than
        under-return.
        """
        for field, wanted in self.clauses.items():
            available = {normalize(value) for value in candidates.get(field, ()) if value}
            if not available & set(wanted):
                return False
        return True

    def unmatched_values(
        self, vocabulary: Mapping[str, set[str]]
    ) -> dict[str, tuple[str, ...]]:
        """Filter values that no listing on the board actually used.

        The typo detector. Because filtering happens before storage, a filter
        value that matches nothing produces exactly what an empty board produces
        -- zero rows -- and the mistake would otherwise stay invisible for as
        long as it took to wonder why a company never appears in the digest.

        ``vocabulary`` is the set of normalized values the board really returned
        for each field, so this reports only confident misconfiguration: the
        value is not merely unmatched this run, it is absent from the board's
        entire vocabulary.
        """
        stale = {}
        for field, wanted in self.clauses.items():
            seen = vocabulary.get(field, set())
            missing = tuple(value for value in wanted if value not in seen)
            if missing:
                stale[field] = missing
        return stale


def parse(raw: str, allowed_fields: Sequence[str]) -> BoardFilters:
    """Parse a sheet filter cell, rejecting anything we cannot honour.

    ``allowed_fields`` comes from the adapter, so each ATS declares what it can
    filter on and an unknown field is caught here rather than becoming a clause
    that never matches.

    >>> parse("location: London; team: Engineering", ["location", "team"]).clauses
    {'location': ('london',), 'team': ('engineering',)}
    """
    if not raw or not raw.strip():
        return BoardFilters(clauses={})

    permitted = {normalize(field): field for field in allowed_fields}
    clauses: dict[str, tuple[str, ...]] = {}

    for clause in raw.split(CLAUSE_SEPARATOR):
        if not clause.strip():
            # Trailing semicolons are a typing habit, not a mistake.
            continue

        field, separator, values = clause.partition(FIELD_SEPARATOR)
        if not separator:
            raise FilterError(
                f"Filter clause {clause.strip()!r} is missing a "
                f"{FIELD_SEPARATOR!r}; expected 'field: value, value'."
            )

        canonical = permitted.get(normalize(field))
        if canonical is None:
            raise FilterError(
                f"Cannot filter on {field.strip()!r}. "
                f"This board supports: {', '.join(sorted(allowed_fields))}."
            )

        wanted = tuple(
            normalize(value) for value in values.split(VALUE_SEPARATOR) if value.strip()
        )
        if not wanted:
            raise FilterError(
                f"Filter on {canonical!r} has no values. Give it at least one, "
                f"or remove the clause to stop filtering on it."
            )

        # Repeating a field is a natural way to extend a filter in a sheet;
        # read it as another set of alternatives rather than a redefinition.
        clauses[canonical] = tuple(dict.fromkeys(clauses.get(canonical, ()) + wanted))

    return BoardFilters(clauses=clauses)
