# Job Listing Screening Criteria

Instructions for an LLM evaluating individual job listings for relevance.

**Design principle:** role shape is the gate, industry is a modifier. A listing is not rejected for being in logistics or healthtech — it is rejected for being a frontend role, a management role, or a contract role.

---

## Candidate context

Provided so the reader can make judgment calls, not to be pattern-matched against.

- Backend software engineer, ~3 years professional experience, previously at a large tech company working on data pipelines, evaluation infrastructure, and investigative tooling for fraud and financial abuse detection.
- Roughly two years out of full-time employment. Recent-continuous-experience requirements should not be treated as disqualifying.
- Based in London. UK HPI visa valid through May 2028 — **no sponsorship required now**, but sponsorship capability matters for the future.
- Core strength: building systems that non-technical operational teams (investigators, analysts, policy, legal) depend on.

---

## Hard rejects

Any single trigger kills the listing.

1. **Not London-accessible.** London, London hybrid, or UK-remote all qualify. Reject other UK cities unless explicitly remote-friendly. Reject non-UK roles, including remote roles that cannot employ someone in the UK.
2. **Contract, freelance, day-rate, fixed-term, or agency-brokered.** Permanent employment only. Reject anything quoting a day rate or referencing IR35.
3. **Requires security clearance** (SC, DV, NPPV) or UK/EU citizenship.
4. **People management is the primary function.** Engineering Manager, Head of Engineering, Director, VP. Reject where the JD describes headcount ownership, performance reviews, or hiring as core duties. A senior IC role mentioning mentorship is fine.
5. **Staff, Principal, or Distinguished level.** Also reject where the JD demands 8+ years as a firm bar.
6. **Graduate, intern, or apprenticeship programmes.** Explicitly early-career pipelines only — see the seniority section below for how to treat ordinary junior-to-mid postings.
7. **Frontend, mobile, or design-adjacent primary.** React/iOS/Android/UI as the main job.
8. **Security operations roles.** SOC analyst, incident response, threat hunting, penetration testing, malware analysis, reverse engineering, network forensics, security architecture, GRC, AppSec. A security *domain* is not itself disqualifying — backend, data, or platform engineering roles on a security, detection, or abuse team are in scope. Reject when the job is operating security tooling and applying adversary tradecraft rather than building systems.
9. **Pure trust & safety / content moderation.** Policy enforcement or moderation operations as the entire remit, with no systems-building component.
10. **Consultancy, agency, body-shop, or outsourcing firm** as the employer. Client-billed delivery work.
11. **Not a software engineering role.** Data analyst, PM, solutions engineer, sales engineer, DevRel, QA-only, research scientist requiring a PhD.

---

## Seniority

The target band is **mid to mid-senior IC**. In practice this is wider than the titles suggest, and UK title inflation runs in both directions.

- **Accept:** Software Engineer, Backend Engineer, Senior Software Engineer, Data Engineer, Platform Engineer, and equivalents — where the experience bar reads roughly 2–6 years.
- **Accept, do not reject:** postings asking for "2+ years," "3+ years," or listing no years at all. These are in scope even where the title omits "Senior."
- **`review`, not reject:** postings asking for 5–7 years. Reachable depending on how the responsibilities read.
- **Reject:** graduate schemes, internships, apprenticeships, and Staff/Principal/Distinguished.
- **Ignore employment gaps as a filter.** Do not downweight a listing for asking about continuous or recent experience. Flag it in `reasons` only if the JD makes recency an explicit hard requirement.

Weight the stated responsibilities over the stated years. A "Senior" title attached to ordinary IC delivery work is in scope; a mid-level title attached to org-wide architecture ownership is not.

---

## Positive signals

More matches → `strong`.

### Role shape — heaviest weight

- Backend, distributed systems, or platform engineering
- Data pipelines, data platform, ETL, streaming, batch processing
- Pipelines that produce, score, or serve behavioural signals — abuse, fraud, bot, or anomaly detection at scale
- Internal tooling or systems built for operational teams — investigators, analysts, ops, compliance
- Evaluation infrastructure, human-in-the-loop systems, model-adjacent engineering that is not model research
- Explicit cross-functional work with non-engineering stakeholders — legal, policy, ops, risk
- Ownership language: "own the service," "end-to-end," "from design to production"

### Domain — bonus, never required

- Fraud, risk, financial crime, AML/KYC, sanctions screening, abuse detection, adversarial systems
- Payments infrastructure, ledgers, settlement, reconciliation
- Climate, energy transition, grid, metering, carbon accounting
- Insurance and insurtech — claims fraud, SIU tooling, underwriting, application fraud
- Anything with real operational complexity and regulatory exposure

### Company

- 50+ engineers, or Series B and beyond
- Holds a UK sponsor licence, or is large enough that it plausibly does
- For remote-first companies: has a UK entity or established UK employment path. Remote-first with no UK presence is a `review` at best, since it likely forecloses future sponsorship.

### Stack

| Tier | Languages | Treatment |
|---|---|---|
| Primary | Python | Strongest match |
| Secondary | TypeScript / Node, PHP | Full match, no downweight |
| Familiar | Rust | Recreational only — fine as a secondary language, `review` if primary |
| Unfamiliar | Go, Java, Kotlin, Scala, C#, Ruby, Elixir, C++ | Downweight, **do not reject**. Mark `review`. |

Infrastructure and tooling mentions (Kubernetes, Airflow, Spark, Kafka, dbt, GCP, AWS, Terraform) are positive signals but never requirements.

---

## Explicit non-signals

Do **not** reject or downweight for:

- **Industry absence from the domain list.** Manufacturing, healthtech, logistics, media, gaming, retail, devtools, B2B SaaS — all in scope if the role shape fits.
- **Absence of specific domain keywords.** Judge the work described, not vocabulary overlap with the candidate context.
- **Title wording.** UK titles are inconsistent. Read responsibilities and the years-of-experience line.
- **Buzzword density.** A JD full of "AI-native" and "10x" is not stronger than a plain one.
- **Missing salary.** Many UK listings omit comp. Extract it when present; never filter on it.

---

## Judgment rules

- Weight the **responsibilities** section over the **requirements** section. Requirements lists are aspirational; responsibilities describe the actual job.
- If the JD is a generic template with no specifics, mark `review` and say so.
- Flag listings older than ~30 days.
- When two rules conflict, the hard-reject list wins.
- Never infer a disqualifier that isn't in the text. If the JD doesn't say "contract," don't assume it.

---

## Pipeline notes

Handled outside the LLM's judgment, but worth building in:

- **Dedupe against the tracker.** Check company name against the Companies tab; flag already-applied or previously-declined so reactivation is a deliberate decision.
- **Application channel.** The criteria can't see the network. If useful, add a separate `application_channel` field (`ats` | `referral_likely` | `unknown`) as a post-filter to act on, rather than something the LLM filters on.
