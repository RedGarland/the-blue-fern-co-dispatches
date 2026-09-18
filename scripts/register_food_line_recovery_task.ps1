[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$TaskName = "Blue Fern Food Line Recovery",
    [string]$TaskPath = "\Blue Fern Co\",
    [string]$UserId = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$wrapper = Join-Path $repoRoot "scripts\windows\run_food_line_recovery_executor.ps1"
if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
    throw "Repository root does not contain the Food recovery wrapper: $repoRoot"
}
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Repository root does not contain the expected virtualenv Python: $python"
}

$effectiveUserId = if ($UserId) { $UserId } else { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$sameNameElsewhere = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -ne $TaskPath })
if ($sameNameElsewhere) {
    throw "A Food recovery task with the same name exists outside the required path: $($sameNameElsewhere.TaskPath -join ', ')"
}

$slots = @("05:45","06:15","06:45","07:15","07:45","08:15","08:45","09:15","09:45","10:15","10:45","11:15","11:45")
$today = (Get-Date).Date
$triggers = @()
foreach ($slot in $slots) {
    $parts = $slot.Split(":")
    $at = $today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    if ($at -le (Get-Date)) { $at = $at.AddDays(1) }
    $triggers += New-ScheduledTaskTrigger -Daily -At $at
}

$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$wrapper`" -RepositoryRoot `"$repoRoot`""
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $actionArguments -WorkingDirectory $repoRoot
$principal = New-ScheduledTaskPrincipal -UserId $effectiveUserId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew `
    -WakeToRun:$false

$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Update Food recovery task")) {
        Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings
    }
} elseif ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Register Food recovery task")) {
    Register-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Runs bounded Food Line same-day recovery only: Source Watch status-resume, Source Watch Resume status-resume, and dependency-recovered Current Intake. No publication, Pages, Care, or ICE recovery."
}

Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
