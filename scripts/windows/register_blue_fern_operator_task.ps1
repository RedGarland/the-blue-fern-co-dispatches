[CmdletBinding()]
param(
    [string]$RepositoryRoot = "C:\\BlueFernRunner\\BlueFernOperatorCurrent",
    [string]$TaskPath = "\\Blue Fern Co.\\",
    [string]$TaskName = "Blue Fern Operator",
    [ValidateRange(5, 1440)]
    [int]$EveryMinutes = 30,
    [System.Management.Automation.PSCredential]$Credential,
    [switch]$WhatIf
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$Wrapper = Join-Path $RepositoryRoot "scripts\\run_blue_fern_operator.ps1"
$Python = Join-Path $RepositoryRoot ".venv\\Scripts\\python.exe"
$OperatorScript = Join-Path $RepositoryRoot "scripts\\blue_fern_operator.py"
$PowerShell = "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Wrapper`""
$StartAt = (Get-Date).AddMinutes(1)

foreach ($RequiredPath in @($RepositoryRoot, $Wrapper, $Python, $OperatorScript)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required Operator path is missing: $RequiredPath"
    }
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument $Arguments `
    -WorkingDirectory $RepositoryRoot

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $StartAt `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

if ($WhatIf) {
    [pscustomobject]@{
        TaskPath = $TaskPath
        TaskName = $TaskName
        Execute = $PowerShell
        Arguments = $Arguments
        WorkingDirectory = $RepositoryRoot
        StartAt = $StartAt.ToString("s")
        EveryMinutes = $EveryMinutes
        MultipleInstances = "IgnoreNew"
        StartWhenAvailable = $true
        ExecutionTimeLimitMinutes = 15
        RunLevel = "Limited"
        RegistrationPerformed = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

if (-not $Credential) {
    $Credential = Get-Credential `
        -UserName $env:USERNAME `
        -Message "Credentials are used only to register the Blue Fern Operator scheduled task."
}

$Password = $Credential.GetNetworkCredential().Password
if (-not $Password) {
    throw "A non-empty password is required for Password-logon Task Scheduler registration."
}

Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User $Credential.UserName `
    -Password $Password `
    -RunLevel Limited `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
$Info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName

[pscustomobject]@{
    TaskPath = $TaskPath
    TaskName = $TaskName
    State = [string]$Task.State
    LastRunTime = $Info.LastRunTime
    LastTaskResult = $Info.LastTaskResult
    NextRunTime = $Info.NextRunTime
    EveryMinutes = $EveryMinutes
    RegistrationPerformed = $true
} | ConvertTo-Json -Depth 4
