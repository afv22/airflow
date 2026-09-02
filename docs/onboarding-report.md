# Onboarding reports for newly tracked companies

## Problem

When a company is first added to the watchlist, its entire board is scraped in
one sweep, and the fit stage can pass many listings at once. The daily digest
caps at 4 total listings per morning, so a new company's backlog drips out over
days — in arbitrary order, competing with every other company's leads, and
never giving a side-by-side view of the whole board. Picking the ~3 roles per
company actually worth applying to is a human decision, and it needs the full
cluster in one place.

## Design

Keep one pipeline. Registration stays as light as it is today (name + ATS type
in the sheet). The existing DAG grows one task, `send_onboarding_report`,
which runs between `filter_fit` and `send_digest`:

```
sync_watchlist >> scan_boards >> filter_relevance >> filter_fit
    >> send_onboarding_report >> send_digest
```

The morning after a company is added, the normal run scans its board and
judges every listing as usual. `send_onboarding_report` then sends **one email
per newly onboarded company** containing *all* of that company's sendable
listings (strong and review, labelled and grouped), marks them sent, and
records the company as onboarded. Because it runs upstream of `send_digest`
and marks its listings sent, none of them leak into that morning's digest —
the digest only ever carries deltas from already-onboarded companies.

Separate emails per company are deliberate: each is a cluster to sit down
with once, sift, and pick ~3 targets from.

## State

### New table: `company_scan_state`

The table the `sync_watchlist/store.py` docstring already promises but never
creates. Pipeline-owned, keyed by company name, deliberately outside the
`companies` mirror so the wholesale sheet rewrite cannot destroy it. Created
by `sync_watchlist.store.init_schema` alongside `companies`.

```sql
CREATE TABLE IF NOT EXISTS company_scan_state (
    company_name        TEXT NOT NULL PRIMARY KEY,
    first_scanned_at    TEXT,   -- set by scan_boards on the first successful scrape
    onboarded_at        TEXT    -- set by send_onboarding_report; NULL = report still owed
)
```

- `scan_boards` upserts (`INSERT ... ON CONFLICT DO NOTHING`, then
  `UPDATE ... WHERE first_scanned_at IS NULL`) after a company's scraper runs
  without raising — including a scrape that returns zero listings. A failed or
  unsupported board writes nothing, so the company stays "not yet scanned" and
  onboards whenever the board first succeeds.
- Rows outlive their company, matching the existing scan-state philosophy: a
  company removed and later re-added is *not* re-onboarded. If a genuine
  re-onboard is ever wanted, deleting the row is the manual lever.

### Listing state

No new column on `job_listings`. The onboarding report marks its listings
`sent = 1` through the existing `mark_sent`, and `sent` comes to mean
"included in an email" rather than specifically "digested" — the distinction
between report and digest lives in `company_scan_state.onboarded_at`, not per
listing. (If per-listing provenance ever matters, a `sent_via` column is a
straightforward later migration; it earns nothing today.)

## The task

`send_onboarding_report` (new package `send_onboarding/`, mirroring the
`send_digest` layout: `task.py`, `store.py`, `template.py`).

Per run:

1. **Find companies owed a report**: rows in `company_scan_state` where
   `first_scanned_at IS NOT NULL AND onboarded_at IS NULL`.
2. **Defer if judging is unfinished**: skip a company that still has listings
   with `fit_decision = 'pending'` (e.g. a fit batch that errored mid-run).
   It stays owed and reports tomorrow, so the report is always the complete
   board, never a partial one.
3. **Gather the cluster**: all unsent listings for the company with
   `fit_decision IN ('strong', 'review')` — no cap. Strong first, review
   after, `fit_reasoning` shown for each, same recency sort as the digest
   within each group.
4. **Send one email per company** via Resend, subject like:
   `Onboarding: Acme — 8 roles (3 strong) — 02 Sep`.
   A company whose board produced zero sendable listings sends no email but
   is still marked onboarded (logged, not mailed — "nothing worth seeing" is
   a normal outcome, not a report).
5. **Mark state, send-first**: after Resend accepts a company's message, mark
   its listings sent and set `onboarded_at`, per company, in that order. Same
   failure trade-off as the digest: a failed send leaves everything eligible
   for tomorrow; a send that lands but fails to mark costs one duplicate
   report, the cheaper error. One company's failure doesn't block the others
   (same isolation stance as `scan_boards`).
6. **Task outcome**: `AirflowSkipException` when no company is owed a report,
   so the common morning shows as skipped, consistent with `send_digest`.

`send_digest` needs no changes at all — its pool query already excludes
anything the report marked sent.

## Template

Start from `send_digest/template.py` with the cap-related framing removed and
two labelled sections (Strong fits / Worth review). Each listing: title,
location, link, published date, fit reasoning. A count header up top
("8 roles passed filtering: 3 strong, 5 review") so the sift has a shape
before the scroll.

## Deployment note

On first deploy, every existing company has no `company_scan_state` row and
would fire an "onboarding" report for whatever unsent backlog it has. Andrew
is handling the existing backlog manually — before enabling the task, seed
the table so history starts clean:

```sql
INSERT INTO company_scan_state (company_name, first_scanned_at, onboarded_at)
SELECT DISTINCT company_name, datetime('now'), datetime('now')
FROM job_listings;
```

(Or leave specific companies out of the seed to let the report do the
catch-up for them.)

## Edge cases considered

- **Board added with a typo'd slug**: scrape fails, no `first_scanned_at`,
  report simply waits until the board first scrapes clean.
- **Unsupported ATS type**: never scanned, never onboarded — consistent with
  today's "gap to fill, not a broken run" stance.
- **New roles posted the day after onboarding**: ordinary deltas; they flow
  through the daily digest like any other company's.
- **Fit stage partially errored on the sweep**: report defers (step 2) rather
  than sending an incomplete board.
- **Company paused in the sheet before its report fires**: its listings stop
  being scanned but the owed report still sends from what was stored — if
  Andrew paused it deliberately, deleting the scan-state row (or ignoring the
  email) is cheap.
