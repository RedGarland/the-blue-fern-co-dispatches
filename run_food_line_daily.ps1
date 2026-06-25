param(
  [string]$Date,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = if ($env:BLUEFERN_PROJECT_ROOT) { $env:BLUEFERN_PROJECT_ROOT } else { "C:\PythonProjects\Dispatches From The Blue Fern Co" }
$LogRoot = if ($env:BLUEFERN_FOOD_LINE_LOG_ROOT) { $env:BLUEFERN_FOOD_LINE_LOG_ROOT } else { Join-Path $ProjectRoot "logs\food-line\daily_ops" }

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

function Format-FoodLineArgument {
  param(
    [Parameter(Mandatory = $true)][string]$Value
  )

  if ($Value -match '[\s"]') {
    return '"' + ($Value -replace '"', '\"') + '"'
  }
  return $Value
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
  $argumentLine = ($Arguments | ForEach-Object { Format-FoodLineArgument $_ }) -join ' '

  Write-FoodLineLogLine -Path $LogPath -Message "$Label command: `"$FilePath`" $argumentLine"
  Write-FoodLineLogLine -Path $LogPath -Message "$Label working_directory: $WorkingDirectory"

  try {
    $process = Start-Process `
      -FilePath $FilePath `
      -ArgumentList $argumentLine `
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
  $stdoutLines = @()
  if (Test-Path -LiteralPath $stdoutPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "--- $Label stdout ---"
    $stdoutLines = Get-Content -LiteralPath $stdoutPath
    foreach ($line in $stdoutLines) {
      Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    }
  }
  $stderrLines = @()
  if (Test-Path -LiteralPath $stderrPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "--- $Label stderr ---"
    $stderrLines = Get-Content -LiteralPath $stderrPath
    foreach ($line in $stderrLines) {
      Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    }
  }
  Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

  return @{
    ExitCode = $process.ExitCode
    StdoutLines = $stdoutLines
    StderrLines = $stderrLines
  }
}

function ConvertFrom-FoodLineJsonLines {
  param(
    [Parameter(Mandatory = $true)][string[]]$Lines
  )

  $jsonText = ($Lines -join [Environment]::NewLine).Trim()
  if (-not $jsonText) {
    throw "Dispatch output did not include JSON."
  }
  return $jsonText | ConvertFrom-Json
}

try {
  if ($Date -and $Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Date must use YYYY-MM-DD."
  }

  $EditionDate = if ($Date) { $Date } else { (Get-Date).AddDays(-1).ToString("yyyy-MM-dd") }
  $PythonExe = if ($env:BLUEFERN_PYTHON_EXE) { $env:BLUEFERN_PYTHON_EXE } else { Join-Path $ProjectRoot ".venv\Scripts\python.exe" }
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
    "[$(Get-Date -Format o)] Food Line scheduled run started"
    "[$(Get-Date -Format o)] RepoRoot: $ProjectRoot"
    "[$(Get-Date -Format o)] WorkingDirectory: $ProjectRoot"
    "[$(Get-Date -Format o)] PowerShell: $($PSVersionTable.PSVersion.ToString())"
    "[$(Get-Date -Format o)] Python executable: $PythonExe"
    "[$(Get-Date -Format o)] Command: $(($commandArgs | ForEach-Object { Format-FoodLineArgument $_ }) -join ' ')"
    "[$(Get-Date -Format o)] Date: $EditionDate"
    "[$(Get-Date -Format o)] DryRun: $($DryRun.IsPresent)"
  )

  if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
  }

  $dispatchResult = Invoke-FoodLineLoggedProcess `
    -LogPath $LogPath `
    -Label "dispatch" `
    -FilePath $PythonExe `
    -Arguments $commandArgs `
    -WorkingDirectory $ProjectRoot

  $dispatchExitCode = [int]$dispatchResult.ExitCode

  if ($dispatchExitCode -eq 1) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run completed successfully"
    Write-Host "No qualifying food-line content for $EditionDate (exit code 1 - expected, skipping publish/push)"
    exit 0
  } elseif ($dispatchExitCode -ne 0) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    throw "Food Line dispatch run failed for $EditionDate (exit code $dispatchExitCode)"
  }

  $dispatchPayload = ConvertFrom-FoodLineJsonLines -Lines $dispatchResult.StdoutLines
  if (-not $dispatchPayload.ok) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    throw "Food Line dispatch returned ok=false for $EditionDate"
  }

  if ($DryRun) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run completed successfully"
    Write-Host "Food Line dry-run completed for $EditionDate (skipping publish/push)"
    exit 0
  }

  if (($dispatchPayload.public_rendered -eq $false) -or ($dispatchPayload.edition_mode -eq "no_public_edition")) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run completed: no public edition today."
    Write-Host "Food Line scheduled run completed: no public edition today."
    exit 0
  }

  Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
  Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run completed successfully"
} catch {
  if ($LogPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    if (-not (Select-String -LiteralPath $LogPath -Pattern 'Python exit code:' -Quiet)) {
      Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: unknown"
    }
    Write-FoodLineLogLine -Path $LogPath -Message "wrapper error: $($_.Exception.Message)"
  } else {
    Write-Host "Food Line wrapper failed before log initialization: $($_.Exception.Message)"
  }
  throw
}
