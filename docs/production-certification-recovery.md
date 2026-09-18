# Production Certification And Same-Day Recovery

## Deployment State Model

Use precise state labels during production rollout:

1. `CODE_VALIDATED`
2. `DEPLOYED`
3. `PRODUCTION_PROOF_PASSED`
4. `AWAITING_NATURAL_CERTIFICATION`
5. `NATURAL_RUNTIME_CERTIFIED`

Isolated certification is immediate engineering proof after a source change or
runner sync. Natural scheduled execution remains the final production
certification because it proves the operating-system scheduler boundary.

`AWAITING_NATURAL_CERTIFICATION` is not a source-development freeze.

Once source validation passes, deployment succeeds, immediate
production-equivalent certification passes, and no unresolved production safety
defect remains, unrelated or next-phase source development may continue. The
natural scheduled run certifies the operating-system scheduler invocation, live
environment and wrapper boundary, true scheduled timing, live runtime-state
integration, and naturally ordered downstream or exporter behavior. It is not
the first functional test and is not a reason to stop safe source work.

Block further source work only when the pending natural result is necessary to
decide the design of that same code path, immediate certification exposed an
unresolved defect, proceeding would mutate or compound uncertain production
state, or the next task would activate behavior whose safety depends on the
pending natural proof.

```text
SOURCE DEVELOPMENT:
NON-BLOCKING after PRODUCTION_PROOF_PASSED

PRODUCTION ACTIVATION:
may remain gated on NATURAL_RUNTIME_CERTIFIED where appropriate
```

Do not use this anti-pattern:

```text
fix -> deploy -> wait hours -> continue engineering only after natural run
```

Use:

```text
fix
-> validate
-> deploy
-> immediate production proof
-> continue safe source development
-> natural run independently certifies scheduler/runtime boundary
```

## Certification Harness

Run source-level, non-public certification with an explicit isolated proof root:

```powershell
python scripts\run_production_certification.py `
  --dispatch care-line `
  --source-root <source-or-runner-copy> `
  --proof-root <isolated-proof-root>
```

The harness emits `bluefern_production_certification_v1`. It checks source
state, preflight, imports/compile, configuration, receipt compatibility,
dependency evaluation, and isolated operational-status simulation. It does not
publish, write Pages, update RSS, send social posts, generate public audio,
consume editorial approval, modify production queues, commit, push, or alter
scheduled tasks.

Level 2 isolated runtime proof is reported as `UNSUPPORTED_ISOLATED_EXECUTION`
unless every write can be redirected into the proof root. Do not weaken
production guards to make certification pass.

## Same-Day Recovery Evaluator

Run the read-only evaluator:

```powershell
python scripts\evaluate_scheduled_recovery.py `
  --dispatch ice `
  --source-root <runner-or-fixture-root> `
  --date 2026-09-16 `
  --evaluated-at 2026-09-17T04:30:00Z
```

The evaluator recommends only. It never executes production tasks or installs
retry jobs.

Implemented recommendation states:

- `NO_ACTION`
- `WAITING_FOR_SCHEDULE`
- `WAITING_FOR_GRACE`
- `RETRY_ELIGIBLE`
- `UPSTREAM_BLOCKED`
- `MANUAL_ATTENTION`
- `MISSED`
- `RECOVERY_WINDOW_EXPIRED`

Publication tasks default to `MANUAL_ATTENTION`; publication retries require a
separate operator-controlled idempotency and authorization model.

## Bounded Same-Day Recovery Executor

The recovery evaluator and executor are separate roles:

- evaluator decides whether a scheduled instance is `RETRY_ELIGIBLE`
- executor cannot widen eligibility
- executor may consider only evaluator-selected `RETRY_ELIGIBLE` instances
- executor is hard allowlisted by dispatch and task key

The executor is intentionally narrow. It plans all dispatches from the evaluator
report, but automatic execution is limited to audited Food Line recovery steps:
Source Watch and Source Watch Resume recover through the existing
`status-resume` pathway, and Current Intake may be retried only when a prior
dependency-blocked intake receipt is followed by newer durable Source
Watch/Resume evidence proving the same edition/run is now qualifying and
intake-ready. The executor must not launch a fresh Source Watch as recovery.
Care collection and ICE monitor recovery remain planning-only until a separate
source audit proves same-instance idempotency, duplicate protection, instance
addressability, and no public side effects.

Executor invariants:

- dry-run is the default; `--execute` is required for a child process
- publication task keys are hard-denied even if a malformed report says
  `RETRY_ELIGIBLE`
- maximum automatic attempts are two per exact scheduled instance
- minimum backoff is 30 minutes between attempts
- at most one recovery action may run per executor invocation
- stale-plan suppression re-evaluates the same instance immediately before
  execution and cancels if it is no longer `RETRY_ELIGIBLE`
- executor locks are per exact recovery target
- real execution requires the expected branch, safe repository preflight, known
  source head, and an allowlisted fixed argv adapter
- commands are argv vectors run with `shell=False`; no caller-supplied
  executable or free-form shell command is accepted
- recovery is confirmed only by a new normal operational-health receipt, not by
  child exit code alone

Food Current Intake recovery remains dependency-aware and private. The adapter
uses the scheduler `intake` command only after the evaluator emits the specific
`dependency_recovered_current_intake` classification. A late successful intake
may rebuild private review queue and proposed-edition artifacts, but it must not
invoke publication, write Pages, consume approval, generate public output, or
trigger social/audio side effects.

Execution attempts are recorded separately from task receipts under:

```text
status/operational-recovery/<dispatch>/<date>/<scheduled-instance>/
```

The attempt ledger stores only sanitized execution metadata: attempt identity,
dispatch, original task key, scheduled instance, adapter, attempt number,
timestamps, source head, evaluator recommendation, original status and
classification, child exit category/code, post-execution receipt observation,
`public_side_effects = false`, and execution mode. It must not store secrets,
environment dumps, raw provider payloads, source article text, or private
editorial material.

Implemented does not mean activated. Merging or deploying executor source does
not install an hourly watcher, register a Windows scheduled task, or enable
automatic production recovery. Production activation is a separate production
change requiring its own protected runtime proof and scheduler authorization.

## Food Recovery Scheduler Plumbing

Food recovery activation uses a dedicated Windows wrapper:

```powershell
scripts\windows\run_food_line_recovery_executor.ps1
```

The wrapper is fixed to Food Line recovery only. It derives the edition date in
Pacific time, derives the evaluation timestamp in UTC, and invokes the bounded
executor with:

```text
python scripts\run_scheduled_recovery_executor.py --dispatch food-line --execute --expected-branch add/pages-repo-default
```

The wrapper does not accept an arbitrary executable, task key, shell command,
proof root, dispatch name, publication command, Pages path, or Care/ICE target.
Routine production runs write a bounded UTF-8 terminal record under:

```text
logs/food-line/recovery-executor/<edition-date>/
```

The terminal record is operational evidence for the scheduler boundary only. A
real executed recovery action is identified by the executor receipt's
`recovery_attempt_id`; inspection reports use the same canonical ID when
deciding whether a recovery action actually executed. Terminal evidence must
not include credentials, provider payloads, source article text, private
editorial material, environment dumps, or public publication receipts.

The source registration script is:

```powershell
scripts\register_food_line_recovery_task.ps1
```

It registers or updates only `\Blue Fern Co\Blue Fern Food Line Recovery` for
the Food runner root `C:\BlueFernRunner\FoodLineCurrent6`. The task runs as the
current Windows user with `Interactive` logon and `Limited` run level, uses
`MultipleInstances IgnoreNew`, and has bounded execution time. It schedules
daily slots every 30 minutes from 05:45 through 11:45 Pacific time. Registration
requires the production Windows host local timezone to be
`Pacific Standard Time`; a non-Pacific host fails closed before registration or
update, including during `-WhatIf`. The script must not change the Windows
timezone or convert the daily trigger slots to another local clock. Registration
does not start the task and supports `-WhatIf`.

The read-only inspection helper is:

```powershell
scripts\inspect_food_line_recovery_task.ps1
```

It reports task existence, enabled/state information, last and next scheduler
timestamps, task result code, and the latest wrapper terminal decision/action
state, including `host_timezone_id`, the `Pacific Standard Time` scheduler
contract, and `recovery_attempt_id` when a recovery action executed. It must not
register, update, enable, disable, or start the task.

Activation and execution remain distinct:

- merging, deploying, or registering this plumbing does not itself recover Food
- activation requires explicit production authorization to register or enable
  the task
- natural certification requires observing a scheduled invocation cross the
  Windows Task Scheduler boundary on a host whose local timezone is
  `Pacific Standard Time`
- wrapper edition-date derivation still independently uses Pacific time rather
  than assuming the local clock or UTC date
- the wrapper may run only the executor's already audited Food recovery paths:
  Source Watch status-resume, Source Watch Resume status-resume, and
  dependency-recovered Current Intake
- publication, Pages writes, approval consumption, public audio/social output,
  Food manual triggers outside the executor, Care recovery, and ICE recovery
  remain unsupported by this task

## ICE Operational-Status Migration

The serialized external status exporter remains the single exporter.

When `--ice-source-root` / `-IceSourceRoot` is omitted, ICE remains:

```text
migration_status = NOT_MIGRATED
aggregate_status = UNKNOWN
```

When an ICE source root is explicitly supplied, the exporter consumes local
`ice_monitor` operational-health receipts and writes:

```text
ops/status/ice/latest.json
ops/status/ice/history/YYYY-MM-DD.json
```

System latest then marks ICE as `MIGRATED` and uses the evaluated ICE status.
The exporter sanitizes task summaries and does not export raw provider payloads,
article text, review queue content, credentials, or local absolute paths.

The dedicated ops branch remains the runtime-health authority. The source
branch must not be treated as live operational status.
