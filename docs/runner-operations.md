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
