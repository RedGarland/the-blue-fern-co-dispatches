[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = "C:\BlueFernRunner\FoodLineDailyCurrent",
    [string]$PagesRepo = "C:\BlueFernRunner\FoodLineRelease20260814-2\bluefern-dispatches-pages",
    [string]$PythonExecutable = "C:\BlueFernRunner\Dispatches From The Blue Fern Co\.venv\Scripts\python.exe",
    [string]$SourceBranch = "agent/refine-care-line-signal-wire-public-rendering",
    [string]$UserId = "",
    [string]$TaskPath = "\Blue Fern Co.\",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$SourceTaskName = "Blue Fern Food Line Daily Source Watch"
$ResumeTaskName = "Blue Fern Food Line Source Watch Resume"
$IntakeTaskName = "Blue Fern Food Line Current Intake"
$PublishTaskName = "Blue Fern Food Line Daily Publish"
$LegacyTaskName = "Blue Fern Food Line Daily Dispatch"

function Test-PowerShellSyntax {
    param([string]$Path)
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        throw "PowerShell syntax check failed for ${Path}: $($errors[0].Message)"
    }
}

function Add-LocalOperationalExcludes {
    param([string]$Root)
    $excludePath = Join-Path $Root ".git\info\exclude"
    $patterns = @(
        "logs/food-line/",
        "status/food-line/",
        "data/dispatches/food-line/agent-inbox/*.json",
        "data/dispatches/food-line/agent-inbox/processed/",
        "data/dispatches/food-line/agent-intake/",
        "data/dispatches/food-line/review/"
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
    throw "Production runner is not a Git checkout: $RepositoryRoot"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}
if (-not (Test-Path -LiteralPath $PagesRepo -PathType Container)) {
    throw "Pages repository not found: $PagesRepo"
}
if ((Get-TimeZone).Id -ne "Pacific Standard Time") {
    throw "Task Scheduler host must use Pacific Standard Time; found $((Get-TimeZone).Id)"
}

$branch = (& git -C $RepositoryRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne $SourceBranch) {
    throw "Production runner branch mismatch: expected $SourceBranch, found $branch"
}
$dirty = @(& git -C $RepositoryRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -gt 0) {
    throw "Production runner must be clean before task setup."
}

$helper = Join-Path $RepositoryRoot "scripts\food_line_daily_scheduler.py"
$definitions = @(
    [pscustomobject]@{ Name = $SourceTaskName; Script = (Join-Path $RepositoryRoot "scripts\windows\run_food_line_daily_current.ps1"); Hour = 5; Minute = 30; LimitMinutes = 40 },
    [pscustomobject]@{ Name = $ResumeTaskName; Script = (Join-Path $RepositoryRoot "scripts\windows\resume_food_line_daily_current.ps1"); Hour = 6; Minute = 0; LimitMinutes = 35 },
    [pscustomobject]@{ Name = $IntakeTaskName; Script = (Join-Path $RepositoryRoot "scripts\windows\run_food_line_current_intake.ps1"); Hour = 6; Minute = 10; LimitMinutes = 20 },
    [pscustomobject]@{ Name = $PublishTaskName; Script = (Join-Path $RepositoryRoot "scripts\windows\run_food_line_daily_publish.ps1"); Hour = 8; Minute = 30; LimitMinutes = 40 }
)
foreach ($path in @($helper) + @($definitions.Script)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required scheduler file not found: $path" }
}
foreach ($definition in $definitions) { Test-PowerShellSyntax -Path $definition.Script }
Test-PowerShellSyntax -Path $MyInvocation.MyCommand.Path
& $PythonExecutable -m py_compile $helper
if ($LASTEXITCODE -ne 0) { throw "Python scheduler helper syntax check failed." }

$legacy = Get-ScheduledTask -TaskPath $TaskPath -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
$effectiveUser = if ($UserId) { $UserId } elseif ($legacy) { $legacy.Principal.UserId } else { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$planned = @()
foreach ($definition in $definitions) {
    $sameNameElsewhere = @(Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -ne $TaskPath })
    if ($sameNameElsewhere) {
        throw "Task name exists outside ${TaskPath}: $($definition.Name)"
    }
    $existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name -ErrorAction SilentlyContinue
    $start = (Get-Date).Date.AddHours($definition.Hour).AddMinutes($definition.Minute)
    if ($start -le (Get-Date)) { $start = $start.AddDays(1) }
    $arguments = if ($definition.Name -eq $PublishTaskName) {
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($definition.Script)`" -PublicationRoot `"$RepositoryRoot`" -PagesRepo `"$PagesRepo`" -SourceBranch `"$SourceBranch`" -PagesBranch `"gh-pages`" -PythonExecutable `"$PythonExecutable`""
    } else {
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($definition.Script)`" -RepositoryRoot `"$RepositoryRoot`" -PythonExecutable `"$PythonExecutable`" -SourceBranch `"$SourceBranch`""
    }
    $planned += [pscustomobject]@{
        task_path = $TaskPath
        task_name = $definition.Name
        operation = if ($existing) { "update" } else { "create" }
        enabled = $true
        trigger = $start.ToString("o")
        schedule = "daily"
        execute = "PowerShell.exe"
        arguments = $arguments
        working_directory = if ($definition.Name -eq $PublishTaskName) { $RepositoryRoot } else { $RepositoryRoot }
        principal = if ($existing) { $existing.Principal.UserId } else { $effectiveUser }
        logon_type = if ($existing) { [string]$existing.Principal.LogonType } else { "S4U" }
        multiple_instances = "IgnoreNew"
        execution_time_limit_minutes = $definition.LimitMinutes
        publication_capability = $definition.Name -eq $PublishTaskName
        post_bluesky_enabled = $definition.Name -eq $PublishTaskName
        description = if ($definition.Name -eq $PublishTaskName) {
            "Publishes the current release-ready Food Line edition, pushes GitHub Pages, then attempts the downstream Bluesky daily post. Makes no editorial decisions."
        } else {
            "Runs the private Food Line source-watch/intake flow. Never publishes, pushes Pages, posts social content, generates audio/maps, or makes editorial decisions."
        }
    }
}
$checkResult = [pscustomobject]@{
    check_only = [bool]$CheckOnly
    repository_root = $RepositoryRoot
    source_branch = $SourceBranch
    source_commit = (& git -C $RepositoryRoot rev-parse HEAD).Trim()
    python_executable = $PythonExecutable
    timezone = (Get-TimeZone).Id
    operational_logs = (Join-Path $RepositoryRoot "logs\food-line")
    operational_state = (Join-Path $RepositoryRoot "status\food-line")
    pages_repo = $PagesRepo
    tasks = $planned
    legacy_task = if ($legacy) { [pscustomobject]@{ full_name = "$TaskPath$LegacyTaskName"; current_state = [string]$legacy.State; planned_action = "disable" } } else { $null }
    automatic_publication_task_created = $true
}
$checkResult | ConvertTo-Json -Depth 8
if ($CheckOnly) { return }

Add-LocalOperationalExcludes -Root $RepositoryRoot
$results = @()
foreach ($definition in $definitions) {
    $existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name -ErrorAction SilentlyContinue
    $start = (Get-Date).Date.AddHours($definition.Hour).AddMinutes($definition.Minute)
    if ($start -le (Get-Date)) { $start = $start.AddDays(1) }
    $arguments = if ($definition.Name -eq $PublishTaskName) {
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($definition.Script)`" -PublicationRoot `"$RepositoryRoot`" -PagesRepo `"$PagesRepo`" -SourceBranch `"$SourceBranch`" -PagesBranch `"gh-pages`" -PythonExecutable `"$PythonExecutable`""
    } else {
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($definition.Script)`" -RepositoryRoot `"$RepositoryRoot`" -PythonExecutable `"$PythonExecutable`" -SourceBranch `"$SourceBranch`""
    }
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $arguments -WorkingDirectory $RepositoryRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $start
    $principal = if ($existing) {
        New-ScheduledTaskPrincipal -UserId $existing.Principal.UserId -LogonType $existing.Principal.LogonType -RunLevel $existing.Principal.RunLevel
    } else {
        New-ScheduledTaskPrincipal -UserId $effectiveUser -LogonType S4U -RunLevel Limited
    }
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes $definition.LimitMinutes) -MultipleInstances IgnoreNew -WakeToRun:$false
    if ($existing) {
        if ($PSCmdlet.ShouldProcess("$TaskPath$($definition.Name)", "Update private Food Line task")) {
            Set-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        }
        $operation = "updated"
    } else {
        if ($PSCmdlet.ShouldProcess("$TaskPath$($definition.Name)", "Create private Food Line task")) {
            $description = if ($definition.Name -eq $PublishTaskName) {
                "Publishes the current release-ready Food Line edition, pushes GitHub Pages, then attempts the downstream Bluesky daily post. Makes no editorial decisions."
            } else {
                "Runs the private Food Line source-watch/intake flow. Never publishes, pushes Pages, posts social content, generates audio/maps, or makes editorial decisions."
            }
            Register-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description | Out-Null
        }
        $operation = "created"
    }
    $results += [pscustomobject]@{ task = "$TaskPath$($definition.Name)"; operation = $operation }
}

$legacyAction = "not_found"
if ($legacy) {
    if ($PSCmdlet.ShouldProcess("$TaskPath$LegacyTaskName", "Disable overlapping automatic Food Line publication task")) {
        Disable-ScheduledTask -TaskPath $TaskPath -TaskName $LegacyTaskName | Out-Null
    }
    $legacyAction = "disabled"
}
[pscustomobject]@{
    check_only = $false
    tasks = $results
    legacy_task_action = $legacyAction
    automatic_publication_task_created = $true
} | ConvertTo-Json -Depth 6
