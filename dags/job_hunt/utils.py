def format_job(job: dict) -> str:
    """Render one approved listing as an HTML list item for the digest."""
    reasons = "".join(f"<li>{reason}</li>" for reason in job["reasons"])

    return f"<li><a href=\"{job['url']}\">{job['title']}</a>" f"<ul>{reasons}</ul></li>"
