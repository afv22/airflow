import pytest
import requests

from job_lead_research.scan_boards.scrapers import AshbyScraper
from job_lead_research.scan_boards.scrapers.ashby import API_ROOT
from job_lead_research.types import ATSProvider, Company


def company():
    return Company(
        name="Elliptic",
        board_url="",
        board_type=ATSProvider.ASHBY,
        board_slug="elliptic",
        status="active",
        notes="",
        synced_at="",
    )


def test_fetch_board(fake_get, load_fixture):
    calls = fake_get(load_fixture("ashby_board.json"))

    jobs = AshbyScraper(company()).fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Engineering Manager - DevOps & AI Platform"
    url, kwargs = calls[0]
    assert url == f"{API_ROOT}/elliptic"
    assert kwargs["params"] == {"includeCompensation": "true"}


def test_fetch_jobs_raises_on_http_error(fake_get):
    fake_get({}, status_code=502)

    with pytest.raises(requests.HTTPError):
        AshbyScraper(company()).fetch_jobs()


def test_parse_job(load_fixture):
    board = load_fixture("ashby_board.json")
    board_company = company()

    job = AshbyScraper(board_company).parse_job(board["jobs"][0])

    assert job.id == "7639d887-6204-450f-99ae-071d717fa1f4"
    assert job.company_name == board_company.name
    assert job.title == "Engineering Manager - DevOps & AI Platform"
    assert job.location == "London, United Kingdom, Washington, D.C."
    assert job.description == board["jobs"][0]["descriptionPlain"]
    assert (
        job.listing_url
        == f"https://jobs.ashbyhq.com/elliptic/7639d887-6204-450f-99ae-071d717fa1f4"
    )
    assert job.published_at == "2026-09-07T08:12:26.087+00:00"
