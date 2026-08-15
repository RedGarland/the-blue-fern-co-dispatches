[CmdletBinding()]
param(
    [string]$PublicationRoot = "C:\BlueFernRunner\FoodLineDailyCurrent",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$PagesBranch = "gh-pages",
    [string]$PythonExecutable = "",
    [string]$RunnerDispatchScript = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

function Write-Json {
    param([hashtable]$Payload)
    $Payload | ConvertTo-Json -Depth 8
}

function Get-PacificDate {
    [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time").ToString("yyyy-MM-dd")
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

if (-not (Test-Path -LiteralPath $PublicationRoot -PathType Container)) {
    throw "Publication root not found: $PublicationRoot"
}
$PublicationRoot = (Resolve-Path -LiteralPath $PublicationRoot).Path
if (-not $PagesRepo) {
    $PagesRepo = Join-Path $PublicationRoot "bluefern-dispatches-pages"
}
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $PublicationRoot ".venv\Scripts\python.exe"
}
if (-not $RunnerDispatchScript) {
    $RunnerDispatchScript = Join-Path $PublicationRoot "scripts\run_runner_dispatch.ps1"
}
$dailyCurrentRoot = $PublicationRoot
$today = Get-PacificDate
$proposedPath = Join-Path $dailyCurrentRoot "data\dispatches\food-line\review\proposed-editions\$today.json"
$signalReviewPath = Join-Path $dailyCurrentRoot "data\dispatches\food-line\review\signal-reviews\$today.json"
$readinessPath = Join-Path $dailyCurrentRoot "data\dispatches\food-line\review\release-readiness\$today.json"
$proposal = Read-JsonFile -Path $proposedPath
$signalReview = Read-JsonFile -Path $signalReviewPath
$readiness = Read-JsonFile -Path $readinessPath
$blueskyHandle = [bool]$env:BLUESKY_HANDLE
$blueskyPassword = [bool]$env:BLUESKY_APP_PASSWORD

$checkResult = @{
    ok = $true
    status = if ($readiness) { "release_ready" } else { "skipped_not_release_ready" }
    edition_date = $today
    source_commit = if ($readiness) { [string]$readiness.source_commit } else { $null }
    source_branch = $SourceBranch
    private_runner_root = $dailyCurrentRoot
    publication_runner = $RunnerDispatchScript
    pages_repo = $PagesRepo
    proposed_task_name = "Blue Fern Food Line Daily Publish"
    proposed_trigger = "08:30 Pacific"
    principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    bluesky_handle_available = $blueskyHandle
    bluesky_app_password_available = $blueskyPassword
    publication_capability = [bool]$readiness
    post_bluesky_enabled = [bool]$readiness
    existing_private_tasks_unchanged = $true
    proposal_path = if (Test-Path -LiteralPath $proposedPath) { $proposedPath } else { $null }
    signal_review_path = if (Test-Path -LiteralPath $signalReviewPath) { $signalReviewPath } else { $null }
    release_readiness_path = if (Test-Path -LiteralPath $readinessPath) { $readinessPath } else { $null }
}

if ($CheckOnly) {
    Write-Output (Write-Json $checkResult)
    return
}

if (-not $readiness) {
    Write-Output (Write-Json $checkResult)
    return
}

$publicationArgs = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $RunnerDispatchScript,
    "-Dispatch",
    "food-line",
    "-RepoRoot",
    $PublicationRoot,
    "-PagesRepo",
    $PagesRepo,
    "-SourceBranch",
    $SourceBranch,
    "-PagesBranch",
    $PagesBranch,
    "-Date",
    $today,
    "-Push",
    "-PostBluesky"
)

& powershell.exe @publicationArgs
exit $LASTEXITCODE
