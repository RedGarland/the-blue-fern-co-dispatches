param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

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

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$baseTemp = Join-Path $env:TEMP "bluefern-pytest-$ts"

Write-Host "Using pytest basetemp: $baseTemp"

Push-Location $repoRoot
try {
    & $pythonExe -B -m pytest @PytestArgs --basetemp $baseTemp
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
