"""Board adapters, one module per ATS, resolved by a company's ``board_type``.

Adding an ATS is a new module plus a line in this registry -- deliberately the
smallest possible change, because the watchlist grows faster than adapters get
written and every unsupported board is a visible gap rather than a silent one.
Each module defines the five names :class:`~..scanning.BoardAdapter` asks for;
the shared scan loop in ``scanning`` does the rest.
"""

from ...sync_watchlist.types import ATSProvider
from ..scanning import BoardAdapter
from . import ashby, greenhouse

REGISTRY: dict[ATSProvider, BoardAdapter] = {
    ATSProvider.ASHBY: ashby,
    ATSProvider.GREENHOUSE: greenhouse,
}


def resolve(board_type: ATSProvider) -> BoardAdapter | None:
    """Return the adapter for a board type, or ``None`` if none is written yet.

    A missing adapter is not an error the harness should raise past: it is a
    prompt to write one (or fix the sheet row), recorded in scan health so it
    surfaces without failing a run that other companies scanned fine.
    """
    return REGISTRY.get(board_type)


__all__ = ["REGISTRY", "BoardAdapter", "resolve"]
