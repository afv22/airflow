# Onboarding report — implementation plan

Staged build of the design in [onboarding-report.md](onboarding-report.md).
Each stage is independently deployable: the pipeline keeps running its current
behavior until the final wiring step, so a stage can sit in production for a
day or two and be verified against real runs before the next one lands.

There is no test suite in this repo; each stage ends with a concrete manual
verification instead.

---

## Stage 1 — `company_scan_state` table and first-scan stamping

**Changes**

- `sync_watchlist/store.py`: add `company_scan_state` to `SCHEMA` (finally
  matching the module docstring), with `company_name` PK, `first_scanned_at`,
  `onboarded_at`.
- New function `mark_first_scanned(company_name)` — insert-or-ignore then
  `UPDATE ... SET first_scanned_at = datetime('now') WHERE first_scanned_at
  IS NULL`, so the stamp is write-once. Lives in `sync_watchlist/store.py`
  with the table; `scan_boards` imports it the way it already imports
  `active_companies`.
- `scan_boards/task.py`: call it after each company's scraper returns without
  raising (zero new listings still counts as a successful scan). Skipped
  (unsupported) and failed companies are not stamped.

**Exists after this stage**

- The table, created on the next run, filling with `first_scanned_at` rows
  for every company that scrapes clean. `onboarded_at` is NULL everywhere and
  nothing reads it yet.
- Emails and digest behavior completely unchanged.

**Verify**: after one scheduled run,
`SELECT * FROM company_scan_state` shows one row per scannable company;
a company with a broken slug has no row.

---

## Stage 2 — seed existing companies as already onboarded

A data step, not a code step, and it must land after stage 1 creates the
table but before stage 4 wires the task in — otherwise every current company
fires a catch-up report on the task's first run.

**Changes**

- Run the seed from the design doc against the state db:

  ```sql
  INSERT INTO company_scan_state (company_name, first_scanned_at, onboarded_at)
  SELECT DISTINCT company_name, datetime('now'), datetime('now')
  FROM job_listings
  ON CONFLICT (company_name) DO UPDATE
      SET onboarded_at = COALESCE(onboarded_at, datetime('now'));
  ```

- Deliberately leave out (or reset `onboarded_at` for) any company whose
  backlog should get the report treatment instead of manual handling.

**Exists after this stage**

- Every pre-existing company marked onboarded; only companies added to the
  sheet from now on (plus any deliberately reset) are candidates for a report.

**Verify**: `SELECT company_name FROM company_scan_state WHERE onboarded_at
IS NULL` returns only the companies intentionally left open.

---

## Stage 3 — `send_onboarding` package (store + template, no wiring)

**Changes** — new package `dags/job_lead_research/send_onboarding/` mirroring
the `send_digest` layout:

- `store.py`:
  - `companies_owed_report()` — `first_scanned_at IS NOT NULL AND
    onboarded_at IS NULL`.
  - `pending_count(company_name)` — listings still `fit_decision = 'pending'`,
    for the defer rule.
  - `sendable_listings(company_name)` — all unsent strong + review rows,
    strong first, digest's recency sort within each group, no LIMIT.
  - `mark_onboarded(company_name)` — sets `onboarded_at`.
  - Reuses `send_digest.store.mark_sent` for the listing flags rather than
    duplicating it.
- `template.py`: adapted from `send_digest/template.py` — count header
  ("8 roles passed filtering: 3 strong, 5 review"), two labelled sections
  (Strong fits / Worth review), per listing the same fields the digest shows.
- `task.py`: the task function, complete but **not yet imported by the DAG**:
  1. Gather owed companies; `AirflowSkipException` if none.
  2. Per company: defer (log, leave owed) if `pending_count > 0`; gather
     sendable listings.
  3. Zero sendable listings → `mark_onboarded`, log, no email.
  4. Otherwise send one email
     (`Onboarding: <name> — N roles (M strong) — DD Mon`), then
     `mark_sent(listings)`, then `mark_onboarded` — send-first ordering, per
     company, one company's failure not blocking the rest (collect and log
     failures, matching `scan_boards`' stance).

**Exists after this stage**

- The full package, importable and runnable by hand, invisible to Airflow —
  scheduled runs are byte-for-byte unchanged.

**Verify**: from a shell in the container, call the store functions against
the live db (owed list should match stage 2's query; `sendable_listings` for
a test company returns the expected rows in order). Render `template.py`
output to a file and eyeball the HTML.

---

## Stage 4 — wire the task into the DAG

**Changes**

- `dag.py`: import `send_onboarding_report`, insert it between `filter_fit`
  and `send_digest`:

  ```python
  fit_done = filter_fit(upstream=relevance_filtered)
  fit_done >> send_onboarding_report() >> send_digest()
  ```

  `send_digest` must depend on the report task (not run in parallel), and the
  report's skip must not skip the digest — with both tasks raising
  `AirflowSkipException` on empty pools, `send_digest` needs
  `trigger_rule="none_failed"` (worth setting on the digest task when wiring;
  the default `all_success` would skip it every morning the report skips).
- Update `dag.py` and `README.md` docstrings to name the new stage.

**Exists after this stage**

- The complete flow, live. Mornings with no new company: report task shows
  skipped, digest behaves exactly as today.

**Verify**: `airflow dags test job_lead_research` on a morning with no owed
companies — report skipped, digest still sent. Confirm in the UI that the
dependency chain renders as expected.

---

## Stage 5 — end-to-end shakedown with a real company

Not a code stage; the acceptance test.

- Add a genuinely new company (or reset one via
  `UPDATE company_scan_state SET onboarded_at = NULL WHERE company_name = ?`
  after clearing its listings' `sent` flags) and let the next scheduled run
  execute.

**Exists after this stage**

- One onboarding email received: full board, strong/review sections, no cap.
- That company's listings marked `sent = 1`; its `onboarded_at` set.
- The same morning's digest contains none of that company's roles; the next
  day's digest carries only genuine deltas.

**Verify**: the email itself, plus
`SELECT sent, fit_decision, COUNT(*) FROM job_listings WHERE company_name = ?
GROUP BY 1, 2`.
