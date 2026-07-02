param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gaza", "food-line")]
    [string]$Dispatch,
    [string]$Date = "",
    [string]$PagesRepo = "",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$PagesBranch = "gh-pages",
    [string]$CredentialTarget = "bluefern-smtp",
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

function Invoke-LoggedCommand {
    param(
        [string]$Python,
        [string[]]$Arguments
    )

    Write-Log ("Running: {0} {1}" -f $Python, ($Arguments -join " "))
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Python @Arguments 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    $_
                }
            } |
            Tee-Object -FilePath $LogFile -Append
        return $LASTEXITCODE
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
    $exitCode = Invoke-LoggedCommand -Python $python -Arguments $syncArgs
    if ($exitCode -ne 0) {
        throw "Runner sync/preflight failed with exit code $exitCode."
    }

    if ($Dispatch -eq "gaza") {
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

    $exitCode = Invoke-LoggedCommand -Python $python -Arguments $dispatchArgs

    $postflightArgs = @(
        "scripts\runner_repo_maintenance.py",
        "postflight",
        "--source-repo", $RepoRoot,
        "--pages-repo", $PagesRepo,
        "--source-branch", $SourceBranch,
        "--pages-branch", $PagesBranch
    )
    $postflightCode = Invoke-LoggedCommand -Python $python -Arguments $postflightArgs
    if ($postflightCode -ne 0) {
        throw "Runner postflight cleanup/check failed with exit code $postflightCode."
    }

    Write-Log "Runner dispatch finished with exit code $exitCode."

    Get-ChildItem -Path $LogDir -Filter "runner-*.log" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $KeepLogs |
        Remove-Item -Force

    exit $exitCode
} catch {
    Write-Log ("Runner wrapper failed: {0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message)
    exit 10
}
