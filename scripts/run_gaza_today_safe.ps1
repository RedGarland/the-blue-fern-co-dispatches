param(
    [string]$Date = (Get-Date -Format 'yyyy-MM-dd'),
    [switch]$DryRun,
    [switch]$PostBluesky,
    [switch]$GenerateAudio,
    [switch]$EmailReport,
    [switch]$Push,
    [switch]$SkipAudio,
    [switch]$SkipBluesky,
    [switch]$ForcePagesRebuild,
    [switch]$ManualSourceCheckOnly,
    [switch]$PostBlueskyOnly,
    [switch]$ForceBlueskyPost,
    [switch]$ForceAudio,
    [switch]$SmtpDebug
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$PythonExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = 'python'
}
$OperatorScript = Join-Path $RepoRoot 'scripts\run_gaza_daily_operator.py'

$pythonArgs = @(
    $OperatorScript,
    '--date', $Date
)

if ($DryRun) { $pythonArgs += '--dry-run' }
if ($PostBluesky) { $pythonArgs += '--post-bluesky' }
if ($GenerateAudio) { $pythonArgs += '--generate-audio' }
if ($EmailReport) { $pythonArgs += '--email-report' }
if ($Push) { $pythonArgs += '--push' }
if ($SkipAudio) { $pythonArgs += '--skip-audio' }
if ($SkipBluesky) { $pythonArgs += '--skip-bluesky' }
if ($ForcePagesRebuild) { $pythonArgs += '--force-pages-rebuild' }
if ($ManualSourceCheckOnly) { $pythonArgs += '--manual-source-check-only' }
if ($PostBlueskyOnly) { $pythonArgs += '--post-bluesky-only' }
if ($ForceBlueskyPost) { $pythonArgs += '--force-bluesky-post' }
if ($ForceAudio) { $pythonArgs += '--force-audio' }
if ($SmtpDebug) { $pythonArgs += '--smtp-debug' }

Push-Location $RepoRoot
try {
    & $PythonExe @pythonArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
