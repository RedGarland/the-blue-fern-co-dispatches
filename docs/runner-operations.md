# Runner Operations

Production dispatches run from dedicated Windows checkouts under `C:\BlueFernRunner`.
Do not point scheduled production tasks at an active development worktree.

The checked-in runner configuration and guarded synchronization scripts are the
source of truth. Do not infer current production paths from historical docs,
old task XML, or archived receipts.

## Current production topology

The dispatch runner roots are defined in `ops/operator/config.json`. The
guarded synchronization set is defined by
`scripts/sync_active_production_runners.ps1`.

Current topology:

```text
C:\BlueFernRunner\
  BlueFernOperatorCurrent\
  FoodLineCurrent6\
  CareLineNationalCurrent8\
  GazaDispatchesCurrent6\
  ICEMonitorCurrent\
  OperationalStatusCurrent\
```

The source runners track protected branch `add/pages-repo-default`.
`OperationalStatusCurrent` tracks the configured operational-status branch.
Pages/publication state is separate from source-runner synchronization.

If these paths change, update the canonical configuration/scripts and their
consistency tests together rather than adding another hard-coded path elsewhere.

## Development worktrees

Development work must stay separate from production runners. A local development
checkout may live anywhere; examples use:

```text
C:\BlueFernDev\the-blue-fern-co-dispatches\
C:\BlueFernDev\the-blue-fern-co-dispatches\bluefern-dispatches-pages\
```

No production script should depend on that example path.

## Environment setup

For a newly provisioned checkout, install the checked-in runtime dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Do not copy `.env`, credentials, or private runtime state from another
worktree into source control.


## Local runner boundary handoff

When a chat or remote execution session cannot access the Windows runner host,
`C:\\BlueFernRunner`, or Task Scheduler, the handoff must be a ready-to-run
Codex/Work draft rather than loose operator commands.

The draft must carry the exact protected SHA or require a protected HEAD re-read,
the Windows paths, allowed work, forbidden work, stop conditions, expected
receipts, and final checkpoint format. Include commands only as part of that
executor-ready draft.

This prevents the human operator from becoming the integration layer between
GitHub/source work and Windows-local runner proof.

## Autopilot handoff continuity

Runner handoff is an execution boundary, not an approval boundary. Once Windows Codex returns proof that stayed within the allowed actions and reports no public side effects, source-side Codex must continue through routine proof recording, PR creation/update, bounded CI remediation, exact-head merge when permitted, and the next operational diagnosis.

Do not ask the operator to type "proceed" for routine post-proof checkpointing or safe status updates. Stop only when the proof exposes unsafe runner state, unexplained source drift, a public/scheduler/evidence/credential/destructive boundary, or materially ambiguous operational risk.

## Guarded synchronization

Use the canonical synchronization helper rather than ad hoc pull/reset commands.

Plan first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync_active_production_runners.ps1
```

Apply only through the helper's supported guarded apply mode after the plan is
safe. The helper must preserve sanctioned runtime evidence and fail closed on
unexpected tracked source drift.

Expected active set:

- Operator
- Food Line
- Care Line
- Gaza
- ICE

Cascadia is intentionally inactive and must not be provisioned or synchronized
as an active production runner without explicit operator authorization.

## Local Operator runner sync

The Operator exposes a receipt-writing wrapper around the guarded runner sync
helper so long runner reconciliation can happen on the Windows host rather than
inside a browser chat turn.

Plan-only checkpoint:

```powershell
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\BlueFernOperatorCurrent\scripts\run_blue_fern_operator.ps1" -SyncRunners
```

Guarded apply with operational-status proof, pinned to the protected head already
recorded by the handoff:

```powershell
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\BlueFernOperatorCurrent\scripts\run_blue_fern_operator.ps1" -SyncRunners -ApplyRunnerSync -ProveStatusExport -ExpectedProtectedHead <protected-sha>
```

The command writes a `blue_fern_operator_runner_sync_receipt_v1` receipt under
`ops/operator/runs/<date>/runner-sync-.../`. A plan that finds safe fast-forward
work exits successfully as `PLAN_READY_TO_FAST_FORWARD`; blocked plans and failed
applies keep their exact PowerShell report path in the receipt.

This wrapper does not publish, collect, mutate Pages, activate Cascadia, or
change scheduled task definitions. It may fast-forward production runner source
checkouts only when `-ApplyRunnerSync` is explicitly present.

## Dispatch entry points

Use canonical wrappers from the appropriate synchronized runner.

### Gaza

Runner root:

`C:\BlueFernRunner\GazaDispatchesCurrent6`

The general runner wrapper supports Gaza and derives its repository root from
its own location when `-RepoRoot` is omitted:

```powershell
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\GazaDispatchesCurrent6\scripts\run_runner_dispatch.ps1" -Dispatch gaza
```

Public push, Bluesky, and audio flags remain separate publication-side-effect
choices. Do not add them merely for health or readiness proof.

### Food Line

Runner root:

`C:\BlueFernRunner\FoodLineCurrent6`

For normal production scheduling use the current checked-in Food Line wrappers.
The legacy root `run_food_line_daily.ps1` now derives its project root from its
own file location unless `BLUEFERN_PROJECT_ROOT` explicitly overrides it.

### Care Line

Runner root:

`C:\BlueFernRunner\CareLineNationalCurrent8`

Canonical collection-only recovery uses:

`scripts/run_care_line_national_collection.ps1`

Collection proof is separate from reviewed-event queue and publication work.

### ICE

Runner root:

`C:\BlueFernRunner\ICEMonitorCurrent`

Canonical monitor-only execution uses:

`scripts/run_ice_monitor.ps1`

Monitor proof must not be interpreted as public publication.

## Operational status

Operational-status checkout:

`C:\BlueFernRunner\OperationalStatusCurrent`

The configured status branch is defined in `ops/operator/config.json` and the
status export wrappers. The branch name must remain dispatch-neutral.

Use exported operational status and canonical runtime receipts as runtime-health
truth. Do not infer health from Git commits or Pages activity alone.

## Preflight and recovery

Before any production recovery:

1. inspect the relevant checkout;
2. run the canonical repository preflight;
3. use guarded synchronization if the runner is behind;
4. preserve sanctioned runtime evidence;
5. stop on unexplained tracked source drift;
6. prove the task-specific terminal receipt after remediation.

Do not use:

- `git reset --hard`
- `git clean`
- force checkout
- broad deletion of runtime evidence
- manual file replacement merely to obtain a clean status

If a runner cannot be safely reconciled through existing guarded tooling, report
the exact blocked state rather than bypassing the guard.

## Scheduler configuration

Task Scheduler definitions and registration helpers under `ops/` and
`scripts/` are the authoritative scheduler contracts.

Do not copy scheduler command lines from historical documentation. Registration
or mutation of scheduled tasks must use the dispatch-specific checked-in helper
and the currently configured production runner root.

## Cleanup policy

Generated/runtime cleanup must remain narrow. Never use cleanup to hide source
drift.

Allowed runtime evidence should be classified through the existing preflight and
runtime-path policy. Unexpected changes under source/config/test/documentation
paths remain blocking until explained.

## Proof standard

A runner is not considered recovered merely because code merged or a command
returned exit code 0.

Require the strongest applicable proof:

- protected source HEAD deployed;
- preflight/doctor passes;
- task-specific terminal receipt exists;
- operational status is fresh and complete;
- public side effects match the authorized scope.

For public workflows, publication proof is a separate boundary from runner
health proof.
