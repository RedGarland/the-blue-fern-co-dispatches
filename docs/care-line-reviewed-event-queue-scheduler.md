# Care Line reviewed-event queue scheduler template

This is a registration template only. It does not register or start a Windows
scheduled task. An operator must choose the cadence and start time.

Suggested task name: `Blue Fern Care Line Reviewed Event Queue`

Registered Task Scheduler location:

`\Blue Fern Care Line Reviewed Event Queue`

The task is in the Task Scheduler root (`TaskPath` `\`), not in a
Care Line subfolder. Use `scripts/register_care_line_reviewed_event_queue_task.ps1`
for registration or update. It looks up, updates, and verifies the task with
`-TaskPath "\" -TaskName "Blue Fern Care Line Reviewed Event Queue"` and refuses
to create a same-name task in another folder.

The queue reads the canonical reviewed-record contract described in
`docs/care-line-national-data-model-phase-b.md`. Queue release eligibility is
separate from evidence verification and now depends on normalized workflow and
verification states in the reviewed record.

```powershell
$RepositoryRoot = 'C:\PythonProjects\Dispatches From The Blue Fern Co'
$Action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RepositoryRoot\scripts\run_care_line_reviewed_event_queue.ps1`" -RepositoryRoot `"$RepositoryRoot`""
$Trigger = New-ScheduledTaskTrigger -Once -At '<START_TIME>' # choose cadence separately
$Principal = New-ScheduledTaskPrincipal -UserId '<USER_OR_SERVICE_ACCOUNT>' -LogonType Password -RunLevel LeastPrivilege
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
# Register-ScheduledTask -TaskPath '\' -TaskName 'Blue Fern Care Line Reviewed Event Queue' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings
```

Read-only inspection uses the same explicit root path:

```powershell
$TaskPath = '\'
$TaskName = 'Blue Fern Care Line Reviewed Event Queue'
Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
```

Choose and document the operator account, logged-off behavior, start time, and
cadence before registration. The task runs the absolute repository path with
`NoProfile` and an explicit execution policy. It only enqueues, inspects,
selects a bounded release set, and prepares a guarded dry-run report. It does
not publish Pages, post to Bluesky, approve events, commit, push, or retry
automatically.
