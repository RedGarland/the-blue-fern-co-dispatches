[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$RunDate = "",
    [switch]$IncludeManualReview,
    [switch]$ExcludePartial,
    [switch]$AllowInsecureTls,
    [int]$FetchTimeout = 20,
    [int]$MaxItemsPerSource = 25,
    [int]$ActiveQueueLimit = 150,
    [int]$LowPriorityCap = 25
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepositoryRoot) { $RepositoryRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path }
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe" }
if (-not $RunDate) {
    $pacific = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time")
    $RunDate = $pacific.ToString("yyyy-MM-dd")
}
$helper = Join-Path $RepositoryRoot "scripts\care_line_collection_scheduler.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Care Line collection scheduler helper not found: $helper" }
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Python executable not found: $PythonExecutable" }

$arguments = @(
    $helper,
    "--repo-root", $RepositoryRoot,
    "--run-date", $RunDate,
    "--branch", $SourceBranch,
    "--fetch-timeout", $FetchTimeout,
    "--max-items-per-source", $MaxItemsPerSource,
    "--active-queue-limit", $ActiveQueueLimit,
    "--low-priority-cap", $LowPriorityCap
)
if ($IncludeManualReview) { $arguments += "--include-manual-review" }
if ($ExcludePartial) { $arguments += "--exclude-partial" }
if ($AllowInsecureTls) { $arguments += "--allow-insecure-tls" }

& $PythonExecutable @arguments
exit $LASTEXITCODE
