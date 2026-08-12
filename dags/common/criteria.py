"""Loaders for the free-form criteria files under ``dags/criteria``."""

from pathlib import Path

CRITERIA_DIR = Path(__file__).resolve().parent.parent / "criteria"

JOB_SEARCH_FILE = "job_search.md"


def load_criteria(filename: str = JOB_SEARCH_FILE) -> str:
    """Return the raw text of a criteria file."""
    return (CRITERIA_DIR / filename).read_text()
