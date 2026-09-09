# ICE Dispatch Phase 1 production contract

## Mission

The ICE Dispatch tracks material, source-backed developments involving U.S. Immigration and Customs Enforcement activity in the United States and U.S. territories. Phase 1 is non-public: it establishes durable schemas, source policy, collection diagnostics, evidence preservation, dedupe semantics, collection-health reporting, and map-ready event serialization. It does not publish an ICE page, create a scheduler, generate audio, post to Bluesky, or modify existing dispatch behavior.

## Scope and geography

Covered geography: all 50 states, the District of Columbia, Puerto Rico, Guam, U.S. Virgin Islands, Northern Mariana Islands, and American Samoa.

Covered categories:

- enforcement operations, arrests, raids, workplace operations, courthouse activity, residential/community enforcement, targeted operations, and multi-agency operations;
- detention facilities, capacity changes, overcrowding, contracts, expansions, closures, transfers, relocations, conditions, and emergency events;
- removals/deportations, removal flights, repatriations, transfer chains, destination changes, and unusual removal practices;
- fatalities, injuries, hospitalization, death in custody, delayed-care allegations, suicide/self-harm, and medical emergencies;
- use of force, including shootings, firearm discharge, Taser deployment, physical force, pursuits, tactical deployments, and restraint incidents;
- legal, oversight, and accountability actions;
- DHS/ICE policy and operations changes;
- verified community impact only when directly evidenced and materially tied to ICE activity.

Speculative political reaction and generalized commentary are out of scope unless tied to a concrete operational or legal development.

## Architecture decisions

Reused patterns:

- per-dispatch data root under `data/dispatches/<dispatch>/`;
- YAML source registry with source state, tiering, collection mechanism, geography, cadence, and notes;
- source-first normalization into structured records;
- explicit run manifests and provider health reporting;
- event-ledger style event identity and source preservation;
- focused `tmp_path` tests that do not retain persistent generated output.

Rejected patterns for Phase 1:

- no Gaza-style full site build or nested Pages clone in tests;
- no Food/Care publication runner or approved-release scheduler;
- no audio/social wrappers;
- no forced daily edition semantics;
- no public archive/RSS integration;
- no broad historical backfill machinery.

Rationale: ICE needs a first-class contract and non-public pipeline foundation, but it should not inherit historical storage growth or publication complexity before real-world collection and manual review are proven.

## Event schema

Every normalized event carries:

- identity: `event_id`, `dispatch`, durable fingerprint, canonical event ID, related/supersession/correction/edition lineage;
- timing: `event_date`, optional `event_time`, `first_observed_at`, `last_updated_at`;
- location: country, state/territory, county, city, facility, latitude, longitude, precision, geography source, geography provenance;
- classification: primary category, secondary categories, event type, severity, status;
- people/impact: arrests, detained, removed, fatalities, injuries, hospitalized, children affected, and other quantitative impact;
- agencies: ICE, DHS, CBP, state/local agencies, other federal agencies, contractors, facility operators;
- evidence: source URL, canonical URL, publisher, source type, tier, publication date, retrieval timestamp, exact supporting passage, archive/reference metadata;
- editorial state: verification status, corroboration count, source quality, geographic confidence, event confidence, public eligibility, exclusion reason, curation notes.

Unknown counts stay `null`; they are never defaulted to zero.

## Geography and map readiness

Location precision values are:

- `exact_facility`
- `street_or_site`
- `city`
- `county`
- `state_or_territory`
- `multi_location`
- `unknown`

Coordinates may be stored only when provenance exists: explicit source, reliable geocoding step, established facility registry, or another auditable location source. The map-ready serialization includes event ID, date, category, severity, state/territory, facility, event type, coordinates, precision, geography source, and provenance. This supports future filtering by date range, category, severity, state/territory, facility, and event type.

## Severity

Severity is operational/human-impact based, not political judgment.

- `critical`: death in custody, fatal enforcement incident, mass casualty, major detention emergency, or equivalent immediate severe human impact.
- `high`: serious injury, hospitalization, firearm discharge, major detention capacity/transfer event, major multi-site operation, or material court order.
- `medium`: substantial enforcement/removal activity, facility contract/capacity change, legal/policy implementation, or verified community disruption.
- `low`: minor administrative/procedural update without immediate operational effect.

## Source policy

Tier 1: ICE, DHS, DOJ, federal courts/PACER-derived official records where available, inspectors general, Congress, state/local government, facility/operator official material, and medical examiner/coroner records.

Tier 2: AP, Reuters, major national/regional newspapers, public radio, established local investigative outlets, and credible nonprofit investigative organizations with primary documentation.

Tier 3: advocacy, legal, community, union, and local organizations. Tier 3 can surface important events but disputed claims generally need corroboration, especially casualty attribution, enforcement identity, force allegations, medical-care allegations, precise counts, misconduct allegations, and contested detention conditions.

Standalone social posts are insufficient unless they are official agency/public-official communications preserved under project source rules.

Prime rule: all public claims must be traceable.

## Publication eligibility and cadence

Cadence: event-driven / when feasible. A monitor may run regularly, but no daily edition is forced.

Event eligibility requires: in-scope ICE event, U.S./territory geography, new or meaningful update, traceable source, preserved factual support, no speculation, no duplicate-only coverage, and required corroboration.

Edition eligibility normally requires one of:

- at least one verified high/critical event;
- a coherent group of multiple verified medium events;
- one unusually consequential legal/policy development with clear operational impact.

A valid result is `NO_PUBLICATION_NEEDED`. It is separate from `COLLECTION_DEGRADED` and `COLLECTION_FAILED`; collection degradation must not masquerade as an editorial conclusion.

## Collection health

Provider health records include configured, attempted, successful, failed, accepted records, publisher count, tier diversity, geography coverage, category coverage, shared-service failures, HTTP status, retry attempts, timestamps, and safe response metadata.

Health states:

- `healthy`: attempted providers succeeded and produced usable records;
- `limited_source_update`: some providers failed or no records were accepted, but the collection path itself remains usable;
- `collection_degraded`: substantial provider/shared-service failure; do not publish on this basis;
- `collection_failed`: no provider path succeeded or no providers were attempted.

Operator output must surface degraded/failed states directly.

## Dedupe, updates, and corrections

Dedupe is event-centered. Fingerprints use event type, date, geography, facility, agencies, and material counts. Relationships are:

- `new_event`: no existing matching fingerprint;
- `update_to_existing_event`: same event with material status/date/count change;
- `duplicate_coverage`: same event and same source set without material change;
- `correction`: supersedes a previous fact;
- `follow_up_with_new_material_facts`: new source attaches to same event even if no public event is created.

Corrections preserve original evidence, correction evidence, date of correction, affected edition IDs, and lineage. Phase 1 does not add a public correction UI.

## Artifact retention and storage hygiene

Durable runtime evidence belongs under `data/dispatches/ice/runtime/` when an operator explicitly requests output. Tests use temporary directories and fixture-sized records; they must not create persistent `output/test-runs` or nested Pages clones. Phase 1 collection diagnostics are non-public and do not mutate Pages.

## Phase 1 publication-disabled state

ICE is not added to homepage navigation, public `/ice/`, public archive, public RSS, audio, Bluesky, site remodel, public map, or any scheduler in this phase.
