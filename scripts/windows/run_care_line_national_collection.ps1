[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$RunId = "",
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
if (-not $RunId) {
    $RunId = "{0}-{1}" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")), $PID
}
$helper = Join-Path $RepositoryRoot "scripts\care_line_collection_scheduler.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Care Line collection scheduler helper not found: $helper" }

function New-CareLineSchedulerRecord {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$EditionDate,
        [Parameter(Mandatory)][string]$RunIdentifier,
        [Parameter(Mandatory)][string]$Branch,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$PythonPath,
        [Parameter(Mandatory)][string]$WrapperPath,
        [Parameter(Mandatory)][string]$ChildCommandText,
        [Parameter(Mandatory)][string]$ReceiptPath,
        [Parameter(Mandatory)][string]$LogPath
    )

    $lockPath = if ($SmokeTest) {
        "status/care-line/locks/smoke/national-collection.lock"
    } else {
        "status/care-line/locks/national-collection.lock"
    }

    [ordered]@{
        schema_version = "care_line_collection_scheduler_receipt_v1"
        run_id = $RunIdentifier
        edition_date = $EditionDate
        status = "starting"
        ok = $false
        collection_only = $true
        smoke_test = [bool]$SmokeTest
        started_at = ([DateTime]::UtcNow.ToString("o").Replace("+00:00", "Z"))
        completed_at = $null
        repo_root = $Root
        working_directory = $WorkingDirectory
        source_branch = $Branch
        source_commit = $null
        principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        process_id = $PID
        wrapper_path = $WrapperPath
        python_executable = $PythonPath
        lock_path = $lockPath
        stale_lock_recovered = $false
        pipeline_exit_code = $null
        pipeline_status = $null
        pipeline_run_id = $null
        child_process_id = $null
        child_command = $ChildCommandText
        child_exit_code = $null
        child_stdout_tail = @()
        child_stderr_tail = @()
        wrapper_exception_type = $null
        wrapper_exception_message = $null
        failure_stage = $null
        run_manifest_path = ""
        review_queue_path = ""
        candidate_registry_path = ""
        log_path = $LogPath
        receipt_path = $ReceiptPath
        publication_side_effects = [ordered]@{
            proposal_approval = $false
            edition_generation = $false
            release_manifest_generation = $false
            pages_sync = $false
            public_site_updates = $false
            signal_wire_publication = $false
            social_cards = $false
            audio = $false
            podcast = $false
            map = $false
            rss_publication = $false
            bluesky_publication = $false
        }
    }
}

function Write-CareLineSchedulerRecord {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Record
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $json = $Record | ConvertTo-Json -Depth 12
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $encoding)
}

$arguments = @(
    $helper,
    "--repo-root", $RepositoryRoot,
    "--run-date", $RunDate,
    "--run-id", $RunId,
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

$logRoot = if ($SmokeTest) { Join-Path $RepositoryRoot "logs\care-line\collection-scheduler\smoke" } else { Join-Path $RepositoryRoot "logs\care-line\collection-scheduler" }
$receiptRoot = if ($SmokeTest) { Join-Path $RepositoryRoot "status\care-line\scheduler-runs\smoke" } else { Join-Path $RepositoryRoot "status\care-line\scheduler-runs" }
$logPath = Join-Path (Join-Path $logRoot $RunDate) "$RunId.log"
$receiptPath = Join-Path (Join-Path $receiptRoot $RunDate) "$RunId.json"
$initialRecord = New-CareLineSchedulerRecord `
    -Root $RepositoryRoot `
    -EditionDate $RunDate `
    -RunIdentifier $RunId `
    -Branch $SourceBranch `
    -WorkingDirectory $RepositoryRoot `
    -PythonPath $PythonExecutable `
    -WrapperPath (Join-Path $RepositoryRoot "scripts\windows\run_care_line_national_collection.ps1") `
    -ChildCommandText ($arguments -join " ") `
    -ReceiptPath $receiptPath `
    -LogPath $logPath
Write-CareLineSchedulerRecord -Path $receiptPath -Record $initialRecord
$logLines = @(
    "started_at=$($initialRecord.started_at)",
    "status=starting",
    "ok=False",
    "process_id=$PID",
    "principal=$($initialRecord.principal)",
    "working_directory=$RepositoryRoot",
    "repo_root=$RepositoryRoot",
    "python_executable=$PythonExecutable",
    "source_branch=$SourceBranch",
    "wrapper_path=$($initialRecord.wrapper_path)",
    "command=$($arguments -join ' ')",
    "child_process_id=",
    "child_exit_code="
)
$logDirectory = Split-Path -Parent $logPath
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Content -LiteralPath $logPath -Value ($logLines -join [Environment]::NewLine) -Encoding UTF8

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    $initialRecord.status = "failure"
    $initialRecord.completed_at = ([DateTime]::UtcNow.ToString("o").Replace("+00:00", "Z"))
    $initialRecord.wrapper_exception_type = "ItemNotFoundException"
    $initialRecord.wrapper_exception_message = "Python executable not found: $PythonExecutable"
    $initialRecord.failure_stage = "launch_python"
    Write-CareLineSchedulerRecord -Path $receiptPath -Record $initialRecord
    Set-Content -LiteralPath $logPath -Value @(
        "started_at=$($initialRecord.started_at)",
        "completed_at=$($initialRecord.completed_at)",
        "status=$($initialRecord.status)",
        "ok=False",
        "process_id=$PID",
        "principal=$($initialRecord.principal)",
        "working_directory=$RepositoryRoot",
        "repo_root=$RepositoryRoot",
        "python_executable=$PythonExecutable",
        "source_branch=$SourceBranch",
        "wrapper_path=$($initialRecord.wrapper_path)",
        "command=$($arguments -join ' ')",
        "wrapper_exception_type=$($initialRecord.wrapper_exception_type)",
        "wrapper_exception_message=$($initialRecord.wrapper_exception_message)",
        "failure_stage=$($initialRecord.failure_stage)"
    ) -Encoding UTF8
    throw $initialRecord.wrapper_exception_message
}

& $PythonExecutable @arguments
exit $LASTEXITCODE
