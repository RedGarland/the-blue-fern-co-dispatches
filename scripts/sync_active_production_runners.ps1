[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$ProveStatusExport,
    [string]$TargetBranch = "add/pages-repo-default",
    [string]$ExpectedProtectedHead = "",
    [string]$ReportPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Targets = @(
    [ordered]@{ Dispatch = "operator"; Root = "C:\BlueFernRunner\BlueFernOperatorCurrent" },
    [ordered]@{ Dispatch = "food-line"; Root = "C:\BlueFernRunner\FoodLineCurrent6" },
    [ordered]@{ Dispatch = "care-line"; Root = "C:\BlueFernRunner\CareLineNationalCurrent8" },
    [ordered]@{ Dispatch = "gaza"; Root = "C:\BlueFernRunner\GazaDispatchesCurrent6" },
    [ordered]@{ Dispatch = "ice"; Root = "C:\BlueFernRunner\ICEMonitorCurrent" }
)

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell promotes native-process stderr to ErrorRecord objects.
        # With the script-wide preference set to Stop, benign Git progress such as
        # "From https://..." can otherwise terminate a successful fetch before
        # $LASTEXITCODE is inspected.
        $ErrorActionPreference = "Continue"
        $output = @(& git -C $Root @Arguments 2>&1)
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if (-not $AllowFailure -and $code -ne 0) {
        throw ('git -C "{0}" {1} failed ({2}): {3}' -f $Root, ($Arguments -join ' '), $code, ($output -join ' '))
    }
    return [pscustomobject]@{
        ExitCode = $code
        Output = ($output -join "`n").Trim()
    }
}

function Normalize-GitPath {
    param([string]$Line)
    if (-not $Line) { return "" }
    $text = $Line.Replace("\\", "/").Trim()
    if ($text.Contains(" -> ")) {
        $parts = $text -split " -> ", 2
        $text = $parts[-1]
    }
    return $text.Trim()
}

function Get-StatusPaths {
    param([Parameter(Mandatory = $true)][string]$Root)
    $status = Invoke-Git -Root $Root -Arguments @("status", "--porcelain=v1", "--untracked-files=all")
    $tracked = @()
    $untracked = @()
    foreach ($raw in @($status.Output -split "`r?`n")) {
        if (-not $raw -or $raw.Length -lt 4) { continue }
        $state = $raw.Substring(0, 2)
        $path = Normalize-GitPath $raw.Substring(3)
        if ($state -eq "??") {
            $untracked += $path
        }
        else {
            $tracked += $path
        }
    }
    return [pscustomobject]@{
        Tracked = @($tracked | Sort-Object -Unique)
        Untracked = @($untracked | Sort-Object -Unique)
    }
}

function Get-IncomingPaths {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$TargetRef
    )
    $diff = Invoke-Git -Root $Root -Arguments @("diff", "--name-only", "HEAD..$TargetRef")
    return @(
        $diff.Output -split "`r?`n" |
            ForEach-Object { Normalize-GitPath $_ } |
            Where-Object { $_ } |
            Sort-Object -Unique
    )
}

function Get-UntrackedCollisions {
    param(
        [string[]]$Untracked,
        [string[]]$Incoming
    )
    $collisions = @()
    foreach ($u in $Untracked) {
        foreach ($i in $Incoming) {
            if ($u -eq $i -or $u.StartsWith("$i/") -or $i.StartsWith("$u/")) {
                $collisions += $u
                break
            }
        }
    }
    return @($collisions | Sort-Object -Unique)
}

function Invoke-RunnerValidation {
    param([Parameter(Mandatory = $true)][string]$Root)

    $python = Join-Path $Root ".venv\Scripts\python.exe"
    $preflight = Join-Path $Root "scripts\preflight_repo_state.py"
    $doctor = Join-Path $Root "scripts\doctor.py"

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        return [pscustomobject]@{ Ok = $false; Stage = "validation_capability"; Message = "missing $python" }
    }
    if (-not (Test-Path -LiteralPath $preflight -PathType Leaf)) {
        return [pscustomobject]@{ Ok = $false; Stage = "validation_capability"; Message = "missing $preflight" }
    }
    if (-not (Test-Path -LiteralPath $doctor -PathType Leaf)) {
        return [pscustomobject]@{ Ok = $false; Stage = "validation_capability"; Message = "missing $doctor" }
    }

    $preflightOutput = @(& $python $preflight --source-repo $Root 2>&1)
    $preflightCode = $LASTEXITCODE
    if ($preflightCode -ne 0) {
        return [pscustomobject]@{
            Ok = $false
            Stage = "preflight"
            Message = ($preflightOutput -join "`n")
        }
    }

    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = "src"
        Push-Location $Root
        try {
            $doctorOutput = @(& $python $doctor 2>&1)
            $doctorCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }

    if ($doctorCode -ne 0) {
        return [pscustomobject]@{
            Ok = $false
            Stage = "doctor"
            Message = ($doctorOutput -join "`n")
        }
    }

    return [pscustomobject]@{ Ok = $true; Stage = "complete"; Message = "preflight and doctor passed" }
}

function Get-StatusExporterTaskEvidence {
    try {
        $task = Get-ScheduledTask -TaskPath "\Blue Fern Co\" -TaskName "Blue Fern Operational Status Export" -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskPath "\Blue Fern Co\" -TaskName "Blue Fern Operational Status Export" -ErrorAction Stop
        $actions = @($task.Actions | ForEach-Object {
            [ordered]@{
                Execute = [string]$_.Execute
                Arguments = [string]$_.Arguments
                WorkingDirectory = [string]$_.WorkingDirectory
            }
        })
        return [ordered]@{
            Found = $true
            State = [string]$task.State
            LastRunTime = $info.LastRunTime.ToUniversalTime().ToString("o")
            LastTaskResult = $info.LastTaskResult
            NextRunTime = $info.NextRunTime.ToUniversalTime().ToString("o")
            Actions = $actions
        }
    }
    catch {
        return [ordered]@{
            Found = $false
            Error = $_.Exception.Message
        }
    }
}

if ($ProveStatusExport -and -not $Apply) {
    throw "-ProveStatusExport requires -Apply because proof must run against the synchronized production checkouts."
}

$startedAt = (Get-Date).ToUniversalTime().ToString("o")
$results = @()
$targetHeads = @()

# Phase 1: inspect every runner and freeze one common protected target before
# any production Git HEAD is allowed to move.
foreach ($target in $Targets) {
    $dispatch = [string]$target.Dispatch
    $root = [string]$target.Root
    $row = [ordered]@{
        Dispatch = $dispatch
        Root = $root
        Result = "BLOCKED"
        BeforeHead = $null
        TargetHead = $null
        AfterHead = $null
        Branch = $null
        TrackedDirtyPaths = @()
        UntrackedPaths = @()
        IncomingPaths = @()
        UntrackedIncomingCollisions = @()
        PreValidation = $null
        MergeAttempted = $false
        PostValidation = $null
        Error = $null
    }

    try {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            throw "runner root is missing"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $root ".git"))) {
            throw "runner root is not a Git checkout or worktree"
        }

        $targetRef = "origin/$TargetBranch"
        $remoteRefSpec = "+refs/heads/${TargetBranch}:refs/remotes/origin/${TargetBranch}"
        $null = Invoke-Git -Root $root -Arguments @("fetch", "--no-tags", "origin", $remoteRefSpec)
        $branchResult = Invoke-Git -Root $root -Arguments @("branch", "--show-current")
        $before = Invoke-Git -Root $root -Arguments @("rev-parse", "HEAD")
        $targetHeadResult = Invoke-Git -Root $root -Arguments @("rev-parse", $targetRef)

        $row.Branch = $branchResult.Output
        $row.BeforeHead = $before.Output
        $row.TargetHead = $targetHeadResult.Output
        $row.AfterHead = $before.Output
        $targetHeads += $targetHeadResult.Output

        if ($row.Branch -ne $TargetBranch) {
            throw "branch mismatch: expected $TargetBranch, found $($row.Branch)"
        }
        if ($ExpectedProtectedHead -and $row.TargetHead -ne $ExpectedProtectedHead) {
            throw "target head mismatch: expected $ExpectedProtectedHead, fetched $($row.TargetHead)"
        }

        $status = Get-StatusPaths -Root $root
        $row.TrackedDirtyPaths = @($status.Tracked)
        $row.UntrackedPaths = @($status.Untracked)

        if ($row.TrackedDirtyPaths.Count -gt 0) {
            throw "tracked working-tree changes are present"
        }

        $ancestor = Invoke-Git -Root $root -Arguments @("merge-base", "--is-ancestor", "HEAD", $targetRef) -AllowFailure
        if ($ancestor.ExitCode -ne 0) {
            throw "current HEAD is not an ancestor of $targetRef"
        }

        $incoming = Get-IncomingPaths -Root $root -TargetRef $targetRef
        $row.IncomingPaths = @($incoming)
        $collisions = Get-UntrackedCollisions -Untracked $row.UntrackedPaths -Incoming $incoming
        $row.UntrackedIncomingCollisions = @($collisions)
        if ($collisions.Count -gt 0) {
            throw "untracked runtime paths overlap incoming tracked paths: $($collisions -join ', ')"
        }

        $pre = Invoke-RunnerValidation -Root $root
        $row.PreValidation = $pre
        if (-not $pre.Ok) {
            throw "pre-rollout validation failed at $($pre.Stage): $($pre.Message)"
        }

        if ($row.BeforeHead -eq $row.TargetHead) {
            $row.Result = "ALREADY_CURRENT"
            $row.PostValidation = $pre
        }
        else {
            $row.Result = "READY_TO_FAST_FORWARD"
        }
    }
    catch {
        $row.Error = $_.Exception.Message
    }

    $results += [pscustomobject]$row
}

$uniqueTargets = @($targetHeads | Where-Object { $_ } | Sort-Object -Unique)
$targetHeadConsistent = ($uniqueTargets.Count -le 1)
$frozenTargetHead = if ($uniqueTargets.Count -eq 1) { [string]$uniqueTargets[0] } else { $null }

# Phase 2: apply only after all runner discovery has completed and the target
# SHA is frozen. A newer remote commit that appears after this point is left
# for the next synchronization rather than creating mixed production heads.
if ($Apply -and $targetHeadConsistent -and $frozenTargetHead) {
    foreach ($row in $results) {
        if ($row.Result -ne "READY_TO_FAST_FORWARD") { continue }
        $root = [string]$row.Root
        try {
            # Reconfirm the runner itself did not change between discovery and apply.
            $currentHead = (Invoke-Git -Root $root -Arguments @("rev-parse", "HEAD")).Output
            if ($currentHead -ne $row.BeforeHead) {
                throw "runner HEAD changed after discovery: expected $($row.BeforeHead), found $currentHead"
            }
            $currentStatus = Get-StatusPaths -Root $root
            if ($currentStatus.Tracked.Count -gt 0) {
                throw "tracked working-tree changes appeared after discovery"
            }
            $lateCollisions = Get-UntrackedCollisions -Untracked @($currentStatus.Untracked) -Incoming @($row.IncomingPaths)
            if ($lateCollisions.Count -gt 0) {
                throw "untracked runtime collision appeared after discovery: $($lateCollisions -join ', ')"
            }

            $row.MergeAttempted = $true
            $null = Invoke-Git -Root $root -Arguments @("merge", "--ff-only", $frozenTargetHead)
            $after = (Invoke-Git -Root $root -Arguments @("rev-parse", "HEAD")).Output
            $row.AfterHead = $after

            if ($row.AfterHead -ne $frozenTargetHead) {
                throw "fast-forward completed but HEAD does not match frozen target"
            }

            $post = Invoke-RunnerValidation -Root $root
            $row.PostValidation = $post
            if (-not $post.Ok) {
                $row.Result = "FAST_FORWARDED_VALIDATION_FAILED"
                throw "post-rollout validation failed at $($post.Stage): $($post.Message)"
            }

            $row.Result = "FAST_FORWARDED"
        }
        catch {
            if ($row.Result -ne "FAST_FORWARDED_VALIDATION_FAILED") {
                $row.Result = "BLOCKED"
            }
            $row.Error = $_.Exception.Message
            try {
                $row.AfterHead = (Invoke-Git -Root $root -Arguments @("rev-parse", "HEAD") -AllowFailure).Output
            }
            catch {
                $row.AfterHead = $null
            }
        }
    }
}

$statusExportProof = [ordered]@{
    Requested = [bool]$ProveStatusExport
    Attempted = $false
    Passed = $false
    ExitCode = $null
    StatusCheckout = "C:\BlueFernRunner\OperationalStatusCurrent"
    SystemStatusPath = "ops/status/system/latest.json"
    GazaStatusPath = "ops/status/gaza/latest.json"
    Error = $null
    Dispatches = @{}
}

if ($Apply -and $ProveStatusExport) {
    $unsafeRows = @($results | Where-Object {
        $_.Result -notin @("FAST_FORWARDED", "ALREADY_CURRENT")
    })
    if ($unsafeRows.Count -eq 0 -and $targetHeadConsistent -and $frozenTargetHead) {
        $statusExportProof.Attempted = $true
        try {
            $foodRoot = "C:\BlueFernRunner\FoodLineCurrent6"
            $statusScript = Join-Path $foodRoot "scripts\run_operational_status_export.ps1"
            if (-not (Test-Path -LiteralPath $statusScript -PathType Leaf)) {
                throw "status exporter wrapper is missing: $statusScript"
            }

            $proofOutput = @(& $statusScript 2>&1)
            $proofCode = $LASTEXITCODE
            $statusExportProof.ExitCode = $proofCode
            if ($proofCode -ne 0) {
                throw "status exporter proof failed ($proofCode): $($proofOutput -join ' ')"
            }

            $statusRoot = [string]$statusExportProof.StatusCheckout
            $systemPath = Join-Path $statusRoot "ops\status\system\latest.json"
            $gazaPath = Join-Path $statusRoot "ops\status\gaza\latest.json"
            if (-not (Test-Path -LiteralPath $systemPath -PathType Leaf)) {
                throw "status exporter proof did not create system latest status"
            }
            if (-not (Test-Path -LiteralPath $gazaPath -PathType Leaf)) {
                throw "status exporter proof did not create Gaza latest status"
            }

            $system = Get-Content -LiteralPath $systemPath -Raw | ConvertFrom-Json
            $gaza = Get-Content -LiteralPath $gazaPath -Raw | ConvertFrom-Json
            foreach ($dispatch in @("food-line", "care-line", "gaza", "ice")) {
                $state = $system.dispatches.$dispatch
                if ($null -eq $state) {
                    throw "system status is missing dispatch: $dispatch"
                }
                $statusExportProof.Dispatches[$dispatch] = [ordered]@{
                    MigrationStatus = [string]$state.migration_status
                    AggregateStatus = [string]$state.aggregate_status
                    CurrentRunnerHead = [string]$state.current_runner_head
                    CurrentRunnerBranch = [string]$state.current_runner_branch
                }
                if ([string]$state.current_runner_head -ne $frozenTargetHead) {
                    throw "$dispatch current_runner_head does not match frozen target"
                }
                if ([string]$state.current_runner_branch -ne $TargetBranch) {
                    throw "$dispatch current_runner_branch does not match $TargetBranch"
                }
            }
            if ([string]$gaza.migration_status -ne "MIGRATED") {
                throw "Gaza status did not transition to MIGRATED"
            }
            $statusExportProof.Passed = $true
        }
        catch {
            $statusExportProof.Error = $_.Exception.Message
        }
    }
    else {
        $statusExportProof.Error = "status export proof skipped because one or more production checkouts were not safely synchronized"
    }
}

$report = [ordered]@{
    SchemaVersion = "bluefern.production_runner_sync.v1"
    StartedAt = $startedAt
    CompletedAt = (Get-Date).ToUniversalTime().ToString("o")
    ApplyRequested = [bool]$Apply
    TargetBranch = $TargetBranch
    ExpectedProtectedHead = $(if ($ExpectedProtectedHead) { $ExpectedProtectedHead } else { $null })
    FetchedTargetHeads = $uniqueTargets
    TargetHeadConsistent = $targetHeadConsistent
    FrozenTargetHead = $frozenTargetHead
    Runners = $results
    StatusExporterTask = Get-StatusExporterTaskEvidence
    StatusExportProof = $statusExportProof
    PublicSideEffects = $false
    SchedulerMutation = $false
    CollectionTriggered = $false
    PublicationTriggered = $false
    PagesMutation = $false
    OperationalStatusMutation = [bool]$statusExportProof.Attempted
}

if (-not $ReportPath) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $ReportPath = Join-Path $env:TEMP "bluefern-production-runner-sync-$stamp.json"
}
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
$report | ConvertTo-Json -Depth 10
Write-Host "Report: $ReportPath"

$blocked = @($results | Where-Object { $_.Result -eq "BLOCKED" -or $_.Result -eq "FAST_FORWARDED_VALIDATION_FAILED" })
$ready = @($results | Where-Object { $_.Result -eq "READY_TO_FAST_FORWARD" })

if (-not $targetHeadConsistent) {
    Write-Error "Production runners fetched inconsistent protected heads; no fast-forward was attempted."
    exit 3
}
if ($blocked.Count -gt 0) {
    Write-Error "$($blocked.Count) runner(s) blocked or failed validation."
    exit 2
}
if (-not $Apply -and $ready.Count -gt 0) {
    exit 10
}
if ($ProveStatusExport -and -not $statusExportProof.Passed) {
    Write-Error "Production runners were synchronized but the non-public status exporter proof did not pass."
    exit 4
}
exit 0
