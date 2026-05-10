Set-StrictMode -Version Latest

# Determine repo root (parent of scripts directory) unless overridden
$RepoRoot = $env:REPO_ROOT
if (-not $RepoRoot) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $RepoRoot = Resolve-Path (Join-Path $ScriptDir '..')
}
$RepoRoot = $RepoRoot.Path

# Python executable (override via env var PYTHON_EXE if needed)
# Determine Python executable: prefer env override, then common repo venvs, then system python
$PythonExe = $env:PYTHON_EXE
if (-not $PythonExe) {
    $candidates = @(
        Join-Path $RepoRoot '.venv\Scripts\python.exe',
        Join-Path $RepoRoot 'venv\Scripts\python.exe',
        'py',
        'python'
    )
    foreach ($cand in $candidates) {
        if ($cand -in @('py', 'python') -or (Test-Path $cand)) { $PythonExe = $cand; break }
    }
}

$Script = Join-Path $RepoRoot 'scripts\run_and_notify.py'

# Date to run (override via DISPATCH_DATE env var for testing)
$Date = $env:DISPATCH_DATE
if (-not $Date) { $Date = (Get-Date -Format yyyy-MM-dd) }

# Pages repo path (override via PAGES_REPO env var if needed)
$PagesRepo = $env:PAGES_REPO
if (-not $PagesRepo) { $PagesRepo = Join-Path $RepoRoot 'bluefern-dispatches-pages' }

# Logging
$LogDir = Join-Path $RepoRoot 'logs'
New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
$LogFile = Join-Path $LogDir ("publish-{0}.log" -f $Date)

Write-Output "Running dispatch publish: date=$Date, pages-repo=$PagesRepo, python=$PythonExe" | Out-File -FilePath $LogFile -Append

Push-Location $RepoRoot
try {
    & $PythonExe $Script --date $Date --publish --pages-repo $PagesRepo *> $LogFile 2>&1
    $exitCode = $LASTEXITCODE
    Write-Output "Finished run with exit code $exitCode" | Out-File -FilePath $LogFile -Append
}
catch {
    Write-Output "Exception: $_" | Out-File -FilePath $LogFile -Append
    $exitCode = 1
}
finally {
    Pop-Location
}

exit $exitCode
