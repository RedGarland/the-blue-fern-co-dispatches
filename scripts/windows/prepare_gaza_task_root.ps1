function Test-GazaFullyQualifiedWindowsPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    # Windows PowerShell 5.1 has no IsPathFullyQualified API. Require a drive
    # root or a complete UNC share; reject D:relative and \relative paths.
    return ($Path -match '^[A-Za-z]:\\' -or $Path -match '^\\\\[^\\]+\\[^\\]+(?:\\|$)')
}

function Assert-GazaNoReparseAncestors {
    param([string]$Path)

    $candidate = $Path
    while ($true) {
        if (Test-Path -LiteralPath $candidate -PathType Any) {
            $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Gaza task-scoped path contains a reparse-point ancestor: $candidate"
            }
        }

        $directory = New-Object System.IO.DirectoryInfo($candidate)
        if ($null -eq $directory.Parent -or $directory.Parent.FullName -eq $candidate) {
            break
        }
        $candidate = $directory.Parent.FullName
    }
}

function Get-GazaTaskRootFreeBytes {
    param([string]$Path)

    $root = [System.IO.Path]::GetPathRoot($Path)
    if ($root -notmatch '^[A-Za-z]:\\$') {
        throw "Gaza task-scoped capacity check requires a local drive-rooted path: $Path"
    }
    $drive = New-Object System.IO.DriveInfo($root)
    if (-not $drive.IsReady) {
        throw "Gaza task-scoped capacity is unavailable for: $Path"
    }
    return [UInt64]$drive.AvailableFreeSpace
}

function Set-GazaTaskScopedTestRoot {
    [CmdletBinding()]
    param(
        [AllowEmptyString()]
        [string]$BasePath,
        [string]$Date = ""
    )

    # Never inherit a stale user/machine value into a scheduled Gaza run.
    Remove-Item Env:DISPATCHES_TEST_ROOT -ErrorAction SilentlyContinue

    if ([string]::IsNullOrWhiteSpace($BasePath)) {
        throw "Gaza task-scoped TestRootBase is required; no fallback is permitted."
    }
    if (-not (Test-GazaFullyQualifiedWindowsPath -Path $BasePath)) {
        throw "Gaza task-scoped TestRootBase must be an absolute path."
    }
    if (-not (Test-Path -LiteralPath $BasePath -PathType Container)) {
        throw "Gaza task-scoped TestRootBase does not exist: $BasePath"
    }

    Assert-GazaNoReparseAncestors -Path $BasePath
    $base = (Resolve-Path -LiteralPath $BasePath -ErrorAction Stop).Path
    $baseItem = Get-Item -LiteralPath $base -Force -ErrorAction Stop
    if (($baseItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Gaza task-scoped TestRootBase is a reparse point: $base"
    }

    $minimumFreeBytes = [UInt64](2 * 1024 * 1024 * 1024)
    $freeBytes = Get-GazaTaskRootFreeBytes -Path $base
    if ($freeBytes -lt $minimumFreeBytes) {
        throw "Gaza task-scoped capacity admission failed: $freeBytes bytes free, $minimumFreeBytes required."
    }

    $dateLabel = if ([string]::IsNullOrWhiteSpace($Date)) { (Get-Date -Format "yyyy-MM-dd") } else { $Date }
    $safeDate = $dateLabel -replace "[^0-9A-Za-z_.-]", "_"
    $name = "gaza-scheduled-{0}-{1}" -f $safeDate, ([guid]::NewGuid().ToString("N"))
    $child = Join-Path $base $name
    New-Item -ItemType Directory -Path $child -ErrorAction Stop | Out-Null
    $child = (Resolve-Path -LiteralPath $child -ErrorAction Stop).Path
    $childItem = Get-Item -LiteralPath $child -Force -ErrorAction Stop
    if (($childItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Gaza task-scoped test root resolved to a reparse point: $child"
    }

    $basePrefix = $base.TrimEnd('\') + '\'
    if (-not $child.StartsWith($basePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Gaza task-scoped test root escaped its configured base: $child"
    }

    $env:DISPATCHES_TEST_ROOT = $child
    return $child
}
