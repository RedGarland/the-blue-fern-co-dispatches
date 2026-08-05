[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = "C:\tmp\care-line-phase-g-source",
    [string]$PythonExecutable = "C:\tmp\care-line-phase-g-source\.venv\Scripts\python.exe",
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$UserId = "",
    [string]$TaskPath = "\Blue Fern Co.\",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$TaskName = "Blue Fern Care Line National Collection"

function Test-PowerShellSyntax {
    param([string]$Path)
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        throw "PowerShell syntax check failed for ${Path}: $($errors[0].Message)"
    }
}

function Test-PythonCompile {
    param(
        [string]$PythonExecutable,
        [string]$Path
    )

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "care-line-phase-g-pycompile"
    $compiledPath = Join-Path $tempRoot ([System.IO.Path]::GetFileName($Path) + "c")
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        & $PythonExecutable -c "import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)" $Path $compiledPath
        if ($LASTEXITCODE -ne 0) {
            throw "Python scheduler helper syntax check failed."
        }
    }
    finally {
        Remove-Item -LiteralPath $compiledPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tempRoot -Force -ErrorAction SilentlyContinue
    }
}

function Add-LocalOperationalExcludes {
    param([string]$Root)
    $excludePath = Join-Path $Root ".git\info\exclude"
    $patterns = @(
        "logs/care-line/",
        "status/care-line/",
        "data/dispatches/care-line/collection-runs/",
        "data/dispatches/care-line/review/candidate-registry.json",
        "data/dispatches/care-line/review/current-review-queue.json",
        "data/dispatches/care-line/review/current-review-backlog.json",
        "data/dispatches/care-line/review/current-exclusions.json",
        "data/dispatches/care-line/review/current-duplicates.json",
        "data/dispatches/care-line/review/current-failed-extractions.json",
        "data/dispatches/care-line/review/current-manual-review.json"
    )
    $existing = if (Test-Path -LiteralPath $excludePath) { @(Get-Content -LiteralPath $excludePath) } else { @() }
    foreach ($pattern in $patterns) {
        if ($existing -notcontains $pattern) {
            Add-Content -LiteralPath $excludePath -Value $pattern -Encoding UTF8
        }
    }
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot ".git"))) {
    throw "Collection runner is not a Git checkout: $RepositoryRoot"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}
if ((Get-TimeZone).Id -ne "Pacific Standard Time") {
    throw "Task Scheduler host must use Pacific Standard Time; found $((Get-TimeZone).Id)"
}

$branch = (& git -C $RepositoryRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne $SourceBranch) {
    throw "Collection runner branch mismatch: expected $SourceBranch, found $branch"
}
$dirty = @(& git -C $RepositoryRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -gt 0) {
    throw "Collection runner must be clean before task setup."
}

$helper = Join-Path $RepositoryRoot "scripts\care_line_collection_scheduler.py"
$runner = Join-Path $RepositoryRoot "scripts\windows\run_care_line_national_collection.ps1"
foreach ($path in @($helper, $runner, $MyInvocation.MyCommand.Path)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required scheduler file not found: $path" }
}
Test-PowerShellSyntax -Path $runner
Test-PowerShellSyntax -Path $MyInvocation.MyCommand.Path
Test-PythonCompile -PythonExecutable $PythonExecutable -Path $helper

$sameNameElsewhere = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -ne $TaskPath })
if ($sameNameElsewhere) {
    throw "Task name exists outside ${TaskPath}: $TaskName"
}
$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
$effectiveUser = if ($UserId) { $UserId } elseif ($existing) { $existing.Principal.UserId } else { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$principalLogon = if ($existing) { [string]$existing.Principal.LogonType } else { "S4U" }

$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -RepositoryRoot `"$RepositoryRoot`" -PythonExecutable `"$PythonExecutable`" -SourceBranch `"$SourceBranch`""
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(6))),
    (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(12))),
    (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(18)))
)
$planned = [pscustomobject]@{
    task_path = $TaskPath
    task_name = $TaskName
    operation = if ($existing) { "update" } else { "create" }
    execute = "PowerShell.exe"
    arguments = $arguments
    working_directory = $RepositoryRoot
    principal = if ($existing) { $existing.Principal.UserId } else { $effectiveUser }
    logon_type = $principalLogon
    schedule = @("06:00", "12:00", "18:00")
    timezone = (Get-TimeZone).Id
    multiple_instances = "IgnoreNew"
    execution_time_limit_minutes = 75
    collection_only = $true
    automatic_editorial_approval = $false
    automatic_pages_sync = $false
    automatic_publication = $false
    allow_insecure_tls = $false
}

$checkResult = [pscustomobject]@{
    check_only = [bool]$CheckOnly
    repository_root = $RepositoryRoot
    source_branch = $SourceBranch
    source_commit = (& git -C $RepositoryRoot rev-parse HEAD).Trim()
    python_executable = $PythonExecutable
    timezone = (Get-TimeZone).Id
    task = $planned
}
$checkResult | ConvertTo-Json -Depth 8
if ($CheckOnly) { return }

Add-LocalOperationalExcludes -Root $RepositoryRoot
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $arguments -WorkingDirectory $RepositoryRoot
$principal = if ($existing) {
    New-ScheduledTaskPrincipal -UserId $existing.Principal.UserId -LogonType $existing.Principal.LogonType -RunLevel $existing.Principal.RunLevel
} else {
    New-ScheduledTaskPrincipal -UserId $effectiveUser -LogonType S4U -RunLevel Limited
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 75) -MultipleInstances IgnoreNew -WakeToRun:$false

if ($existing) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Update Care Line collection-only task")) {
        Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings | Out-Null
    }
    $operation = "updated"
} else {
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", "Create Care Line collection-only task")) {
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description "Runs the Care Line national collection-only pipeline at 06:00, 12:00, and 18:00 Pacific. Never approves, generates editions, syncs Pages, publishes Signal Wire, posts social content, or creates public artifacts." | Out-Null
    }
    $operation = "created"
}

[pscustomobject]@{
    check_only = $false
    task = "$TaskPath$TaskName"
    operation = $operation
    schedule = @("06:00", "12:00", "18:00")
    collection_only = $true
    automatic_publication = $false
} | ConvertTo-Json -Depth 6
