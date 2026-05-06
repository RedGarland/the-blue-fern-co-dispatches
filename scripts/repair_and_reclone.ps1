<#
repair_and_reclone.ps1

Safe, elevated helper to backup and reclone the GitHub Pages repository used by the project.

Usage (run as Administrator):
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\repair_and_reclone.ps1
  # optionally provide a repo URL or pages dir:
  powershell -File .\scripts\repair_and_reclone.ps1 -RepoUrl 'https://github.com/RedGarland/the-blue-fern-co-dispatches' -PagesDir 'C:\path\to\pages'

This script:
- Requires Administrator privileges.
- Moves or mirrors the existing pages dir into backups/ with a timestamp.
- Attempts to take ownership and reset ACLs if needed.
- Uses robocopy to mirror-copy the folder to the backup location.
- Removes the original folder and reclones the pages repo.

CAUTION: /MIR can delete files in the destination to mirror the source. This script writes to a backup directory under the repo root.
#>

param(
    [string]$RepoUrl = 'https://github.com/RedGarland/the-blue-fern-co-dispatches',
    [string]$PagesDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'bluefern-dispatches-pages')
)

function Write-Log {
    param($msg)
    $ts = Get-Date -Format o
    $line = "[$ts] $msg"
    Write-Output $line
    if ($global:LogFile) { Add-Content -Path $global:LogFile -Value $line }
}

# Ensure elevated
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator. Exiting."
    exit 1
}

$RepoRoot = Resolve-Path (Join-Path (Split-Path -Parent $PSScriptRoot) '..')
$RepoRoot = $RepoRoot.Path

$LogDir = Join-Path $RepoRoot 'logs'
New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
$global:LogFile = Join-Path $LogDir ("repair-reclone-{0}.log" -f (Get-Date -Format yyyyMMddHHmmss))

Write-Log "Starting repair_and_reclone. RepoUrl=$RepoUrl PagesDir=$PagesDir"

if (-not (Test-Path $PagesDir)) {
    Write-Log "Pages dir not found at $PagesDir. Cloning directly."
    git clone $RepoUrl $PagesDir 2>&1 | ForEach-Object { Write-Log $_ }
    if ($LASTEXITCODE -ne 0) { Write-Log "git clone failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }
    Write-Log "Clone complete."
    exit 0
}

# Prepare backup path
$BackupDir = Join-Path $RepoRoot ('backups\bluefern-pages-' + (Get-Date -Format yyyyMMddHHmmss))
New-Item -Path (Split-Path $BackupDir -Parent) -ItemType Directory -Force | Out-Null

try {
    Write-Log "Taking ownership of $PagesDir"
    & takeown /F "$PagesDir" /R /D Y 2>&1 | ForEach-Object { Write-Log $_ }
    Write-Log "Resetting ACLs"
    & icacls "$PagesDir" /reset /T 2>&1 | ForEach-Object { Write-Log $_ }
    Write-Log "Granting current user full control"
    & icacls "$PagesDir" /grant "$env:USERNAME:(OI)(CI)F" /T 2>&1 | ForEach-Object { Write-Log $_ }

    Write-Log "Mirror-copying to backup: $BackupDir"
    # robocopy returns codes <8 on success/warnings
    & robocopy "$PagesDir" "$BackupDir" /MIR /COPYALL /R:3 /W:5 | ForEach-Object { Write-Log $_ }
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { Write-Log "robocopy failed with exit code $rc"; exit $rc }
    Write-Log "robocopy completed with code $rc"

    Write-Log "Removing original pages dir"
    Remove-Item -LiteralPath $PagesDir -Recurse -Force -ErrorAction Stop
    Write-Log "Original removed"
}
catch {
    Write-Log "Exception during backup/cleanup: $_"
    Write-Log "Aborting to avoid data loss. Please inspect $BackupDir and logs."
    exit 1
}

Write-Log "Cloning fresh pages repo from $RepoUrl into $PagesDir"
git clone $RepoUrl $PagesDir 2>&1 | ForEach-Object { Write-Log $_ }
if ($LASTEXITCODE -ne 0) { Write-Log "git clone failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }

Write-Log "Repair and reclone completed successfully."
exit 0
