[CmdletBinding()]
param(
    [string]$RepositoryRoot = 'C:\BlueFernRunner\FoodLineCurrent6',
    [string]$TaskName = "Blue Fern Food Line Recovery",
    [string]$TaskPath = "\Blue Fern Co\"
)

$ErrorActionPreference = "Stop"

function Read-LatestTerminalRecord {
    param([Parameter(Mandatory = $true)][string]$Root)
    $logRoot = Join-Path $Root "logs\food-line\recovery-executor"
    if (-not (Test-Path -LiteralPath $logRoot)) { return $null }
    $latest = Get-ChildItem -LiteralPath $logRoot -Recurse -File -Filter "*.json" |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $latest) { return $null }
    try {
        $payload = Get-Content -Raw -LiteralPath $latest.FullName | ConvertFrom-Json
        return [ordered]@{
            path = $latest.FullName
            completed_at = $payload.completed_at
            executor_decision = $payload.executor_decision
            selected_task_key = $payload.selected_task_key
            recovery_adapter = $payload.recovery_adapter
            recovery_action_executed = [bool]$payload.execution_receipt_id
            execution_receipt_id = $payload.execution_receipt_id
            exit_classification = $payload.exit_classification
        }
    }
    catch {
        return [ordered]@{
            path = $latest.FullName
            parse_error = $_.Exception.Message
        }
    }
}

$repoRoot = if ($RepositoryRoot) { (Resolve-Path -LiteralPath $RepositoryRoot).Path } else { $RepositoryRoot }
$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
$info = if ($task) { Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName } else { $null }
$terminal = Read-LatestTerminalRecord -Root $repoRoot

$result = [ordered]@{
    schema_version = "bluefern_food_recovery_task_inspection_v1"
    task_name = $TaskName
    task_path = $TaskPath
    repository_root = $repoRoot
    exists = [bool]$task
    enabled = if ($task) { $task.State -ne "Disabled" } else { $false }
    state = if ($task) { [string]$task.State } else { $null }
    last_run_time = if ($info) { $info.LastRunTime.ToString("o") } else { $null }
    last_task_result = if ($info) { $info.LastTaskResult } else { $null }
    next_run_time = if ($info) { $info.NextRunTime.ToString("o") } else { $null }
    latest_terminal_result = $terminal
    latest_executor_decision = if ($terminal) { $terminal.executor_decision } else { $null }
    recovery_action_executed = if ($terminal) { [bool]$terminal.recovery_action_executed } else { $false }
}

$result | ConvertTo-Json -Depth 8
