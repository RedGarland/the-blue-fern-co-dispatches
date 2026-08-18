[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = "",
    [string]$UserId = ""
)

$ErrorActionPreference = "Stop"
$TaskName = "Blue Fern Care Line Reviewed Event Queue"
$TaskPath = "\"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$derivedRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path
$repoRoot = if ($RepositoryRoot) { (Resolve-Path -LiteralPath $RepositoryRoot).Path } else { $derivedRoot }
$runner = Join-Path $repoRoot "scripts\run_care_line_reviewed_event_queue.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Repository root does not contain the Care Line queue runner: $repoRoot"
}

$effectiveUserId = if ($UserId) { $UserId } else { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$sameNameElsewhere = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -ne $TaskPath })
if ($sameNameElsewhere) {
    throw "A Care Line queue task with the same name exists outside the required root path: $($sameNameElsewhere.TaskPath -join ', ')"
}

$start = (Get-Date).Date.AddHours(8)
if ($start -le (Get-Date)) { $start = $start.AddDays(1) }
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -RepositoryRoot `"$repoRoot`""
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $actionArguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $start
$principal = New-ScheduledTaskPrincipal -UserId $effectiveUserId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -WakeToRun:$false

$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Update Care Line queue task")) {
        Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    }
} elseif ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Register Care Line queue task")) {
    Register-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Runs the guarded Care Line reviewed-event queue poll; never publishes, pushes Pages, commits, or posts to Bluesky."
}

Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
