[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$RunId = "",
    [int]$WindowHours = 168,
    [int]$MaxPerSource = 5
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepositoryRoot) { $RepositoryRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path }
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe" }
if (-not $RunId) {
    $RunId = "ice-monitor-{0}-{1}" -f ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")), ([guid]::NewGuid().ToString("N").Substring(0, 8))
}

$helper = Join-Path $RepositoryRoot "scripts\run_ice_monitor.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "ICE monitor helper not found: $helper" }
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Python executable not found: $PythonExecutable" }

$arguments = @(
    $helper,
    "--repo-root", $RepositoryRoot,
    "--run-id", $RunId,
    "--window-hours", $WindowHours,
    "--max-per-source", $MaxPerSource,
    "--enforce-production-preflight",
    "--branch", $SourceBranch
)

& $PythonExecutable @arguments
exit $LASTEXITCODE
