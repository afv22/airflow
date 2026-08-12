def format_job(job: dict) -> str:
    """Render one approved listing as an HTML list item for the digest."""
    reasons = "".join(f"<li>{reason}</li>" for reason in job["reasons"])

    extracted = job["extracted"]
    facts = [
        extracted[key]
        for key in ("seniority_band", "stack", "location", "comp_range")
        if extracted.get(key)
    ]
    facts_line = f"<br/><small>{' &middot; '.join(facts)}</small>" if facts else ""

    return (
        f"<li><a href=\"{job['url']}\">{job['title']}</a>"
        f"{facts_line}"
        f"<ul>{reasons}</ul></li>"
    )