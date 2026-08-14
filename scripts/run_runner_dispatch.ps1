param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gaza", "food-line")]
    [string]$Dispatch,
    [string]$Date = "",
    [string]$RepoRoot = "",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "",
    [string]$PagesBranch = "gh-pages",
    [string]$CredentialTarget = "bluefern-smtp",
    [switch]$CheckOnly,
    [Alias("SmokeFull")]
    [switch]$DryRunFull,
    [switch]$Push,
    [switch]$PostBluesky,
    [switch]$GenerateAudio,
    [ValidateSet("openai", "none")]
    [string]$TtsProvider = "",
    [switch]$SmtpDebug,
    [int]$KeepLogs = 30
)

$ErrorActionPreference = "Stop"
$script:LogFile = $null
$script:IntendedLogPath = $null
$script:FileLoggingAvailable = $false
$script:DurableLogWritten = $false
$script:StdoutFallbackUsed = $false
$script:FallbackLogMessages = New-Object System.Collections.Generic.List[string]

function Append-LogLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [AllowEmptyString()]
        [Parameter(Mandatory = $true)]
        [string]$Line
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
            try {
                $writer = [System.IO.StreamWriter]::new($stream, $encoding)
                try {
                    $writer.WriteLine($Line)
                    $writer.Flush()
                    return $true
                } finally {
                    $writer.Dispose()
                }
            } finally {
                $stream.Dispose()
            }
        } catch [System.UnauthorizedAccessException], [System.IO.IOException] {
            Start-Sleep -Milliseconds (200 * ($attempt + 1))
        }
    }
    return $false
}

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    if ($script:LogFile) {
        if (Append-LogLine -Path $script:LogFile -Line $line) {
            $script:FileLoggingAvailable = $true
            $script:DurableLogWritten = $true
            return
        }
        $script:LogFile = $null
        $script:FileLoggingAvailable = $false
    }

    $script:StdoutFallbackUsed = $true
    [void]$script:FallbackLogMessages.Add($line)
    if ($Dispatch -ne "food-line") {
        Write-Host $line
    }
}

function Save-CommandOutputLine {
    param([AllowEmptyString()][string]$Line)

    if ($script:LogFile) {
        if (Append-LogLine -Path $script:LogFile -Line $Line) {
            $script:FileLoggingAvailable = $true
            $script:DurableLogWritten = $true
            return
        }
        $script:LogFile = $null
        $script:FileLoggingAvailable = $false
    }
    $script:StdoutFallbackUsed = $true
    [void]$script:FallbackLogMessages.Add($Line)
    if ($Dispatch -ne "food-line") {
        Write-Host $Line
    }
}

function Write-FoodLineMachineResult {
    param(
        $Result,
        [string[]]$Errors = @()
    )

    $payload = [ordered]@{}
    if ($null -ne $Result) {
        if ($Result -is [System.Collections.IDictionary]) {
            foreach ($key in $Result.Keys) {
                $payload[[string]$key] = $Result[$key]
            }
        } else {
            foreach ($property in $Result.PSObject.Properties) {
                $payload[$property.Name] = $property.Value
            }
        }
    }
    if (-not $payload.Contains("ok")) {
        $payload["ok"] = $false
    }
    if (-not $payload.Contains("status")) {
        $payload["status"] = "wrapper_failed"
    }
    if (-not $payload.Contains("dispatch")) {
        $payload["dispatch"] = "food-line"
    }
    if ($Errors.Count -gt 0) {
        $payload["errors"] = @($Errors)
    }
    $payload["logging"] = [ordered]@{
        file_logging_available = [bool]$script:FileLoggingAvailable
        intended_log_path = [string]$script:IntendedLogPath
        stdout_fallback_used = [bool]$script:StdoutFallbackUsed
        durable_log_written = [bool]$script:DurableLogWritten
        fallback_messages = @($script:FallbackLogMessages)
    }
    [Console]::Out.WriteLine(($payload | ConvertTo-Json -Depth 100))
}

function Remove-OldRunnerLogs {
    param(
        [string]$LogDirectory,
        [int]$Keep
    )

    if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) {
        return
    }
    Get-ChildItem -Path $LogDirectory -Filter "runner-*.log" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Keep |
        Remove-Item -Force
}

function Load-SmtpCredential {
    param([string]$Target)
    $loaded = $false
    if (Get-Command Get-StoredCredential -ErrorAction SilentlyContinue) {
        try {
            $credential = Get-StoredCredential -Target $Target -ErrorAction SilentlyContinue
            if ($credential) {
                $env:SMTP_USER = $credential.UserName
                $password = $credential.GetNetworkCredential().Password
                if (-not [string]::IsNullOrEmpty($password)) {
                    $env:SMTP_PASSWORD = $password
                    Write-Log "Loaded SMTP credential from Windows Credential Manager target '$Target'."
                    $loaded = $true
                } else {
                    Remove-Item Env:SMTP_PASSWORD -ErrorAction SilentlyContinue
                    Write-Log "Credential Manager target '$Target' had no password blob; leaving SMTP_PASSWORD unset so environment or .env fallback can load it."
                }
            }
        } catch {
            Write-Log "Credential Manager read failed: $($_.Exception.Message)"
        }
    }

    if (-not $loaded) {
        if ($env:SMTP_USER -and $env:SMTP_PASSWORD) {
            Write-Log "Using SMTP credentials from environment."
        } else {
            Write-Log "SMTP credentials were not found. Gaza email reporting may fail."
        }
    }
}

function ConvertTo-JsonTailObject {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    $trimmed = $Text.TrimEnd()
    $firstJsonStart = -1
    for ($i = 0; $i -lt $trimmed.Length; $i++) {
        $char = $trimmed[$i]
        if ($char -ne '{' -and $char -ne '[') {
            continue
        }
        if ($i -eq 0 -or $trimmed[$i - 1] -eq "`n" -or $trimmed[$i - 1] -eq "`r") {
            $firstJsonStart = $i
            break
        }
    }
    if ($firstJsonStart -lt 0) {
        $braceIndex = $trimmed.IndexOf("{")
        $arrayIndex = $trimmed.IndexOf("[")
        $candidates = @($braceIndex, $arrayIndex) | Where-Object { $_ -ge 0 }
        if ($candidates.Count -eq 0) {
            return $null
        }
        $firstJsonStart = ($candidates | Measure-Object -Minimum).Minimum
    }

    try {
        return ($trimmed.Substring($firstJsonStart) | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Get-JsonField {
    param(
        $Object,
        [string]$FieldName
    )

    if ($null -eq $Object -or [string]::IsNullOrWhiteSpace($FieldName)) {
        return $null
    }

    if ($Object -is [System.Array]) {
        if ($Object.Length -eq 1) {
            return Get-JsonField -Object $Object[0] -FieldName $FieldName
        }
        return $null
    }

    if ($Object -is [System.Collections.IList] -and -not ($Object -is [string])) {
        if ($Object.Count -eq 1) {
            return Get-JsonField -Object $Object[0] -FieldName $FieldName
        }
        return $null
    }

    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($FieldName)) {
            return $Object[$FieldName]
        }
        return $null
    }

    $property = $Object.PSObject.Properties[$FieldName]
    if ($property) {
        return $property.Value
    }

    return $null
}

function Get-JsonFieldPath {
    param(
        $Object,
        [string[]]$Path
    )

    $current = $Object
    foreach ($segment in $Path) {
        $current = Get-JsonField -Object $current -FieldName $segment
        if ($null -eq $current) {
            return $null
        }
    }
    return $current
}

function Test-JsonFieldPresent {
    param(
        $Object,
        [string]$FieldName
    )

    return $null -ne (Get-JsonField -Object $Object -FieldName $FieldName)
}

function Invoke-LoggedCommand {
    param(
        [string]$Python,
        [string[]]$Arguments,
        [switch]$ParseJsonTail
    )

    Write-Log ("Running: {0} {1}" -f $Python, ($Arguments -join " "))
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $combinedOutput = @(
            & $Python @Arguments 2>&1 |
            ForEach-Object {
                $outputLine = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    [string]$_
                }
                Save-CommandOutputLine -Line $outputLine
                $outputLine
            }
        )
        $exitCode = $LASTEXITCODE
        $outputText = (($combinedOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
        $json = $null
        if ($ParseJsonTail) {
            $json = ConvertTo-JsonTailObject -Text $outputText
        }
        return [pscustomobject]@{
            ExitCode = $exitCode
            OutputText = $outputText
            Json = $json
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Invoke-LoggedGitCommand {
    param(
        [string[]]$Arguments,
        [string]$SafeDirectory = ""
    )

    $commandArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($SafeDirectory)) {
        $commandArgs += "-c"
        $commandArgs += "safe.directory=$SafeDirectory"
    }
    $commandArgs += $Arguments

    Write-Log ("Running: git {0}" -f ($commandArgs -join " "))
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $combinedOutput = @(
            & git @commandArgs 2>&1 |
            ForEach-Object {
                $outputLine = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    [string]$_
                }
                Save-CommandOutputLine -Line $outputLine
                $outputLine
            }
        )
        $exitCode = $LASTEXITCODE
        $outputText = (($combinedOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
        return [pscustomobject]@{
            ExitCode = $exitCode
            OutputText = $outputText
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Get-TrackedDirtyRepoPaths {
    param(
        [string]$Repo
    )

    $status = Invoke-LoggedGitCommand -Arguments @("-C", $Repo, "status", "--short", "--untracked-files=all") -SafeDirectory $Repo
    if ($status.ExitCode -ne 0) {
        throw "Could not inspect repo state for ${Repo}: $($status.OutputText)"
    }

    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($status.OutputText -split "\r?\n")) {
        $trimmed = $line.TrimEnd()
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }
        if ($trimmed.Length -lt 4) {
            continue
        }
        $pathText = $trimmed.Substring(3).Trim()
        if ($pathText.Contains(" -> ")) {
            $pathText = $pathText.Split(" -> ", 2)[1].Trim()
        }
        if ([string]::IsNullOrWhiteSpace($pathText)) {
            continue
        }
        $normalizedPath = $pathText -replace "/", "\"
        if ($normalizedPath.StartsWith("logs\") -or $normalizedPath.StartsWith("output\") -or $normalizedPath.StartsWith(".pytest") -or $normalizedPath.StartsWith(".venv\") -or $normalizedPath.StartsWith("bluefern-dispatches-pages\")) {
            continue
        }
        if (-not $paths.Contains($normalizedPath)) {
            [void]$paths.Add($normalizedPath)
        }
    }

    return $paths
}

function Copy-RepoSnapshotIntoClone {
    param(
        [string]$SourceRepo,
        [string]$TempRepo,
        [string]$SnapshotMessage
    )

    $dirtyPaths = Get-TrackedDirtyRepoPaths -Repo $SourceRepo
    if ($dirtyPaths.Count -eq 0) {
        return [pscustomobject]@{
            DirtyPaths = @()
            CommitCreated = $false
        }
    }

    foreach ($relativePath in $dirtyPaths) {
        $sourcePath = Join-Path $SourceRepo $relativePath
        $tempPath = Join-Path $TempRepo $relativePath
        if (Test-Path -LiteralPath $sourcePath) {
            $parent = Split-Path -Parent $tempPath
            if ($parent) {
                New-Item -ItemType Directory -Force -Path $parent | Out-Null
            }
            Copy-Item -LiteralPath $sourcePath -Destination $tempPath -Force
        } elseif (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Recurse -Force
        }
    }

    $stageArgs = @("-C", $TempRepo, "add", "-A", "--") + $dirtyPaths
    $stageResult = Invoke-LoggedGitCommand -Arguments $stageArgs
    if ($stageResult.ExitCode -ne 0) {
        throw "Could not stage isolated snapshot for ${TempRepo}: $($stageResult.OutputText)"
    }

    $commitResult = Invoke-LoggedGitCommand -Arguments @("-C", $TempRepo, "commit", "-m", $SnapshotMessage, "--no-gpg-sign")
    if ($commitResult.ExitCode -ne 0) {
        throw "Could not commit isolated snapshot for ${TempRepo}: $($commitResult.OutputText)"
    }

    return [pscustomobject]@{
        DirtyPaths = @($dirtyPaths)
        CommitCreated = $true
    }
}

function Invoke-IsolatedGazaDryRun {
    param(
        [string]$Python,
        [string]$SourceRepo,
        [string]$PagesRepo,
        [string]$Date,
        [string]$SourceBranch,
        [string]$PagesBranch
    )

    $tempWorkspace = Join-Path ([System.IO.Path]::GetTempPath()) ("bluefern-gaza-dryrun-{0}-{1}" -f $Stamp, $PID)
    if (Test-Path -LiteralPath $tempWorkspace) {
        Remove-Item -LiteralPath $tempWorkspace -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $tempWorkspace | Out-Null
    $tempSourceRepo = Join-Path $tempWorkspace "source"
    $tempPagesRepo = Join-Path $tempWorkspace "pages"

    try {
        $sourceClone = Invoke-LoggedGitCommand -SafeDirectory $SourceRepo -Arguments @(
            "clone",
            "--no-local",
            "--branch", $SourceBranch,
            "--single-branch",
            $SourceRepo,
            $tempSourceRepo
        )
        if ($sourceClone.ExitCode -ne 0) {
            throw "Isolated source clone failed: $($sourceClone.OutputText)"
        }

        $sourceSnapshot = Copy-RepoSnapshotIntoClone -SourceRepo $SourceRepo -TempRepo $tempSourceRepo -SnapshotMessage "Temporary isolated Gaza dry-run snapshot"
        $tempSourceOrigin = Join-Path $tempWorkspace "source-origin.git"
        $sourceOriginClone = Invoke-LoggedGitCommand -Arguments @("clone", "--bare", $tempSourceRepo, $tempSourceOrigin)
        if ($sourceOriginClone.ExitCode -ne 0) {
            throw "Isolated source origin clone failed: $($sourceOriginClone.OutputText)"
        }
        $sourceRemote = Invoke-LoggedGitCommand -Arguments @("-C", $tempSourceRepo, "remote", "set-url", "origin", $tempSourceOrigin)
        if ($sourceRemote.ExitCode -ne 0) {
            throw "Could not repoint isolated source origin: $($sourceRemote.OutputText)"
        }

        $pagesClone = Invoke-LoggedGitCommand -SafeDirectory $PagesRepo -Arguments @(
            "clone",
            "--no-local",
            "--branch", $PagesBranch,
            "--single-branch",
            $PagesRepo,
            $tempPagesRepo
        )
        if ($pagesClone.ExitCode -ne 0) {
            throw "Isolated Pages clone failed: $($pagesClone.OutputText)"
        }

        $pagesSnapshot = Copy-RepoSnapshotIntoClone -SourceRepo $PagesRepo -TempRepo $tempPagesRepo -SnapshotMessage "Temporary isolated Gaza dry-run pages snapshot"
        $tempPagesOrigin = Join-Path $tempWorkspace "pages-origin.git"
        $pagesOriginClone = Invoke-LoggedGitCommand -Arguments @("clone", "--bare", $tempPagesRepo, $tempPagesOrigin)
        if ($pagesOriginClone.ExitCode -ne 0) {
            throw "Isolated Pages origin clone failed: $($pagesOriginClone.OutputText)"
        }
        $pagesRemote = Invoke-LoggedGitCommand -Arguments @("-C", $tempPagesRepo, "remote", "set-url", "origin", $tempPagesOrigin)
        if ($pagesRemote.ExitCode -ne 0) {
            throw "Could not repoint isolated Pages origin: $($pagesRemote.OutputText)"
        }

        $tempOperatorScript = Join-Path $tempSourceRepo "scripts\run_gaza_daily_operator.py"
        if (-not (Test-Path -LiteralPath $tempOperatorScript -PathType Leaf)) {
            throw "Temp Gaza operator script not found: $tempOperatorScript"
        }

        $dispatchArgs = @(
            $tempOperatorScript,
            "--date", $Date,
            "--dry-run",
            "--generate-audio",
            "--allow-listing-shrink",
            "--pages-repo", $tempPagesRepo,
            "--pages-branch", $PagesBranch,
            "--expected-source-branch", $SourceBranch,
            "--tts-provider", "none"
        )

        Write-Log "Isolated Gaza dry-run workspace: $tempWorkspace"
        Write-Log "Isolated source clone: $tempSourceRepo"
        Write-Log "Isolated Pages clone: $tempPagesRepo"
        Write-Log ("Isolated source snapshot paths: {0}" -f ($sourceSnapshot.DirtyPaths.Count))
        Write-Log ("Isolated Pages snapshot paths: {0}" -f ($pagesSnapshot.DirtyPaths.Count))

        $dispatchResult = Invoke-LoggedCommand -Python $Python -Arguments $dispatchArgs -ParseJsonTail
        if ($dispatchResult.ExitCode -ne 0) {
            throw "Isolated Gaza dry-run failed with exit code $($dispatchResult.ExitCode)."
        }
        if (-not $dispatchResult.Json) {
            throw "Isolated Gaza dry-run did not return parseable JSON."
        }

        $jsonObject = $dispatchResult.Json
        if (($jsonObject -is [System.Array] -and $jsonObject.Length -eq 1) -or ($jsonObject -is [System.Collections.IList] -and -not ($jsonObject -is [string]) -and $jsonObject.Count -eq 1)) {
            $jsonObject = $jsonObject[0]
        }

        $jsonType = if ($null -eq $jsonObject) { "<null>" } else { $jsonObject.GetType().FullName }
        $rootOk = Get-JsonField -Object $jsonObject -FieldName "ok"
        $operatorStatus = Get-JsonField -Object $jsonObject -FieldName "operator_status"
        $pagesPushOk = Get-JsonField -Object $jsonObject -FieldName "pages_push_ok"
        $blueskyStatus = Get-JsonField -Object $jsonObject -FieldName "bluesky_status"
        $emailStatus = Get-JsonField -Object $jsonObject -FieldName "email_status"

        Write-Log ("DryRunFull JSON diagnostics: type={0}; ok_present={1}; operator_status_present={2}; pages_push_ok_present={3}; bluesky_status_present={4}; email_status_present={5}" -f `
            $jsonType, `
            ($null -ne $rootOk), `
            ($null -ne $operatorStatus), `
            ($null -ne $pagesPushOk), `
            ($null -ne $blueskyStatus), `
            ($null -ne $emailStatus))

        if ($null -eq $rootOk) {
            throw "DryRunFull run failed: parsed JSON missing root ok field."
        }
        if (-not [bool]$rootOk) {
            throw "DryRunFull run failed: ok=false"
        }
        if ([string]::IsNullOrWhiteSpace([string]$operatorStatus)) {
            throw "DryRunFull run failed: parsed JSON missing operator_status."
        }
        if ([string]$operatorStatus -ne "DRY_RUN_READY") {
            throw "DryRunFull run failed: operator_status=$operatorStatus"
        }
        if ($null -ne $pagesPushOk -and [bool]$pagesPushOk) {
            throw "DryRunFull run failed: pages_push_ok=true"
        }
        if ([string]::IsNullOrWhiteSpace([string]$emailStatus)) {
            throw "DryRunFull run failed: parsed JSON missing email_status."
        }
        if ([string]$emailStatus -ne "not_requested") {
            throw "DryRunFull run failed: email_status=$emailStatus"
        }
        if ([string]::IsNullOrWhiteSpace([string]$blueskyStatus)) {
            throw "DryRunFull run failed: parsed JSON missing bluesky_status."
        }
        if ([string]$blueskyStatus -eq "success") {
            throw "DryRunFull run failed: bluesky_status=success"
        }

        return [pscustomobject]@{
            ExitCode = 0
            OutputText = $dispatchResult.OutputText
            Json = $jsonObject
            TempWorkspace = $tempWorkspace
            TempSourceRepo = $tempSourceRepo
            TempPagesRepo = $tempPagesRepo
        }
    } catch {
        throw
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
            throw "RepoRoot must not be empty and wrapper location is unavailable."
        }
        $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
        $repoRootSource = "wrapper location"
    } else {
        $repoRootSource = "-RepoRoot"
    }
    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw "RepoRoot does not exist: $RepoRoot"
    }
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $LogDir = Join-Path $RepoRoot "logs"
    $script:IntendedLogPath = Join-Path $LogDir "runner-$Dispatch-$Stamp-$PID.log"
    try {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $script:LogFile = $script:IntendedLogPath
    } catch {
        $script:LogFile = $null
        $script:FileLoggingAvailable = $false
        $script:StdoutFallbackUsed = $true
        [void]$script:FallbackLogMessages.Add(("[{0}] File logging unavailable for {1}: {2}" -f (Get-Date -Format "s"), $script:IntendedLogPath, $_.Exception.Message))
    }

    if (-not $PagesRepo) {
        $PagesRepo = Join-Path $RepoRoot "bluefern-dispatches-pages"
    }
    if (-not (Test-Path -LiteralPath $PagesRepo -PathType Container)) {
        throw "Pages repo does not exist: $PagesRepo"
    }
    $PagesRepo = (Resolve-Path -LiteralPath $PagesRepo).Path

    $env:GIT_CONFIG_COUNT = "4"
    $env:GIT_CONFIG_KEY_0 = "safe.directory"
    $env:GIT_CONFIG_VALUE_0 = ($RepoRoot -replace "\\", "/")
    $env:GIT_CONFIG_KEY_1 = "safe.directory"
    $env:GIT_CONFIG_VALUE_1 = ((Join-Path $RepoRoot ".git") -replace "\\", "/")
    $env:GIT_CONFIG_KEY_2 = "safe.directory"
    $env:GIT_CONFIG_VALUE_2 = ($PagesRepo -replace "\\", "/")
    $env:GIT_CONFIG_KEY_3 = "safe.directory"
    $env:GIT_CONFIG_VALUE_3 = ((Join-Path $PagesRepo ".git") -replace "\\", "/")

    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Repository virtualenv Python not found: $python"
    }

    $runnerMaintenanceScript = Join-Path $RepoRoot "scripts\runner_repo_maintenance.py"
    if (-not (Test-Path -LiteralPath $runnerMaintenanceScript -PathType Leaf)) {
        throw "Runner maintenance script not found: $runnerMaintenanceScript"
    }

    $gazaOperatorScript = Join-Path $RepoRoot "scripts\run_gaza_daily_operator.py"
    $gazaSmokeScript = Join-Path $RepoRoot "scripts\smoke_gaza_operator.py"
    $foodLinePublicationRunnerScript = Join-Path $RepoRoot "scripts\run_food_line_publication_runner.py"
    if ($Dispatch -eq "gaza") {
        if (-not (Test-Path -LiteralPath $gazaOperatorScript -PathType Leaf)) {
            throw "Gaza operator script not found: $gazaOperatorScript"
        }
        if ($CheckOnly -and -not (Test-Path -LiteralPath $gazaSmokeScript -PathType Leaf)) {
            throw "Gaza smoke script not found: $gazaSmokeScript"
        }
    } elseif (-not (Test-Path -LiteralPath $foodLinePublicationRunnerScript -PathType Leaf)) {
        throw "Food Line publication runner not found: $foodLinePublicationRunnerScript"
    }

    Set-Location $RepoRoot

    if ($Dispatch -eq "gaza" -and [string]::IsNullOrWhiteSpace($SourceBranch)) {
        $sourceBranchNow = Invoke-LoggedGitCommand -Arguments @("-C", $RepoRoot, "branch", "--show-current") -SafeDirectory $RepoRoot
        if ($sourceBranchNow.ExitCode -ne 0) {
            throw "Could not determine source branch: $($sourceBranchNow.OutputText)"
        }
        $SourceBranch = $sourceBranchNow.OutputText.Trim()
        if ([string]::IsNullOrWhiteSpace($SourceBranch)) {
            throw "Source repo is detached; pass -SourceBranch explicitly only if detached source execution is supported."
        }
    }

    if (-not $Date) {
        if ($Dispatch -eq "food-line") {
            $Date = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time").ToString("yyyy-MM-dd")
        } else {
            $Date = Get-Date -Format "yyyy-MM-dd"
        }
    }

    $checkOnlyRequested = $PSBoundParameters.ContainsKey("CheckOnly")
    $dryRunFullRequested = $PSBoundParameters.ContainsKey("DryRunFull")
    $pushRequested = $PSBoundParameters.ContainsKey("Push")
    $postBlueskyRequested = $PSBoundParameters.ContainsKey("PostBluesky")
    $generateAudioRequested = $PSBoundParameters.ContainsKey("GenerateAudio")

    $pushEnabled = $false
    $blueskyEnabled = $false
    $audioEnabled = $false
    $resolvedTtsProvider = $null
    if ($Dispatch -eq "gaza") {
        $pushEnabled = $pushRequested
        $blueskyEnabled = $postBlueskyRequested
        $audioEnabled = [bool]($generateAudioRequested -or $dryRunFullRequested)
        if ($generateAudioRequested) {
            if ($dryRunFullRequested) {
                $resolvedTtsProvider = "none"
            } elseif ([string]::IsNullOrWhiteSpace($TtsProvider)) {
                $resolvedTtsProvider = "openai"
            } else {
                $resolvedTtsProvider = [string]$TtsProvider
            }
            Write-Log "TTS provider: $resolvedTtsProvider"
            if (-not $dryRunFullRequested) {
                if ($resolvedTtsProvider -eq "none") {
                    throw "Gaza audio was requested with -TtsProvider none. Use -TtsProvider openai and ensure OPENAI_API_KEY is available."
                }
                if ($resolvedTtsProvider -eq "openai" -and [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
                    throw "Gaza audio was requested with TTS provider openai, but OPENAI_API_KEY is missing or blank."
                }
            }
        }
    } elseif ($Dispatch -eq "food-line") {
        $pushEnabled = $pushRequested
        $blueskyEnabled = $false
        $audioEnabled = $false
    }

    Write-Log ("Resolved repo root from {0}: {1}" -f $repoRootSource, $RepoRoot)
    Write-Log "Resolved Pages repo: $PagesRepo"
    Write-Log "Selected Python path: $python"
    Write-Log "Dispatch: $Dispatch"
    Write-Log "Date: $Date"
    Write-Log "Dry-run full: $dryRunFullRequested"
    Write-Log "Push enabled: $pushEnabled"
    Write-Log "Bluesky enabled: $blueskyEnabled"
    Write-Log "Audio enabled: $audioEnabled"
    Write-Log "Check-only: $checkOnlyRequested"

    if ($Dispatch -eq "food-line") {
        foreach ($requiredParam in @("RepoRoot", "PagesRepo", "SourceBranch", "PagesBranch")) {
            if (-not $PSBoundParameters.ContainsKey($requiredParam) -or [string]::IsNullOrWhiteSpace((Get-Variable -Name $requiredParam -Scope 0).Value)) {
                throw "Food Line dispatch requires explicit -$requiredParam."
            }
        }
        foreach ($unsupportedParam in @("PostBluesky", "GenerateAudio", "SmtpDebug", "TtsProvider")) {
            if ($PSBoundParameters.ContainsKey($unsupportedParam)) {
                throw "Food Line dispatch does not support -$unsupportedParam."
            }
        }

        $foodLineCheckOnlyScript = Join-Path $RepoRoot "scripts\run_food_line_dispatch.py"
        if (-not (Test-Path -LiteralPath $foodLineCheckOnlyScript -PathType Leaf)) {
            throw "Food Line check-only script not found: $foodLineCheckOnlyScript"
        }
        $foodLinePublicationRunnerScript = Join-Path $RepoRoot "scripts\run_food_line_publication_runner.py"
        if (-not (Test-Path -LiteralPath $foodLinePublicationRunnerScript -PathType Leaf)) {
            throw "Food Line publication runner not found: $foodLinePublicationRunnerScript"
        }

        if ($CheckOnly) {
            $checkOnlyArgs = @(
                $foodLineCheckOnlyScript,
                "--date", $Date,
                "--check-only"
            )
            $checkOnlyResult = Invoke-LoggedCommand -Python $python -Arguments $checkOnlyArgs -ParseJsonTail
            if ($checkOnlyResult.ExitCode -ne 0) {
                throw "Food Line check-only gate failed with exit code $($checkOnlyResult.ExitCode)."
            }
            if (-not $checkOnlyResult.Json) {
                throw "Food Line check-only gate did not return parseable JSON."
            }

            $jsonObject = $checkOnlyResult.Json
            if (($jsonObject -is [System.Array] -and $jsonObject.Length -eq 1) -or ($jsonObject -is [System.Collections.IList] -and -not ($jsonObject -is [string]) -and $jsonObject.Count -eq 1)) {
                $jsonObject = $jsonObject[0]
            }

            $jsonType = if ($null -eq $jsonObject) { "<null>" } else { $jsonObject.GetType().FullName }
            $rootOk = Get-JsonField -Object $jsonObject -FieldName "ok"
            $releaseCandidate = Get-JsonField -Object $jsonObject -FieldName "release_candidate"
            $publicationAttempted = Get-JsonField -Object $jsonObject -FieldName "publication_attempted"
            $pagesAttempted = Get-JsonField -Object $jsonObject -FieldName "pages_attempted"

            Write-Log ("Food Line check-only gate JSON diagnostics: type={0}; root_ok_present={1}; release_candidate_present={2}; publication_attempted_present={3}; pages_attempted_present={4}" -f `
                $jsonType, `
                ($null -ne $rootOk), `
                ($null -ne $releaseCandidate), `
                ($null -ne $publicationAttempted), `
                ($null -ne $pagesAttempted))

            if ($null -eq $rootOk) {
                throw "Food Line check-only gate failed: parsed JSON missing root ok field."
            }
            if (-not [bool]$rootOk) {
                throw "Food Line check-only gate failed: ok=false"
            }
            if ($null -eq $releaseCandidate) {
                throw "Food Line check-only gate failed: parsed JSON missing release_candidate."
            }
            if (-not [bool]$releaseCandidate) {
                Write-Log "Food Line check-only gate finished with no release candidate."
                Write-FoodLineMachineResult -Result $jsonObject
                Remove-OldRunnerLogs -LogDirectory $LogDir -Keep $KeepLogs
                exit 0
            }

            $foodLineArgs = @(
                $foodLinePublicationRunnerScript,
                "--repo-root", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch,
                "--date", $Date,
                "--check-only"
            )
        } elseif ($DryRunFull) {
            $foodLineArgs = @(
                $foodLinePublicationRunnerScript,
                "--repo-root", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch,
                "--date", $Date,
                "--dry-run-full"
            )
        } else {
            $foodLineArgs = @(
                $foodLinePublicationRunnerScript,
                "--repo-root", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch,
                "--date", $Date
            )
        }
        if ($Push) {
            $foodLineArgs += "--push"
        }

        $foodLineResult = Invoke-LoggedCommand -Python $python -Arguments $foodLineArgs -ParseJsonTail
        if ($foodLineResult.ExitCode -ne 0) {
            throw "Food Line publication runner failed with exit code $($foodLineResult.ExitCode)."
        }
        if (-not $foodLineResult.Json) {
            throw "Food Line publication runner did not return parseable JSON."
        }

        $jsonObject = $foodLineResult.Json
        if (($jsonObject -is [System.Array] -and $jsonObject.Length -eq 1) -or ($jsonObject -is [System.Collections.IList] -and -not ($jsonObject -is [string]) -and $jsonObject.Count -eq 1)) {
            $jsonObject = $jsonObject[0]
        }

        $jsonType = if ($null -eq $jsonObject) { "<null>" } else { $jsonObject.GetType().FullName }
        $rootOk = Get-JsonField -Object $jsonObject -FieldName "ok"
        $status = Get-JsonField -Object $jsonObject -FieldName "status"
        $modifiedPaths = Get-JsonField -Object $jsonObject -FieldName "proposed_modified_paths"
        $deletedPaths = Get-JsonField -Object $jsonObject -FieldName "proposed_deleted_paths"
        $pushPerformed = Get-JsonField -Object $jsonObject -FieldName "push_performed"

        Write-Log ("Food Line JSON diagnostics: type={0}; root_ok_present={1}; status_present={2}; proposed_modified_paths_present={3}; proposed_deleted_paths_present={4}; push_performed_present={5}" -f `
            $jsonType, `
            ($null -ne $rootOk), `
            ($null -ne $status), `
            ($null -ne $modifiedPaths), `
            ($null -ne $deletedPaths), `
            ($null -ne $pushPerformed))

        if ($null -eq $rootOk) {
            throw "Food Line run failed: parsed JSON missing root ok field."
        }
        if (-not [bool]$rootOk) {
            throw "Food Line run failed: ok=false"
        }
        if ([string]::IsNullOrWhiteSpace([string]$status)) {
            throw "Food Line run failed: parsed JSON missing status."
        }
        if ($CheckOnly -and [string]$status -ne "check_only_success") {
            throw "Food Line check-only run failed: status=$status"
        }
        if ($DryRunFull -and [string]$status -ne "dry_run_full_success") {
            throw "Food Line dry-run run failed: status=$status"
        }
        if (-not $CheckOnly -and -not $DryRunFull -and [string]$status -ne "publication_success") {
            throw "Food Line publication run failed: status=$status"
        }

        if ($CheckOnly) {
            Write-Log "Food Line check-only validation finished with exit code $($foodLineResult.ExitCode)."
        } elseif ($DryRunFull) {
            Write-Log "Food Line isolated dry-run finished with exit code $($foodLineResult.ExitCode)."
        } else {
            Write-Log "Food Line publication finished with exit code $($foodLineResult.ExitCode)."
        }

        Write-FoodLineMachineResult -Result $jsonObject
        Remove-OldRunnerLogs -LogDirectory $LogDir -Keep $KeepLogs
        exit $foodLineResult.ExitCode
    }

    if ($dryRunFullRequested -and $Dispatch -ne "gaza") {
        throw "-DryRunFull is only supported for Gaza."
    }
    if ($dryRunFullRequested -and ($checkOnlyRequested -or $pushRequested -or $postBlueskyRequested)) {
        throw "-DryRunFull cannot be combined with -CheckOnly, -Push, or -PostBluesky."
    }
    if ($Dispatch -eq "gaza" -and -not $dryRunFullRequested) {
        Load-SmtpCredential -Target $CredentialTarget
    }

    if ($dryRunFullRequested) {
        $sourceBranchNow = Invoke-LoggedGitCommand -Arguments @("-C", $RepoRoot, "branch", "--show-current") -SafeDirectory $RepoRoot
        if ($sourceBranchNow.ExitCode -ne 0) {
            throw "Could not determine source branch: $($sourceBranchNow.OutputText)"
        }
        $pagesBranchNow = Invoke-LoggedGitCommand -Arguments @("-C", $PagesRepo, "branch", "--show-current") -SafeDirectory $PagesRepo
        if ($pagesBranchNow.ExitCode -ne 0) {
            throw "Could not determine Pages branch: $($pagesBranchNow.OutputText)"
        }
        $resolvedSourceBranch = $sourceBranchNow.OutputText.Trim()
        $resolvedPagesBranch = $pagesBranchNow.OutputText.Trim()
        if ($resolvedSourceBranch -ne $SourceBranch) {
            $sourceBranchLabel = if ([string]::IsNullOrWhiteSpace($resolvedSourceBranch)) { "<detached>" } else { $resolvedSourceBranch }
            throw "Source repo must be on $SourceBranch; found $sourceBranchLabel."
        }
        if ($resolvedPagesBranch -ne $PagesBranch) {
            $pagesBranchLabel = if ([string]::IsNullOrWhiteSpace($resolvedPagesBranch)) { "<detached>" } else { $resolvedPagesBranch }
            throw "Pages repo must be on $PagesBranch; found $pagesBranchLabel."
        }

        $dryRunFullResult = Invoke-IsolatedGazaDryRun `
            -Python $python `
            -SourceRepo $RepoRoot `
            -PagesRepo $PagesRepo `
            -Date $Date `
            -SourceBranch $SourceBranch `
            -PagesBranch $PagesBranch
        Write-Log "Runner isolated dry-run smoke finished with exit code 0."
        Write-Log "Runner isolated dry-run workspace retained at $($dryRunFullResult.TempWorkspace)"
        $exitCode = 0
        if ($checkOnlyRequested) {
            Write-Log "Runner check-only validation finished with exit code $exitCode."
        } else {
            Write-Log "Runner dispatch finished with exit code $exitCode."
        }
        Remove-OldRunnerLogs -LogDirectory $LogDir -Keep $KeepLogs
        exit $exitCode
    }

    $syncArgs = @(
        $runnerMaintenanceScript,
        "sync",
        "--source-repo", $RepoRoot,
        "--pages-repo", $PagesRepo,
        "--source-branch", $SourceBranch,
        "--pages-branch", $PagesBranch
    )
    $syncResult = Invoke-LoggedCommand -Python $python -Arguments $syncArgs -ParseJsonTail
    if ($syncResult.ExitCode -ne 0) {
        throw "Runner sync/preflight failed with exit code $($syncResult.ExitCode)."
    }
    $syncOk = Get-JsonField -Object $syncResult.Json -FieldName "ok"
    if ($null -ne $syncOk -and (-not [bool]$syncOk)) {
        $syncErrors = @(Get-JsonField -Object $syncResult.Json -FieldName "errors")
        $syncMessage = if ($syncErrors.Count -gt 0) { $syncErrors -join "; " } else { "runner_repo_maintenance.py reported ok=false" }
        throw "Runner sync/preflight reported ok=false: $syncMessage"
    } elseif ($checkOnlyRequested) {
        if ($Dispatch -eq "gaza") {
            $dispatchArgs = @(
            $gazaSmokeScript,
            "--date", $Date,
            "--source-repo", $RepoRoot,
            "--pages-repo", $PagesRepo,
            "--source-branch", $SourceBranch,
            "--pages-branch", $PagesBranch,
            "--protected-path", $script:LogFile
        )
        } else {
        $dispatchArgs = @(
            $runnerMaintenanceScript,
            "postflight",
            "--source-repo", $RepoRoot,
            "--pages-repo", $PagesRepo,
            "--source-branch", $SourceBranch,
            "--pages-branch", $PagesBranch,
            "--protected-path", $script:LogFile
        )
        }
    } elseif ($Dispatch -eq "gaza") {
        $dispatchArgs = @(
            $gazaOperatorScript,
            "--date", $Date,
            "--pages-repo", $PagesRepo,
            "--pages-branch", $PagesBranch,
            "--expected-source-branch", $SourceBranch,
            "--email-report"
        )
        if ($Push) {
            $dispatchArgs += "--push"
        }
        if ($PostBluesky) {
            $dispatchArgs += "--post-bluesky"
        }
        if ($GenerateAudio) {
            $dispatchArgs += "--generate-audio"
            $dispatchArgs += "--tts-provider"
            $dispatchArgs += $resolvedTtsProvider
        }
        if ($SmtpDebug) {
            $dispatchArgs += "--smtp-debug"
        }
    } else {
        $dispatchArgs = @(
            $foodLineScript,
            "--date", $Date,
            "--collect",
            "--audit-source-collection",
            "--publish",
            "--push",
            "--generate-audio"
        )
        if ($PostBluesky) {
            $dispatchArgs += "--post-bluesky"
        }
    }

    $dispatchResult = Invoke-LoggedCommand -Python $python -Arguments $dispatchArgs -ParseJsonTail:$checkOnlyRequested
    $exitCode = $dispatchResult.ExitCode
    if ($exitCode -ne 0) {
        throw "Runner dispatch command failed with exit code $exitCode."
    }
    if ($checkOnlyRequested -and $Dispatch -eq "gaza") {
        if (-not $dispatchResult.Json) {
            throw "CheckOnly smoke run did not return parseable JSON."
        }

        $jsonObject = $dispatchResult.Json
        if (($jsonObject -is [System.Array] -and $jsonObject.Length -eq 1) -or ($jsonObject -is [System.Collections.IList] -and -not ($jsonObject -is [string]) -and $jsonObject.Count -eq 1)) {
            $jsonObject = $jsonObject[0]
        }

        $jsonType = if ($null -eq $jsonObject) { "<null>" } else { $jsonObject.GetType().FullName }
        $rootOk = Get-JsonField -Object $jsonObject -FieldName "ok"
        $rootSmokeMode = Get-JsonField -Object $jsonObject -FieldName "smoke_mode"
        $rootOperatorStatus = Get-JsonField -Object $jsonObject -FieldName "operator_status"
        $operatorResult = Get-JsonField -Object $jsonObject -FieldName "operator_result"
        $postflightResult = Get-JsonField -Object $jsonObject -FieldName "postflight_result"
        $nestedOperatorStatus = Get-JsonFieldPath -Object $jsonObject -Path @("operator_result", "operator_status")

        Write-Log ("CheckOnly JSON diagnostics: type={0}; root_ok_present={1}; root_smoke_mode_present={2}; root_operator_status_present={3}; operator_result_present={4}; postflight_result_present={5}; nested_operator_status_present={6}" -f `
            $jsonType, `
            ($null -ne $rootOk), `
            ($null -ne $rootSmokeMode), `
            ($null -ne $rootOperatorStatus), `
            ($null -ne $operatorResult), `
            ($null -ne $postflightResult), `
            ($null -ne $nestedOperatorStatus))

        $topLevelOk = [bool]$rootOk
        $smokeMode = [string]$rootSmokeMode
        $operatorStatus = if ($null -ne $rootOperatorStatus -and -not [string]::IsNullOrWhiteSpace([string]$rootOperatorStatus)) {
            [string]$rootOperatorStatus
        } else {
            [string]$nestedOperatorStatus
        }

        if ($null -eq $rootOk) {
            throw "CheckOnly smoke run failed: parsed JSON missing root ok field."
        }
        if (-not $topLevelOk) {
            throw "CheckOnly smoke run failed: ok=false"
        }
        if ([string]::IsNullOrWhiteSpace($smokeMode)) {
            throw "CheckOnly smoke run failed: parsed JSON missing smoke_mode."
        }
        if ($smokeMode -ne "gate_only") {
            throw "CheckOnly smoke run failed: smoke_mode=$smokeMode"
        }
        if ([string]::IsNullOrWhiteSpace($operatorStatus)) {
            throw "CheckOnly smoke run failed: parsed JSON missing operator_status."
        }
        if ($operatorStatus -ne "MANUAL_SOURCE_VALID") {
            throw "CheckOnly smoke run failed: ok=$topLevelOk operator_status=$operatorStatus"
        }
    }

    if (-not $checkOnlyRequested -or $Dispatch -ne "gaza") {
        $postflightArgs = @(
            $runnerMaintenanceScript,
            "postflight",
            "--source-repo", $RepoRoot,
            "--pages-repo", $PagesRepo,
            "--source-branch", $SourceBranch,
            "--pages-branch", $PagesBranch,
            "--protected-path", $script:LogFile
        )
        $postflightResult = Invoke-LoggedCommand -Python $python -Arguments $postflightArgs -ParseJsonTail
        if ($postflightResult.ExitCode -ne 0) {
            throw "Runner postflight cleanup/check failed with exit code $($postflightResult.ExitCode)."
        }
        $postflightOk = Get-JsonField -Object $postflightResult.Json -FieldName "ok"
        if ($null -ne $postflightOk -and (-not [bool]$postflightOk)) {
            throw "Runner postflight cleanup/check reported ok=false."
        }
    }

    if ($CheckOnly) {
        Write-Log "Runner check-only validation finished with exit code $exitCode."
    } else {
        Write-Log "Runner dispatch finished with exit code $exitCode."
    }

    Remove-OldRunnerLogs -LogDirectory $LogDir -Keep $KeepLogs

    exit $exitCode
} catch {
    Write-Log ("Runner wrapper failed: {0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message)
    if ($Dispatch -eq "food-line") {
        Write-FoodLineMachineResult -Result ([ordered]@{
            ok = $false
            status = "wrapper_failed"
            mode = if ($CheckOnly) { "check_only" } elseif ($DryRunFull) { "dry_run_full" } else { "publication" }
            dispatch = "food-line"
        }) -Errors @($_.Exception.Message)
    }
    exit 10
}
