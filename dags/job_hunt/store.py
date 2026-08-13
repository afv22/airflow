"""Persistence for job listings seen across DAG runs.

One row per listing, widened as it moves through the pipeline: ``fetch_hn_jobs``
inserts the basic details, the research agent's verdict is added later, and
``email_digest`` records when it went out. Keeping all three stages in one table
means a listing is researched once and mailed once, however many runs see it.

The table lives in the shared SQLite database described in :mod:`common.db`.
"""

import json
from datetime import datetime, timezone
from typing import Any

from common import db

SOURCE_HACKERNEWS = "hackernews"

# Applied by init_schema(); every statement must be idempotent.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS job_listings (
        source          TEXT    NOT NULL,
        source_id       TEXT    NOT NULL,

        -- Populated by fetch_hn_jobs.
        title           TEXT    NOT NULL,
        url             TEXT,
        posted_at       TEXT,
        body            TEXT,
        fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),

        -- Populated by the research agent.
        verdict         TEXT    CHECK (verdict IN ('strong', 'review', 'reject')),
        verdict_json    TEXT,
        researched_at   TEXT,

        -- Populated by email_digest.
        sent_at         TEXT,
        email_id        TEXT,

        PRIMARY KEY (source, source_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_listings_unresearched
        ON job_listings (source, source_id) WHERE verdict IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_listings_unsent
        ON job_listings (verdict) WHERE verdict IS NOT NULL AND sent_at IS NULL
    """,
]


def init_schema() -> None:
    """Create the table and indexes if they are not already there."""
    db.init_schema(SCHEMA)


def utc_timestamp(epoch_seconds: int) -> str:
    """Format a Unix timestamp the way SQLite's ``datetime('now')`` does."""
    moment = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def upsert_listings(listings: list[dict[str, Any]]) -> None:
    """Record every listing seen this run, refreshing the ones already stored.

    Only ``fetched_at`` and the listing's own details are refreshed; anything a
    later stage wrote (verdict, sent_at) is left alone, so re-seeing a listing
    never undoes work already done on it.
    """
    if not listings:
        return

    db.executemany(
        """
        INSERT INTO job_listings (
            source, source_id, title, url, posted_at, body
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, source_id) DO UPDATE SET
            title      = excluded.title,
            url        = excluded.url,
            posted_at  = excluded.posted_at,
            body       = excluded.body,
            fetched_at = datetime('now')
        """,
        [
            (
                listing["source"],
                listing["source_id"],
                listing["title"],
                listing["url"],
                listing["posted_at"],
                listing["body"],
            )
            for listing in listings
        ],
    )


def unresearched(source: str, limit: int) -> list[dict[str, Any]]:
    """Return stored listings that no verdict has been written for yet.

    Newest first, so a capped run always spends its budget on the freshest
    listings; the rest stay in the table and come up on a later run.
    """
    rows = db.execute(
        """
        SELECT source, source_id, title, url, posted_at, body
        FROM job_listings
        WHERE source = ? AND verdict IS NULL
        ORDER BY posted_at DESC
        LIMIT ?
        """,
        (source, limit),
    )
    return [dict(row) for row in rows]


def record_verdict(
    source: str, source_id: str, verdict: str, payload: dict[str, Any]
) -> None:
    """Store a research verdict against a listing."""
    db.execute(
        """
        UPDATE job_listings
        SET verdict = ?, verdict_json = ?, researched_at = datetime('now')
        WHERE source = ? AND source_id = ?
        """,
        (verdict, json.dumps(payload), source, source_id),
    )


def mark_sent(keys: list[tuple[str, str]], email_id: str) -> None:
    """Flag listings as included in a digest that was sent successfully."""
    if not keys:
        return

    db.executemany(
        """
        UPDATE job_listings
        SET sent_at = datetime('now'), email_id = ?
        WHERE source = ? AND source_id = ?
        """,
        [(email_id, source, source_id) for source, source_id in keys],
    )
