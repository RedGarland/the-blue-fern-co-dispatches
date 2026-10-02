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

External handoff receipts do not constitute a scheduled daily aggregate on
their own. A dispatch is `MIGRATED` only when its scheduled operational-health
receipts are exported from the relevant production runner.

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
- `INTENTIONALLY_INACTIVE`

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

System aggregation combines active expected dispatches and explicitly marks intentionally inactive dispatches:

- `gaza`
- `food-line`
- `care-line`
- `ice`
- `american-pressure`

Cascadia is intentionally inactive. Its absence from scheduled receipts must not produce `MISSED`, `FAILED`, `NOT_MIGRATED`, or `UNKNOWN`; status surfaces should report `INTENTIONALLY_INACTIVE` until a separate operator authorization reactivates it.

It reports system health, dispatch states, open incidents, recovery-pending dispatches, and stale observability.

## Migrated dispatch contracts

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
scheduled time plus grace has elapsed. A Care artifact or external handoff
receipt alone is not proof of scheduled production migration or a public
edition.

### ICE

- Task key: `ice_monitor`.
- Emission point: monitor wrapper after non-public collection/review queue result.
- Success/no-op: healthy monitor with new reviewable events is `SUCCESS`; healthy monitor with no new reviewable events may be `SAFE_NO_OP`; provider degradation is `DEGRADED`; monitor failure is `FAILED`.
- Dependencies: configured providers and local monitor state.
- Likely grace window: 180 minutes.

### Cascadia

Cascadia is intentionally inactive. There is no active expected scheduled task, and missing weekly receipts are not missed-run evidence. Historical task key `cascadia_weekly_dispatch` and archive/public content may remain for reference, but no runner should be registered, enabled, or executed without explicit operator authorization.

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

Migrated dispatch status payloads report aggregate dispatch health, receipt
completeness, task summaries, recovery lifecycle, publication state, bounded
staleness fields, and a shared `debug_summary`. They never export raw source
content, editorial notes, private queue data, credentials, environment
variables, or local filesystem paths.

Food Line status also exports a sanitized `private_review_backlog` summary from
`data/dispatches/food-line/review/proposed-editions/*.json`. It reports
pending date counts, alertable unresolved item counts, dispositioned item
counts, count-only gaps, oldest pending date, max age in hours, and proposal
artifact paths. A proposal date is actionable only when it has pending private
items, lacks a matching release-readiness approval, and is not already published
or dispositioned.

Food Line may also carry additive, non-destructive item-level disposition
sidecars under:

`data/dispatches/food-line/review/private-review-dispositions/*.json`

These sidecars do not rewrite proposed-edition evidence or historical receipts.
They are consumed only to subtract specifically identified private-review items
from the actionable backlog after an editorial/publication disposition has been
recorded. Entries are matched by `item_id` first and `source_url` second, scoped
to the proposal `date`. Missing/count-only proposal gaps are never cleared by a
sidecar unless a future source change provides item evidence for them.

Recommended sidecar schema:

```json
{
  "schema_version": "food_line_private_review_dispositions_v1",
  "disposition_id": "food-line-recovery-brief-sept14-28-2026",
  "created_at": "2026-09-29T00:00:00Z",
  "created_by": "operator-or-editor-id",
  "recovery_manifest_path": "output/site/food-line/recovery/.../publication_manifest.json",
  "triage_packet_path": "recovery-review-packets/...json",
  "items": [
    {
      "date": "2026-09-28",
      "item_id": "optional-stable-item-id",
      "source_url": "https://publisher.example/story",
      "disposition": "published_in_recovery_brief",
      "supersedes_disposition_ids": ["optional-prior-disposition-id"],
      "reason": "included in the approved recovery brief",
      "evidence_paths": ["data/dispatches/food-line/review/proposed-editions/2026-09-28.json"],
      "public_url": "https://dispatches.thebluefernco.com/food-line/recovery/..."
    }
  ]
}
```

Supported dispositions are:

- `published_in_recovery_brief`
- `rejected_or_weak`
- `duplicate_or_stale`
- `needs_source_check`
- `hold`
- `unresolved`

Only `published_in_recovery_brief`, `rejected_or_weak`, and
`duplicate_or_stale` subtract from the actionable backlog. `needs_source_check`,
`hold`, `unresolved`, malformed entries, unknown dispositions, and unmatched
entries fail safe: they do not clear the alert. Exported backlog diagnostics
include sanitized `disposition_sources[]` rows plus malformed, unknown, and
duplicate entry counts so a bad sidecar cannot silently turn a backlog green.

When a later source check resolves an earlier hold, the later sidecar entry may
set `supersedes_disposition_ids` to the prior sidecar or entry disposition ID.
The exporter then ignores the superseded match when choosing the active
disposition and does not count that pair as duplicate-match noise. Without an
explicit supersession link, duplicate matches remain fail-safe: a non-subtracting
hold such as `needs_source_check` continues to keep the item alertable.

`debug_summary` is the first field to inspect when a line is not healthy. It
contains a small, dispatch-neutral diagnosis pointer:

- `aggregate_status`
- `recovery_lifecycle`
- `operator_assessment`
- `primary_layer`
- `primary_task_key`
- `primary_task_status`
- `primary_classification`
- `primary_failure_stage`
- `attention_task_count`
- `attention_tasks`
- source failure counts where available
- unaccounted event counts where available
- publication flags
- `stale_observability`
- `receipt_completeness`
- Food Line private review backlog counts where available

`primary_layer` identifies the first likely operational layer to inspect, such
as source dependency, source classification, upstream handoff, wrapper,
publication, scheduler/observability, or task-level failure. Dispatch-specific
details remain in task summaries and receipt artifacts; the summary exists to
prevent each incident from requiring a fresh code walk before the right receipt
is known.

`operator_assessment` is a human-facing triage label layered on top of the
aggregate status. It does not replace `aggregate_status` for automation. It
distinguishes healthy degraded states such as
`HEALTHY_WITH_SOURCE_EXCLUSIONS` and `HEALTHY_WITH_EXTERNAL_RESTRICTIONS`
from `FAILED_ACTION_REQUIRED` or `DEGRADED_ACTION_RECOMMENDED`, so source
imperfections do not look like fresh infrastructure incidents. Care Line source
summaries may also expose `transient_source_failure_count` and
`all_current_failures_non_actionable`; these keep retryable source timeouts
visible without treating a single transient fetch/network failure as a
source-classification incident when all durable failures are already classified.

Watch and operator notification policy should use `operator_assessment` as the
alert gate:

- The current active alert scope is `food-line`, `care-line`, `gaza`, and
  `ice`. `american-pressure` and `cascadia` are outside current alert health
  scope and should not make `alert_required` true.
- Alert on `FAILED_ACTION_REQUIRED`, `DEGRADED_ACTION_RECOMMENDED`, and
  `ACTION_REQUIRED_OBSERVABILITY`.
- Alert on `ACTION_REQUIRED_PENDING_REVIEW` because valid private Food Line
  items are waiting for editorial or publication disposition.
- Do not alert as a failure on `HEALTHY`, `HEALTHY_WITH_SOURCE_EXCLUSIONS`,
  `HEALTHY_WITH_EXTERNAL_RESTRICTIONS`, or
  `HEALTHY_WITH_TRANSIENT_SOURCE_FAILURES`.
- For `HEALTHY_WITH_EXTERNAL_RESTRICTIONS`, notify only when the count of
  failed external sources rises, a required source family loses all current
  coverage, or `unclassified_source_failure_count` becomes nonzero.
- For `HEALTHY_WITH_TRANSIENT_SOURCE_FAILURES`, notify only when the transient
  source remains unrecovered across the retry window, expands to multiple
  current transient source failures, or any non-transient unclassified source
  failure appears.
- For `HEALTHY_WITH_SOURCE_EXCLUSIONS`, notify only when downstream handoff
  fails, receipt completeness is no longer `COMPLETE`, or the source-watch
  exclusion pattern changes into a nonterminal failure.

The Scheduled Dispatch Watch should consume `food-line/latest.json` as follows:

1. Read `aggregate_status` for dispatch health. `FAILED` is a real task failure;
   `STALE_OBSERVABILITY` means the status surface cannot establish a current
   result and must not be treated as yesterday's health.
2. Read `debug_summary.operator_assessment` to decide whether this is an
   action-required failure or a healthy degraded state. Then read
   `debug_summary` for the first failing layer and task, and read
   `task_summaries` to identify failed, upstream-blocked, degraded, or
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

The read-only `scripts/dispatch_ops.py status DISPATCH --date YYYY-MM-DD`
command also surfaces `debug_summary` in its text and JSON output when the
exported status artifact contains it. Use that command for a quick operator
view before opening raw status JSON.

Use `scripts/dispatch_ops.py system` for the same first-look view across all
dispatches in `ops/status/system/latest.json`. When run from the Operator
checkout, the command defaults to `ops/operator/config.json` `operator.status_root`
if that configured status checkout exists. Use `--root` to inspect another
status checkout explicitly.

The system artifact marks migrated active dispatches as `MIGRATED`, Cascadia as
`INTENTIONALLY_INACTIVE`, and unavailable source roots as `NOT_MIGRATED`. Those
entries are not synthesized failures.

Cascadia is intentionally inactive and is not in the active migration queue. It
may re-enter the migration plan only after separate explicit operator
authorization to reactivate Cascadia.

## Git contention strategy

Use one serialized exporter. Do not have every scheduled task independently commit and push. The exporter should use a dedicated operational-status clone/worktree or isolated checkout. Production runners should not need to push from dirty runtime worktrees.

The same contention rule applies to Scheduled Dispatch Watch heartbeat
persistence. Watches submit `bluefern.watch_run.v1` payloads to the
repository-side `watch-heartbeat-ingest.yml` workflow; the workflow owns the
append-only commit to the dedicated `ops/watch-ledger` branch and only stages
paths under `ops/watch-ledger/`. Fallback `automation/...` branches are
diagnostic-only and are not authoritative heartbeat evidence. A heartbeat
ingestion failure is an observability/runtime failure, not authorization to
disable, pause, or mutate the watch schedule.

## Scheduled operational-status export

The single Windows task `\Blue Fern Co\Blue Fern Operational Status Export`
runs at 15 minutes past each hour with `StartWhenAvailable` and
`MultipleInstances IgnoreNew`. Its action is
`scripts/run_operational_status_export.ps1`, which invokes only the serialized
status exporter against the Food Line receipt root and the sanctioned
`OperationalStatusCurrent` checkout. It never invokes Source Watch, Resume,
Current Intake, Daily Publish, editorial review, or Pages publication.

The exporter records a bounded local receipt under
`logs/operational-status-exporter/` with start/completion time, exit code,
status-change and commit/push results, source/status heads, and a failure
classification. These local receipts are not exported as public status data.

The PowerShell wrapper also records a bounded local scheduler-boundary receipt
under `logs/operational-status-exporter-wrapper/`. That parent receipt captures
the wrapper path, working directory, Python executable resolution, child command,
child-launch attempt state, child stdout/stderr paths and tails, wrapper exit
code, and any pre-exporter error so a Task Scheduler launch cannot return
nonzero without leaving local diagnostic evidence. These wrapper receipts are
local operational evidence only; they are not exported as public status data and
do not run collection, intake, Pages, or publication steps.

## History retention

Recommended externally surfaced retention:

- always keep `latest.json`
- keep daily history for 30–90 days
- optionally keep monthly rollups

Local authoritative receipts may retain longer history according to dispatch-specific operational needs. No broad cleanup is implemented in Phase 1.
