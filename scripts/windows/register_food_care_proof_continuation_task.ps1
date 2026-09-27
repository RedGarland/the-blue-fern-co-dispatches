[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$OperatorRoot = "C:\BlueFernRunner\BlueFernOperatorCurrent",
    [string]$TaskName = "Blue Fern Food Care Proof Continuation",
    [string]$TaskPath = "\Blue Fern Co\",
    [int]$IntervalMinutes = 10,
    [switch]$Once
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$script = Join-Path $OperatorRoot "scripts\windows\run_food_care_proof_continuation.ps1"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "Continuation script not found: $script" }
if ($IntervalMinutes -lt 5) { throw "IntervalMinutes must be at least 5." }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $OperatorRoot
$trigger = if ($Once) {
    New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
} else {
    New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
}
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
$qualified = "$TaskPath$TaskName"
if ($PSCmdlet.ShouldProcess($qualified, "Register Food/Care proof continuation task")) {
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -InputObject $task -Force | Out-Null
    Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
}
