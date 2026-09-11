# Blue Fern operational health receipts

Phase 1 defines a shared local receipt contract for Blue Fern scheduled operations. It is an observability contract, not scheduler configuration, publication authorization, or editorial state.

## Common receipt schema

Schema version: `bluefern_operational_health_receipt_v1`.

Required common fields:

- `schema_version`
- `dispatch`
- `task_key`
- `task_name`
- `scheduled_for`
- `started_at`
- `completed_at`
- `observed_at`
- `receipt_created_at`
- `exit_code`
- `status`
- `classification`
- `run_id`
- `failure_stage`
- `runner_id`
- `runner_path`
- `branch`
- `source_head`
- `next_expected_run`
- `public_side_effects`
- `details`

Optional shared fields supported in the same envelope:

- `collection_health`
- `upstream_dependency_status`
- `publication_attempted`
- `publication_status`
- `artifact_refs`
- `operator_attention_ref`

The receipt must not include credentials, tokens, full environment dumps, raw source text, private editorial notes, or full logs. Dispatch-specific extensions live only under `details` unless promoted deliberately into a shared field.

## Two operational health dimensions

Operational status exposes two distinct dimensions:

1. **Scheduled operational health** is the authoritative execution health for
   scheduled pipelines. Food Line daily completeness continues to use only
   `food_line_source_watch`, `food_line_source_watch_resume`,
   `food_line_current_intake`, and `food_line_daily_publish`.
2. **External agent handoff health** is an optional, event-driven status
   dimension derived from sanitized metadata in
   `data/private-agent-handoff/receipts/<dispatch>/` and active inbox metadata.

The handoff dimension uses these states:

- `NO_EXTERNAL_HANDOFF_EXPECTED`: no external delivery is known for the
  evaluation window. This is informational and does not count as a missing
  scheduled receipt or degrade dispatch health.
- `HANDOFF_RECEIVED_SUCCESS`: the latest accepted or safely idempotent attempt
  is terminal and all findings are accounted for.
- `HANDOFF_FAILED`: the latest terminal attempt failed, including malformed
  input, idempotency conflict, archive/write failure, downstream import
  failure, or an accepted receipt with unaccounted findings.
- `HANDOFF_STALE_UNPROCESSED`: delivery evidence exists without a terminal
  handoff receipt, or a nonterminal attempt remains unresolved. Absence of
  external traffic never implies this state.

The exported section is named `agent_handoff` and contains only symbolic run
identifiers, timestamps, status/classification, an unaccounted count, and a
stale flag. Raw envelopes, supporting passages, absolute paths, credentials,
tokens, and retired/quarantined payloads are excluded. A handoff failure may be
alertable at system level, but it does not rewrite scheduled task receipts;
`SAFE_NO_OP` does not mask a scheduled failure.

Care Line remains `NOT_MIGRATED` for scheduled operational-health aggregation.
Its system status may expose the supplementary `agent_handoff` section, but
handoff receipts do not constitute a Care daily aggregate or full migration.

## Common status model

Common statuses:

- `SUCCESS`
- `SAFE_NO_OP`
- `UPSTREAM_BLOCKED`
- `DEGRADED`
- `FAILED`
- `MISSED`
- `UNKNOWN`
- `STALE_OBSERVABILITY`

Task execution health is separate from editorial outcome. A task can complete successfully while producing no public edition. Public edition existence is not authoritative operational-health evidence.

## Recovery lifecycle

Recovery states:

- `HEALTHY`
- `INCIDENT_OPEN`
- `RECOVERY_PENDING_RUNTIME_PROOF`
- `RECOVERED`

A code fix, PR merge, or runner deployment does not close an incident. After deployment the incident is `RECOVERY_PENDING_RUNTIME_PROOF`. Only a subsequent successful real scheduled execution of the affected task or task chain may transition the incident to `RECOVERED`.

## Task expectation model

Each expected task has:

- `dispatch`
- `task_key`
- `task_name`
- `timezone`
- `cadence`
- `expected_time`
- `grace_minutes`
- `required`
- `success_statuses`
- `failure_statuses`
- `upstream_dependencies`

This model intentionally does not duplicate Windows Task Scheduler XML. It describes observability expectations and dependency interpretation.

## Local receipt storage

Authoritative local receipts are written atomically under:

`status/operational-health/<dispatch>/YYYY-MM-DD/`

Current Phase 1 files:

- `latest.json`
- `runs/<task_key>-<run_id>.json`

This path is source-runner local state and is not Pages output.

## Food Line Phase 1 migration

Migrated tasks:

- `food_line_source_watch` — `Blue Fern Food Line Daily Source Watch`
- `food_line_source_watch_resume` — `Blue Fern Food Line Source Watch Resume`
- `food_line_current_intake` — `Blue Fern Food Line Current Intake`
- `food_line_daily_publish` — `Blue Fern Food Line Daily Publish`

Existing Food Line task-specific receipts remain in place for compatibility. The shared operational receipt references the legacy/task receipt through `artifact_refs.task_receipt`.

Food Line mapping:

- Source Watch `completed` => `SUCCESS`
- Source Watch `completed_with_exclusions` => `DEGRADED`
- Source Watch `blocked_overlapping_run`, `stale_lock_ambiguous`, or `failed` => `FAILED`
- Resume `resume_not_required` => `SAFE_NO_OP`
- Resume `resume_qualified` => `SUCCESS`
- Resume `source_watch_not_initialized`, `source_watch_in_progress`, or `upstream_blocked` => `UPSTREAM_BLOCKED`
- Resume `resume_nonqualifying`, `status_resume_failed`, or `failed` => `FAILED`
- Current Intake `success` or `success_with_exclusions` => `SUCCESS`
- Current Intake upstream not-initialized/in-progress/blocked => `UPSTREAM_BLOCKED`
- Current Intake no qualifying release => `SAFE_NO_OP`
- Current Intake failure/corrupt state => `FAILED`
- Daily Publish `published` => `SUCCESS`
- Daily Publish `skipped_not_release_ready` or no qualifying edition => `SAFE_NO_OP`
- Daily Publish `failure` => `FAILED`

September 9 regression contract: Daily Publish `SAFE_NO_OP` must not mask upstream Source Watch, Resume, or Current Intake failures. No public edition must not count as proof of health.

## Dispatch-level aggregation

Dispatch aggregation evaluates expected task receipts and reports:

- `dispatch`
- `evaluated_at`
- `overall_health`
- `recovery_state`
- `expected_tasks`
- `completed_tasks`
- `missed_tasks`
- `failed_tasks`
- `degraded_tasks`
- `upstream_blocked_tasks`
- `stale_observability`
- `latest_success_at`

Rules:

- `HEALTHY`/`SUCCESS`: all required task receipts are within expected/grace windows and acceptable.
- `DEGRADED`: required tasks ran but one or more reported degraded or upstream-limited state.
- `FAILED`: one or more required tasks reported genuine failure.
- `MISSED`: expected grace expired and no receipt exists.
- `STALE_OBSERVABILITY`: receipt/status surface is stale and local outcome cannot be determined.

## System aggregation

System aggregation combines expected dispatches:

- `gaza`
- `food-line`
- `care-line`
- `ice`
- `cascadia`
- `american-pressure`

It reports system health, dispatch states, open incidents, recovery-pending dispatches, and stale observability.

## Future migration contracts

### Gaza

- Task key: `gaza_daily_dispatch`
- Emission point: daily scheduler wrapper/operator result after publication decision is known.
- Success/no-op: successful public dispatch publication is `SUCCESS`; no publication needed is `SAFE_NO_OP`; source/deployment failures are `FAILED`; partial source limitations are `DEGRADED`.
- Dependencies: source collection, site generation, optional audio/social status.
- Likely grace window: 120 minutes.

### Care Line

- Task keys: `care_line_collection`, `care_line_reviewed_event_queue`, `care_line_approved_release_publication`.
- Source execution points are the guarded national collection wrapper
  (`scripts/windows/run_care_line_national_collection.ps1` and
  `scripts/care_line_collection_scheduler.py`), the reviewed-event queue
  runner (`scripts/run_care_line_reviewed_event_queue.py` and
  `src/bluefern_dispatches/care_line_queue_runner.py`), and the approved-release
  publication wrapper (`scripts/windows/run_care_line_approved_release_publication.ps1`
  and `scripts/care_line_publication_scheduler.py`). Each writes the shared
  receipt only after its existing Care-specific receipt is durable.
- The collection task uses a stable task key for every scheduled instance;
  distinct `scheduled_for` and `run_id` values identify repeated runs. Queue
  and publication receipts use their stable task keys as well.
- Collection and publication cadence is intentionally reported as
  `configured scheduler time` until the installed Task Scheduler definitions
  are separately audited. The source wrappers do not register or modify tasks.
- Success/no-op: collection/queue success is `SUCCESS`; no approved release is `SAFE_NO_OP`; blocked upstream approval state is `UPSTREAM_BLOCKED`; guarded publication failure is `FAILED`.
- Dependencies: collection before queue; approved-release artifacts before publication.
- Likely grace window: 120 minutes.

The Care exporter accepts explicit local Care receipts and writes
`ops/status/care-line/latest.json` plus date history. It permits repeated
collection instances and ignores future expected instances until their
scheduled time plus grace has elapsed. The system export still reports Care as
`NOT_MIGRATED`; a Care artifact or external handoff receipt is not proof of a
production migration or a public edition.

### ICE

- Task key: `ice_monitor`.
- Emission point: monitor wrapper after non-public collection/review queue result.
- Success/no-op: healthy monitor with new reviewable events is `SUCCESS`; healthy monitor with no new reviewable events may be `SAFE_NO_OP`; provider degradation is `DEGRADED`; monitor failure is `FAILED`.
- Dependencies: configured providers and local monitor state.
- Likely grace window: 180 minutes.

### Cascadia

- Task key: `cascadia_weekly_dispatch`.
- Emission point: weekly dispatch task wrapper after generation/publication decision.
- Success/no-op: completed scheduled edition is `SUCCESS`; explicitly no qualifying edition is `SAFE_NO_OP`; failures are `FAILED`.
- Dependencies: configured source and publication surfaces.
- Likely grace window: 24 hours.

### American Pressure

- Task keys: `american_pressure_candidate_intake`, `american_pressure_weekly_publish`.
- Emission point: scheduled intake/generation and weekly publication wrappers.
- Success/no-op: successful data/map generation is `SUCCESS`; no publishable state is `SAFE_NO_OP`; partial data is `DEGRADED`; failures are `FAILED`.
- Dependencies: data generation before publication.
- Likely grace window: 180 minutes for daily intake and 24 hours for weekly publication.

## External status export design

Do not push external status from production tasks in Phase 1.

Preferred future architecture:

local task receipt -> local dispatch aggregate -> dedicated serialized status exporter -> non-public `ops/status/` repository path -> watchdog

External sync failure must not change the local task result. Local receipts remain authoritative. If export fails, observability is stale; the task itself is not incorrectly marked failed.

Proposed external paths:

- `ops/status/food-line/latest.json`
- `ops/status/food-line/history/2026-09-10.json`
- `ops/status/care-line/latest.json`
- `ops/status/care-line/history/2026-09-10.json`
- `ops/status/gaza/latest.json`
- `ops/status/system/latest.json`

`ops/status/` must remain excluded from Pages publication.

### Phase 1 serialized exporter

The Phase 1 exporter is `scripts/export_operational_status.py`. It reads the
authoritative local receipt directory, evaluates the whole Food Line task chain,
sanitizes operational fields, and atomically writes the following artifacts to
a dedicated operational-status checkout:

- `ops/status/food-line/latest.json`
- `ops/status/food-line/history/YYYY-MM-DD.json`
- `ops/status/system/latest.json`

The exporter must receive explicit `--source-root` and `--status-checkout`
paths. The status checkout must be separate from every production runner and
from the Pages checkout. One process-wide file lock serializes exporters; a
contending process exits nonzero. Repeated exports reuse the previous export
timestamp when the sanitized payload is byte-equivalent, and atomic replacement
prevents partial JSON artifacts.

The Food Line status payload reports aggregate dispatch health, receipt
completeness, task summaries, recovery lifecycle, publication state, and bounded
staleness fields. It never exports raw source content, editorial notes, private
queue data, credentials, environment variables, or local filesystem paths.

The Scheduled Dispatch Watch should consume `food-line/latest.json` as follows:

1. Read `aggregate_status` for dispatch health. `FAILED` is a real task failure;
   `STALE_OBSERVABILITY` means the status surface cannot establish a current
   result and must not be treated as yesterday's health.
2. Read `task_summaries` to identify failed, upstream-blocked, degraded, or
   safe-no-op tasks. `SAFE_NO_OP` from Daily Publish never masks an upstream
   failure.
3. Require `receipt_completeness == COMPLETE` before treating a day as fully
   observed. `PARTIAL`, `MISSING`, and `INCONSISTENT` are observable data-quality
   conditions, not publication results.
4. Read `recovery_lifecycle` separately. `RECOVERY_PENDING_RUNTIME_PROOF`
   remains pending after code merge, runner rollout, preflight, or export;
   only a later successful runtime receipt can establish `RECOVERED`.

The Watch should alert on `HANDOFF_FAILED` and `HANDOFF_STALE_UNPROCESSED`.
It should not alert on `NO_EXTERNAL_HANDOFF_EXPECTED` or
`HANDOFF_RECEIVED_SUCCESS`. Scheduled Food aggregate status remains the
authoritative pipeline execution signal; Care handoff status remains
supplementary until scheduled Care receipts are migrated.
5. Read `publication_attempted`, `publication_status`, and `public_side_effects`
   independently. No public edition is not itself a task failure.

The system artifact marks Food Line `MIGRATED` and all other dispatches
`NOT_MIGRATED`; those entries are not synthesized failures. Migration order is:

1. Food Line
2. Care Line
3. Gaza
4. ICE
5. Cascadia
6. American Pressure

## Git contention strategy

Use one serialized exporter. Do not have every scheduled task independently commit and push. The exporter should use a dedicated operational-status clone/worktree or isolated checkout. Production runners should not need to push from dirty runtime worktrees.

## History retention

Recommended externally surfaced retention:

- always keep `latest.json`
- keep daily history for 30–90 days
- optionally keep monthly rollups

Local authoritative receipts may retain longer history according to dispatch-specific operational needs. No broad cleanup is implemented in Phase 1.
