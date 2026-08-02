[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$EditionDate = "",
    [string]$RunId = "",
    [switch]$TestMode
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepositoryRoot) { $RepositoryRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path }
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe" }
if (-not $EditionDate) {
    $pacific = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time")
    $EditionDate = $pacific.ToString("yyyy-MM-dd")
}
if (-not $RunId) {
    $RunId = "food-line-scheduled-{0}-{1}-{2}" -f $EditionDate.Replace("-", ""), ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")), ([guid]::NewGuid().ToString("N").Substring(0, 8))
}
$helper = Join-Path $RepositoryRoot "scripts\food_line_daily_scheduler.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Food Line scheduler helper not found: $helper" }
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Python executable not found: $PythonExecutable" }

$arguments = @($helper, "source-watch", "--repo-root", $RepositoryRoot, "--python", $PythonExecutable, "--edition-date", $EditionDate, "--run-id", $RunId, "--branch", $SourceBranch)
if ($TestMode) { $arguments += "--test-mode" }
& $PythonExecutable @arguments
exit $LASTEXITCODE
