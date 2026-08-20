# Job Discovery Pipeline — Design

The pipeline finds new, interesting roles and emails them to Andrew to apply.
It exists because the major aggregators (LinkedIn, ZipRecruiter) carry stale,
unresponsive listings; because the search should reach past the obvious
companies while staying opinionated about role shape; and because responding to
an email of leads is a sustainable habit where a manual morning research
routine is not.

Today the pipeline is a single Airflow DAG (`job_research`): fetch the
HackerNews jobs board, have a Playwright-equipped agent research each listing
against `dags/criteria/job_search.md`, store verdicts in SQLite, email a
digest. This document describes where it goes from there.

---

## Design principles

### Companies are the asset; listings are ephemeral

Aggregators failed on *listings* (stale, no response) but succeeded at
*company discovery*. That is structural: an aggregator listing is a
days-to-weeks-old copy, while the listing on the company's own board is the
source of truth and disappears when the role actually closes. Therefore:

- The durable thing the pipeline builds is a **company watchlist**. Listings
  churn daily; a good company stays interesting for the whole search.
- **Aggregators are demoted from listing-source to discovery-source.** Their
  listings are never ingested as leads; they are mined for company names,
  which are then watched at the source.
- Listing ingestion and company discovery are **two separate loops at
  different cadences**, joined by the watchlist.

### The ATS convergence

Almost every startup/scale-up careers page is a skin over one of ~8 ATS
platforms, most with public unauthenticated JSON APIs:

| ATS | Endpoint shape |
|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` |
| Lever | `api.lever.co/v0/postings/{slug}` |
| Ashby | public posting API |
| Workable, Teamtailor, Personio, Recruitee, SmartRecruiters | similar; the latter few are disproportionately common in UK/EU |

So "watch every company's board" is not N bespoke scrapers: discovery resolves
each company to `(ats_type, ats_slug)`, and ingestion is a handful of polling
adapters returning structured JSON. Bespoke boards are handled by an agent
scraper at lower cadence, or deliberately skipped.

Polling the ATS directly also solves staleness: a listing vanishing from the
poll **is** the close signal. Tracking `first_seen` / `last_seen` per listing
gives listing lifecycle for free and lets the digest highlight freshly posted
roles.

### Source-of-truth split: the sheet is Andrew's, the DB is the pipeline's

A Google Sheet remains the human source of truth for the two things Andrew
actually looks at and edits:

- **Companies** — the targeted-company list (the watchlist input).
- **Submissions** — applications actually made, and their outcomes.

The SQLite database holds everything that only the pipeline needs: every
listing ever seen, verdicts, dedupe bookkeeping, sent-digest records, scan
timestamps. Rule of thumb: *if Andrew would ever open it to read or edit it,
it lives in the sheet; if it exists to make the pipeline idempotent or cheap,
it lives in the DB.* The pipeline reads the sheet's Companies tab as input and
treats the DB as a disposable cache/mirror of it — the sheet always wins.

The Submissions tab also closes the loop later: joined against sent leads, it
is ground truth for tuning the criteria and judging which sources earn their
spend.

---

## Architecture: two loops

### Loop 1 — Listing ingestion (daily, cheap, mechanical)

Polls known, structured sources and writes rows into `job_listings`:

- **Direct boards** (HN jobs today) — one adapter per source.
- **The company watchlist** — via ATS adapters (or, before those exist, an
  agent board-scanner producing the same output shape).

The funnel then has three stages of increasing cost:

1. **Mechanical prefilter, zero LLM** — title/department keywords and location
   strings from structured board data. Kills the obvious ~80% (sales,
   marketing, non-UK offices) for free.
2. **Cheap-model title screen** *(optional; add only if volume demands)* — a
   fast model judging title + location + department against condensed
   criteria, no browsing.
3. **Full agent research** — the existing Playwright agent, judging only the
   *role*, armed with the stored company profile so it never re-derives what
   the company is.

The criteria file's split between hard rejects (mostly mechanical: location,
contract, clearance, seniority keywords) and judgment calls maps directly onto
stages 1 and 3.

### Loop 2 — Company discovery (weekly, agentic, exploratory)

Grows the watchlist. Agent judgment is spent **once per company**, not once
per listing: on entry, a triage agent profiles the company (what it does,
size, UK entity, sponsor-licence match, domain fit against the criteria) and
resolves its careers page to an ATS slug. Rejected companies never cost
anything again.

Discovery feeds, roughly by value:

1. **Aggregators as radar** — weekly agent sweeps of standing searches,
   extracting only company names not already known.
2. **HN "Who is hiring" monthly threads** — text posts the current fetcher
   skips; one LLM parse per month yields dozens of companies.
3. **UK Register of Licensed Sponsors** — public Home Office CSV; both a
   discovery seed and a mechanical validation signal (fuzzy name join,
   refreshed monthly). Directly serves the sponsorship-by-2028 criterion.
4. **UK VC portfolios and funding news** (Index, Balderton, LocalGlobe,
   Seedcamp; Sifted) — "just raised Series B" matches the company-size
   criterion almost verbatim.
5. **Adjacency expansion** — when a company is marked a strong fit, an agent
   asks "who else does this?" Compounds: best leads generate more leads.
6. **Manual add** — zero-friction capture (a row in the sheet). Names heard
   from friends matter more than any feed.

### Cadence summary

- **Daily:** board polling + HN jobs + prefilter + agent research on
  survivors + digest.
- **Weekly:** aggregator radar, adjacency expansion, triage of newly
  discovered companies, retry of failed ATS resolutions.
- **Monthly:** Who-is-hiring parse, sponsor-register refresh, prune pass
  (no relevant openings in N months → paused).

---

## Data model

- **Companies tab (sheet, human-owned):** name, board URL, board type, status,
  notes. Mirrored read-only into a `companies` table at DAG start; later
  rounds add pipeline-written columns to the DB side only (ATS slug
  resolution, triage profile JSON, `last_polled_at`).
- **`job_listings` (DB, pipeline-owned):** existing table, extended with
  `company`, `first_seen` / `last_seen`, normalized title + location.
  `(source, source_id)` remains the dedupe key within a source.
- **Cross-source dedupe:** once the same role can arrive via HN, an
  aggregator, and the ATS — the ATS is canonical. A listing arriving from
  elsewhere for a watched company is resolved to its ATS listing (company +
  normalized title + location) rather than researched independently.
- **Submissions tab (sheet, human-owned):** applications and outcomes; used
  for dedupe-against-applied and, later, criteria tuning.

---

## Staging plan

Sequenced so each round has one theme and one felt deliverable, and so early
rounds define the seams later rounds plug into. The two round-1 interfaces —
the `BoardListing` output shape and the `companies` row — are those seams.

### Round 1 — Watch a manual list of companies *(changes the mornings)*

Andrew adds "name + board URL + board type" rows to the sheet's Companies tab;
next morning's digest includes new engineering roles from those boards,
audited by the existing research agent. Agent inference substitutes for ATS
adapters initially — higher spend, near-zero integration code.

**Settled decisions (2026-08-20):**

- **Sheet access:** a GCP service account, with the sheet shared to its email;
  the pipeline reads the Companies tab via the Sheets API. Setup is on Andrew.
- **Write-back:** none. The pipeline is read-only on the sheet; email is its
  sole output. Andrew copies leads into the sheet when he decides to apply.
- **Scan model:** a cheap/fast model for `scan_board`; the flagship model is
  reserved for research verdicts.

**Structural change:** fetch is decoupled from research. Ingestion tasks
(`fetch_hn_jobs`, `scan_board`) only write to the DB; a single
`select_unresearched` task pulls the research batch across all sources,
applying the global cap and prioritization (watchlist before HN, freshest
first). Adding a future source becomes purely additive.

Pieces, in build order:

1. **`sync_watchlist`** — reads the Companies tab (`name`, `board_url`,
   `board_type`, `status`, `notes`) at the top of each run and fully rewrites
   the DB mirror; the sheet always wins. Pipeline-owned scan state
   (`last_scanned_at`, scan health) lives in a separate table keyed by company
   name, keeping the mirror dumb and replaceable.
2. **`scan_board` agent task** — one Playwright-agent invocation per company,
   cheap model: list every visible listing as structured `{title, url,
   location, department}`. No judgment — a scraper in an agent costume. Its
   output shape is identical to what an ATS adapter will return, so round 2's
   swap is invisible downstream. Failure rules: an errored or empty scan
   writes a scan-health record and never touches listings (a flaky page must
   not look like "all roles closed"); a per-board listing cap stops one large
   board from eating the run. This is the round's cost center and round 2's
   deletion target.
3. **Mechanical prefilter** — pure-Python keyword/location pass before the
   research agent, with keyword lists in a config file (they get tuned weekly
   at first). Rejects only on confident negatives; ambiguity passes through —
   the expensive stage is the safety net, so the cheap stage is allowed to be
   dumb. An hour of work that halves spend from day one.
4. **DAG wiring** — `scan_board` fan-out → flatten → upsert into
   `job_listings` (`source='board_scan'`, listing URL as `source_id`) →
   `select_unresearched` → research → digest, grouped by company with
   watchlist companies first.

Also in round 1: an explicit `status` column on `job_listings`
(`new → filtered | researched → sent`) with a stored `filter_reason`, rather
than inferring state from nullable columns — needed to audit the prefilter's
false-reject rate and to stop filtered rows from being reconsidered; track
`last_seen` on upsert (so close-detection works retroactively); feed the
company's `notes` into the research prompt.

Out of scope: ATS adapters, discovery, company triage, cross-source dedupe,
response tracking. HN continues alongside.

### Round 2 — Codify ATS adapters *(changes the bill)*

One adapter at a time, ordered by watchlist coverage (expect Greenhouse and
Ashby first). Dispatch on `board_type`; agent scan remains the fallback for
bespoke boards and the self-heal when an adapter goes empty (companies migrate
ATS). `last_seen` starts paying: closed-role marking, "new in last 48h" in the
digest.

### Round 3 — Company-level triage *(changes add-a-company friction)*

Adding a company shrinks to just a name: a triage agent finds the careers
page, resolves ATS type/slug, writes the profile (size, UK presence,
sponsor-register match, domain fit) for the research prompt to use.

### Round 4 — Automated discovery *(changes coverage)*

The weekly/monthly feeds above, each a producer of candidate names flowing
into round-3 triage. Also the right time for response tracking via the
Submissions tab, once volume makes criteria tuning worthwhile.

---

## Open items

- **Andrew:** create the GCP service account, share the sheet with its email,
  and store the credential as an Airflow connection.
- Confirm the Companies tab column layout so `sync_watchlist` can parse it
  (needs at least name, board URL, board type, status, notes).
- Capture-response mechanism in the digest (links that flip a flag) — design
  when volume justifies it.
