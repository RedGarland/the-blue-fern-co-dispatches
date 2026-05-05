param(
    [string]$Date = "",
    [switch]$Publish,
    [string]$PagesRepo = "",
    [string]$CredentialTarget = "bluefern-smtp",
    [switch]$SmtpDebug,
    [switch]$FailIfNoCreds,
    [int]$KeepLogs = 30
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "dispatches-$Stamp.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    $line | Tee-Object -FilePath $LogFile -Append
}

function Load-SmtpCredential {
    param([string]$Target)
    function _mask([string]$s) {
        if ([string]::IsNullOrEmpty($s)) { return "" }
        if ($s.Length -le 2) { return "**" }
        return ($s.Substring(0,1) + ('*' * ([Math]::Max(0, $s.Length - 2))) + $s.Substring($s.Length -1))
    }

    $loaded = $false
    if (Get-Command Get-StoredCredential -ErrorAction SilentlyContinue) {
        try {
            $credential = Get-StoredCredential -Target $Target -ErrorAction SilentlyContinue
            if ($credential) {
                $env:SMTP_USER = $credential.UserName
                $env:SMTP_PASSWORD = $credential.GetNetworkCredential().Password
                Write-Log "Loaded SMTP credential from Windows Credential Manager target '$Target' (user: $($env:SMTP_USER))."
                $loaded = $true
            }
        } catch {
            Write-Log "Credential Manager read failed: $($_.Exception.Message)"
        }
    }

    if (-not $loaded) {
        if ($env:SMTP_USER -and $env:SMTP_PASSWORD) {
            Write-Log "Using SMTP credentials from environment (user: $($env:SMTP_USER), password: $(_mask $env:SMTP_PASSWORD))."
        } else {
            $msg = "No SMTP credentials found in Credential Manager target '$Target' or environment variables."
            if ($FailIfNoCreds) {
                Write-Log $msg
                throw $msg
            }
            Write-Log $msg + " Continuing without credentials (may fail if server requires auth)."
        }
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

    Load-SmtpCredential -Target $CredentialTarget

    $script = Join-Path $RepoRoot "scripts\run_and_notify.py"
    $args = @($script)
    if ($Date) {
        $args += @("--date", $Date)
    }
    if ($Publish) {
        $args += "--publish"
    }
    if ($PagesRepo) {
        $args += @("--pages-repo", $PagesRepo)
    }
    if ($SmtpDebug) {
        $args += "--smtp-debug"
    }

    Write-Log "Starting dispatch run."
    # Pipe combined stdout+stderr through Tee-Object so smtplib debug output
    # is both written to the logfile and streamed to the console. This avoids
    # losing or mangling SMTP debug traces that can occur with shell redirection.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $python @args 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.Exception.Message
                } else {
                    $_
                }
            } |
            Tee-Object -FilePath $LogFile -Append
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    Write-Log "Dispatch run finished with exit code $exitCode."

    Get-ChildItem -Path $LogDir -Filter "dispatches-*.log" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $KeepLogs |
        Remove-Item -Force

    exit $exitCode
} catch {
    Write-Log ("Wrapper failed: {0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message)
    exit 10
}
