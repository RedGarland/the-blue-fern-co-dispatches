[CmdletBinding()]
param(
    [string]$ExpectedProtectedHead = "",
    [string]$OperatorRoot = "C:\BlueFernRunner\BlueFernOperatorCurrent",
    [string]$FoodRoot = "C:\BlueFernRunner\FoodLineCurrent6",
    [string]$CareRoot = "C:\BlueFernRunner\CareLineNationalCurrent8",
    [string]$StatusCheckout = "C:\BlueFernRunner\OperationalStatusCurrent",
    [string]$SourceBranch = "add/pages-repo-default",
    [string]$PagesBranch = "gh-pages",
    [string]$EditionDate = "",
    [string]$RunId = ""
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

$StartedAt = (Get-Date).ToUniversalTime()
if (-not $EditionDate) {
    $pacific = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time")
    $EditionDate = $pacific.ToString("yyyy-MM-dd")
}
if (-not $RunId) {
    $RunId = "food-care-continuation-{0}-{1}" -f $StartedAt.ToString("yyyyMMddTHHmmssZ"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
}
$RunDate = $StartedAt.ToString("yyyy-MM-dd")
$ReceiptDir = Join-Path $OperatorRoot ("ops\operator\runs\{0}\{1}" -f $RunDate, $RunId)
$ReceiptPath = Join-Path $ReceiptDir "food-care-proof-continuation-receipt.json"
$OperatorWrapper = Join-Path $OperatorRoot "scripts\run_blue_fern_operator.ps1"
$FoodPython = Join-Path $FoodRoot ".venv\Scripts\python.exe"
$CarePython = Join-Path $CareRoot ".venv\Scripts\python.exe"

$Steps = New-Object System.Collections.Generic.List[object]
$Outcome = "STARTED"
$ExitCode = 1
$ErrorMessage = $null

function Write-Receipt {
    param([AllowNull()][object]$CompletedAt)
    New-Item -ItemType Directory -Force -Path $ReceiptDir | Out-Null

    $completedValue = $null
    if ($null -ne $CompletedAt) {
        $completedValue = [string]$CompletedAt
    }
    $errorValue = $null
    if ($null -ne $ErrorMessage) {
        $errorValue = [string]$ErrorMessage
    }
    $stepValues = @()
    foreach ($step in $Steps) {
        $stepValues += $step
    }

    $payload = [PSCustomObject]@{
        schema_version = "blue_fern_food_care_proof_continuation_v1"
        run_id = [string]$RunId
        started_at = $StartedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
        completed_at = $completedValue
        edition_date = [string]$EditionDate
        expected_protected_head = [string]$ExpectedProtectedHead
        outcome = [string]$Outcome
        exit_code = [int]$ExitCode
        error = $errorValue
        operator_root = [string]$OperatorRoot
        food_root = [string]$FoodRoot
        care_root = [string]$CareRoot
        status_checkout = [string]$StatusCheckout
        source_branch = [string]$SourceBranch
        pages_branch = [string]$PagesBranch
        steps = $stepValues
        public_side_effects = [PSCustomObject]@{
            collection_triggered = $false
            publication_triggered = $false
            pages_mutation = $false
            scheduler_mutation = $false
            cascadia_activation = $false
            evidence_deletion = $false
        }
    }
    $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
}

function Resolve-ProtectedHead {
    if ($ExpectedProtectedHead) { return $ExpectedProtectedHead }
    Push-Location -LiteralPath $OperatorRoot
    try {
        $remote = & git ls-remote origin ("refs/heads/{0}" -f $SourceBranch)
        if ($LASTEXITCODE -ne 0 -or -not $remote) { throw "Unable to resolve protected head for $SourceBranch" }
        return ($remote -split "\s+")[0]
    } finally {
        Pop-Location
    }
}

function Invoke-ContinuationStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [int[]]$AllowedExitCodes = @(0)
    )
    $stdout = Join-Path $ReceiptDir "$Name.stdout.log"
    $stderr = Join-Path $ReceiptDir "$Name.stderr.log"
    New-Item -ItemType Directory -Force -Path $ReceiptDir | Out-Null
    $started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $completed = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $step = [ordered]@{
        name = $Name
        started_at = $started
        completed_at = $completed
        file = $FilePath
        arguments = $Arguments
        working_directory = $WorkingDirectory
        stdout_path = $stdout
        stderr_path = $stderr
        exit_code = [int]$process.ExitCode
    }
    $Steps.Add($step) | Out-Null
    Write-Receipt -CompletedAt $completed
    if ($AllowedExitCodes -notcontains [int]$process.ExitCode) { throw "$Name failed with exit code $($process.ExitCode)" }
}

try {
    $ExpectedProtectedHead = Resolve-ProtectedHead
    Write-Receipt -CompletedAt $null
    if (-not (Test-Path -LiteralPath $OperatorWrapper -PathType Leaf)) { throw "Operator wrapper missing: $OperatorWrapper" }
    if (-not (Test-Path -LiteralPath $FoodPython -PathType Leaf)) { throw "Food Python missing: $FoodPython" }
    if (-not (Test-Path -LiteralPath $CarePython -PathType Leaf)) { throw "Care Python missing: $CarePython" }

    Invoke-ContinuationStep -Name "guarded_runner_sync" -WorkingDirectory $OperatorRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $OperatorWrapper, "-SyncRunners", "-ApplyRunnerSync", "-ProveStatusExport", "-ExpectedProtectedHead", $ExpectedProtectedHead)
    Invoke-ContinuationStep -Name "food_source_watch" -WorkingDirectory $FoodRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $FoodRoot "scripts\windows\run_food_line_daily_current.ps1"), "-RepositoryRoot", $FoodRoot, "-PythonExecutable", $FoodPython, "-SourceBranch", $SourceBranch, "-EditionDate", $EditionDate, "-RunId", ("food-line-proof-{0}" -f $RunId))
    Invoke-ContinuationStep -Name "food_resume" -WorkingDirectory $FoodRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $FoodRoot "scripts\windows\resume_food_line_daily_current.ps1"), "-RepositoryRoot", $FoodRoot, "-PythonExecutable", $FoodPython, "-SourceBranch", $SourceBranch, "-EditionDate", $EditionDate)
    Invoke-ContinuationStep -Name "food_current_intake" -WorkingDirectory $FoodRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $FoodRoot "scripts\windows\run_food_line_current_intake.ps1"), "-RepositoryRoot", $FoodRoot, "-PythonExecutable", $FoodPython, "-SourceBranch", $SourceBranch, "-EditionDate", $EditionDate, "-LockWaitSeconds", "300")
    Invoke-ContinuationStep -Name "care_approved_release_proof_only" -WorkingDirectory $CareRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $CareRoot "scripts\windows\run_care_line_approved_release_publication.ps1"), "-RepositoryRoot", $CareRoot, "-PagesRepo", (Join-Path $CareRoot "bluefern-dispatches-pages"), "-PythonExecutable", $CarePython, "-SourceBranch", $SourceBranch, "-PagesBranch", $PagesBranch, "-RunDate", $EditionDate, "-RunId", ("care-proof-only-{0}" -f $RunId), "-ProofOnly")
    Invoke-ContinuationStep -Name "final_operational_status_export" -WorkingDirectory $FoodRoot -FilePath "powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $FoodRoot "scripts\run_operational_status_export.ps1"), "-SourceRoot", $FoodRoot, "-StatusCheckout", $StatusCheckout, "-CareSourceRoot", $CareRoot, "-GazaSourceRoot", "C:\BlueFernRunner\GazaDispatchesCurrent6", "-IceSourceRoot", "C:\BlueFernRunner\ICEMonitorCurrent", "-Python", (Join-Path $FoodRoot ".venv\Scripts\python.exe"))

    $Outcome = "COMPLETE"
    $ExitCode = 0
} catch {
    $Outcome = "FAILED"
    $ExitCode = 1
    $ErrorMessage = $_.Exception.Message
    [Console]::Error.WriteLine($ErrorMessage)
} finally {
    Write-Receipt -CompletedAt (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
exit $ExitCode
