[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = "",
    [string]$SourceRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$StatusCheckout = 'C:\BlueFernRunner\OperationalStatusCurrent',
    [string]$UserId = ""
)

$ErrorActionPreference = "Stop"
$TaskName = "Blue Fern Operational Status Export"
$TaskPath = "\Blue Fern Co\"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$derivedRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path
$repoRoot = if ($RepositoryRoot) { (Resolve-Path -LiteralPath $RepositoryRoot).Path } else { $derivedRoot }
$runner = Join-Path $repoRoot "scripts\run_operational_status_export.ps1"
if (-not (Test-Path -LiteralPath $runner)) { throw "Repository root does not contain the operational status runner: $repoRoot" }

$effectiveUserId = if ($UserId) { $UserId } else { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$sameNameElsewhere = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -ne $TaskPath })
if ($sameNameElsewhere) { throw "An operational status task with the same name exists outside the required root path: $($sameNameElsewhere.TaskPath -join ', ')" }

$start = (Get-Date).Date.AddMinutes(15)
if ($start -le (Get-Date)) { $start = $start.AddHours(1) }
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -SourceRoot `"$SourceRoot`" -StatusCheckout `"$StatusCheckout`""
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $actionArguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $effectiveUserId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -WakeToRun:$false

$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Update operational status export task")) {
        Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    }
} elseif ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Register operational status export task")) {
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Exports sanitized operational status only; never runs collection, intake, publication, or Pages changes."
}
Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
