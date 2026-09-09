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

function Get-OperationalStatus {
    param([string]$TaskStatus)
    if ($TaskStatus -eq "published") { return "SUCCESS" }
    if ($TaskStatus -eq "skipped_not_release_ready" -or $TaskStatus -eq "no_qualifying_edition") { return "SAFE_NO_OP" }
    if ($TaskStatus -eq "failure") { return "FAILED" }
    return "UNKNOWN"
}

function Get-SourceHead {
    param([string]$Root)
    try {
        $head = & git -C $Root rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return [string]$head }
    }
    catch {}
    return $null
}

function Write-OperationalHealthReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Date,
        [Parameter(Mandatory = $true)][hashtable]$TaskReceipt,
        [Parameter(Mandatory = $true)][string]$TaskReceiptPath
    )

    $status = [string]$TaskReceipt.status
    $operational = [ordered]@{
        schema_version = "bluefern_operational_health_receipt_v1"
        dispatch = "food-line"
        task_key = "food_line_daily_publish"
        task_name = "Blue Fern Food Line Daily Publish"
        scheduled_for = $Date
        started_at = $TaskReceipt.started_at
        completed_at = $TaskReceipt.completed_at
        observed_at = $TaskReceipt.completed_at
        receipt_created_at = Get-UtcTimestamp
        exit_code = if ($TaskReceipt.ok) { 0 } elseif ($null -ne $TaskReceipt.child_exit_code) { [int]$TaskReceipt.child_exit_code } else { 1 }
        status = Get-OperationalStatus -TaskStatus $status
        classification = $status
        run_id = $TaskReceipt.run_id
        failure_stage = if ($status -eq "failure") { "daily_publish" } else { $null }
        runner_id = $TaskReceipt.host
        runner_path = $Root
        branch = $SourceBranch
        source_head = Get-SourceHead -Root $Root
        next_expected_run = $null
        public_side_effects = @{}
        collection_health = $null
        upstream_dependency_status = $null
        publication_attempted = [bool]$TaskReceipt.publication_attempted
        publication_status = $status
        artifact_refs = @{ task_receipt = $TaskReceiptPath }
        operator_attention_ref = $null
        details = @{
            legacy_schema_version = $TaskReceipt.schema_version
            check_only = [bool]$TaskReceipt.check_only
            release_ready = [bool]$TaskReceipt.release_ready
        }
    }
    $base = Join-Path $Root "status\operational-health\food-line\$Date"
    $runPath = Join-Path (Join-Path $base "runs") ("food_line_daily_publish-{0}.json" -f (($TaskReceipt.run_id -replace '[^A-Za-z0-9_.-]', '-')))
    $latestPath = Join-Path $base "latest.json"
    Write-AtomicJson -Path $runPath -Payload $operational
    Write-AtomicJson -Path $latestPath -Payload $operational
    return $runPath
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
$publicationShell = "powershell.exe"
if (-not (Get-Command -Name $publicationShell -ErrorAction SilentlyContinue)) {
    $publicationShell = "pwsh"
}
$startedAt = Get-UtcTimestamp
$principal = $null
try {
    $principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
}
catch {
    $principal = [System.Environment]::UserName
}
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
    principal = $principal
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

        & $publicationShell @publicationArgs
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
    $operationalReceiptPath = Write-OperationalHealthReceipt -Root $PublicationRoot -Date $today -TaskReceipt $receipt -TaskReceiptPath $receiptPath
    $receipt.operational_health_receipt_path = $operationalReceiptPath
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
