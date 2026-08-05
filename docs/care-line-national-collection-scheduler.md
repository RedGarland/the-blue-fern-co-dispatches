# Care Line national collection-only scheduler

The Care Line national scheduler is collection-only. It never approves,
generates, syncs Pages, publishes, or pushes.

Use a dedicated clean runner checkout. The permanent operational path on this
machine is:

```text
C:\BlueFernRunner\CareLineNational
```

The scheduler invokes the canonical intake entrypoint through a narrow wrapper:

```text
scripts\run_care_line_national_pipeline.py --collection-only --run-date YYYY-MM-DD
```

The PowerShell task action should call:

```text
scripts\windows\run_care_line_national_collection.ps1
```

## Scheduled behavior

Times are Pacific local time on a host configured for `Pacific Standard Time`.

- 06:00
- 12:00
- 18:00

Task name:

```text
\Blue Fern Co.\Blue Fern Care Line National Collection
```

The task uses Task Scheduler `IgnoreNew` and an atomic scheduler lock:

```text
status\care-line\locks\national-collection.lock
```

If a prior run is still active, the next trigger exits cleanly as
`already_running` without starting a second collection run.

## What the task does

- resolves the current Pacific calendar date
- verifies the runner checkout is clean and on
  `agent/refine-care-line-signal-wire-public-rendering`
- runs repository preflight
- runs the canonical Care Line national pipeline in explicit collection-only mode
- preserves durable collection-run artifacts
- updates local persistent candidate state
- updates the mutable review queue and companion review files
- writes a scheduler receipt and log

## Bounded smoke-test mode

Smoke mode is explicit only. It is never part of the installed production task
action.

Wrapper flags:

- `-SmokeTest`
- `-MaxSources`
- `-MaxItemsPerSource`

Helper flags:

- `--smoke-test`
- `--max-sources`
- `--max-items-per-source`

Hard ceilings:

- maximum sources: `3`
- maximum items per source: `3`

Smoke mode rejects:

- insecure TLS
- zero or negative limits
- limits above the hard ceilings
- smoke limits when smoke mode is not explicitly enabled

Smoke artifacts are isolated from production scheduler and review-state files:

- `logs/care-line/collection-scheduler/smoke/`
- `status/care-line/scheduler-runs/smoke/`
- `status/care-line/locks/smoke/`
- `data/dispatches/care-line/collection-runs/smoke/`
- `data/dispatches/care-line/review/smoke/`

Smoke mode records deterministic selected source IDs and keeps production review
queue mutation disabled.

## What the task does not do

- approve review items
- generate a Care Line edition
- generate a release manifest
- sync Pages
- update the public site
- publish Signal Wire
- generate or publish social cards
- generate or publish audio, podcast, map, RSS, or Bluesky artifacts

## Local operational files

The setup script adds runner-local excludes for:

- `logs/care-line/`
- `status/care-line/`
- `data/dispatches/care-line/collection-runs/`
- `data/dispatches/care-line/review/candidate-registry.json`
- `data/dispatches/care-line/review/current-*.json`

These files remain durable on the runner checkout but are not committed by the
scheduled workflow.

## Validation

Dry-run setup check:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\tmp\care-line-phase-g-source\scripts\windows\setup_care_line_collection_tasks.ps1" `
  -RepositoryRoot "C:\BlueFernRunner\CareLineNational" `
  -PythonExecutable "C:\BlueFernRunner\CareLineNational\.venv\Scripts\python.exe" `
  -CheckOnly
```

Manual one-shot collection run:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\tmp\care-line-phase-g-source\scripts\windows\run_care_line_national_collection.ps1" `
  -RepositoryRoot "C:\BlueFernRunner\CareLineNational" `
  -PythonExecutable "C:\BlueFernRunner\CareLineNational\.venv\Scripts\python.exe" `
  -RunDate "2026-08-05"
```

Manual bounded smoke test:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\BlueFernRunner\CareLineNational\scripts\windows\run_care_line_national_collection.ps1" `
  -RepositoryRoot "C:\BlueFernRunner\CareLineNational" `
  -PythonExecutable "C:\BlueFernRunner\CareLineNational\.venv\Scripts\python.exe" `
  -RunDate "2026-08-05" `
  -SmokeTest `
  -MaxSources 3 `
  -MaxItemsPerSource 3
```

Registration after validation:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\BlueFernRunner\CareLineNational\scripts\windows\setup_care_line_collection_tasks.ps1" `
  -RepositoryRoot "C:\BlueFernRunner\CareLineNational" `
  -PythonExecutable "C:\BlueFernRunner\CareLineNational\.venv\Scripts\python.exe"
```
