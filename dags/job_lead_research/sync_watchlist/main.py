"""Mirror the Companies tab of the watchlist sheet into the local database.

Runs at the top of every job-lead run. The sheet is the human source of truth
for which companies to watch; this task copies it in wholesale and hands the
active rows downstream to the board scanner. Nothing is written back -- the
pipeline is read-only on the sheet by design.

Configuration lives in one Airflow Connection (see WATCHLIST_CONN_ID), so the
credential and the sheet it points at move together and neither is hardcoded.
"""

from airflow.sdk import Connection, task

from common import gsheets
from job_lead_research.sync_watchlist import store

# Connection holding both the credential and the sheet coordinates. Create it in
# the Airflow UI (Admin > Connections) as a Generic connection with this extra:
#
#   {
#     "keyfile_dict": "{\"type\": \"service_account\", ...}",
#     "spreadsheet_id": "1AbC...",
#     "companies_range": "Companies!A:E"
#   }
#
# The sheet must be shared (Viewer is enough) with the service account's
# client_email, or the API 404s as though the spreadsheet did not exist.
WATCHLIST_CONN_ID = "job_watchlist_sheet"

# Used only if the connection does not name one, so the common case needs no
# extra config but an unusual tab layout stays overridable.
DEFAULT_RANGE = "Companies!A:E"

# The sheet's headers, normalized by gsheets.rows_to_dicts, mapped onto the
# mirror's columns. Only 'name' is required; a row without one is spacing or a
# half-typed entry, not a company.
COLUMNS = ("name", "board_url", "board_type", "status", "notes")


def as_company_row(record: dict[str, str]) -> dict[str, str]:
    """Map one normalized sheet row onto the columns of ``companies``.

    Unknown columns in the sheet are ignored rather than rejected, so Andrew can
    add his own scratch columns to the tab without breaking the sync.
    """
    return {column: record.get(column, "") for column in COLUMNS}


def fetch_watchlist(conn_id: str = WATCHLIST_CONN_ID) -> list[dict[str, str]]:
    """Read the Companies tab and return one dict per usable row."""
    conn = Connection.get(conn_id)
    extra = conn.extra_dejson

    spreadsheet_id = extra.get("spreadsheet_id")
    if not spreadsheet_id:
        raise ValueError(
            f"Connection {conn_id!r} has no 'spreadsheet_id' in its extra."
        )

    rows = gsheets.read_range(
        conn_id=conn_id,
        spreadsheet_id=spreadsheet_id,
        range_=extra.get("companies_range") or DEFAULT_RANGE,
    )

    records = gsheets.rows_to_dicts(rows)
    return [as_company_row(record) for record in records if record.get("name")]


@task
def sync_watchlist() -> list[dict]:
    """Refresh the company mirror from the sheet and return the rows to scan.

    The mirror is rewritten wholesale, so a company deleted from the sheet stops
    being scanned the same day. Scan state survives that rewrite (it lives in its
    own table), which is what lets the scanner keep prioritizing least-recently
    scanned boards across syncs.
    """
    store.init_schema()

    companies = fetch_watchlist()
    synced = store.replace_companies(companies)

    active = store.active_companies()
    print(f"Synced {synced} companies from the sheet; {len(active)} active to scan.")

    return active
