"""Scan every active company's job board and record what is new.

Runs after ``sync_watchlist``, which is what puts the companies in the mirror
this task reads. Companies are taken from the database rather than passed in as
an argument, so the dependency in the DAG is ordering only.

A company is scanned when its board type has a scraper registered in
:mod:`.scrapers`. Types with no scraper yet are counted and named in the log
rather than treated as an error -- the sheet records what Andrew found, and a
board we cannot read yet is a gap to fill, not a broken run.
"""

from airflow.sdk import task

from job_lead_research.scan_boards import store
from job_lead_research.scan_boards.scrapers import scraper_for
from job_lead_research.sync_watchlist.store import (
    active_companies,
    mark_scanned,
    scanned_since,
)

RESCAN_AFTER_HOURS = 20


@task
def scan_boards(params: dict | None = None) -> int:
    """Scan the boards we can read and return how many new listings were stored.

    One company's board failing does not stop the scan. Boards break in ways
    that have nothing to do with each other -- a slug goes stale, a provider
    502s -- and the run is a daily sweep, so the right response is to record the
    rest and pick the failure up next time rather than lose the whole sweep to
    one bad board.

    Scraping is skipped for companies read within :data:`RESCAN_AFTER_HOURS`,
    which makes re-running the task cheap rather than another sweep of every
    board. The skip is per company, not per run, so a board that failed last
    time is retried while its neighbours are left alone. Trigger with the
    ``force_rescan`` param to scan regardless -- that is the escape hatch for
    when the boards themselves are what you are working on.
    """
    store.init_schema()

    force_rescan = bool((params or {}).get("force_rescan", False))

    companies = active_companies()
    recently_scanned = set() if force_rescan else scanned_since(RESCAN_AFTER_HOURS)
    total_new = 0
    scanned = 0
    skipped = 0
    failed: list[str] = []
    unsupported: list[str] = []

    for company in companies:
        if company.name in recently_scanned:
            skipped += 1
            continue

        scraper_class = scraper_for(company.board_type)
        if scraper_class is None:
            unsupported.append(f"{company.name} ({company.board_type.value})")
            continue

        try:
            new = scraper_class(company).run()
        except Exception as error:
            failed.append(f"{company.name}: {error}")
            continue

        # Stamped only on the success path: a company whose board failed or has
        # no scraper stays unstamped, and so stays a candidate for its opening
        # report whenever the board first reads clean. It is also what holds the
        # skip above off a board that failed, so failures retry next run.
        mark_scanned(company.name)

        total_new += new
        scanned += 1
        print(f"{company.name}: {new} new listings.")

    print(
        f"Scanned {scanned} of {len(companies)} active companies; "
        f"{total_new} new listings."
    )
    if skipped:
        print(
            f"Skipped {skipped} scanned in the last {RESCAN_AFTER_HOURS}h "
            f"(trigger with force_rescan=true to override)."
        )
    if unsupported:
        print(f"No scraper for {len(unsupported)}: {', '.join(unsupported)}")
    if failed:
        print(f"Failed for {len(failed)}: {'; '.join(failed)}")

    return total_new
