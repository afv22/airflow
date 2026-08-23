from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class ATSProvider(Enum):
    """The board platform a company's careers page is a skin over.

    Members exist for platforms we can name in the sheet, not only for ones with
    an adapter written: naming a board type is how Andrew records what he found,
    and an unsupported type should surface as "no adapter yet" in scan health
    rather than collapse into OTHER and lose the information.
    """

    ASHBY = "ashby"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKABLE = "workable"
    OTHER = "other"


@dataclass
class Company:
    name: str
    board_url: str
    board_type: ATSProvider
    board_slug: str
    status: str
    notes: str
    synced_at: str

    @staticmethod
    def load(record: Mapping[str, str]) -> "Company":
        try:
            board_type = ATSProvider(record.get("board_type", "").lower())
        except ValueError:
            board_type = ATSProvider.OTHER

        return Company(
            name=record["name"],
            board_url=record.get("board_url", ""),
            board_type=board_type,
            board_slug=record.get("board_slug", ""),
            status=record.get("status", ""),
            notes=record.get("notes", ""),
            synced_at=record.get("synced_at", ""),
        )

    def dump(self) -> tuple:
        return (
            self.name,
            self.board_url,
            self.board_type.value,
            self.board_slug,
            self.status,
            self.notes,
            self.synced_at,
        )


@dataclass
class JobListing:
    id: str
    company_name: str
    location: str
    title: str
    description: str
    listing_url: str
    published_at: str
    added_at: str

    @staticmethod
    def load(record: Mapping[str, str]) -> "JobListing":
        return JobListing(
            id=record["id"],
            company_name=record.get("company_name", ""),
            location=record.get("location", ""),
            title=record.get("title", ""),
            description=record.get("description", ""),
            listing_url=record.get("listing_url", ""),
            published_at=record.get("published_at", ""),
            added_at=record.get("added_at", ""),
        )

    def dump(self) -> tuple:
        return (
            self.id,
            self.company_name,
            self.location,
            self.title,
            self.description,
            self.listing_url,
            self.published_at,
            self.added_at,
        )
