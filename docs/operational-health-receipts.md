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
- Emission point: each wrapper/runner after durable local receipt is written.
- Success/no-op: collection/queue success is `SUCCESS`; no approved release is `SAFE_NO_OP`; blocked upstream approval state is `UPSTREAM_BLOCKED`; guarded publication failure is `FAILED`.
- Dependencies: collection before queue; approved-release artifacts before publication.
- Likely grace window: 120 minutes.

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
- `ops/status/gaza/latest.json`
- `ops/status/system/latest.json`

`ops/status/` must remain excluded from Pages publication.

## Git contention strategy

Use one serialized exporter. Do not have every scheduled task independently commit and push. The exporter should use a dedicated operational-status clone/worktree or isolated checkout. Production runners should not need to push from dirty runtime worktrees.

## History retention

Recommended externally surfaced retention:

- always keep `latest.json`
- keep daily history for 30–90 days
- optionally keep monthly rollups

Local authoritative receipts may retain longer history according to dispatch-specific operational needs. No broad cleanup is implemented in Phase 1.
