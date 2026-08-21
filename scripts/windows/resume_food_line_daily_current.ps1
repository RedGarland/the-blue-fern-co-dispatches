[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$EditionDate = "",
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
$helper = Join-Path $RepositoryRoot "scripts\food_line_daily_scheduler.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Food Line scheduler helper not found: $helper" }
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Python executable not found: $PythonExecutable" }

$arguments = @($helper, "resume", "--repo-root", $RepositoryRoot, "--python", $PythonExecutable, "--edition-date", $EditionDate, "--branch", $SourceBranch)
if ($TestMode) { $arguments += "--test-mode" }
& $PythonExecutable @arguments
exit $LASTEXITCODE
