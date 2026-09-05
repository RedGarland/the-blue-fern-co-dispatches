[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PagesRepo = "",
    [string]$PythonExecutable = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$PagesBranch = "gh-pages",
    [string]$RunDate = "",
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepositoryRoot) { $RepositoryRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path }
if (-not $PagesRepo) { $PagesRepo = Join-Path $RepositoryRoot "bluefern-dispatches-pages" }
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $RepositoryRoot ".venv\Scripts\python.exe" }
if (-not $RunDate) {
    $pacific = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time")
    $RunDate = $pacific.ToString("yyyy-MM-dd")
}
if (-not $RunId) { $RunId = "{0}-{1}" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")), $PID }

$helper = Join-Path $RepositoryRoot "scripts\care_line_publication_scheduler.py"
$wrapperPath = $MyInvocation.MyCommand.Path
$receiptPath = Join-Path (Join-Path $RepositoryRoot "status\care-line\publication-scheduler-runs\$RunDate") "$RunId.json"
$logPath = Join-Path (Join-Path $RepositoryRoot "logs\care-line\publication-scheduler\$RunDate") "$RunId.log"

function Write-FailureEvidence {
    param(
        [Parameter(Mandatory)][string]$Stage,
        [Parameter(Mandatory)][string]$Message
    )
    $completedAt = ([DateTime]::UtcNow.ToString("o")).Replace("+00:00", "Z")
    $payload = [ordered]@{
        schema_version = "care_line_publication_scheduler_receipt_v1"
        run_id = $RunId
        run_date = $RunDate
        status = "failure"
        ok = $false
        completed_at = $completedAt
        repo_root = $RepositoryRoot
        pages_repo = $PagesRepo
        working_directory = $RepositoryRoot
        source_branch = $SourceBranch
        pages_branch = $PagesBranch
        wrapper_path = $wrapperPath
        python_executable = $PythonExecutable
        publication_attempted = $false
        pages_changed = $false
        source_changed = $false
        failure_stage = $Stage
        error = $Message
        receipt_path = $receiptPath
        log_path = $logPath
        unauthorized_side_effects = [ordered]@{
            editorial_approval_creation = $false
            queue_promotion = $false
            source_collection = $false
            audio = $false
            social = $false
        }
    }
    $receiptDirectory = Split-Path -Parent $receiptPath
    $logDirectory = Split-Path -Parent $logPath
    New-Item -ItemType Directory -Path $receiptDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($receiptPath, ($payload | ConvertTo-Json -Depth 10) + [Environment]::NewLine, $encoding)
    [System.IO.File]::WriteAllText($logPath, "completed_at=$completedAt`nstatus=failure`nok=False`nfailure_stage=$Stage`nerror=$Message`n", $encoding)
}

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    $message = "Python executable not found: $PythonExecutable"
    Write-FailureEvidence -Stage "launch_python" -Message $message
    Write-Error $message
    exit 1
}
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    $message = "Care Line publication scheduler helper not found: $helper"
    Write-FailureEvidence -Stage "launch_helper" -Message $message
    Write-Error $message
    exit 1
}

$arguments = @(
    $helper,
    "--repo-root", $RepositoryRoot,
    "--pages-repo", $PagesRepo,
    "--source-branch", $SourceBranch,
    "--pages-branch", $PagesBranch,
    "--run-date", $RunDate,
    "--run-id", $RunId
)

Push-Location -LiteralPath $RepositoryRoot
try {
    & $PythonExecutable @arguments
    $exitCode = $LASTEXITCODE
}
catch {
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        Write-FailureEvidence -Stage "launch_helper" -Message $_.Exception.Message
    }
    throw
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
    Write-FailureEvidence -Stage "missing_receipt" -Message "Care Line publication scheduler exited without a durable receipt."
    exit 1
}
exit $exitCode
