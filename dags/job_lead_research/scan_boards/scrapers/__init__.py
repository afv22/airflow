"""Board scrapers, and the registry that maps a company to the right one.

``SCRAPERS`` is the single place a provider becomes supported. ``ATSProvider``
deliberately has members for platforms with no scraper written yet, so the two
do not line up one-to-one: a missing key here is the normal state for a board
type Andrew has recorded but which nobody has implemented, and it is what lets
the scan report "no scraper yet" instead of failing.
"""

from job_lead_research.types import ATSProvider
from .base import ATSScraper
from .ashby import AshbyScraper
from .greenhouse import GreenhouseScraper
from .ibm import IBMScraper
from .pinpoint import PinpointScraper

SCRAPERS: dict[ATSProvider, type[ATSScraper]] = {
    ATSProvider.ASHBY: AshbyScraper,
    ATSProvider.GREENHOUSE: GreenhouseScraper,
    ATSProvider.PINPOINT: PinpointScraper,
    ATSProvider.IBM: IBMScraper,
}


def scraper_for(board_type: ATSProvider) -> type[ATSScraper] | None:
    """The scraper class handling this board type, or None if none does."""
    return SCRAPERS.get(board_type)


__all__ = [
    "ATSScraper",
    "AshbyScraper",
    "GreenhouseScraper",
    "IBMScraper",
    "PinpointScraper",
    "SCRAPERS",
    "scraper_for",
]
