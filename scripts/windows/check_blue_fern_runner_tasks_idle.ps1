[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunnerRoot,
    [string]$TaskPath = "\Blue Fern Co.\"
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$resolvedRoot = if (Test-Path -LiteralPath $RunnerRoot) {
    (Resolve-Path -LiteralPath $RunnerRoot).Path
} else {
    $RunnerRoot
}

$tasks = @(Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue)
$matching = @()
foreach ($task in $tasks) {
    $matchesRunner = $false
    foreach ($action in @($task.Actions)) {
        $workingDirectory = [string]$action.WorkingDirectory
        $arguments = [string]$action.Arguments
        $execute = [string]$action.Execute
        if (
            ($workingDirectory -and $workingDirectory.IndexOf($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($arguments -and $arguments.IndexOf($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($execute -and $execute.IndexOf($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
        ) {
            $matchesRunner = $true
            break
        }
    }
    if ($matchesRunner) {
        $matching += [pscustomobject]@{
            task_path = [string]$task.TaskPath
            task_name = [string]$task.TaskName
            state = [string]$task.State
        }
    }
}

$running = @($matching | Where-Object { $_.state -eq "Running" })
$result = [ordered]@{
    schema_version = "bluefern_runner_task_idle_check_v1"
    runner_root = $resolvedRoot
    task_path = $TaskPath
    matching_task_count = $matching.Count
    running_task_count = $running.Count
    idle = ($matching.Count -gt 0 -and $running.Count -eq 0)
    reason = if ($matching.Count -eq 0) {
        "NO_MATCHING_SCHEDULED_TASK"
    } elseif ($running.Count -gt 0) {
        "RUNNER_TASK_RUNNING"
    } else {
        "IDLE"
    }
    tasks = @($matching)
}
$result | ConvertTo-Json -Depth 5
exit 0
