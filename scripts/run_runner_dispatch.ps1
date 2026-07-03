param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gaza", "food-line")]
    [string]$Dispatch,
    [string]$Date = "",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$PagesBranch = "gh-pages",
    [string]$CredentialTarget = "bluefern-smtp",
    [switch]$CheckOnly,
    [switch]$SmtpDebug,
    [int]$KeepLogs = 30
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $PagesRepo) {
    $PagesRepo = Join-Path $RepoRoot "bluefern-dispatches-pages"
}
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "runner-$Dispatch-$Stamp.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    $line | Tee-Object -FilePath $LogFile -Append
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
    Set-Location $RepoRoot

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $python = $venvPython
        Write-Log "Using repository virtualenv Python."
    } else {
        $python = "python"
        Write-Log "Repository virtualenv not found; using system Python from PATH."
    }

    if (-not $Date) {
        $Date = Get-Date -Format "yyyy-MM-dd"
    }
    if ($Dispatch -eq "gaza") {
        Load-SmtpCredential -Target $CredentialTarget
    }

    $syncArgs = @(
        "scripts\runner_repo_maintenance.py",
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
                "scripts\smoke_gaza_operator.py",
                "--date", $Date,
                "--source-repo", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch
            )
        } else {
            $dispatchArgs = @(
                "scripts\runner_repo_maintenance.py",
                "postflight",
                "--source-repo", $RepoRoot,
                "--pages-repo", $PagesRepo,
                "--source-branch", $SourceBranch,
                "--pages-branch", $PagesBranch
            )
        }
    } elseif ($Dispatch -eq "gaza") {
        $dispatchArgs = @(
            "scripts\run_gaza_daily_operator.py",
            "--date", $Date,
            "--pages-repo", $PagesRepo,
            "--pages-branch", $PagesBranch,
            "--expected-source-branch", $SourceBranch,
            "--push",
            "--post-bluesky",
            "--generate-audio",
            "--email-report"
        )
        if ($SmtpDebug) {
            $dispatchArgs += "--smtp-debug"
        }
    } else {
        $dispatchArgs = @(
            "scripts\run_food_line_dispatch.py",
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
            "scripts\runner_repo_maintenance.py",
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
