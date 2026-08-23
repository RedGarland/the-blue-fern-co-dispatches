[CmdletBinding()]
param(
    [string]$PublicationRoot = "",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "add/pages-repo-default",
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

function Get-UtcTimestamp {
    ([DateTime]::UtcNow.ToString("o")).Replace("+00:00", "Z")
}

function Get-PacificDate {
    [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time").ToString("yyyy-MM-dd")
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Payload
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([Guid]::NewGuid().ToString("N").Substring(0, 8)), $PID)
    $json = $Payload | ConvertTo-Json -Depth 12
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, $encoding)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PublicationRoot) {
    $PublicationRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
}
if (-not $PagesRepo) {
    $PagesRepo = Join-Path $PublicationRoot "bluefern-dispatches-pages"
}
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $PublicationRoot ".venv\Scripts\python.exe"
}
if (-not $RunnerDispatchScript) {
    $RunnerDispatchScript = Join-Path $PublicationRoot "scripts\run_runner_dispatch.ps1"
}
$PublicationRoot = (Resolve-Path -LiteralPath $PublicationRoot).Path
$today = Get-PacificDate
$runId = "{0}-{1}-{2}" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")), $PID, ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$receiptPath = Join-Path (Join-Path $PublicationRoot "status\food-line\daily-publish\scheduler-runs\$today") ("{0}-{1}.json" -f $PID, ([Guid]::NewGuid().ToString("N").Substring(0, 8)))
$proposedPath = Join-Path $PublicationRoot "data\dispatches\food-line\review\proposed-editions\$today.json"
$signalReviewPath = Join-Path $PublicationRoot "data\dispatches\food-line\review\signal-reviews\$today.json"
$readinessPath = Join-Path $PublicationRoot "data\dispatches\food-line\review\release-readiness\$today.json"
$proposal = Read-JsonFile -Path $proposedPath
$signalReview = Read-JsonFile -Path $signalReviewPath
$readiness = Read-JsonFile -Path $readinessPath
$blueskyHandle = [bool]$env:BLUESKY_HANDLE
$blueskyPassword = [bool]$env:BLUESKY_APP_PASSWORD
$startedAt = Get-UtcTimestamp
$terminalStatus = "starting"
$ok = $false
$childExitCode = $null
$errorClassification = $null
$errorMessage = $null
$publicationAttempted = $false
$receipt = [ordered]@{
    schema_version = "food_line_daily_publish_scheduler_receipt_v1"
    run_id = $runId
    task_name = "Blue Fern Food Line Daily Publish"
    task_path = "\Blue Fern Co.\"
    started_at = $startedAt
    completed_at = $null
    host = [System.Net.Dns]::GetHostName()
    process_id = $PID
    wrapper_path = $MyInvocation.MyCommand.Path
    working_directory = $PublicationRoot
    PublicationRoot = $PublicationRoot
    PagesRepo = $PagesRepo
    SourceBranch = $SourceBranch
    PagesBranch = $PagesBranch
    PythonExecutable = $PythonExecutable
    check_only = [bool]$CheckOnly
    publication_capability = [bool]$readiness
    release_ready = [bool]$readiness
    status = "starting"
    terminal_status = $null
    ok = $false
    child_exit_code = $null
    error_classification = $null
    error_message = $null
}
Write-AtomicJson -Path $receiptPath -Payload $receipt

$checkResult = @{
    ok = $true
    status = if ($readiness) { "release_ready" } else { "skipped_not_release_ready" }
    edition_date = $today
    source_commit = if ($readiness) { [string]$readiness.source_commit } else { $null }
    source_branch = $SourceBranch
    private_runner_root = $PublicationRoot
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

try {
    if ($CheckOnly) {
        $terminalStatus = if ($readiness) { "release_ready" } else { "skipped_not_release_ready" }
        $ok = $true
        Write-Output (Write-Json $checkResult)
    }
    elseif (-not $readiness) {
        $terminalStatus = "skipped_not_release_ready"
        $ok = $true
        Write-Output (Write-Json $checkResult)
    }
    else {
        $publicationAttempted = $true
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
        $childExitCode = $LASTEXITCODE
        $ok = $childExitCode -eq 0
        if ($ok) {
            $terminalStatus = "published"
        }
        else {
            $terminalStatus = "failure"
            $errorClassification = "child_nonzero_exit"
            $errorMessage = "Food Line publication child exited with code $childExitCode"
        }
    }
}
catch {
    $ok = $false
    $terminalStatus = "failure"
    $errorClassification = $_.Exception.GetType().Name
    $errorMessage = $_.Exception.Message
    throw
}
finally {
    $receipt.status = $terminalStatus
    $receipt.terminal_status = $terminalStatus
    $receipt.ok = [bool]$ok
    $receipt.completed_at = Get-UtcTimestamp
    $receipt.child_exit_code = $childExitCode
    $receipt.error_classification = $errorClassification
    $receipt.error_message = $errorMessage
    $receipt.publication_attempted = [bool]$publicationAttempted
    Write-AtomicJson -Path $receiptPath -Payload $receipt
}

if ($CheckOnly -or -not $readiness) {
    exit 0
}
if ($ok) {
    exit 0
}
if ($null -ne $childExitCode) {
    exit [int]$childExitCode
}
exit 1
