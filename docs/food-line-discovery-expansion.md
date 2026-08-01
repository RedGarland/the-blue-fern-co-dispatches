# Food Line Discovery Expansion

Production execution is bounded, partitioned, checkpointed, and resumable.
Use the `daily-current` profile described in
`docs/food-line-bounded-source-watch.md`. Plain CLI execution also defaults
to that profile; `--legacy-unbounded` is compatibility-only and must not be
used for routine collection.

This layer broadens Food Line source discovery without changing publication rules.

## Purpose

- Find more candidate sources before qualification.
- Keep blocked, partial, and manual-review records instead of dropping them.
- Separate discovery breadth from publication strictness.
- Produce a daily audit that explains what was searched and what survived discovery.

## Discovery Versus Qualification

Discovery is intentionally broad.

- Discovery can keep Google News leads, blocked pages, duplicates, and manual fallback records.
- Qualification still belongs to the Food Line dispatch pipeline and remains strict.
- A candidate being discovered does not make it publishable.

## Query Families

The expansion layer uses these families:

- `core_hunger`
- `pressure`
- `policy_program`
- `cost_pressure`
- `state_territory`
- `metro`

The first four families search broadly for food-pressure context. The state and metro families add geographic coverage for all states, the District of Columbia, and U.S. territories, plus a configurable metro list.

## State, Territory, And Metro Coverage

State and territory queries are generated from a fixed coverage list.

Included territories:

- Puerto Rico
- Guam
- U.S. Virgin Islands
- American Samoa
- Northern Mariana Islands

Metro coverage is data-driven in `data/dispatches/food-line/discovery_expansion_config.json` so new metros can be added without changing code.

## Candidate Schema

Discovery candidates are written to:

- `data/dispatches/food-line/discovery/YYYY-MM-DD/discovery_candidates.json`

Key fields include:

- `candidate_id`
- `discovery_date`
- `query_family`
- `query_text`
- `geographic_scope`
- `state_or_territory`
- `metro`
- `discovery_channel`
- `discovered_title`
- `discovered_publisher`
- `discovered_url`
- `canonical_url`
- `google_news_url`
- `publication_date`
- `fetch_status`
- `fetch_error`
- `final_trace_url`
- `duplicate_of`
- `review_status`
- `classification_status`
- `exclusion_reason`
- `pressure_terms_detected`
- `location_terms_detected`
- `manual_review_required`

## Google News Handling

Google News is discovery metadata only.

- The Google News URL is preserved in `google_news_url`.
- The publisher URL or canonical URL is preserved in `final_trace_url`.
- Google News is not treated as source evidence.
- Publication checks should rely on the publisher URL when available.

## Blocked Fetches

Blocked or failed fetches stay in the candidate file and the audit.

Examples:

- `403`
- `401`
- `404`
- timeout
- paywall
- script-blocked
- parse failure

The retained candidate records the failure in `fetch_status` and `fetch_error`, and `manual_review_required` stays `true` unless the record came from the explicit manual fallback path.

## Manual Fallback Records

Manual fallback records are for valid sources that could not be fully fetched by automation.

Required fields:

- `publisher`
- `canonical_url`
- `headline`
- `date`
- `location`
- `manually_reviewed_summary`
- `pressure_evidence_summary`
- `affected_groups`
- `limitations`
- `extraction_quality`
- `reviewer_or_source_note`
- `final_trace_url`

Manual fallback records are validated before they are written into the discovery candidate file.

## Daily Audit

Daily audit files are written to:

- `output/review/food-line/YYYY-MM-DD/discovery_audit.json`
- `output/review/food-line/YYYY-MM-DD/discovery_audit.md`

The audit reports:

- total queries run
- total candidates discovered
- candidates by query family
- candidates by state or territory
- candidates by metro
- duplicate count
- fetchable count
- blocked or failed fetch count
- manually reviewable count
- qualified pressure signals
- context-only records
- manual fallback records
- discovery confidence
- discovery confidence reason

## Discovery Confidence

Discovery confidence is a summary of retention quality, not a publication green light.

- `high` means the day retained strong fetchable pressure signals with little loss.
- `moderate` means pressure signals were retained, but some review or fetch issues remained.
- `limited` means candidates were found, but blocked fetches or context-only results limited confidence.
- `low` means the day retained too little to support a stronger claim.

When `edition_mode` is `no_current_update`, this confidence is especially important because it distinguishes:

- no candidates found
- candidates found but none qualified
- candidates found but fetch blocked
- candidates found but review incomplete
- continuing pressure only

## Axios Charlotte Example

An Axios Charlotte story that returned `403` to automated fetches would still be retained as a discovery candidate.

It would show:

- the Google News URL in discovery metadata
- the Axios publisher URL in `final_trace_url`
- `fetch_status` recorded as blocked
- `manual_review_required` set to `true`
- no silent disappearance from the audit

That preserves traceability without weakening Food Line publication standards.

For the intake bridge that feeds the daily dispatch path, see `docs/food-line-discovery-intake.md`.
