# Care Line reviewed-event queue scheduler template

This is a registration template only. It does not register or start a Windows
scheduled task. An operator must choose the cadence and start time.

Suggested task name: `Blue Fern Care Line Reviewed Event Queue`

```powershell
$RepositoryRoot = 'C:\PythonProjects\Dispatches From The Blue Fern Co'
$Action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RepositoryRoot\scripts\run_care_line_reviewed_event_queue.ps1`" -RepositoryRoot `"$RepositoryRoot`""
$Trigger = New-ScheduledTaskTrigger -Once -At '<START_TIME>' # choose cadence separately
$Principal = New-ScheduledTaskPrincipal -UserId '<USER_OR_SERVICE_ACCOUNT>' -LogonType Password -RunLevel LeastPrivilege
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
# Register-ScheduledTask -TaskName 'Blue Fern Care Line Reviewed Event Queue' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings
```

Choose and document the operator account, logged-off behavior, start time, and
cadence before registration. The task runs the absolute repository path with
`NoProfile` and an explicit execution policy. It only enqueues, inspects,
selects a bounded release set, and prepares a guarded dry-run report. It does
not publish Pages, post to Bluesky, approve events, commit, push, or retry
automatically.
