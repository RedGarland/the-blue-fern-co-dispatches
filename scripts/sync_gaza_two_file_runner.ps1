[CmdletBinding()]
param(
    [switch]$Apply,
    [Parameter(Mandatory = $true)][string]$ExpectedProtectedHead,
    [Parameter(Mandatory = $true)][string]$ExpectedOperatorBlob,
    [Parameter(Mandatory = $true)][string]$ExpectedBlueskyBlob,
    [string]$TargetBranch = "add/pages-repo-default",
    [string]$TargetRoot = "C:\BlueFernRunner\GazaDispatchesCurrent6",
    [string]$BackupRoot = "",
    [string]$ReportPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# This controller is intentionally Gaza-only. It never enumerates or fetches
# another production runner. It advances a protected Git checkout only when
# the incoming revision changes the two explicitly approved runtime paths.
$AllowedPaths = @(
    "scripts/run_gaza_daily_operator.py",
    "src/bluefern_dispatches/bluesky_post.py"
)
$ExpectedBlobs = [ordered]@{
    "scripts/run_gaza_daily_operator.py" = $ExpectedOperatorBlob
    "src/bluefern_dispatches/bluesky_post.py" = $ExpectedBlueskyBlob
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git -C $TargetRoot @Arguments 2>&1)
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if (-not $AllowFailure -and $code -ne 0) {
        throw ('git -C "{0}" {1} failed ({2}): {3}' -f $TargetRoot, ($Arguments -join ' '), $code, ($output -join ' '))
    }
    # Preserve leading porcelain status whitespace. Trimming here turns
    # ` M path` into `M path`, which makes the path parser drop its first
    # character and can bypass the dirty approved-path guard.
    return [pscustomobject]@{ ExitCode = $code; Output = ($output -join "`n") }
}

function Normalize-GitPath {
    param([string]$Line)
    if (-not $Line) { return "" }
    $text = $Line.Replace("\\", "/").Trim()
    if ($text.Contains(" -> ")) { $text = ($text -split " -> ", 2)[-1] }
    return $text.Trim()
}

function Get-StatusSnapshot {
    $result = Invoke-Git -Arguments @("status", "--porcelain=v1", "--untracked-files=all")
    $byPath = [ordered]@{}
    $content = [ordered]@{}
    foreach ($raw in @($result.Output -split "`r?`n")) {
        if (-not $raw -or $raw.Length -lt 4) { continue }
        $path = Normalize-GitPath $raw.Substring(3)
        $byPath[$path] = $raw.Substring(0, 2)
        $absolute = [IO.Path]::GetFullPath((Join-Path $TargetRoot ($path -replace '/', '\')))
        $targetPrefix = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\') + '\'
        if (-not $absolute.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Status path escapes TargetRoot: $path"
        }
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) {
            throw "Status path is not a regular file: $path"
        }
        $content[$path] = Get-Sha256 -Path $absolute
    }
    return [pscustomobject]@{ Raw = $byPath; Content = $content; Paths = @($byPath.Keys | Sort-Object) }
}

function Get-IncomingPaths {
    $result = Invoke-Git -Arguments @("diff", "--name-only", "HEAD..$ExpectedProtectedHead")
    return @(
        $result.Output -split "`r?`n" |
            ForEach-Object { Normalize-GitPath $_ } |
            Where-Object { $_ } |
            Sort-Object -Unique
    )
}

function Get-GitBlob {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Invoke-Git -Arguments @("rev-parse", "${ExpectedProtectedHead}:$Path")).Output
}

function Get-LiveRemoteHead {
    $result = Invoke-Git -Arguments @("ls-remote", "--heads", "origin", "refs/heads/$TargetBranch")
    $line = @($result.Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if (-not $line) { throw "Live remote branch is unavailable: origin/$TargetBranch" }
    $sha = (($line -split "\s+", 2)[0]).Trim()
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw "Live remote returned an invalid branch SHA: $line" }
    return $sha
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [IO.File]::ReadAllBytes($Path)
        return [ordered]@{
            bytes = $bytes.Length
            sha256 = ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
        }
    }
    finally { $sha.Dispose() }
}

function Assert-BackupRoot {
    param([Parameter(Mandatory = $true)][string]$Root)
    $targetFull = [IO.Path]::GetFullPath($TargetRoot)
    $targetPrefix = $targetFull.TrimEnd([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)) + [IO.Path]::DirectorySeparatorChar
    $backupFull = [IO.Path]::GetFullPath($Root)
    if ($backupFull.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $backupFull.Equals($targetFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "BackupRoot must not be inside TargetRoot."
    }
    Assert-SafeArtifactPath -Path $Root -Label "BackupRoot"
}

function Assert-SafeArtifactPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $full = [IO.Path]::GetFullPath($Path)
    $targetFull = [IO.Path]::GetFullPath($TargetRoot)
    $targetPrefix = $targetFull.TrimEnd([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)) + [IO.Path]::DirectorySeparatorChar
    if ($full.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $full.Equals($targetFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must not be inside TargetRoot: $Path"
    }
    $probe = $full
    while ($probe -and -not (Test-Path -LiteralPath $probe)) {
        $parent = Split-Path -Parent $probe
        if ($parent -eq $probe) { break }
        $probe = $parent
    }
    while ($probe -and (Test-Path -LiteralPath $probe)) {
        $item = Get-Item -LiteralPath $probe -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label resolves through a reparse point: $probe"
        }
        $parent = Split-Path -Parent $probe
        if ($parent -eq $probe) { break }
        $probe = $parent
    }
}

function Write-JsonReport {
    param([Parameter(Mandatory = $true)][object]$Report)
    if (-not $ReportPath) {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $script:ReportPath = Join-Path $env:TEMP "gaza-two-file-sync-$stamp.json"
    }
    try {
        Assert-SafeArtifactPath -Path $ReportPath -Label "ReportPath"
    }
    catch {
        $rejected = $ReportPath
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $script:ReportPath = Join-Path $env:TEMP "gaza-two-file-sync-rejected-path-$stamp-$([guid]::NewGuid().ToString('N')).json"
        $Report.report_path_rejected = $rejected
        if (-not $Report.error) { $Report.error = $_.Exception.Message }
    }
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Host "Report: $ReportPath"
}

$report = [ordered]@{
    schema_version = "bluefern.gaza_two_file_sync.v1"
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    completed_at = $null
    apply_requested = [bool]$Apply
    target_root = $TargetRoot
    target_branch = $TargetBranch
    expected_protected_head = $ExpectedProtectedHead
    allowed_paths = $AllowedPaths
    expected_blobs = $ExpectedBlobs
    before_head = $null
    after_head = $null
    incoming_paths = @()
    before_status = $null
    before_content = $null
    after_status = $null
    after_content = $null
    report_path_rejected = $null
    backup_manifest = $null
    result = "BLOCKED"
    production_state_mutated = $false
    production_state_mutation_status = "not_attempted"
    merge_attempted = $false
    public_side_effects = $false
    collection_triggered = $false
    publication_triggered = $false
    scheduler_mutation = $false
    error = $null
}

try {
    if (-not $ExpectedProtectedHead -or $ExpectedProtectedHead -notmatch '^[0-9a-f]{40}$') {
        throw "ExpectedProtectedHead must be a full 40-character commit SHA."
    }
    foreach ($blob in @($ExpectedOperatorBlob, $ExpectedBlueskyBlob)) {
        if (-not $blob -or $blob -notmatch '^[0-9a-f]{40}$') {
            throw "Expected runtime blobs must be full 40-character Git blob SHAs."
        }
    }
    if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container)) {
        throw "Gaza TargetRoot is missing: $TargetRoot"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $TargetRoot ".git"))) {
        throw "TargetRoot is not a Git checkout/worktree: $TargetRoot"
    }
    if ($ReportPath) { Assert-SafeArtifactPath -Path $ReportPath -Label "ReportPath" }
    if ($Apply) {
        if (-not $BackupRoot) { throw "BackupRoot is required with -Apply." }
        Assert-BackupRoot -Root $BackupRoot
    }

    $branch = (Invoke-Git -Arguments @("branch", "--show-current")).Output
    if ($branch -ne $TargetBranch) {
        throw "Target branch mismatch: expected $TargetBranch, found $branch"
    }
    $beforeHead = (Invoke-Git -Arguments @("rev-parse", "HEAD")).Output
    $report.before_head = $beforeHead

    # This is a single-target metadata refresh, not a fleet-wide operation.
    $refspec = "+refs/heads/${TargetBranch}:refs/remotes/origin/${TargetBranch}"
    $null = Invoke-Git -Arguments @("fetch", "--no-tags", "origin", $refspec)
    $remoteHead = (Invoke-Git -Arguments @("rev-parse", "origin/$TargetBranch")).Output
    if ($remoteHead -ne $ExpectedProtectedHead) {
        throw "Protected-head drift: expected $ExpectedProtectedHead, fetched $remoteHead"
    }
    $null = Invoke-Git -Arguments @("cat-file", "-e", $ExpectedProtectedHead)

    foreach ($path in $AllowedPaths) {
        $actualBlob = Get-GitBlob -Path $path
        if ($actualBlob -ne $ExpectedBlobs[$path]) {
            throw "Expected blob mismatch for $($path): expected $($ExpectedBlobs[$path]), got $actualBlob"
        }
    }

    $status = Get-StatusSnapshot
    $report.before_status = $status.Raw
    $report.before_content = $status.Content
    $incoming = @(Get-IncomingPaths)
    $report.incoming_paths = $incoming
    $outside = @($incoming | Where-Object { $_ -notin $AllowedPaths })
    if ($outside.Count -gt 0) {
        $report.result = "FULL_SOURCE_ADVANCEMENT_REQUIRED"
        throw "Protected revision is broader than the approved two-file scope: $($outside -join ', ')"
    }
    if ($beforeHead -ne $ExpectedProtectedHead -and $incoming.Count -eq 0) {
        throw "Target HEAD differs from the protected head but has no incoming path delta."
    }

    $dirtyAllowed = @($status.Paths | Where-Object { $_ -in $AllowedPaths })
    if ($dirtyAllowed.Count -gt 0) {
        throw "Approved source paths are already dirty: $($dirtyAllowed -join ', ')"
    }
    if ($beforeHead -ne $ExpectedProtectedHead) {
        $ancestor = Invoke-Git -Arguments @("merge-base", "--is-ancestor", "HEAD", $ExpectedProtectedHead) -AllowFailure
        if ($ancestor.ExitCode -ne 0) { throw "Target HEAD is not an ancestor of ExpectedProtectedHead." }
    }

    if ($beforeHead -eq $ExpectedProtectedHead) {
        $report.result = "ALREADY_CURRENT"
    }
    elseif (-not $Apply) {
        $report.result = "PLAN_READY_TO_FAST_FORWARD"
    }
    else {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $backupDir = Join-Path $BackupRoot ("gaza-two-file-{0}-{1}" -f $stamp, [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null
        $backupEntries = @()
        foreach ($path in $AllowedPaths) {
            $source = Join-Path $TargetRoot ($path -replace '/', '\')
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
                throw "Cannot back up missing target file: $source"
            }
            $destination = Join-Path $backupDir ($path -replace '/', '\')
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
            $sourceHash = Get-Sha256 -Path $source
            if (Test-Path -LiteralPath $destination) { throw "Backup destination already exists: $destination" }
            Copy-Item -LiteralPath $source -Destination $destination
            $backupHash = Get-Sha256 -Path $destination
            if (($sourceHash.sha256 -ne $backupHash.sha256) -or ($sourceHash.bytes -ne $backupHash.bytes)) {
                throw "Backup byte verification failed for $path"
            }
            $backupEntries += [ordered]@{
                path = $path
                backup_path = $destination
                source_file = $sourceHash
                backup_file = $backupHash
            }
        }
        $manifest = [ordered]@{
            schema_version = "bluefern.gaza_two_file_backup.v1"
            created_at = (Get-Date).ToUniversalTime().ToString("o")
            target_root = $TargetRoot
            before_head = $beforeHead
            expected_protected_head = $ExpectedProtectedHead
            entries = $backupEntries
            rollback = "Use a reviewed protected rollback head with this guarded controller; do not copy backups into the tracked checkout."
        }
        $manifestPath = Join-Path $backupDir "backup-manifest.json"
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        $report.backup_manifest = $manifestPath

        # Recheck the branch, HEAD, status, and remote head immediately before
        # the only production mutation. No reset, clean, checkout, or force op.
        if ((Invoke-Git -Arguments @("branch", "--show-current")).Output -ne $TargetBranch) { throw "Target branch changed before apply." }
        if ((Invoke-Git -Arguments @("rev-parse", "HEAD")).Output -ne $beforeHead) { throw "Target HEAD changed before apply." }
        $lateStatus = Get-StatusSnapshot
        if (($lateStatus.Raw | ConvertTo-Json -Compress) -ne ($status.Raw | ConvertTo-Json -Compress) -or
            ($lateStatus.Content | ConvertTo-Json -Compress) -ne ($status.Content | ConvertTo-Json -Compress)) {
            throw "Target dirty-state paths, statuses, or contents changed before apply."
        }
        if ((Get-LiveRemoteHead) -ne $ExpectedProtectedHead) { throw "Protected head changed on the live remote before apply." }

        $report.merge_attempted = $true
        $null = Invoke-Git -Arguments @("merge", "--ff-only", $ExpectedProtectedHead)
        # A successful merge command has already changed the checkout. Mark it
        # before any post-merge read/verification so a later failure cannot be
        # misreported as a non-mutating block.
        $report.production_state_mutated = $true
        $report.production_state_mutation_status = "mutated_unverified"
        $afterHead = (Invoke-Git -Arguments @("rev-parse", "HEAD")).Output
        if ($afterHead -ne $ExpectedProtectedHead) { throw "Fast-forward completed at unexpected HEAD: $afterHead" }
        $report.production_state_mutated = $true
        foreach ($path in $AllowedPaths) {
            if ((Invoke-Git -Arguments @("rev-parse", "HEAD:$path")).Output -ne $ExpectedBlobs[$path]) { throw "Post-apply blob mismatch: $path" }
        }
        $afterStatus = Get-StatusSnapshot
        $report.after_status = $afterStatus.Raw
        $report.after_content = $afterStatus.Content
        $unexpected = @($afterStatus.Paths | Where-Object { $_ -notin $status.Paths })
        if ($unexpected.Count -gt 0) { throw "Unexpected post-apply dirty paths: $($unexpected -join ', ')" }
        $report.after_head = $afterHead
        $report.production_state_mutation_status = "mutated_verified"
        $report.result = "APPLY_COMPLETE"
    }
}
catch {
    $report.error = $_.Exception.Message
    if ($report.merge_attempted -and -not $report.production_state_mutated) {
        $report.production_state_mutated = $null
        $report.production_state_mutation_status = "unknown"
    }
    if ($report.result -notin @("APPLY_COMPLETE", "FULL_SOURCE_ADVANCEMENT_REQUIRED")) {
        $report.result = "BLOCKED"
    }
}
finally {
    $report.completed_at = (Get-Date).ToUniversalTime().ToString("o")
    Write-JsonReport -Report $report
}

if ($report.result -in @("PLAN_READY_TO_FAST_FORWARD", "ALREADY_CURRENT", "APPLY_COMPLETE")) { exit 0 }
exit 2

