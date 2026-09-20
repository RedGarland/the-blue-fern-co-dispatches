Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Phase2BCases = @(
    [ordered]@{
        Label = "Food"
        RunnerRoot = "C:\BlueFernRunner\FoodLineCurrent6"
        Dispatch = "food-line"
        Date = "2026-09-09"
        ExpectedOutcome = "NO_ACTION"
        ExpectedExitCode = 0
        EvidenceFiles = @(
            "data\dispatches\food-line\coverage-gaps\2026-09-09.json",
            "data\dispatches\food-line\historical-intake\2026-09-09\reconciliation-receipt.json"
        )
        WatchedRoots = @(
            "data\dispatches\food-line",
            "status",
            "output\review\food-line"
        )
    }
    [ordered]@{
        Label = "Care"
        RunnerRoot = "C:\BlueFernRunner\CareLineNationalCurrent8"
        Dispatch = "care-line"
        Date = "2026-09-18"
        ExpectedOutcome = "REFUSED"
        ExpectedExitCode = 3
        EvidenceFiles = @(
            "status\operational-health\care-line\2026-09-18\runs\care_line_approved_release_publication-20260918T153001Z-20956.json",
            "status\operational-health\care-line\2026-09-18\runs\care_line_collection-20260918T010001Z-11928.json",
            "status\operational-health\care-line\2026-09-18\runs\care_line_collection-20260918T140427Z-14980.json",
            "status\operational-health\care-line\2026-09-18\runs\care_line_collection-20260918T190001Z-12368.json",
            "status\operational-health\care-line\2026-09-18\runs\care_line_reviewed_event_queue-20260918T150002Z-6fb971d0.json",
            "status\operational-health\care-line\2026-09-19\runs\care_line_collection-20260919T010001Z-17256.json"
        )
        WatchedRoots = @(
            "status\operational-health\care-line",
            "data\dispatches\care-line\review",
            "logs\care-line"
        )
    }
    [ordered]@{
        Label = "Gaza"
        RunnerRoot = "C:\BlueFernRunner\GazaDispatchesCurrent6"
        Dispatch = "gaza"
        Date = "2026-09-19"
        ExpectedOutcome = "NO_ACTION"
        ExpectedExitCode = 0
        EvidenceFiles = @(
            "bluefern-dispatches-pages\gaza\status\no-updates\2026-09-19.json",
            "output\site\gaza\status\no-updates\2026-09-19.json",
            "data\dispatches\gaza\editions\2026-09-19\run_manifest.json",
            "data\dispatches\gaza\editions\2026-09-19\collection_report.json"
        )
        WatchedRoots = @(
            "data\dispatches\gaza\editions\2026-09-19",
            "output\site\gaza\status\no-updates",
            "bluefern-dispatches-pages\gaza\status\no-updates"
        )
    }
    [ordered]@{
        Label = "ICE"
        RunnerRoot = "C:\BlueFernRunner\ICEMonitorCurrent"
        Dispatch = "ice"
        Date = "2026-09-17"
        ExpectedOutcome = "NO_ACTION"
        ExpectedExitCode = 0
        EvidenceFiles = @(
            "status\operational-health\ice\2026-09-17\runs\ice_monitor-ice-monitor-20260917T041501Z-9a443763.json",
            "status\operational-health\ice\2026-09-17\runs\ice_monitor-ice-monitor-20260918T041501Z-3514f7aa.json"
        )
        WatchedRoots = @(
            "status\operational-health\ice"
        )
    }
)

function Get-FileSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$RunnerRoot,
        [Parameter(Mandatory = $true)][string[]]$EvidenceFiles
    )
    $rows = @()
    foreach ($relativePath in $EvidenceFiles) {
        $path = Join-Path $RunnerRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Missing evidence file: $path"
        }
        $item = Get-Item -LiteralPath $path
        $rows += [ordered]@{
            Path = $relativePath
            Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
            MTimeUtc = $item.LastWriteTimeUtc.ToString("o")
        }
    }
    return $rows
}

function Get-CountSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$RunnerRoot,
        [Parameter(Mandatory = $true)][string[]]$WatchedRoots
    )
    $rows = @()
    foreach ($relativePath in $WatchedRoots) {
        $path = Join-Path $RunnerRoot $relativePath
        $count = "MISSING"
        if (Test-Path -LiteralPath $path) {
            $count = [string]((Get-ChildItem -LiteralPath $path -Recurse -File -Force | Measure-Object).Count)
        }
        $rows += [ordered]@{
            Path = $relativePath
            Count = $count
        }
    }
    return $rows
}

function Get-RunnerSnapshot {
    param([Parameter(Mandatory = $true)][hashtable]$Case)
    $root = [string]$Case.RunnerRoot
    return [ordered]@{
        Head = (& git -C $root rev-parse HEAD)
        Status = @(& git -C $root status --short)
        Files = @(Get-FileSnapshot -RunnerRoot $root -EvidenceFiles ([string[]]$Case.EvidenceFiles))
        Counts = @(Get-CountSnapshot -RunnerRoot $root -WatchedRoots ([string[]]$Case.WatchedRoots))
    }
}

function Compare-RunnerSnapshot {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )
    if ($Before.Head -ne $After.Head) { return $false }
    if (($Before.Status -join "`n") -cne ($After.Status -join "`n")) { return $false }
    if ($Before.Files.Count -ne $After.Files.Count) { return $false }
    if ($Before.Counts.Count -ne $After.Counts.Count) { return $false }
    for ($i = 0; $i -lt $Before.Files.Count; $i++) {
        if ($Before.Files[$i].Path -ne $After.Files[$i].Path) { return $false }
        if ($Before.Files[$i].Hash -ne $After.Files[$i].Hash) { return $false }
        if ($Before.Files[$i].MTimeUtc -ne $After.Files[$i].MTimeUtc) { return $false }
    }
    for ($i = 0; $i -lt $Before.Counts.Count; $i++) {
        if ($Before.Counts[$i].Path -ne $After.Counts[$i].Path) { return $false }
        if ($Before.Counts[$i].Count -ne $After.Counts[$i].Count) { return $false }
    }
    return $true
}

function Invoke-Phase2BCase {
    param([Parameter(Mandatory = $true)][hashtable]$Case)
    $root = [string]$Case.RunnerRoot
    $python = Join-Path $root ".venv\Scripts\python.exe"
    $dispatchOps = Join-Path $root "scripts\dispatch_ops.py"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Missing runner Python: $python"
    }
    if (-not (Test-Path -LiteralPath $dispatchOps -PathType Leaf)) {
        throw "Missing dispatch_ops.py: $dispatchOps"
    }

    $before = Get-RunnerSnapshot -Case $Case
    $commandArgs = @(
        $dispatchOps,
        "recover",
        [string]$Case.Dispatch,
        "--date",
        [string]$Case.Date,
        "--apply",
        "--confirm",
        "VERIFY_PUBLIC_STATE",
        "--json"
    )
    Push-Location $root
    try {
        $raw = @(& $python @commandArgs 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $jsonText = $raw -join "`n"
    try {
        $payload = $jsonText | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Could not parse dispatch_ops JSON for $($Case.Label): $jsonText"
    }
    $after = Get-RunnerSnapshot -Case $Case
    $readOnly = Compare-RunnerSnapshot -Before $before -After $after

    $actual = [string]$payload.outcome
    $plannedAction = [string]$payload.planned_action
    $expected = [string]$Case.ExpectedOutcome
    $expectedExitCode = [int]$Case.ExpectedExitCode
    $result = "PASS"
    if ($actual -ne $expected) { $result = "FAIL" }
    if ($exitCode -ne $expectedExitCode) { $result = "FAIL" }
    if (-not $readOnly) { $result = "FAIL" }
    if ($plannedAction -ne "NONE" -and $plannedAction -ne "VERIFY_PUBLIC_STATE" -and $actual -ne "REFUSED") {
        $result = "FAIL"
    }

    return [ordered]@{
        Dispatch = [string]$Case.Label
        Date = [string]$Case.Date
        Expected = $expected
        Actual = $actual
        ReadOnly = $(if ($readOnly) { "PASS" } else { "FAIL" })
        Result = $result
        ExitCode = $exitCode
        RawJson = $payload
    }
}

$rows = @()
foreach ($case in $Phase2BCases) {
    try {
        $rows += Invoke-Phase2BCase -Case $case
    }
    catch {
        $rows += [ordered]@{
            Dispatch = [string]$case.Label
            Date = [string]$case.Date
            Expected = [string]$case.ExpectedOutcome
            Actual = "ERROR"
            ReadOnly = "FAIL"
            Result = "FAIL"
            ExitCode = 1
            RawJson = $null
        }
        Write-Warning $_
    }
}

$rows | Select-Object Dispatch, Date, Expected, Actual, ReadOnly, Result | Format-Table -AutoSize
if ($rows | Where-Object { $_.Result -ne "PASS" }) {
    exit 1
}
exit 0
