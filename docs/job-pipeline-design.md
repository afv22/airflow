# Job Discovery Pipeline — Design

The pipeline finds new, interesting roles and emails them to Andrew to apply.
It exists because the major aggregators (LinkedIn, ZipRecruiter) carry stale,
unresponsive listings; because the search should reach past the obvious
companies while staying opinionated about role shape; and because responding to
an email of leads is a sustainable habit where a manual morning research
routine is not.

Today the pipeline is a single Airflow DAG (`job_research`): fetch the
HackerNews jobs board, have a Playwright-equipped agent research each listing
against the `job-criteria` Airflow Variable, store verdicts in SQLite, email a
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
adapters returning structured JSON. Bespoke boards are deliberately skipped
(recorded in scan health) until they earn a dedicated deterministic adapter.

### AI judges; the rails are deterministic

AI earns its keep at exactly two points in this system: **deciding whether a
role is worth applying to**, and **discovering and vetting new companies**.
Loading and combing through a job board is neither — it is plumbing, and
plumbing is deterministic code. Ingestion, dedupe, filtering, and scheduling
are plain Python against structured APIs; AI credits are focused on the
judgment calls those rails deliver listings to. A board type without a
supported adapter is a gap to note, not a reason to point an agent at a web
page.

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
- **The company watchlist** — via ATS adapters, one per `board_type`.

The funnel then has stages of increasing cost:

0. **Adapter-level filters, defined per company by Andrew in the sheet** —
   applied by the adapter before anything is stored. Where an ATS honours
   query parameters these are pushed into the request; where it does not
   (Ashby, verified 2026-08-22) the adapter applies them client-side to the
   full response. Either way the stage is the adapter's contract, not the
   endpoint's capability.
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

## Board filtering (settled 2026-08-22)

### Ashby findings, verified against a live board

Tested against `api.ashbyhq.com/posting-api/job-board/Watershed` (34 roles):

- **The posting API honours no filter parameters.** `?location=London` returns
  all 34 roles, unchanged. The only documented parameter that does anything is
  `?includeCompensation=true`, which adds a `compensation` field. So Ashby
  filtering is entirely client-side, in the adapter.
- **`locationId` from a frontend board URL is unusable.** The UUID in
  `jobs.ashbyhq.com/Watershed?locationId=9df2f056-…` appears **nowhere** in the
  API payload — it is an internal entity ID the frontend resolves separately.
  Pasting a filtered board URL into the sheet therefore cannot filter anything.
  The API's location vocabulary is the human-readable string (`London`,
  `New York City`, `San Francisco`).
- **Fields available to filter on:** `location`, `secondaryLocations`,
  `department`, `team`, `employmentType`, `workplaceType`, `isRemote`,
  `title`. On Watershed, `location: London` alone cuts 34 → 9 and
  `team: Engineering` cuts 34 → 8.

### The filter model

- **Filters are applied inside the adapter, before storage.** Only kept roles
  are written. Accepted trade-off: a filtered-out role leaves no row, so the
  prefilter's false-reject rate cannot be audited from the table. The
  mitigations below exist to keep a misconfigured filter from being silent.
- **DSL, one sheet cell.** Keys are ATS field names; values comma-separated and
  OR'd within a key, AND'd across keys:

      location: London, Remote - UK; team: Engineering, Design

  Parsed to `dict[str, list[str]]`. An **unknown key fails the scan** rather
  than silently matching nothing. Negation (`exclude_title: Sales`) is
  deliberately deferred — the `exclude_` prefix can be added later without
  invalidating existing rows, and `title` is the field that will want it first.
- **Matching is literal, case- and whitespace-insensitive.** Andrew writes what
  that board actually uses. No cross-company normalization: the watchlist is
  hand-curated, so inspecting a board once when adding it is acceptable, and a
  hidden mapping layer would be a new failure mode.
- **Location matches primary *or* any `secondaryLocations` entry.** A role with
  `location: "San Francisco"` and `London` among its secondaries *is* a London
  role; a primary-only filter would drop it silently, which is the expensive
  direction to be wrong in.

### Making a bad filter visible

Filtering before storage means a typo'd filter and an empty board both produce
zero rows. Two mitigations, neither requiring the rejected rows to be stored:

- **`company_scan_state.last_warning`** (new column), separate from
  `last_error` so `status` keeps meaning what it means: a scan can succeed and
  still report that it looks misconfigured.
- **Filter values are validated against the vocabulary the board returned.** If
  `team: Enginering` matches none of the 34 fetched roles, that is recorded as
  a warning naming the values the board actually uses. This catches typo'd
  *values*, which key validation cannot — and is strictly more informative than
  a `fetched_count` / `kept_count` pair, which was considered and dropped in
  its favour.
- **A `board_url` carrying frontend filter params** (`?locationId=…`) does not
  fail the scan: the slug is extracted, params dropped, and the drop recorded
  as a warning. No run fails over a cosmetic URL detail, but the mistaken
  belief that a filter is active is discoverable.

---

## Staging plan

Sequenced so each round has one theme and one felt deliverable, and so early
rounds define the seams later rounds plug into. The two round-1 interfaces —
the `BoardListing` output shape and the `companies` row — are those seams.

### Round 1 — Watch a manual list of companies *(changes the mornings)*

Andrew adds "name + board URL + board type" rows to the sheet's Companies tab;
next morning's digest includes new engineering roles from those boards,
audited by the existing research agent. The workflow:

1. **Sync** the company watchlist from the sheet into the local mirror table.
   *(Built.)*
2. **Pull** each company's listings from its board API endpoint via the
   adapter for its `board_type`, applying user-defined endpoint filters where
   the ATS supports them. Dedupe against the local table; save new listings.
3. **Judge** — an agent goes through each new listing and marks it with a
   decision and reasoning.
4. **Send** the digest.

**Settled decisions (2026-08-20):**

- **Sheet access:** a GCP service account, with the sheet shared to its email;
  the pipeline reads the Companies tab via the Sheets API. Setup is on Andrew.
- **Write-back:** none. The pipeline is read-only on the sheet; email is its
  sole output. Andrew copies leads into the sheet when he decides to apply.
- ~~**Scan model:** a cheap/fast model for `scan_board`~~ — superseded below;
  there is no scan model because there is no scan agent.

**Settled decisions (2026-08-22) — no agent scraping:**

Agents are good at exploring and vetting new companies; they add nothing to
loading and combing through a job board. The original plan — agent scanning
first, ATS adapters as a cost optimization later — is inverted: **ATS API
adapters are built directly in round 1**, and AI spend is focused on judging
the listings those adapters return. The old "Round 2 — codify ATS adapters"
collapses into this round. Consequences:

- A `board_type` without an adapter is skipped and surfaced in scan health —
  it is a prompt to write an adapter (or fix the sheet row), never a fallback
  to an agent.
- Adapters apply per-company filters (department, team, location) defined by
  Andrew in the sheet row. Pushing them into the request is an optimization
  available only where the ATS honours query parameters — not the general
  case. See the Ashby findings below.
- The per-board listing cap survives as a sanity bound, but structured JSON
  makes it unlikely to bind.

**Settled decisions (2026-08-22) — the scan harness** *(scanner-agnostic;
these survive the shift from agents to adapters unchanged, except the second,
which is superseded):*

- **Scanner interface:** a scanner is a plain callable
  `scan(company: Company) -> ScanResult`, looked up in a registry keyed by
  `board_type`. The harness is one mapped task per company that resolves a
  scanner, calls it, records scan health, and returns listings. Agents and ATS
  adapters are both just entries in that registry, which is what keeps the
  agents-vs-API question out of the harness entirely. With agent scraping
  dropped, every registry entry is an API adapter — the registry itself is
  unchanged.
- ~~**The agent scanner is a callable, not a `@task.agent`.**~~ Superseded:
  there is no agent scanner. The reasoning is preserved only in git history;
  the surviving point is that the seam is a function signature, so nothing
  about the harness changes.
- **`ScanResult` carries listings *and* health** (`status`, `listing_count`,
  `error`, whether the per-board cap was hit). Exceptions are reserved for real
  crashes. This is what keeps `empty` ("board loaded, genuinely no roles")
  distinct from `error` ("scan failed"), the distinction `record_scan` exists
  to preserve.
- **Board listings get their own table**, owned by `job_lead_research`, rather
  than extending `job_listings`. `job_hunt` is being rewritten, so inheriting a
  schema shaped by HackerNews buys nothing; cross-source selection is deferred
  to the round where a second source actually exists.
- **Listing identity:** normalized URL (query string and fragment stripped) as
  `source_id`, with a `content_hash` over normalized title + location +
  department stored alongside. The hash makes URL churn measurable before
  committing to a more elaborate key — some boards mint per-session URLs, which
  would otherwise mint phantom "new" listings every run.
- **`last_seen` updates only on a trustworthy scan** — `status='ok'` and the
  per-board cap not hit. A capped or failed scan is a partial view of the board,
  and letting it touch `last_seen` would make close-detection read absent roles
  as closed. Close-marking itself stays out of round 1.
- **Failure isolation:** one company's failure never fails the run; the scan
  task records health and returns no listings. The run fails only if every scan
  failed, which is a systemic problem rather than a flaky board.

**Structural change:** fetch is decoupled from research. Ingestion tasks
(`fetch_hn_jobs`, `scan_board`) only write to the DB; a single
`select_unresearched` task pulls the research batch across all sources,
applying the global cap and prioritization (watchlist before HN, freshest
first). Adding a future source becomes purely additive.

Pieces, in build order:

1. **`sync_watchlist`** — reads the Companies tab (`name`, `board_url`,
   `board_type`, `status`, `notes`, endpoint filters) at the top of each run
   and fully rewrites the DB mirror; the sheet always wins. Pipeline-owned
   scan state (`last_scanned_at`, scan health) lives in a separate table keyed
   by company name, keeping the mirror dumb and replaceable. **(Built.)**
2. **ATS adapters** — one plain-Python function per `board_type`, registered
   by name: slug + optional endpoint filters in, `ScanResult` of `{title, url,
   location, department}` out. Build in order of watchlist coverage (expect
   Greenhouse and Ashby first). Failure rules per the harness decisions above:
   an errored scan writes a scan-health record and never touches listings; an
   unknown `board_type` is recorded as skipped.
3. **Mechanical prefilter** — pure-Python keyword/location pass before the
   research agent, with keyword lists in a config file (they get tuned weekly
   at first). Rejects only on confident negatives; ambiguity passes through —
   the expensive stage is the safety net, so the cheap stage is allowed to be
   dumb. Endpoint filters (stage 0) thin the input before this ever runs.
4. **DAG wiring** — per-company scan fan-out → flatten → upsert (normalized
   listing URL as `source_id`) → `select_unresearched` → research → digest,
   grouped by company with watchlist companies first.

Also in round 1: an explicit `status` column on `job_listings`
(`new → filtered | researched → sent`) with a stored `filter_reason`, rather
than inferring state from nullable columns — needed to audit the prefilter's
false-reject rate and to stop filtered rows from being reconsidered; track
`last_seen` on upsert (so close-detection works retroactively); feed the
company's `notes` into the research prompt.

Out of scope: discovery, company triage, cross-source dedupe, response
tracking, closed-role marking (though `last_seen` is tracked so it works
retroactively). HN continues alongside.

*(The former "Round 2 — codify ATS adapters" is absorbed into round 1 by the
no-agent-scraping decision; later rounds renumbered.)*

### Round 2 — Company-level triage *(changes add-a-company friction)*

Adding a company shrinks to just a name: a triage agent finds the careers
page, resolves ATS type/slug, writes the profile (size, UK presence,
sponsor-register match, domain fit) for the research prompt to use. Also the
natural home for `last_seen` payoffs: closed-role marking and "new in the
last 48h" in the digest.

### Round 3 — Automated discovery *(changes coverage)*

The weekly/monthly feeds above, each a producer of candidate names flowing
into round-2 triage. Also the right time for response tracking via the
Submissions tab, once volume makes criteria tuning worthwhile.

---

## Open items

- **Andrew:** create the GCP service account, share the sheet with its email,
  and store the credential as an Airflow connection.
- Confirm the Companies tab column layout so `sync_watchlist` can parse it
  (needs at least name, board URL, board type, status, notes).
- Capture-response mechanism in the digest (links that flip a flag) — design
  when volume justifies it.
