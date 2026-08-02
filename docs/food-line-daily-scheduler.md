# Food Line daily source-watch scheduler

The production Food Line schedule stops at a private proposed edition. It does
not make editorial decisions, render public HTML, modify Pages, publish, push,
post to Bluesky, generate audio or maps, or update podcast feeds.

## Production runner

Use the dedicated clean checkout:

```text
C:\BlueFernRunner\FoodLineDailyCurrent
```

It must track `agent/refine-care-line-signal-wire-public-rendering`. The source
watch fails closed when the checkout is dirty, on another branch, or cannot
fast-forward. It fetches `origin`, performs a fast-forward-only update, runs
repository preflight, and records the exact source commit in the daily receipt.

Private operational files are excluded locally through `.git/info/exclude` in
the runner checkout. They are never committed. The exclusions cover only Food
Line logs, scheduler state, inbox payloads, agent intake, and private review
artifacts.

## Daily flow

All times are Pacific local time on a host configured for `Pacific Standard
Time`.

1. 05:30 — `Blue Fern Food Line Daily Source Watch` runs the 57-query
   `daily-current` profile and requests a private inbox export.
2. 06:00 — `Blue Fern Food Line Source Watch Resume` inspects the same run and
   performs at most one bounded same-ID resume when required.
3. 06:10 — `Blue Fern Food Line Current Intake` waits a bounded time for the
   source lock, verifies qualifying completion, and builds the private review
   queue and proposed edition.
4. An operator reviews every pending item and chooses `approve`,
   `approve_with_edit`, `hold`, or `reject`.
5. Publication remains a separate guarded workflow requiring explicit
   authorization.

A qualifying completed run with `no_exportable_findings` is successful. Intake
then writes `blocked_no_reviewable_current_signals` and exits successfully.
Partial, timed-out, cancelled, failed, missing, corrupt, or structurally invalid
collection state blocks intake.

The three tasks use Task Scheduler `IgnoreNew`. Source watch and resume also
share the atomic directory lock:

```text
status\food-line\locks\source-watch.lock
```

Intake waits up to five minutes for that lock and then rechecks durable run
state. Existing and stale locks fail closed and create an operator-attention
report; stale locks are not silently removed.

## Logs, receipts, and alerts

Receipts contain operational counts and hashes, not source text, private
evidence, credentials, or raw inbox payloads.

```text
logs\food-line\source-watch\YYYY-MM-DD\
logs\food-line\current-intake\YYYY-MM-DD\
logs\food-line\operator-attention\YYYY-MM-DD\
status\food-line\runs\YYYY-MM-DD.json
```

Operator-attention reports cover checkout synchronization, runner startup,
nonqualifying state after one resume, missing/corrupt state, lock problems,
intake validation, unexpected side effects, and processor failure. A qualifying
empty run does not alert.

## Setup and task management

Check syntax, runner state, timezone, Python, and planned definitions without
network collection or Task Scheduler mutation:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File "C:\BlueFernRunner\FoodLineDailyCurrent\scripts\windows\setup_food_line_daily_tasks.ps1" `
  -RepositoryRoot "C:\BlueFernRunner\FoodLineDailyCurrent" `
  -PythonExecutable "C:\BlueFernRunner\Dispatches From The Blue Fern Co\.venv\Scripts\python.exe" `
  -CheckOnly
```

Omit `-CheckOnly` to create or update the three exact task names. Setup is
idempotent and disables the overlapping legacy
`\Blue Fern Co.\Blue Fern Food Line Daily Dispatch` task, whose old wrapper
contains automatic publication actions.

Disable or enable a task:

```powershell
Disable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Daily Source Watch"
Enable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Daily Source Watch"
Disable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Source Watch Resume"
Enable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Source Watch Resume"
Disable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Current Intake"
Enable-ScheduledTask -TaskPath "\Blue Fern Co.\" -TaskName "Blue Fern Food Line Current Intake"
```

## Manual operator commands

Set the Pacific edition date first:

```powershell
$PacificDate = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
  [DateTimeOffset]::UtcNow,
  "Pacific Standard Time"
).ToString("yyyy-MM-dd")
$Root = "C:\BlueFernRunner\FoodLineDailyCurrent"
$Python = "C:\BlueFernRunner\Dispatches From The Blue Fern Co\.venv\Scripts\python.exe"
```

Locate the scheduled run and inspect status:

```powershell
$Record = Get-Content -Raw "$Root\status\food-line\runs\$PacificDate.json" | ConvertFrom-Json
& $Python "$Root\scripts\run_food_line_discovery_expansion.py" --status-run $Record.run_id
```

Perform the guarded same-ID resume wrapper:

```powershell
& "$Root\scripts\windows\resume_food_line_daily_current.ps1" `
  -RepositoryRoot $Root -PythonExecutable $Python -EditionDate $PacificDate
```

Rerun private intake after confirming qualifying collection:

```powershell
& "$Root\scripts\windows\run_food_line_current_intake.ps1" `
  -RepositoryRoot $Root -PythonExecutable $Python -EditionDate $PacificDate
```

Open the operator artifacts:

```powershell
Get-Content -Raw "$Root\data\dispatches\food-line\review\current-signal-review.json"
Get-Content -Raw "$Root\data\dispatches\food-line\review\proposed-editions\$PacificDate.md"
```

These commands do not approve or publish anything.
