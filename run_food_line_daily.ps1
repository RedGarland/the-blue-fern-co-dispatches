param(
  [string]$Date,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\PythonProjects\Dispatches From The Blue Fern Co"
$PagesRepo = Join-Path $ProjectRoot "bluefern-dispatches-pages"
$LogRoot = Join-Path $ProjectRoot "logs\food-line\daily_ops"

function Write-FoodLineLogLine {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Message
  )

  $timestamp = Get-Date -Format o
  Add-Content -LiteralPath $Path -Value "[$timestamp] $Message" -Encoding utf8
}

function Write-FoodLineLogSection {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Heading,
    [Parameter(Mandatory = $true)][string[]]$Lines
  )

  Write-FoodLineLogLine -Path $Path -Message $Heading
  foreach ($line in $Lines) {
    Add-Content -LiteralPath $Path -Value $line -Encoding utf8
  }
}

function Invoke-FoodLineLoggedProcess {
  param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
  )

  $logBase = [System.IO.Path]::GetFileNameWithoutExtension($LogPath)
  $logDir = Split-Path -Parent $LogPath
  $stdoutPath = Join-Path $logDir "$logBase.$Label.stdout.tmp"
  $stderrPath = Join-Path $logDir "$logBase.$Label.stderr.tmp"

  Write-FoodLineLogLine -Path $LogPath -Message "$Label command: `"$FilePath`" $($Arguments -join ' ')"
  Write-FoodLineLogLine -Path $LogPath -Message "$Label working_directory: $WorkingDirectory"

  try {
    $process = Start-Process `
      -FilePath $FilePath `
      -ArgumentList $Arguments `
      -WorkingDirectory $WorkingDirectory `
      -Wait `
      -PassThru `
      -RedirectStandardOutput $stdoutPath `
      -RedirectStandardError $stderrPath
  } catch {
    Write-FoodLineLogLine -Path $LogPath -Message "$Label launch_failed: $($_.Exception.Message)"
    throw
  }

  Write-FoodLineLogLine -Path $LogPath -Message "$Label exit_code: $($process.ExitCode)"
  if (Test-Path -LiteralPath $stdoutPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "--- $Label stdout ---"
    foreach ($line in Get-Content -LiteralPath $stdoutPath) {
      Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    }
  }
  if (Test-Path -LiteralPath $stderrPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "--- $Label stderr ---"
    foreach ($line in Get-Content -LiteralPath $stderrPath) {
      Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    }
  }
  Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

  return $process.ExitCode
}

try {
  if ($Date -and $Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Date must use YYYY-MM-DD."
  }

  $EditionDate = if ($Date) { $Date } else { (Get-Date).AddDays(-1).ToString("yyyy-MM-dd") }
  $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
  $DispatchScript = Join-Path $ProjectRoot "scripts\run_food_line_dispatch.py"
  $LogPath = Join-Path $LogRoot "$EditionDate.log"

  New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
  New-Item -ItemType File -Path $LogPath -Force | Out-Null

  $commandArgs = @(
    $DispatchScript
    "--date"
    $EditionDate
    "--publish"
  )
  if ($DryRun) {
    $commandArgs += "--dry-run"
  }

  Set-Content -LiteralPath $LogPath -Encoding utf8 -Value @(
    "[$(Get-Date -Format o)] Food Line daily wrapper start"
    "[$(Get-Date -Format o)] project_root: $ProjectRoot"
    "[$(Get-Date -Format o)] pages_repo: $PagesRepo"
    "[$(Get-Date -Format o)] log_path: $LogPath"
    "[$(Get-Date -Format o)] cwd: $ProjectRoot"
    "[$(Get-Date -Format o)] python_executable: $PythonExe"
    "[$(Get-Date -Format o)] command_args: $($commandArgs -join ' ')"
    "[$(Get-Date -Format o)] dry_run: $($DryRun.IsPresent)"
  )

  if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
  }

  $dispatchExitCode = Invoke-FoodLineLoggedProcess `
    -LogPath $LogPath `
    -Label "dispatch" `
    -FilePath $PythonExe `
    -Arguments $commandArgs `
    -WorkingDirectory $ProjectRoot

  if ($dispatchExitCode -eq 1) {
    Write-FoodLineLogLine -Path $LogPath -Message "dispatch result: no qualifying food-line content for $EditionDate"
    Write-Host "No qualifying food-line content for $EditionDate (exit code 1 - expected, skipping publish/push)"
    exit 0
  } elseif ($dispatchExitCode -ne 0) {
    throw "Food Line dispatch run failed for $EditionDate (exit code $dispatchExitCode)"
  }

  if ($DryRun) {
    Write-FoodLineLogLine -Path $LogPath -Message "wrapper result: dry-run completed without publish/push"
    Write-Host "Food Line dry-run completed for $EditionDate (skipping publish/push)"
    exit 0
  }

  $publishScript = Join-Path $ProjectRoot "scripts\publish_github_pages.py"
  $publishArgs = @(
    $publishScript
    "--pages-repo"
    $PagesRepo
    "--remote-url"
    "https://github.com/RedGarland/the-blue-fern-co-dispatches.git"
    "--pages-branch"
    "gh-pages"
    "--expect-date"
    $EditionDate
    "--expect-dispatch"
    "food-line"
    "--commit"
    "--no-push"
  )

  $publishExitCode = Invoke-FoodLineLoggedProcess `
    -LogPath $LogPath `
    -Label "publish" `
    -FilePath $PythonExe `
    -Arguments $publishArgs `
    -WorkingDirectory $ProjectRoot

  if ($publishExitCode -ne 0) {
    throw "Food Line Pages publish failed for $EditionDate (exit code $publishExitCode)"
  }

  $gitExitCode = Invoke-FoodLineLoggedProcess `
    -LogPath $LogPath `
    -Label "git_push" `
    -FilePath "git" `
    -Arguments @("push", "origin", "gh-pages") `
    -WorkingDirectory $PagesRepo

  if ($gitExitCode -ne 0) {
    throw "Food Line Pages push failed for $EditionDate (exit code $gitExitCode)"
  }

  Write-FoodLineLogLine -Path $LogPath -Message "wrapper result: success"
} catch {
  if ($LogPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "wrapper error: $($_.Exception.Message)"
  } else {
    Write-Host "Food Line wrapper failed before log initialization: $($_.Exception.Message)"
  }
  throw
}
