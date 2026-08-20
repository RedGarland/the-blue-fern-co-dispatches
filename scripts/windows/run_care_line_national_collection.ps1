[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$RunDate = "",
    [switch]$SmokeTest,
    [int]$MaxSources = 0,
    [switch]$IncludeManualReview,
    [switch]$ExcludePartial,
    [switch]$AllowInsecureTls,
    [int]$FetchTimeout = 20,
    [int]$MaxItemsPerSource = 0,
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
    "--active-queue-limit", $ActiveQueueLimit,
    "--low-priority-cap", $LowPriorityCap
)
if ($SmokeTest) {
    $arguments += "--smoke-test"
    if (-not $PSBoundParameters.ContainsKey("MaxSources") -or $MaxSources -le 0) {
        throw "Smoke-test mode requires a positive -MaxSources value."
    }
    if (-not $PSBoundParameters.ContainsKey("MaxItemsPerSource") -or $MaxItemsPerSource -le 0) {
        throw "Smoke-test mode requires a positive -MaxItemsPerSource value."
    }
    $arguments += @("--max-sources", $MaxSources, "--max-items-per-source", $MaxItemsPerSource)
} elseif ($PSBoundParameters.ContainsKey("MaxSources") -or $PSBoundParameters.ContainsKey("MaxItemsPerSource")) {
    throw "MaxSources and MaxItemsPerSource are smoke-test-only parameters."
}
if ($IncludeManualReview) { $arguments += "--include-manual-review" }
if ($ExcludePartial) { $arguments += "--exclude-partial" }
if ($AllowInsecureTls) { $arguments += "--allow-insecure-tls" }

& $PythonExecutable @arguments
exit $LASTEXITCODE
