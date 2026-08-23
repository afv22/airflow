"""Shared shape for the per-provider board scrapers.

Every ATS exposes the same thing behind a different payload: a list of open
postings for one company. A scraper's job is to get that list and flatten each
posting onto :class:`JobListing`; everything after that -- deduping, storage,
the accumulate-don't-overwrite policy -- is identical across providers and lives
here so no adapter can get it subtly wrong.

A scraper is constructed for one company and run once:

    AshbyScraper(company).run()
"""

from abc import ABC, abstractmethod

from job_lead_research.types import Company, JobListing
from job_lead_research.scan_boards.store import insert_listings


class ATSScraper(ABC):
    def __init__(self, company: Company):
        self.company = company

    def run(self) -> int:
        """Scan the company's board and store what is new, returning that count.

        Postings that fail to parse are skipped rather than failing the scan: a
        single malformed row on a board of fifty should not cost us the other
        forty-nine, and the board is re-read in full next run anyway.
        """
        jobs = []
        for blob in self.fetch_jobs():
            try:
                jobs.append(self.parse_job(blob))
            except (KeyError, TypeError, ValueError) as error:
                print(
                    f"Skipping unparseable posting from {self.company.name}: {error}"
                )

        return self.save_jobs(jobs)

    @abstractmethod
    def fetch_jobs(self) -> list[dict]:
        """Return one raw blob per open posting on the company's board."""

    @abstractmethod
    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one raw posting onto the common listing shape."""

    def save_jobs(self, jobs: list[JobListing]) -> int:
        return insert_listings(jobs)
