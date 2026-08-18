[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [int]$MaxEvents = 5
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$derivedRoot = (Resolve-Path (Join-Path $scriptRoot "..") -ErrorAction Stop).Path
$repoRoot = if ($RepositoryRoot) { (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).Path } else { $derivedRoot }
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "scripts\run_care_line_reviewed_event_queue.py"))) {
    throw "Repository root does not contain the Care Line queue runner: $repoRoot"
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $python) {
    try { $null = & $python -c "import sqlalchemy" 2>&1 } catch { $LASTEXITCODE = 1 }
    if ($LASTEXITCODE -ne 0) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
} else { $python = (Get-Command python.exe -ErrorAction Stop).Source }
& $python (Join-Path $repoRoot "scripts\run_care_line_reviewed_event_queue.py") --repo-root $repoRoot --max-events $MaxEvents
exit $LASTEXITCODE
