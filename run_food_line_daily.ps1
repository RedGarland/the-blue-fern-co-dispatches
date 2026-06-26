param(
  [string]$Date,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = if ($env:BLUEFERN_PROJECT_ROOT) { $env:BLUEFERN_PROJECT_ROOT } else { "C:\PythonProjects\Dispatches From The Blue Fern Co" }
$LogRoot = if ($env:BLUEFERN_FOOD_LINE_LOG_ROOT) { $env:BLUEFERN_FOOD_LINE_LOG_ROOT } else { Join-Path $ProjectRoot "logs\food-line\daily_ops" }
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding($false)

function Add-FoodLineLogContent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string[]]$Lines
  )

  $writer = New-Object System.IO.StreamWriter($Path, $true, $Utf8NoBomEncoding)
  try {
    foreach ($line in $Lines) {
      $writer.WriteLine($line)
    }
  } finally {
    $writer.Dispose()
  }
}

function Write-FoodLineLogLine {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Message
  )

  $timestamp = Get-Date -Format o
  Add-FoodLineLogContent -Path $Path -Lines @("[$timestamp] $Message")
}

function Write-FoodLineLogSection {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Heading,
    [Parameter(Mandatory = $true)][string[]]$Lines
  )

  Write-FoodLineLogLine -Path $Path -Message $Heading
  if ($Lines.Count -gt 0) {
    Add-FoodLineLogContent -Path $Path -Lines $Lines
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

function Get-FoodLineCommandArgs {
  param(
    [Parameter(Mandatory = $true)][string]$DispatchScript,
    [Parameter(Mandatory = $true)][string]$EditionDate,
    [Parameter(Mandatory = $true)][bool]$DryRunRequested
  )

  $commandArgs = @(
    $DispatchScript
    "--date"
    $EditionDate
    "--collect"
    "--include-discovery-gap-summary"
  )

  if ($DryRunRequested) {
    $commandArgs += "--dry-run"
    return $commandArgs
  }

  $commandArgs += @(
    "--publish"
    "--push"
    "--post-bluesky"
    "--generate-audio"
    "--tts-provider"
    "openai"
    "--audio-format"
    "mp3"
    "--audio-model"
    "gpt-4o-mini-tts"
    "--audio-voice"
    "alloy"
  )
  return $commandArgs
}

function Get-FoodLinePayloadValue {
  param(
    [Parameter(Mandatory = $true)]$Payload,
    [Parameter(Mandatory = $true)][string]$Path
  )

  $value = $Payload
  foreach ($segment in $Path.Split('.')) {
    if ($null -eq $value) {
      return $null
    }
    $property = $value.PSObject.Properties[$segment]
    if ($null -eq $property) {
      return $null
    }
    $value = $property.Value
  }
  return $value
}

function ConvertTo-FoodLineSummaryValue {
  param(
    $Value
  )

  if ($null -eq $Value) {
    return ""
  }
  if ($Value -is [bool]) {
    return $Value.ToString().ToLowerInvariant()
  }
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
    return (($Value | ForEach-Object { "$_" }) -join ", ")
  }
  return "$Value"
}

function Write-FoodLinePayloadSummary {
  param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [Parameter(Mandatory = $true)]$Payload
  )

  $fieldMap = [ordered]@{
    "ok" = "ok"
    "edition_date" = "edition_date"
    "source_count" = "source_count"
    "public_rendered" = "public_rendered"
    "edition_mode" = "edition_mode"
    "source_freshness_status" = "source_freshness_status"
    "qualified_primary_count" = "qualified_primary_count"
    "public_signal_count" = "public_signal_count"
    "pressure_signal_count" = "pressure_signal_count"
    "food_line_publish_blocked_reason" = "food_line_publish_blocked_reason"
    "skip_reason" = "skip_reason"
    "public_url" = "public_url"
    "pages_publish_copied" = "pages_publish_copied"
    "pushed" = "pushed"
    "bluesky_status" = "bluesky_status"
    "bluesky_reason" = "bluesky_reason"
    "audio_status" = "audio_status"
    "collector_result.ok" = "collector_result.ok"
    "collector_result.source_count" = "collector_result.source_count"
    "discovery_gap_check.run" = "discovery_gap_check.run"
    "discovery_gap_likely_qualifying_count" = "discovery_gap_likely_qualifying_count"
    "discovery_gap_warning" = "discovery_gap_warning"
  }

  foreach ($entry in $fieldMap.GetEnumerator()) {
    $value = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path $entry.Value)
    Write-FoodLineLogLine -Path $LogPath -Message "summary.$($entry.Key): $value"
  }
}

function Get-FoodLineRunClassification {
  param(
    $Payload,
    [Parameter(Mandatory = $true)][bool]$DryRunRequested
  )

  if ($DryRunRequested) {
    return "DRY_RUN_COMPLETED"
  }
  if ($null -eq $Payload) {
    return "FAILED"
  }
  if ((Get-FoodLinePayloadValue -Payload $Payload -Path "public_rendered") -eq $true -and
      (Get-FoodLinePayloadValue -Payload $Payload -Path "edition_mode") -eq "no_current_update") {
    return "NO_CURRENT_UPDATE_PUBLIC_RENDERED"
  }
  if ((Get-FoodLinePayloadValue -Payload $Payload -Path "edition_mode") -eq "no_public_edition" -or
      (Get-FoodLinePayloadValue -Payload $Payload -Path "public_rendered") -eq $false) {
    return "NO_PUBLIC_EDITION"
  }
  if ((Get-FoodLinePayloadValue -Payload $Payload -Path "public_rendered") -eq $true) {
    return "PUBLISHED"
  }
  return "FAILED"
}

function Get-FoodLineConsoleSummary {
  param(
    [Parameter(Mandatory = $true)][string]$EditionDate,
    $Payload,
    [Parameter(Mandatory = $true)][bool]$DryRunRequested
  )

  if ($DryRunRequested) {
    return "Food Line dry-run completed ${EditionDate}: source_count=$(ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path 'source_count')) public_rendered=$(ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path 'public_rendered')) edition_mode=$(ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path 'edition_mode'))"
  }

  $editionMode = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "edition_mode")
  $publicRendered = Get-FoodLinePayloadValue -Payload $Payload -Path "public_rendered"
  $sourceCount = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "source_count")
  $reason = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "food_line_publish_blocked_reason")
  if (-not $reason) {
    $reason = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "skip_reason")
  }

  if ($editionMode -eq "no_current_update" -and $publicRendered -eq $true) {
    return "Food Line no-current-update ${EditionDate}: public_rendered=$(ConvertTo-FoodLineSummaryValue $publicRendered) source_count=$sourceCount reason=`"$reason`""
  }
  if ($editionMode -eq "no_public_edition" -or $publicRendered -eq $false) {
    return "Food Line no public edition ${EditionDate}: source_count=$sourceCount reason=`"$reason`""
  }

  $publicUrl = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "public_url")
  $pushed = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "pushed")
  $blueskyStatus = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "bluesky_status")
  $audioStatus = ConvertTo-FoodLineSummaryValue (Get-FoodLinePayloadValue -Payload $Payload -Path "audio_status")
  return "Food Line published ${EditionDate}: $publicUrl pushed=$pushed bluesky=$blueskyStatus audio=$audioStatus"
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
    if ($stdoutLines.Count -gt 0) {
      Add-FoodLineLogContent -Path $LogPath -Lines $stdoutLines
    }
  }
  $stderrLines = @()
  if (Test-Path -LiteralPath $stderrPath) {
    Write-FoodLineLogLine -Path $LogPath -Message "--- $Label stderr ---"
    $stderrLines = Get-Content -LiteralPath $stderrPath
    if ($stderrLines.Count -gt 0) {
      Add-FoodLineLogContent -Path $LogPath -Lines $stderrLines
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

  $jsonText = (($Lines -join [Environment]::NewLine).Trim()) -replace '^\uFEFF', ''
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

  $commandArgs = Get-FoodLineCommandArgs `
    -DispatchScript $DispatchScript `
    -EditionDate $EditionDate `
    -DryRunRequested ([bool]$DryRun)

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
  $dispatchPayload = $null
  if ($dispatchResult.StdoutLines.Count -gt 0) {
    $dispatchPayload = ConvertFrom-FoodLineJsonLines -Lines $dispatchResult.StdoutLines
    Write-FoodLinePayloadSummary -LogPath $LogPath -Payload $dispatchPayload
  }

  if ($dispatchExitCode -eq 1) {
    if ($null -eq $dispatchPayload -or
        (Get-FoodLinePayloadValue -Payload $dispatchPayload -Path "edition_mode") -ne "no_public_edition") {
      Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
      Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
      throw "Food Line dispatch run failed for $EditionDate (exit code $dispatchExitCode)"
    }
  } elseif ($dispatchExitCode -ne 0) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    throw "Food Line dispatch run failed for $EditionDate (exit code $dispatchExitCode)"
  }

  if ($null -eq $dispatchPayload) {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    throw "Food Line dispatch did not return parseable JSON for $EditionDate"
  }

  $classification = Get-FoodLineRunClassification -Payload $dispatchPayload -DryRunRequested ([bool]$DryRun)
  $consoleSummary = Get-FoodLineConsoleSummary -EditionDate $EditionDate -Payload $dispatchPayload -DryRunRequested ([bool]$DryRun)

  if (-not $dispatchPayload.ok -and $classification -ne "NO_PUBLIC_EDITION") {
    Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
    Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run failed"
    throw "Food Line dispatch returned ok=false for $EditionDate"
  }

  Write-FoodLineLogLine -Path $LogPath -Message "Python exit code: $dispatchExitCode"
  Write-FoodLineLogLine -Path $LogPath -Message "Food Line scheduled run status: $classification"
  Write-FoodLineLogLine -Path $LogPath -Message $consoleSummary
  Write-Host $consoleSummary
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
