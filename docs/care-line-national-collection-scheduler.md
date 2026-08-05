# Care Line national collection-only scheduler

The Care Line national scheduler is collection-only. It never approves,
generates, syncs Pages, publishes, or pushes.

Use a dedicated clean runner checkout. For Phase G implementation on this
machine, the validated runner path is:

```text
C:\tmp\care-line-phase-g-source
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
  -RepositoryRoot "C:\tmp\care-line-phase-g-source" `
  -PythonExecutable "C:\tmp\care-line-phase-g-source\.venv\Scripts\python.exe" `
  -CheckOnly
```

Manual one-shot collection run:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\tmp\care-line-phase-g-source\scripts\windows\run_care_line_national_collection.ps1" `
  -RepositoryRoot "C:\tmp\care-line-phase-g-source" `
  -PythonExecutable "C:\tmp\care-line-phase-g-source\.venv\Scripts\python.exe" `
  -RunDate "2026-08-05"
```

Registration after validation:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\tmp\care-line-phase-g-source\scripts\windows\setup_care_line_collection_tasks.ps1" `
  -RepositoryRoot "C:\tmp\care-line-phase-g-source" `
  -PythonExecutable "C:\tmp\care-line-phase-g-source\.venv\Scripts\python.exe"
```
