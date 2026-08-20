from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class ATSProvider(Enum):
    OTHER = "other"


@dataclass
class Company:
    name: str
    board_url: str
    board_type: ATSProvider
    filters: str
    status: str
    notes: str
    synced_at: str | None = None

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
            filters=record.get("filters", ""),
            status=record.get("status", ""),
            notes=record.get("notes", ""),
        )

    def dump(self) -> tuple:
        return (
            self.name,
            self.board_url,
            self.board_type.value,
            self.filters,
            self.status,
            self.notes,
        )
