[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PythonExecutable = "",
    [string]$ExpectedBranch = "add/pages-repo-default",
    [string]$EditionDate = "",
    [string]$EvaluatedAt = ""
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"

function Get-UtcTimestamp {
    ([DateTime]::UtcNow.ToString("o")).Replace("+00:00", "Z")
}

function Get-PacificDate {
    param([DateTimeOffset]$Now = [DateTimeOffset]::UtcNow)
    [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($Now, "Pacific Standard Time").ToString("yyyy-MM-dd")
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([Guid]::NewGuid().ToString("N").Substring(0, 8)), $PID)
    $json = $Payload | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Get-SourceHead {
    param([Parameter(Mandatory = $true)][string]$Root)
    try {
        $head = & git -C $Root rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return [string]$head }
    }
    catch {}
    return $null
}

function Get-JsonFromOutput {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $trimmed = $Text.Trim()
    $start = $trimmed.IndexOf("{")
    $end = $trimmed.LastIndexOf("}")
    if ($start -lt 0 -or $end -lt $start) { return $null }
    return $trimmed.Substring($start, $end - $start + 1) | ConvertFrom-Json
}

function Get-ExitCodeForReport {
    param(
        $Report,
        [int]$ChildExitCode,
        [string]$ParseStatus
    )
    if ($ChildExitCode -ne 0) { return 1 }
    if ($ParseStatus -ne "parsed") { return 1 }
    $decision = [string]$Report.decision
    $successDecisions = @(
        "NO_CANDIDATE",
        "DRY_RUN",
        "RECOVERY_CONFIRMED",
        "RECOVERY_DEGRADED_TERMINAL",
        "STALE_PLAN_SUPPRESSED",
        "EXECUTION_DEFERRED_BACKOFF",
        "EXECUTION_DENIED_ATTEMPT_LIMIT",
        "EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED",
        "EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK"
    )
    if ($successDecisions -contains $decision) { return 0 }
    return 1
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepositoryRoot) {
    $RepositoryRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
}
$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}
$executor = Join-Path $RepositoryRoot "scripts\run_scheduled_recovery_executor.py"
if (-not (Test-Path -LiteralPath $executor -PathType Leaf)) {
    throw "Recovery executor script not found: $executor"
}
if (-not $EditionDate) { $EditionDate = Get-PacificDate }
if (-not $EvaluatedAt) { $EvaluatedAt = Get-UtcTimestamp }

$startedAt = Get-UtcTimestamp
$sourceHead = Get-SourceHead -Root $RepositoryRoot
$runId = "{0}-{1}-{2}" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")), $PID, ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$logPath = Join-Path $RepositoryRoot ("logs\food-line\recovery-executor\{0}\{1}.json" -f $EditionDate, $runId)

$arguments = @(
    $executor,
    "--dispatch", "food-line",
    "--source-root", $RepositoryRoot,
    "--date", $EditionDate,
    "--evaluated-at", $EvaluatedAt,
    "--execute",
    "--expected-branch", $ExpectedBranch
)

$stdout = ""
$stderr = ""
$childExitCode = $null
$report = $null
$parseStatus = "not_parsed"
$errorClassification = $null
$errorMessage = $null

try {
    $output = & $PythonExecutable @arguments 2>&1
    $childExitCode = $LASTEXITCODE
    $stdout = [string]::Join([Environment]::NewLine, @($output))
    if ($childExitCode -eq 0) {
        $report = Get-JsonFromOutput -Text $stdout
        if ($null -eq $report) {
            $parseStatus = "malformed_executor_json"
            $errorClassification = "malformed_executor_json"
            $errorMessage = "Executor completed without parseable JSON output."
        }
        else {
            $parseStatus = "parsed"
        }
    }
    else {
        $parseStatus = "child_nonzero_exit"
        $errorClassification = "child_nonzero_exit"
        $errorMessage = "Recovery executor exited with code $childExitCode."
    }
}
catch {
    $childExitCode = 1
    $parseStatus = "wrapper_exception"
    $errorClassification = $_.Exception.GetType().Name
    $errorMessage = $_.Exception.Message
    $stderr = $errorMessage
}

$completedAt = Get-UtcTimestamp
$decision = if ($report) { [string]$report.decision } else { $null }
$plan = if ($report) { $report.plan } else { $null }
$executionReceipt = if ($report) { $report.execution_receipt } else { $null }
$exitCode = Get-ExitCodeForReport -Report $report -ChildExitCode $childExitCode -ParseStatus $parseStatus

$terminal = [ordered]@{
    schema_version = "bluefern_food_recovery_executor_terminal_v1"
    run_id = $runId
    started_at = $startedAt
    completed_at = $completedAt
    edition_date = $EditionDate
    evaluated_at = $EvaluatedAt
    repository_root = $RepositoryRoot
    expected_branch = $ExpectedBranch
    source_head = $sourceHead
    wrapper_path = $MyInvocation.MyCommand.Path
    python_executable = $PythonExecutable
    executor_script = $executor
    dispatch = "food-line"
    execute_requested = $true
    executor_decision = $decision
    selected_task_key = if ($plan) { [string]$plan.task_key } else { $null }
    recovery_adapter = if ($plan) { [string]$plan.recovery_adapter } else { $null }
    scheduled_instance = if ($plan) { [string]$plan.scheduled_instance } else { $null }
    execution_receipt_id = if ($executionReceipt) { [string]$executionReceipt.execution_id } else { $null }
    child_exit_code = $childExitCode
    post_execution_result = if ($executionReceipt) { [string]$executionReceipt.post_execution_receipt_observation } else { $null }
    parse_status = $parseStatus
    exit_classification = if ($exitCode -eq 0) { "scheduler_success" } else { "scheduler_failure" }
    error_classification = $errorClassification
    error_message = $errorMessage
    stdout_char_count = $stdout.Length
    stderr_char_count = $stderr.Length
}

Write-AtomicJson -Path $logPath -Payload $terminal
Write-Output ($terminal | ConvertTo-Json -Depth 12)
exit $exitCode
