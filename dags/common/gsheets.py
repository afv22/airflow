"""Read-only Google Sheets access via a service-account credential.

Deliberately not ``apache-airflow-providers-google``: that provider brings the
whole GCP SDK (ray, google-ads, aiplatform -- ~95 dependencies) to service what
is a single read call. ``google-api-python-client`` plus ``google-auth`` covers
it. If a second GCP service ever appears in this project, reconsider.

The credential lives in an Airflow Connection so it is Fernet-encrypted at rest
alongside the other secrets, rather than sitting as a plaintext key file on a
bind mount. See ``read_range`` for the expected connection shape.
"""

import json
from typing import Any

from airflow.sdk import Connection
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Read-only: the pipeline never writes to the sheet (the sheet is the human's
# source of truth, email is the pipeline's only output). Asking for the
# narrower scope means a misconfigured DAG cannot clobber Andrew's data.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Values come back as display strings rather than raw/typed cell values, which
# is what a watchlist of names and URLs wants.
VALUE_RENDER_OPTION = "FORMATTED_VALUE"


def _credentials(conn: Connection) -> Credentials:
    """Build service-account credentials from the connection's keyfile JSON.

    The key is stored as a JSON *string* inside the extra dict (``keyfile_dict``)
    rather than as nested JSON, because that is what the Airflow UI's extra
    field round-trips cleanly. Both forms are accepted here anyway, since
    hand-editing a connection makes the nested form easy to produce by accident.
    """
    keyfile: Any = conn.extra_dejson.get("keyfile_dict")
    if not keyfile:
        raise ValueError(
            f"Connection {conn.conn_id!r} has no 'keyfile_dict' in its extra. "
            "Paste the service account JSON there as a string."
        )

    if isinstance(keyfile, str):
        keyfile = json.loads(keyfile)

    return Credentials.from_service_account_info(keyfile, scopes=SCOPES)


def read_range(conn_id: str, spreadsheet_id: str, range_: str) -> list[list[str]]:
    """Return the cell values in ``range_`` as a list of rows.

    The connection needs ``keyfile_dict`` in its extra field, holding the
    service account JSON. The sheet must be shared with that service account's
    ``client_email`` -- sharing is what grants access; the API returns 404
    (not 403) for a sheet the account cannot see, which reads misleadingly like
    a wrong spreadsheet id.

    Trailing empty cells are not padded by the API: a row whose last columns are
    blank comes back short, and a wholly empty row is omitted. Callers must
    tolerate ragged rows -- :func:`rows_to_dicts` does.
    """
    conn = Connection.get(conn_id)

    service = build(
        "sheets",
        "v4",
        credentials=_credentials(conn),
        # The discovery document is fetched over the network by default, which
        # makes task startup depend on a Google endpoint that is not the API
        # itself. The client ships a static copy; use it.
        static_discovery=True,
        cache_discovery=False,
    )

    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueRenderOption=VALUE_RENDER_OPTION,
        )
        .execute()
    )

    return response.get("values", [])


def rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    """Map a header row plus data rows onto dicts, keyed by header name.

    Headers are lowercased and whitespace-collapsed to underscores, so a human
    retitling "Board URL" to "board url" in the sheet does not break the parse.
    Short rows (the API truncates trailing blanks) are padded with empty
    strings, and every value is stripped.
    """
    if not rows:
        return []

    header, *data = rows
    keys = [cell.strip().lower().replace(" ", "_") for cell in header]

    records = []
    for row in data:
        padded = row + [""] * (len(keys) - len(row))
        record = {key: str(value).strip() for key, value in zip(keys, padded)}
        # A row that is blank across every column is spacing, not data.
        if any(record.values()):
            records.append(record)

    return records
