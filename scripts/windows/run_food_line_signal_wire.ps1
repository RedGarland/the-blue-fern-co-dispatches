param(
    [switch]$CheckOnly,
    [switch]$DryRun,
    [switch]$PublishLive,
    [switch]$PostBluesky,
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$PagesRepo = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "bluefern-dispatches-pages"),
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$PagesBranch = "gh-pages",
    [string]$RunId
)

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$script = Join-Path $RepoRoot "scripts\run_food_line_signal_wire.py"
$args = @("--repo-root", $RepoRoot)
$args += @("--pages-repo", $PagesRepo, "--source-branch", $SourceBranch, "--pages-branch", $PagesBranch)
if ($CheckOnly) { $args += "--check-only" }
if ($DryRun) { $args += "--dry-run" }
if ($PublishLive) { $args += "--publish-live" }
if ($PostBluesky) { $args += "--post-bluesky" }
if ($RunId) { $args += @("--run-id", $RunId) }
& $python $script @args
