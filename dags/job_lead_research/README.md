# Job Board Triage

A pipeline to surface best-fit listings from source-of-truth company job boards.

## Motivation

Job aggregation sites (e.g. LinkedIn) are noisy and stale. Individual company boards are reliable but time consuming to manually monitor. Candidates spend significant time wading through irrelevant listings rather than writing applications or preparing for interviews. The unstructured nature of the job search makes it difficult to continue feeding the funnel of applications.

## Goals

- Focus human effort on tailored applications and edge cases.
- Quickly respond to fresh, strong-fit listings on first-party boards.
- Maintain consistent cadence of submissions.

## Design

An automated pipeline that monitors company job boards and uses LLMs to surface high-relevance roles. Uses deterministic Airflow DAGs to ensure reliable data flow and AI decision making to allow for nuance and parse inconsistently formatted listings. Retains human prose and decision making for final application writing and submission.

### Structure

#### Job Board Monitors

A manually curated registry of companies is pulled and matched with ATS provider-specific job board scrapers. Each board is fetched and roles are deduplicated against previously processed listings. New roles are converted into a common interface and added to a master list.

At this point, the listings are unique but low signal. Each company structures their filters and details differently (e.g. "London" vs "London, UK" vs "Greater London"), so applying hard-coded filters at the ingestion stage would be ineffective. The goal of this stage is to extract details as they are provided into a least common denominator format.

#### Low-Cost Relevance Filter

A low-cost LLM is used to apply a broad relevancy filter. Each role is fed to the model with a simple prompt to determine whether it fits simple criteria. For example, whether the role is for some sort of software engineering role in the greater London area. Decisions are recorded for later stages.

This stage exists to avoid spending expensive inference compute on obviously irrelevant roles. Since false positives will be dropped later by a more nuanced filter, false negatives are the failure mode here. Cheap LLMs are capable of extracting clear but inconsistent details, meaning roles can be categorized without relying on fragile and time consuming deterministic criteria for each company.

#### Nuanced Fit Filter

A higher-capability model is then used to apply a tailored fit determination. The candidate's resume and preferences are sent to the LLM along with the listing and instructions to label the role a strong fit, worth review, or poor fit. Decisions are recorded.

This stage contains the core decision making. A more intelligent model is able to make more subtle determinations about company culture, daily responsibilities, and candidate preferences in order to surface the most applicable roles. This stage also errs on the side of false positives, as the repercussions of an irrelevant role getting surfaced are wasting 30s of time reading the listing, while an incorrectly dropped role means a missed opportunity for a stong-fit application. Including the option for the model to mark a role as worth reviewing means edge cases can be escalated to the human for final decision.

#### Application

This pipeline intentionally does not include any form of automated submission. It is the candidate's responsibility to make the final decision on each role and write the application. AI is used in the background to handle tedious busy work, allowing the candidate to focus on differentiating themselves through their own taste and written voice.

### Cadence

#### Daily Digest

Ongoing monitoring is accomplished at a daily cadence by running the pipeline for all tracked companies and emailing a collection of the best-fit roles to the candidate for review. The candidate has a steady stream of fresh, relevant roles to apply to.

#### Company Onboarding

When a company is first registered in the system, a bulk sweep of its job board is run and a separate digest is sent with all review-worthy roles. This is done separately from the daily run to avoid the case where a large company with many applicable roles overwhelms the daily digest for days after initial registration. It also avoids gradually accumulating too many open applications at a single company. Seeing the full set at once lets the candidaste decide which 2-3 listings to apply to, keeping their overall application load focused.

## Future Work

#### Company Discovery

This pipeline does not currently have a method for identifying new companies to monitor. Using aggregators like LinkedIn or VC boards could provide inspiration for new firms to onboard while still using the first-party boards to ensure freshness.

#### Quieter Monitors

Some ATS providers are less welcoming to scrapers than others. Ensuring that data ingestion does not cause undue load on the providers makes data access more sustainable.
