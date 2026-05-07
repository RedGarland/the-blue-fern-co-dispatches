param(
    [string]$Date = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$ArchiveWeek,
    [string]$WeekStart,
    [string]$WeekEnd,
    [switch]$PublishPages,
    [switch]$Push,
    [switch]$EmailReport
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogPath = Join-Path $LogDir "cascadia-weekly-$Date.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot

$ArgsList = @("scripts\run_cascadia_dispatch.py", "--date", $Date, "--weekly-public")
if ($ArchiveWeek) {
    $ArgsList += @("--archive-week", $ArchiveWeek)
}
if ($WeekStart -or $WeekEnd) {
    $ArgsList += @("--week-start", $WeekStart, "--week-end", $WeekEnd)
}

& $Python @ArgsList *>&1 | Tee-Object -FilePath $LogPath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($PublishPages) {
    $PublishArgs = @(
        "scripts\publish_github_pages.py",
        "--pages-repo",
        (Join-Path $ProjectRoot "bluefern-dispatches-pages"),
        "--pages-branch",
        "gh-pages",
        "--commit",
        "--no-push"
    )
    & $Python @PublishArgs *>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if ($Push) {
    Write-Output "Push was requested, but this wrapper does not push automatically. Run git push from the Pages repo after review." | Tee-Object -FilePath $LogPath -Append
}

if ($EmailReport) {
    Write-Output "EmailReport is reserved for the existing notification framework; no weekly email hook is configured here." | Tee-Object -FilePath $LogPath -Append
}
