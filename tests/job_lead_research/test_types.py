from job_lead_research.types import (
    ATSProvider,
    Company,
    FitDecision,
    JobListing,
    RelevanceDecision,
)


def test_load_minimal_company():
    record = {
        "name": "Company Name",
        "board_type": "Ashby",
    }
    company = Company.load(record)
    assert company.name == "Company Name"
    assert company.board_type == ATSProvider.ASHBY
    assert company.board_slug == ""


def test_load_full_company():
    record = {
        "name": "Company Name",
        "board_url": "https://example.com/careers",
        "board_type": "Ashby",
        "board_slug": "company-slug",
        "status": "active",
        "notes": "Some notes",
        "synced_at": "2024-01-01T00:00:00Z",
    }
    company = Company.load(record)
    assert company.name == "Company Name"
    assert company.board_url == "https://example.com/careers"
    assert company.board_type == ATSProvider.ASHBY
    assert company.board_slug == "company-slug"
    assert company.status == "active"
    assert company.notes == "Some notes"
    assert company.synced_at == "2024-01-01T00:00:00Z"


def test_load_minimal_job_listing():
    record = {
        "id": "123",
        "published_at": "2024-01-01T00:00:00Z",
    }
    listing = JobListing.load(record)
    assert listing.id == "123"
    assert listing.company_name == ""
    assert listing.location == ""
    assert listing.title == ""
    assert listing.description == ""
    assert listing.listing_url == ""
    assert listing.published_at == "2024-01-01T00:00:00Z"
    assert listing.added_at == ""
    assert listing.relevance_decision == RelevanceDecision.PENDING
    assert listing.relevance_rejection == ""
    assert listing.fit_decision == FitDecision.PENDING
    assert listing.fit_reasoning == ""


def test_load_full_job_listing():
    record = {
        "id": "123",
        "company_name": "Company Name",
        "location": "Remote",
        "title": "Software Engineer",
        "description": "Job description",
        "listing_url": "https://example.com/jobs/123",
        "published_at": "2024-01-01T00:00:00Z",
        "added_at": "2024-01-02T00:00:00Z",
        "relevance_decision": "pass",
        "relevance_rejection": "",
        "fit_decision": "strong",
        "fit_reasoning": "Great fit",
    }
    listing = JobListing.load(record)
    assert listing.id == "123"
    assert listing.company_name == "Company Name"
    assert listing.location == "Remote"
    assert listing.title == "Software Engineer"
    assert listing.description == "Job description"
    assert listing.listing_url == "https://example.com/jobs/123"
    assert listing.published_at == "2024-01-01T00:00:00Z"
    assert listing.added_at == "2024-01-02T00:00:00Z"
    assert listing.relevance_decision == RelevanceDecision.PASS
    assert listing.relevance_rejection == ""
    assert listing.fit_decision == FitDecision.STRONG
    assert listing.fit_reasoning == "Great fit"
