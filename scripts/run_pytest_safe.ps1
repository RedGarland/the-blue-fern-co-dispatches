param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$testRoot = [Environment]::GetEnvironmentVariable("DISPATCHES_TEST_ROOT")
if ([string]::IsNullOrWhiteSpace($testRoot)) {
    throw "DISPATCHES_TEST_ROOT must explicitly name a project test root; refusing TEMP fallback."
}
if (-not (Test-Path -LiteralPath $testRoot -PathType Container)) {
    throw "Configured DISPATCHES_TEST_ROOT does not exist: $testRoot"
}
$rootInfo = Get-Item -LiteralPath $testRoot -Force -ErrorAction Stop
if (($rootInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Configured DISPATCHES_TEST_ROOT must not be a redirect or reparse point: $testRoot"
}
$resolvedTestRoot = $rootInfo.FullName
$driveName = (Split-Path -Qualifier $resolvedTestRoot).TrimEnd("").TrimEnd(":")
$drive = Get-PSDrive -Name $driveName -ErrorAction Stop
$minFreeBytes = 2GB
if ($drive.Free -lt $minFreeBytes) {
    throw "Test-root admission refused: $resolvedTestRoot has $($drive.Free) bytes free; $minFreeBytes required."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
    $PytestArgs = @("tests\test_food_line_dispatch.py", "-q", "-p", "no:cacheprovider")
}
else {
    $hasExplicitTarget = $false
    foreach ($arg in $PytestArgs) {
        $looksLikePath = $arg -match '^[.\\/]' -or $arg -match '[\\/]' -or $arg.EndsWith('.py')
        if ($looksLikePath) {
            $hasExplicitTarget = $true
            break
        }
    }
    if (-not $hasExplicitTarget) {
        $PytestArgs = @("tests\test_food_line_dispatch.py", "-q", "-p", "no:cacheprovider") + $PytestArgs
    }
}

$ts = Get-Date -Format "yyyyMMdd-HHmmssfff"
$baseTemp = Join-Path $resolvedTestRoot ("bluefern-pytest-{0}-{1}" -f $ts, [guid]::NewGuid().ToString("N"))
$rootPrefix = $resolvedTestRoot.TrimEnd("\") + "\"
$fullBaseTemp = [System.IO.Path]::GetFullPath($baseTemp)
if (-not $fullBaseTemp.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Computed pytest basetemp escaped DISPATCHES_TEST_ROOT: $fullBaseTemp"
}
if (Test-Path -LiteralPath $fullBaseTemp) {
    throw "Computed pytest basetemp already exists: $fullBaseTemp"
}

Write-Host "Using pytest basetemp: $fullBaseTemp"
$baseTemp = $fullBaseTemp

Push-Location $repoRoot
try {
    & $pythonExe -B -m pytest @PytestArgs --basetemp $baseTemp
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
