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
    $candidateStarts = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt $trimmed.Length; $i++) {
        if ($trimmed[$i] -eq '{' -and ($i -eq 0 -or $trimmed[$i - 1] -eq "`n" -or $trimmed[$i - 1] -eq "`r")) {
            $candidateStarts.Add($i)
        }
    }
    if ($candidateStarts.Count -eq 0) {
        $candidateStarts.Add($trimmed.LastIndexOf("{"))
    }

    for ($index = $candidateStarts.Count - 1; $index -ge 0; $index--) {
        $start = $candidateStarts[$index]
        if ($start -lt 0) {
            continue
        }
        try {
            return ($trimmed.Substring($start) | ConvertFrom-Json -ErrorAction Stop)
        } catch {
            continue
        }
    }
    return $null
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
    if ($syncResult.Json -and ($syncResult.Json.PSObject.Properties.Name -contains "ok") -and (-not [bool]$syncResult.Json.ok)) {
        $syncErrors = @($syncResult.Json.errors)
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
        $operatorStatus = [string]$dispatchResult.Json.operator_status
        if (-not [bool]$dispatchResult.Json.ok -or $operatorStatus -ne "MANUAL_SOURCE_VALID") {
            throw "CheckOnly smoke run failed: ok=$($dispatchResult.Json.ok) operator_status=$operatorStatus"
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
        if ($postflightResult.Json -and ($postflightResult.Json.PSObject.Properties.Name -contains "ok") -and (-not [bool]$postflightResult.Json.ok)) {
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
