[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$StatusCheckout = 'C:\BlueFernRunner\OperationalStatusCurrent',
    [string]$CareSourceRoot = 'C:\BlueFernRunner\CareLineNationalCurrent8',
    [string]$GazaSourceRoot = 'C:\BlueFernRunner\GazaDispatchesCurrent6',
    [string]$IceSourceRoot = 'C:\BlueFernRunner\ICEMonitorCurrent',
    [string]$Python = 'python.exe'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exporterScript = Join-Path $scriptRoot 'run_operational_status_export.py'
$startedAt = (Get-Date).ToUniversalTime()
$wrapperRunId = "wrapper-{0}-{1}" -f $startedAt.ToString("yyyyMMddTHHmmssZ"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$wrapperExitCode = 1
$wrapperError = $null
$childLaunchAttempted = $false

function Test-ExecutablePath {
    param([string]$Executable)

    if ([string]::IsNullOrWhiteSpace($Executable)) {
        return $false
    }
    if ($Executable.Contains('\') -or $Executable.Contains('/') -or $Executable.Contains(':')) {
        return Test-Path -LiteralPath $Executable -PathType Leaf
    }
    return $null -ne (Get-Command $Executable -ErrorAction SilentlyContinue)
}

function Write-WrapperReceipt {
    param([string]$CompletedAt)

    $receiptRoot = if (Test-Path -LiteralPath $SourceRoot -PathType Container) { $SourceRoot } else { $scriptRoot }
    $receiptDir = Join-Path $receiptRoot 'logs\operational-status-exporter-wrapper'
    New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
    $receiptPath = Join-Path $receiptDir "$wrapperRunId.json"
    $receipt = [ordered]@{
        schema = 'bluefern.operational_status.wrapper.v1'
        run_id = $wrapperRunId
        started_at = $startedAt.ToString('o')
        completed_at = $CompletedAt
        wrapper = $PSCommandPath
        script_root = $scriptRoot
        working_directory = (Get-Location).Path
        source_root = $SourceRoot
        status_checkout = $StatusCheckout
        care_source_root = $CareSourceRoot
        gaza_source_root = $GazaSourceRoot
        ice_source_root = $IceSourceRoot
        python = $Python
        python_resolves = Test-ExecutablePath -Executable $Python
        exporter_script = $exporterScript
        exporter_script_exists = Test-Path -LiteralPath $exporterScript -PathType Leaf
        child_launch_attempted = $childLaunchAttempted
        child_command = @($Python) + $arguments
        wrapper_exit_code = $wrapperExitCode
        error = $wrapperError
        public_side_effects = $false
    }
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
}

$arguments = @(
    $exporterScript,
    '--source-root', $SourceRoot,
    '--status-checkout', $StatusCheckout,
    '--prepare-branch', 'ops/status/current'
)
if (-not [string]::IsNullOrWhiteSpace($CareSourceRoot)) {
    $arguments += @('--care-source-root', $CareSourceRoot)
}
if (-not [string]::IsNullOrWhiteSpace($GazaSourceRoot)) {
    $arguments += @('--gaza-source-root', $GazaSourceRoot)
}
if (-not [string]::IsNullOrWhiteSpace($IceSourceRoot)) {
    $arguments += @('--ice-source-root', $IceSourceRoot)
}
try {
    if (-not (Test-Path -LiteralPath $exporterScript -PathType Leaf)) {
        throw "Operational status exporter script does not exist: $exporterScript"
    }
    if (-not (Test-ExecutablePath -Executable $Python)) {
        throw "Operational status Python executable does not resolve: $Python"
    }
    $childLaunchAttempted = $true
    & $Python @arguments
    $wrapperExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
} catch {
    $wrapperError = $_.Exception.Message
    $wrapperExitCode = 1
    [Console]::Error.WriteLine($wrapperError)
} finally {
    try {
        Write-WrapperReceipt -CompletedAt (Get-Date).ToUniversalTime().ToString('o')
    } catch {
        [Console]::Error.WriteLine("Failed to write operational status wrapper receipt: $($_.Exception.Message)")
    }
}
exit $wrapperExitCode
