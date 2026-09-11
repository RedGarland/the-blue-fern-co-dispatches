[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$StatusCheckout = 'C:\BlueFernRunner\OperationalStatusCurrent',
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
& $Python @arguments
exit $LASTEXITCODE
