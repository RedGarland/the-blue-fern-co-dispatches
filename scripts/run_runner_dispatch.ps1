param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gaza", "food-line")]
    [string]$Dispatch,
    [string]$Date = "",
    [string]$RepoRoot = "C:\PythonProjects\Dispatches From The Blue Fern Co",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$PagesBranch = "gh-pages",
    [string]$CredentialTarget = "bluefern-smtp",
    [switch]$CheckOnly,
    [switch]$Push,
    [switch]$PostBluesky,
    [switch]$GenerateAudio,
    [switch]$SmtpDebug,
    [int]$KeepLogs = 30
)

$ErrorActionPreference = "Stop"
$script:LogFile = $null

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    if ($script:LogFile) {
        $line | Tee-Object -FilePath $script:LogFile -Append
    } else {
        Write-Host $line
    }
}

function Load-SmtpCredential {
    param([string]$Target)
    $loaded = $false
    if (Get-Command Get-StoredCredential -ErrorAction SilentlyContinue) {
        try {
            $credential = Get-StoredCredential -Target $Target -ErrorAction SilentlyContinue
            if ($credential) {
                $env:SMTP_USER = $credential.UserName
                $env:SMTP_PASSWORD = $credential.GetNetworkCredential().Password
                Write-Log "Loaded SMTP credential from Windows Credential Manager target '$Target'."
                $loaded = $true
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
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    $_
                }
            } |
            Tee-Object -FilePath $LogFile -Append
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

try {
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        throw "RepoRoot must not be empty."
    }
    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw "RepoRoot does not exist: $RepoRoot"
    }
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

    $LogDir = Join-Path $RepoRoot "logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $script:LogFile = Join-Path $LogDir "runner-$Dispatch-$Stamp.log"

    if (-not $PagesRepo) {
        $PagesRepo = Join-Path $RepoRoot "bluefern-dispatches-pages"
    }
    if (-not (Test-Path -LiteralPath $PagesRepo -PathType Container)) {
        throw "Pages repo does not exist: $PagesRepo"
    }
    $PagesRepo = (Resolve-Path -LiteralPath $PagesRepo).Path

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
    $foodLineScript = Join-Path $RepoRoot "scripts\run_food_line_dispatch.py"
    if ($Dispatch -eq "gaza") {
        if (-not (Test-Path -LiteralPath $gazaOperatorScript -PathType Leaf)) {
            throw "Gaza operator script not found: $gazaOperatorScript"
        }
        if ($CheckOnly -and -not (Test-Path -LiteralPath $gazaSmokeScript -PathType Leaf)) {
            throw "Gaza smoke script not found: $gazaSmokeScript"
        }
    } elseif (-not (Test-Path -LiteralPath $foodLineScript -PathType Leaf)) {
        throw "Food Line dispatch script not found: $foodLineScript"
    }

    Set-Location $RepoRoot

    if (-not $Date) {
        $Date = Get-Date -Format "yyyy-MM-dd"
    }

    $pushEnabled = $false
    $blueskyEnabled = $false
    $audioEnabled = $false
    if ($Dispatch -eq "gaza") {
        $pushEnabled = [bool]$Push
        $blueskyEnabled = [bool]$PostBluesky
        $audioEnabled = [bool]$GenerateAudio
    } elseif (-not $CheckOnly) {
        $pushEnabled = $true
        $blueskyEnabled = $true
        $audioEnabled = $true
    }

    Write-Log "Resolved repo root: $RepoRoot"
    Write-Log "Resolved Pages repo: $PagesRepo"
    Write-Log "Selected Python path: $python"
    Write-Log "Dispatch: $Dispatch"
    Write-Log "Date: $Date"
    Write-Log "Push enabled: $pushEnabled"
    Write-Log "Bluesky enabled: $blueskyEnabled"
    Write-Log "Audio enabled: $audioEnabled"
    Write-Log "Check-only: $([bool]$CheckOnly)"

    if ($Dispatch -eq "gaza") {
        Load-SmtpCredential -Target $CredentialTarget
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
    }

    if ($CheckOnly) {
        if ($Dispatch -eq "gaza") {
            $dispatchArgs = @(
                $gazaSmokeScript,
                "--date", $Date,
                "--source-repo", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch
            )
        } else {
            $dispatchArgs = @(
                $runnerMaintenanceScript,
                "postflight",
                "--source-repo", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch
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
            "--post-bluesky",
            "--generate-audio"
        )
    }

    $dispatchResult = Invoke-LoggedCommand -Python $python -Arguments $dispatchArgs -ParseJsonTail:$CheckOnly
    $exitCode = $dispatchResult.ExitCode
    if ($exitCode -ne 0) {
        throw "Runner dispatch command failed with exit code $exitCode."
    }
    if ($CheckOnly -and $Dispatch -eq "gaza") {
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

    if (-not $CheckOnly -or $Dispatch -ne "gaza") {
        $postflightArgs = @(
            $runnerMaintenanceScript,
            "postflight",
            "--source-repo", $RepoRoot,
            "--pages-repo", $PagesRepo,
            "--source-branch", $SourceBranch,
            "--pages-branch", $PagesBranch
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

    Get-ChildItem -Path $LogDir -Filter "runner-*.log" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $KeepLogs |
        Remove-Item -Force

    exit $exitCode
} catch {
    Write-Log ("Runner wrapper failed: {0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message)
    exit 10
}
