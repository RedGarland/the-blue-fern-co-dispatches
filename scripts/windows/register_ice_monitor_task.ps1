[CmdletBinding()]
param(
    [string]$RepositoryRoot = "C:\BlueFernRunner\ICEMonitorCurrent",
    [string]$PythonExecutable = "",
    [string]$TaskPath = "\Blue Fern Co.\",
    [string]$TaskName = "Daily - ICE Monitor",
    [string]$At = "21:15",
    [string]$UserId = $env:USERNAME,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe" }
$wrapper = Join-Path $RepositoryRoot "scripts\windows\run_ice_monitor.ps1"
$workingDirectory = $RepositoryRoot
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" -RepositoryRoot `"$RepositoryRoot`" -PythonExecutable `"$PythonExecutable`" -WindowHours 168 -MaxPerSource 5"

$action = New-ScheduledTaskAction -Execute $ps -Argument $argument -WorkingDirectory $workingDirectory
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal

if ($WhatIf) {
    [pscustomobject]@{
        TaskPath = $TaskPath
        TaskName = $TaskName
        Execute = $ps
        Arguments = $argument
        WorkingDirectory = $workingDirectory
        Cadence = "daily"
        At = $At
        MultipleInstances = "IgnoreNew"
    } | ConvertTo-Json -Depth 4
    exit 0
}

Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
