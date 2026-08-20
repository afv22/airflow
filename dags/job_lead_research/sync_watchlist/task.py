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
from .types import Company

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


def fetch_watchlist(conn_id: str = WATCHLIST_CONN_ID) -> list[Company]:
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
    return [Company.load(record) for record in records if record.get("name")]


@task
def sync_watchlist() -> int:
    """Refresh the company mirror from the sheet and return number of active rows.

    The mirror is rewritten wholesale, so a company deleted from the sheet stops
    being scanned the same day. Scan state survives that rewrite (it lives in its
    own table), which is what lets the scanner keep prioritizing least-recently
    scanned boards across syncs.

    Downstream tasks should ingest directly from the db.
    """
    store.init_schema()

    companies = fetch_watchlist()
    synced = store.replace_companies(companies)

    active = store.active_companies()
    print(f"Synced {synced} companies from the sheet; {len(active)} active to scan.")

    return len(active)
