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
report, but automatic execution is initially limited to Food Line Source Watch
and Source Watch Resume recovery through the existing `status-resume` pathway.
It must not launch a fresh Source Watch as recovery. Care collection and ICE
monitor recovery remain planning-only until a separate source audit proves
same-instance idempotency, duplicate protection, instance addressability, and no
public side effects.

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
