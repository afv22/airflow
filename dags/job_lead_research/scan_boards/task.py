"""Scan every watched company's job board and store the roles worth researching.

One mapped task per company, so a board that hangs or 500s costs exactly that
company: the failure is caught here, recorded in scan health, and the run
carries on. Only a run where *every* company failed is treated as a real
failure, because that is a systemic problem (network, database) rather than a
flaky board.

The harness knows nothing about any particular ATS. It resolves an adapter by
``board_type``, hands it a context, and stores what comes back -- which is what
makes adding an ATS additive.
"""

from airflow.sdk import task

from job_lead_research.scan_boards import adapters, scanning, store
from job_lead_research.scan_boards.types import ScanContext, ScanResult, ScanStatus
from job_lead_research.sync_watchlist import store as watchlist_store
from job_lead_research.sync_watchlist.types import Company

# Sanity bound per board, not a budget. Structured ATS responses are small
# enough that this should never bind; if it does, something is wrong with the
# filters and the warning it raises is the point.
MAX_LISTINGS_PER_BOARD = 200

# Ceiling on how many roles one run hands to the research agent.
MAX_RESEARCH_BATCH = 50


def scan_company(company: Company) -> ScanResult:
    """Run one company's board through its adapter, converting failure to health.

    Every exit path returns a :class:`ScanResult`; nothing raises. A board that
    is unreachable, an adapter that is not written yet, and a filter cell that
    does not parse are all things one company can be wrong about without
    spoiling the run for the rest.
    """
    adapter = adapters.resolve(company.board_type)
    if adapter is None:
        return ScanResult.failed(
            company.name,
            f"No adapter for board_type {company.board_type.value!r}. "
            f"Write one, or correct the sheet row.",
        )

    context = ScanContext.from_company(company, max_listings=MAX_LISTINGS_PER_BOARD)
    try:
        return scanning.scan(adapter, context)
    except Exception as exc:
        return ScanResult.failed(company.name, f"{type(exc).__name__}: {exc}")


@task
def scan_boards() -> int:
    """Scan every active company's board, returning how many roles are new.

    Runs after ``sync_watchlist``, which is what guarantees the company list
    reflects the sheet rather than a stale mirror.
    """
    store.init_schema()

    companies = watchlist_store.active_companies()
    if not companies:
        print("No active companies to scan.")
        return 0

    stored = 0
    results: list[ScanResult] = []
    for company in companies:
        result = scan_company(company)
        results.append(result)

        # Listings first, then health: a crash between the two leaves stored
        # listings looking unscanned, which the next run simply redoes. The
        # reverse order would claim a scan succeeded with nothing to show.
        if result.listings:
            stored += store.upsert_listings(
                result.listings, advance_last_seen=result.trustworthy
            )
        store.record_scan(result)

        print(
            f"{company.name}: {result.status} "
            f"fetched={result.fetched_count} kept={result.kept_count}"
            + (f" -- {result.warning}" if result.warning else "")
        )

    failed = [result for result in results if result.status is ScanStatus.ERROR]
    for result in failed:
        print(f"FAILED {result.company}: {result.error}")

    if failed and len(failed) == len(results):
        raise RuntimeError(
            f"Every board scan failed ({len(failed)} companies). "
            f"First error: {failed[0].error}"
        )

    print(
        f"Scanned {len(results)} companies: {stored} listings stored, "
        f"{len(failed)} failed."
    )
    return stored


@task
def select_unresearched() -> list[dict]:
    """Pull this run's research batch across every scanned board.

    Separate from scanning so that adding a source never means touching the
    research stage: everything that lands in ``board_listings`` competes in one
    prioritized, capped batch.
    """
    batch = store.unresearched(limit=MAX_RESEARCH_BATCH)
    print(f"Selected {len(batch)} listings to research.")
    return batch
