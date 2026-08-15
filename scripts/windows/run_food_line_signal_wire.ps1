param(
    [switch]$CheckOnly,
    [switch]$DryRun,
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$RunId
)

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$script = Join-Path $RepoRoot "scripts\run_food_line_signal_wire.py"
$args = @("--repo-root", $RepoRoot)
if ($CheckOnly) { $args += "--check-only" }
if ($DryRun) { $args += "--dry-run" }
if ($RunId) { $args += @("--run-id", $RunId) }
& $python $script @args
