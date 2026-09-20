Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$OperatorRoot = "C:\BlueFernRunner\BlueFernOperatorCurrent"
$Python = Join-Path $OperatorRoot ".venv\Scripts\python.exe"
$OperatorScript = Join-Path $OperatorRoot "scripts\blue_fern_operator.py"
$OperatorRunsRoot = Join-Path $OperatorRoot "ops\operator\runs"
$StartedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$RunDate = $StartedAt.Substring(0, 10)
$RunId = "operator-wrapper-" + $StartedAt.Replace("-", "").Replace(":", "").Replace("Z", "Z")
$StdoutPath = Join-Path $env:TEMP "$RunId.stdout.log"
$StderrPath = Join-Path $env:TEMP "$RunId.stderr.log"

function Write-FailureReceipt {
    param(
        [int]$ExitCode,
        [string]$Reason
    )
    $completedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $runDir = Join-Path $OperatorRunsRoot $RunDate
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    $receipt = [ordered]@{
        schema_version = "blue_fern_operator_run_receipt_v1"
        run_id = $RunId
        started_at = $StartedAt
        completed_at = $completedAt
        exit_code = $ExitCode
        outcome = "OPERATOR_FAILURE"
        operator_head = $null
        dispatch_summary = @()
        incident_count = 0
        new_incidents = @()
        changed_incidents = @()
        recoveries = @()
        automatic_remediations = @()
        remediation_failures = @()
        notification_required = $true
        notification_reasons = @("OPERATOR_FAILURE")
        notification = [ordered]@{
            schema_version = "blue_fern_operator_notification_v1"
            notification_required = $true
            reasons = @("OPERATOR_FAILURE")
            dispatches = @()
            incident_ids = @()
            summary = @(
                [ordered]@{
                    dispatch = "operator"
                    incident_id = $RunId
                    classification = "OPERATOR_FAILURE"
                    state = "FAILED"
                    status_state = "FAILED"
                    recovery_action = "INVESTIGATE_STATUS_EXPORT"
                    reasons = @("OPERATOR_FAILURE")
                    reason = $Reason
                }
            )
        }
    }
    $receiptPath = Join-Path $runDir "$RunId.json"
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $OperatorRoot -PathType Container)) {
    Write-FailureReceipt -ExitCode 1 -Reason "Operator checkout is missing."
    throw "Operator checkout is missing: $OperatorRoot"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-FailureReceipt -ExitCode 1 -Reason "Dedicated Operator virtualenv python is missing."
    throw "Operator virtualenv python is missing: $Python"
}
if (-not (Test-Path -LiteralPath $OperatorScript -PathType Leaf)) {
    Write-FailureReceipt -ExitCode 1 -Reason "Operator script is missing."
    throw "Operator script is missing: $OperatorScript"
}

Set-Location -LiteralPath $OperatorRoot
$process = Start-Process -FilePath $Python -ArgumentList @($OperatorScript, "check", "--json") -Wait -PassThru -NoNewWindow -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
if (Test-Path -LiteralPath $StdoutPath) {
    Get-Content -LiteralPath $StdoutPath | ForEach-Object { [Console]::Out.WriteLine($_) }
}
if (Test-Path -LiteralPath $StderrPath) {
    Get-Content -LiteralPath $StderrPath | ForEach-Object { [Console]::Error.WriteLine($_) }
}
if ($process.ExitCode -ne 0) {
    Write-FailureReceipt -ExitCode $process.ExitCode -Reason "Operator process exited non-zero."
}
exit $process.ExitCode
