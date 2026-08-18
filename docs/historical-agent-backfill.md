# Historical agent backfill

Historical scheduled-agent exports are preserved privately under `data/agent-history/`:

```text
food-line/{raw,normalized,reports}
care-line/{raw,normalized,reports}
gaza/{raw,normalized,reports}
ice/{raw,normalized,reports}
```

Raw records are content-addressed by SHA-256, retain the original text and original bytes (base64), and are written atomically. Reimporting identical bytes is an idempotent no-op. Historical data is never treated as current merely because it was imported today. Event dates or intervals, source publication dates, agent detection dates, `captured_at`, `original_run_at`, `imported_at`, and normalization-maintenance times remain separate.

## Commands

```powershell
python scripts/import_historical_agent_runs.py validate --domain food-line --input <export>
python scripts/import_historical_agent_runs.py dry-run --domain food-line --input <export>
python scripts/import_historical_agent_runs.py import --domain food-line --input <export>
python scripts/import_historical_agent_runs.py normalize --domain food-line --input <export>
python scripts/import_historical_agent_runs.py inventory
python scripts/import_historical_agent_runs.py report --domain food-line --input <export>
python scripts/import_historical_agent_runs.py batch-validate --domain care-line --input-dir data/agent-history-staging/care-line
python scripts/import_historical_agent_runs.py batch-dry-run --domain care-line --input-dir data/agent-history-staging/care-line
python scripts/import_historical_agent_runs.py batch-import --domain care-line --input-dir data/agent-history-staging/care-line
```

The importer accepts plain text, Markdown, JSON lists, and the Food Line agent envelope. A preserved text or Markdown alert containing exactly one labeled (`json`) or unlabeled fenced JSON object is normalized through that embedded envelope; the complete raw bytes and human-readable text remain private provenance. Multiple or malformed fences fail closed. Dry runs write nothing. No approval, publication, Pages, Bluesky, scheduler, or public output path is used.

Food Line reuses the existing AgentFinding and Food Line pressure adapter and marks findings `pending_review` and `historical_backfill: true`. Care Line preserves event/source identity and does not requeue published event IDs. Gaza records are matched by source text against existing source/edition artifacts without creating stories. ICE records remain private with pending verification and support event category, event date or interval, source publication date, detection date, location, agency/facility, injury/fatality, detention/removal/legal/policy, evidence, source, severity, and verification fields when present.

## Care Line controlled historical batch

Copy each first-batch export unchanged to `data/agent-history-staging/care-line/`.

Private Care Line match targets are:

- `data/universal_events/publication-state/care-line-signal-wire.json` — authoritative published event IDs and publication state.
- `data/universal_events/shadow/care-line/phase14f-signal-wire/phase14f-source-to-event-lineage-report.json` — reviewed event, source-item, producer-record, canonical URL, and review-decision lineage.
- `data/universal_events/shadow/care-line/phase14f-signal-wire/phase14f-proposed-universal-events.json` — private event identity and reviewed event fields.
- `data/dispatches/care-line/reviewed/` and `data/dispatches/care-line/evidence-reviews/` — reviewed records and evidence decisions.
- `data/dispatches/care-line/sources/` — private source snapshots and source-record identity.
- `data/universal_events/publication-state/care-line-reviewed-event-queue.json` — queue identity/state when present.
- `data/agent-history/care-line/normalized/` — prior private historical identities.

Care normalization emits `matched_published_event`, `matched_reviewed_event`, `matched_existing_source`, `duplicate_historical`, `new_historical_candidate`, `archived_invalid`, or `needs_manual_review`. Published matches create provenance links only; reviewed/source matches are not duplicated; unmatched valid findings remain pending private review; evidence-insufficient findings are archived invalid; and no outcome is publication-ready. Reported historical queue actions are limited to `none`, `provenance_only`, and `historical_review_candidate`; historical imports never enqueue or publish.

Evidence-insufficient Food Line findings are still preserved in the private historical archive as `archived_invalid` records with `review_status: excluded`, `candidate_created: false`, and `publication_eligible: false`. They never enter current intake, queues, editions, or approval state. A later approved evidence correction creates a separate normalized revision while retaining the original invalid record and raw bytes.

## Gaza controlled historical batch

Copy complete, unchanged Gaza exports to `data/agent-history-staging/gaza/`. Structured normalization sidecars, when required, belong under `data/agent-history-staging/gaza/corrections/`.

Private Gaza matching authority is:

- `data/records/editions.json` and `output/dispatches/gaza/editions/*/edition_manifest.json` for published edition identity and edition dates.
- `data/dispatches/gaza/sources/*/manual_sources.json`, `raw/*/raw_sources.json`, `normalized/*/normalized_sources.json`, `output/dispatches/gaza/editions/*/sources_manifest.json`, and Gaza rows in `data/records/sources.json` for source URLs, source/manual IDs, publishers, source dates, roles, edition dates, and story use.
- `data/records/story_memory.json`, private Gaza dedupe reports, and private curation manifests for exact story IDs, event-cluster IDs, topic fingerprints, normalized event keys, and source-to-cluster provenance.
- `data/dispatches/gaza/editions/*/run_manifest.json` for private publication-run metadata. No separate private Gaza audio index currently exists; rendered audio pages and feeds are protected outputs, not matching authority.
- `data/agent-history/gaza/normalized/` for prior historical identities.

Gaza matching prefers canonical source URL and explicit source/story/cluster identifiers. A title match is accepted only as an exact normalized title + date + publisher composite; headline similarity alone is never sufficient. Conflicting dates, URLs, or identifiers do not get reconciled by fuzzy title matching.

Gaza historical outcomes are `matched_published_edition`, `matched_existing_source`, `matched_existing_cluster`, `duplicate_historical`, `new_historical_candidate`, `archived_context`, `archived_invalid`, and `needs_manual_review`. Published, source, and cluster matches are provenance-only and create no story. Unmatched traceable findings remain private pending-review candidates. West Bank-only or explicit non-Gaza context is archived as context; findings without exact evidence are archived invalid. Every outcome has `publication_eligible: false` and `publication_approval: false`.

Read-only first-batch checks:

```powershell
python scripts/import_historical_agent_runs.py batch-validate --domain gaza --input-dir "data/agent-history-staging/gaza"
python scripts/import_historical_agent_runs.py batch-dry-run --domain gaza --input-dir "data/agent-history-staging/gaza"
```

Do not run `batch-import` until the operator supplies and reviews a real Gaza export. Historical Gaza import writes are restricted to the private raw archive, normalized record, report, and history index. They never rebuild an edition, update source counts, create a story, alter audio or podcast artifacts, change published timestamps, or write Pages.

## ICE controlled historical batch

Copy each complete, unchanged ICE export to `data/agent-history-staging/ice/`. Raw prose, Markdown, JSON envelopes, and structured findings are accepted. When a prose export needs structured normalization, put the reviewed sidecar under `data/agent-history-staging/ice/corrections/`. A sidecar authorizes historical normalization only: it must declare the raw SHA-256, the `ice` domain, stable finding identities, `approval_scope: historical_normalization_only`, and `publication_approval: false`. Hash, domain, identity, duplicate-finding, unsupported-fact, or publication-approval conflicts fail closed.

Private ICE matching authority is limited to traceable records under:

- `data/dispatches/ice/` for private incident, source, legal, facility, and contract identifiers when such records exist.
- `data/dispatches/cascadia/detention_watch/source_registry.json` and `data/dispatches/cascadia/detention_watch/baseline_2026-05-26.json` for the current private detention-facility monitoring source IDs, facility identity, locations, claim classes, source references, and historical dates. Their placeholder URLs are fixtures and are not canonical URL authority.
- `data/universal_events/` for non-seed private immigration-enforcement identities. Synthetic seed fixtures and placeholder URLs are not matching authority.
- `data/agent-history/ice/normalized/` for previously archived historical identities.

The ICE historical tree may contain privately imported findings and maintenance audits. `data/universal_events/seed/universal_events_seed.json` and the unreviewed immigration-related candidates under `data/dispatches/american-pressure/candidates/` are fixtures or intake material, not authoritative ICE incident matches. No public ICE dispatch pipeline is inferred from historical records.

Matching uses explicit incident, legal/docket, source, facility/contract, removal-flight, canonical-URL, or normalized historical identities. Event date, location, facility, agency, incident type, affected person or group, and removal destination are conflict checks or fingerprint components; a similar headline never establishes a match. Reports include the explicit match basis and any conflicting-field diagnostics.

The controlled primary and secondary event categories are:

- Enforcement and custody: `enforcement_operation`, `arrest_or_apprehension`, `detention_transfer`, `detention_capacity_change`, `detention_facility_opening`, `detention_facility_closure`, `detention_overcrowding`, `removal_or_deportation`, and `removal_flight`.
- Harm and force: `death_in_custody`, `serious_injury`, `hospitalization`, `medical_emergency`, `suicide_or_self_harm`, `shooting_or_firearm_discharge`, `taser_use`, `physical_force`, `pursuit`, `tactical_deployment`, and `delayed_or_denied_care`.
- Legal, policy, and investigation: `legal_ruling`, `lawsuit_or_settlement`, `civil_rights_investigation`, `misconduct_investigation`, `policy_change`, `287g_action`, and `sanctuary_or_local_response`.
- Community and context: `demonstration_or_community_disruption`, `workforce_or_business_disruption`, `school_or_agricultural_disruption`, `humanitarian_response`, and `archived_context`.

Severity is private review metadata based only on documented facts. `critical` covers a fatality or death in custody, a documented officer-involved shooting or firearm injury, mass casualty, serious force with serious injury, or an explicit urgent system-wide detention or humanitarian crisis. `high` may cover documented serious injury, hospitalization, self-harm, major overcrowding, large enforcement or removal activity, a broad court injunction, or verified widespread community disruption. `medium`, `low`, and `context` retain findings that do not meet those thresholds. Unsupported elevated severity fails closed instead of being inferred from rhetoric.

ICE outcomes are `matched_existing_incident`, `matched_existing_source`, `matched_existing_legal_record`, `duplicate_historical`, `new_historical_candidate`, `archived_context`, `archived_invalid`, and `needs_manual_review`. Every result remains private, has `publication_eligible: false` and `publication_approval: false`, and performs no live queue action. New candidates remain `pending_review`; invalid findings are `excluded`; context-only records use `historical_context`. Human review is required before any later workflow may use a historical finding.

Per-finding reports retain raw hash, event date or interval, source publication date, agent detection date, archive capture time, import time, category/subtype, severity, location/facility, agency, casualty counts, activity flags, evidence level, match result, candidate state, review state, exclusion reason, and publication ineligibility. `last_normalized_at` appears only after an explicit maintenance operation. Batch reports aggregate raw runs, normalized findings, critical/high findings, fatalities, deaths in custody, serious injuries, hospitalizations, force incidents, enforcement operations, detention changes, removals, legal and policy actions, community disruptions, duplicates, invalid findings, and pending review. Publication-ready count is always zero.

ICE date meanings are intentionally non-interchangeable:

- `event_date` or `event_period` describes when the underlying event occurred.
- `source_published_at` describes when the source was published.
- `detection_date` describes when the agent explicitly detected the finding. A date-only value is stored as `YYYY-MM-DD`; an explicit source timestamp may retain ISO 8601 time precision.
- `captured_at` describes when the raw export entered archive processing.
- `imported_at` describes when the immutable raw archive was first written.
- `last_normalized_at` describes an explicitly authorized normalization-maintenance operation.

Detection dates are optional. Missing values remain JSON null. They are parsed only from a structured `detection_date` value or an explicit raw-alert `Detection Date` field. They are never inferred from filenames, directories, source publication dates, event dates, file timestamps, run IDs, capture times, or import times. Impossible dates and raw/sidecar conflicts fail closed.

An already archived ICE record is not silently rewritten by `import` or `batch-import`. After a reviewed sidecar explicitly authorizes a newly supported field, use the private maintenance command:

```powershell
python scripts/import_historical_agent_runs.py renormalize --domain ice --input "data/agent-history-staging/ice/<alert>"
```

`renormalize` verifies the immutable raw bytes, raw SHA-256, normalized-record identity, sidecar identity, historical-only approval scope, and raw evidence. It changes only an absent approved `detection_date`, preserves the original per-record import report and historical outcome, creates no finding or candidate, and writes a private audit under `data/agent-history/ice/reports/maintenance/`. The audit records old/new values and normalized digests, source evidence, sidecar digest, reviewer, scope, maintenance time, and `publication_approval: false`. Repeating the command returns `idempotent_noop`.

An operator may accept an independent substantive review for an existing ICE, Care Line, or Gaza historical candidate with the status-only private review command:

```powershell
python scripts/import_historical_agent_runs.py review --domain ice --raw-sha <raw-sha256> --decision substantively-valid --review-artifact "data/agent-history/ice/reviews/<review>.json" --review-artifact-sha256 <review-sha256>
python scripts/import_historical_agent_runs.py review --domain care-line --raw-sha <raw-sha256> --decision substantively-valid --review-artifact "data/agent-history/care-line/reviews/<review>.json" --review-artifact-sha256 <review-sha256>
python scripts/import_historical_agent_runs.py review --domain gaza --raw-sha <raw-sha256> --decision substantively-valid --review-artifact "data/agent-history/gaza/reviews/<review>.json" --review-artifact-sha256 <review-sha256>
python scripts/import_historical_agent_runs.py review --domain food-line --raw-sha <raw-sha256> --decision substantively-valid --review-artifact "data/agent-history/food-line/reviews/<review>.json" --review-artifact-sha256 <review-sha256>
```

The command supports only the explicit fail-closed `ice:substantively-valid`, `care-line:substantively-valid`, `gaza:substantively-valid`, and `food-line:substantively-valid` decisions. All paths verify the immutable raw bytes, raw and finding identities, exact review-artifact digest, `substantively_valid_historical_candidate` recommendation, authorization flags, and private queue/publication state. ICE additionally preserves high severity. Care Line additionally verifies its domain-specific review schema, materiality, taxonomy, effective date, scheduled status, editorial restrictions, historical-only queue action, and absence of a new live event, queue, or source match. Gaza additionally verifies its domain-specific review schema, July 25 event date, July 30 source date, qualified attribution, humanitarian-worker taxonomy, unknown operational impact, `operating_impact_unclear` materiality, editorial restrictions, and absence of a new authoritative edition, source, cluster, or prior historical match. Food Line additionally verifies its domain-specific review schema, agent run and normalized finding identities, pressure signal and type, location, food-access materiality, editorial restrictions, publication isolation, and the absence of an exact edition, intake, inbox, source-ledger, or prior historical match. Legacy Food Line records may omit explicit false publication fields and store the candidate outcome as `deduplication_outcome`; the review path accepts only those absent-or-false guards and the exact `new_historical_candidate` outcome. A successful transition changes only `review_status` from `pending_review` to `substantively_reviewed`, updates that domain's private review counters, and writes one deterministic audit under the domain's `reviews/decisions/` directory. It does not create or authorize an intake record, edition, map item, source record, story cluster, audio item, public artifact, or publication. Repeating the identical command returns `idempotent_noop` without rewriting the decision timestamp, normalized record, inventory, or audit.

Read-only first-batch checks:

```powershell
python scripts/import_historical_agent_runs.py batch-validate --domain ice --input-dir "data/agent-history-staging/ice"
python scripts/import_historical_agent_runs.py batch-dry-run --domain ice --input-dir "data/agent-history-staging/ice"
```

After explicit operator review, the private import command is:

```powershell
python scripts/import_historical_agent_runs.py batch-import --domain ice --input-dir "data/agent-history-staging/ice"
```

Do not run it until the operator supplies and reviews a real ICE export. ICE imports may write only `data/agent-history/ice/{raw,normalized,reports}` and private batch/history-index records. They must never write a public dispatch, `output/site`, Pages, another domain, Bluesky state, schedules, or a public event or publication queue.

## Operator export procedure

1. Open the historical alert or task result.
2. Copy/export the complete result without editing.
3. Save it in a private temporary staging directory.
4. Run `validate`.
5. Run `dry-run`.
6. Review matches, duplicates, invalid records, and manual-review outcomes.
7. Run `import`.
8. Preserve the raw archive permanently.

Do not assume repository access to ChatGPT task history, and do not scan arbitrary Downloads or install a watcher. No real historical data is imported by this change.

## Controlled batch workflow

1. Copy each complete, unchanged alert directly into the domain staging folder.
2. Put approved normalization sidecars under the staging folder's `corrections/` directory when needed.
3. Run `batch-validate`.
4. Run `batch-dry-run`.
5. Review the aggregate counts, deterministic file order, sidecar matches, and every per-file outcome.
6. Run `batch-import` explicitly.
7. Rerun the identical `batch-import` command and confirm `idempotent_noop` for every previously imported file.

Batch discovery accepts `.txt`, `.md`, and `.json` files directly inside the supplied directory. Hidden, temporary, unsupported, correction, and archive-output files are ignored. Add `--recursive` only when nested alert directories are intentional.

The batch ID is a deterministic hash of the domain and ordered raw-file hashes. File order uses normalized relative paths. `batch-validate` and `batch-dry-run` write nothing. `batch-import` writes a private report to `data/agent-history/<domain>/reports/batches/<batch-id>.json`.

By default, one validation or sidecar failure blocks every import in the batch. `--allow-partial-import` explicitly permits valid files to import while invalid files remain unchanged and reported. Sidecars are matched by raw SHA-256 and checked against declared raw path, domain, and run or finding identity when present. Hash, path, domain, identity, and approval conflicts fail closed; filenames alone never establish a match, and multiple matches fail closed.

Batch imports use the existing single-file domain protections. They do not enqueue Care Line events, insert Food Line current intake, create Gaza stories, expose ICE records, grant publication approval, generate editions, or write Pages, Bluesky, or scheduler state.
