[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$StatusCheckout = 'C:\BlueFernRunner\OperationalStatusCurrent',
    [string]$CareSourceRoot = '',
    [string]$IceSourceRoot = '',
    [string]$Python = 'python.exe'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @(
    (Join-Path $scriptRoot 'run_operational_status_export.py'),
    '--source-root', $SourceRoot,
    '--status-checkout', $StatusCheckout,
    '--prepare-branch', 'ops/status/food-line-2026-09-10'
)
if (-not [string]::IsNullOrWhiteSpace($CareSourceRoot)) {
    $arguments += @('--care-source-root', $CareSourceRoot)
}
if (-not [string]::IsNullOrWhiteSpace($IceSourceRoot)) {
    $arguments += @('--ice-source-root', $IceSourceRoot)
}
& $Python @arguments
exit $LASTEXITCODE
