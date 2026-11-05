[CmdletBinding()]
param([Parameter(Mandatory)][string]$EvidenceParent)
# PREPARED, NOT NATIVE-VALIDATED. Run only on an independently authorized offline
# Windows test executor. No production checkout or installed runtime is needed.
Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Windows path semantics are required' }
$EvidenceParent = [IO.Path]::GetFullPath($EvidenceParent)
if (-not (Test-Path -LiteralPath $EvidenceParent -PathType Container)) { throw 'EvidenceParent must already exist' }
$ancestor = Get-Item -LiteralPath $EvidenceParent -Force
while ($null -ne $ancestor) {
    if (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse-point evidence ancestor refused' }
    $ancestor = $ancestor.Parent
}
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'preparation-manifest.json') -Raw | ConvertFrom-Json
$Candidate = Join-Path $PSScriptRoot 'instrumented-candidate.ps1'
if ((Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash.ToLowerInvariant() -cne $manifest.instrumented_sha256) { throw 'Instrumented candidate hash mismatch' }
$original = Join-Path $PSScriptRoot 'candidate.ps1'
if ((Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash.ToLowerInvariant() -cne $manifest.candidate_sha256) { throw 'Original candidate hash mismatch' }
$tokens = $null; $parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($Candidate,[ref]$tokens,[ref]$parseErrors) | Out-Null
if (@($parseErrors).Count -ne 0) { throw ('Candidate parse errors: ' + ($parseErrors | Out-String)) }
$SuiteRoot = Join-Path $EvidenceParent ('food-wrapper-control-flow-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $SuiteRoot | Out-Null
$Protected = '3f58326266abc49546731b791b9cfad3d0d44393'
$Runtime = '4444444444444444444444444444444444444444'
$Edition = '2026-11-04'
$Results = New-Object System.Collections.Generic.List[object]
$MutationEvidence = New-Object System.Collections.Generic.List[object]

function Assert-Equal($Actual,$Expected,[string]$Label) {
    if (($Actual | ConvertTo-Json -Depth 20 -Compress) -cne ($Expected | ConvertTo-Json -Depth 20 -Compress)) { throw "Assertion failed: $Label; actual=$Actual; expected=$Expected" }
}
function Write-FixtureJson([string]$Path,$Value) {
    [IO.File]::WriteAllText($Path,($Value | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false))
}
function Assert-FixturePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = $FixtureState.Root.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw "Outside fixture: $Path" }
}
function Get-ArgumentValue([string[]]$ArgumentList,[string]$Name) {
    $indices = @(for ($i=0; $i -lt $ArgumentList.Count; $i++) { if ($ArgumentList[$i] -ceq $Name) { $i } })
    if ($indices.Count -ne 1 -or ($indices[0]+1) -ge $ArgumentList.Count) { throw "Expected one bound argument: $Name" }
    return $ArgumentList[$indices[0]+1]
}
function Invoke-FixtureGit {
    $FixtureState.GitCalls.Add(@($args)) | Out-Null
    Assert-Equal (@($args) -join '|') 'ls-remote|origin|refs/heads/add/pages-repo-default' 'Git arguments'
    Set-Variable -Name LASTEXITCODE -Value 0 -Scope 1
    return "$Protected`trefs/heads/add/pages-repo-default"
}
function Invoke-FixtureChildProcess {
    param([string]$FilePath,[string[]]$ArgumentList,[string]$WorkingDirectory,
          [switch]$Wait,[switch]$PassThru,[switch]$NoNewWindow,
          [string]$RedirectStandardOutput,[string]$RedirectStandardError)
    Assert-FixturePath $WorkingDirectory
    Assert-FixturePath $RedirectStandardOutput
    Assert-FixturePath $RedirectStandardError
    $step = [IO.Path]::GetFileName($RedirectStandardOutput).Replace('.stdout.log','')
    $FixtureState.Calls.Add([pscustomobject]@{name=$step;file=$FilePath;arguments=@($ArgumentList);cwd=$WorkingDirectory}) | Out-Null
    if (-not $FixtureState.Allowed.Contains($step)) { throw "Unexpected child action: $step" }
    if ($FilePath -cne 'powershell.exe') { throw 'Unexpected child executable' }
    $target = Get-ArgumentValue $ArgumentList '-File'
    Assert-FixturePath $target
    [IO.File]::WriteAllText($RedirectStandardError,'')
    $code = 0; $output = '{}'
    if ($step -eq 'food_source_watch') {
        Assert-Equal (Get-ArgumentValue $ArgumentList '-RunId') $FixtureState.SourceRunId 'Source run binding'
        Assert-Equal (Get-ArgumentValue $ArgumentList '-EditionDate') $Edition 'Source edition'
        $code = $FixtureState.SourceExit
        $output = [IO.File]::ReadAllText($FixtureState.SourceReceipt)
    } elseif ($step -in @('food_resume_after_capped_source_watch','food_resume')) {
        Assert-Equal (Get-ArgumentValue $ArgumentList '-ContinuationRunDirectory') $FixtureState.Directory 'Selected directory forwarded'
        Assert-Equal (Get-ArgumentValue $ArgumentList '-ExpectedProtectedHead') $Protected 'Protected head forwarded'
        Assert-Equal (Get-ArgumentValue $ArgumentList '-ExpectedRuntimeHead') $Runtime 'Runtime head forwarded'
        Assert-Equal (Get-ArgumentValue $ArgumentList '-EditionDate') $Edition 'Resume edition'
        $code = $FixtureState.ResumeExit
        $output = [IO.File]::ReadAllText($FixtureState.ResumeReceipt)
    } elseif ($step -eq 'guarded_runner_sync') {
        Assert-Equal (Get-ArgumentValue $ArgumentList '-ExpectedProtectedHead') $Protected 'Sync head forwarded'
        $code = $FixtureState.SyncExit
    }
    [IO.File]::WriteAllText($RedirectStandardOutput,$output,[Text.UTF8Encoding]::new($false))
    return [pscustomobject]@{ExitCode=$code}
}
function New-Scenario([string]$Name,[bool]$Explicit,[string]$ResumeStatus) {
    $root = Join-Path $SuiteRoot $Name
    New-Item -ItemType Directory -Path $root | Out-Null
    $roots = @{}
    foreach ($key in @('OperatorRoot','FoodRoot','CareRoot','StatusCheckout')) {
        $roots[$key] = [IO.Path]::GetFullPath((Join-Path $root $key))
        New-Item -ItemType Directory -Path $roots[$key] | Out-Null
    }
    $run = 'fixture-' + $Name
    $sourceRun = 'food-line-proof-' + $run
    $directory = Join-Path $roots.FoodRoot ("data\dispatches\food-line\continuation\$Edition\$sourceRun")
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    foreach ($leaf in @('continuation-ledger.json','continuation-query-plan.json')) { [IO.File]::WriteAllText((Join-Path $directory $leaf),'{}') }
    foreach ($pair in @(@('OperatorRoot','scripts\run_blue_fern_operator.ps1'),@('FoodRoot','.venv\Scripts\python.exe'),@('CareRoot','.venv\Scripts\python.exe'),@('FoodRoot','scripts\windows\resume_food_line_daily_current.ps1'),@('FoodRoot','scripts\windows\run_food_line_daily_current.ps1'))) {
        $path=Join-Path $roots[$pair[0]] $pair[1]
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [IO.File]::WriteAllText($path,'INERT FIXTURE, NOT EXECUTABLE')
    }
    $source = [ordered]@{schema_version='food_line_source_watch_receipt_v1';action='source_watch';edition_date=$Edition;run_id=$sourceRun;source_commit=$Protected;execution_mode='export_enabled';exit_code=2;final_status='incomplete_capped';resume_status='not_attempted';publication_attempted=$false;publication_side_effects=@{};continuation_run_directory=$directory;continuation_ledger_path=(Join-Path $directory 'continuation-ledger.json');continuation_ledger_capture=@{pending_count=3}}
    $binding = [ordered]@{edition_date=$Edition;run_id=$sourceRun;source_identity=$Protected;source_module_sha256=('a'*64);config_sha256=('b'*64);plan_sha256=('c'*64)}
    $handoff = [ordered]@{status='not_requested';intake_eligible=$false;public_export_status='not_requested';publication_attempted=$false;publication_side_effects=@{}}
    $gates = [ordered]@{publication_attempted=$false;publication_side_effects=@{};intake_eligible=$false;release_ready=$false;export_status='not_requested';public_export_status='not_requested';source_stage_handoff=$handoff}
    $child = [ordered]@{schema='food_line_continuation_receipt_v1';execution_mode='collection_only';status=$ResumeStatus;gates=$gates;source_stage_handoff=$handoff;legacy_run_state_path=$null}
    foreach ($key in $binding.Keys) { $child[$key]=$binding[$key] }
    $resumeExit = if ($ResumeStatus -eq 'completed') {0} else {2}
    $resume = [ordered]@{schema_version='food_line_source_watch_receipt_v1';action='status_resume';execution_mode='collection_only';runtime_source_commit=$Runtime;continuation_run_directory=$directory;source_commit=$Protected;publication_attempted=$false;publication_side_effects=@{};child_continuation_receipt=$child;ledger_binding=$binding;run_id=$sourceRun;edition_date=$Edition;child_command_exit_code=$resumeExit;command_exit_code=$resumeExit;exit_code=$resumeExit;intake_eligible=$false;release_ready=$false;export_status='not_requested';inbox_export_path=$null;continuation_progress=@{collection_triggered=$true}}
    $sourcePath=Join-Path $root 'source.json'; $resumePath=Join-Path $root 'resume.json'
    Write-FixtureJson $sourcePath $source; Write-FixtureJson $resumePath $resume
    $calls=New-Object System.Collections.Generic.List[object]
    $gitCalls=New-Object System.Collections.Generic.List[object]
    $allowed=New-Object System.Collections.Generic.List[string]
    foreach ($item in @('guarded_runner_sync','food_source_watch','food_resume_after_capped_source_watch','food_resume')) { $allowed.Add($item) }
    return @{Token='food-wrapper-control-flow-fixture-v1';Root=$root;Roots=$roots;RunId=$run;SourceRunId=$sourceRun;EditionDate=$Edition;Directory=$directory;Calls=$calls;GitCalls=$gitCalls;Allowed=$allowed;SourceReceipt=$sourcePath;ResumeReceipt=$resumePath;Source=$source;Resume=$resume;SourceExit=2;ResumeExit=$resumeExit;SyncExit=0;ExitCode=-999;Explicit=$Explicit}
}
function Test-Scenario([string]$Name,[bool]$Explicit=$false,[string]$Status='completed',[string]$Mutation='',[string]$ScriptPath=$Candidate) {
    $FixtureState = New-Scenario $Name $Explicit $Status
    $parameters = @{OperatorRoot=$FixtureState.Roots.OperatorRoot;FoodRoot=$FixtureState.Roots.FoodRoot;CareRoot=$FixtureState.Roots.CareRoot;StatusCheckout=$FixtureState.Roots.StatusCheckout;EditionDate=$Edition;RunId=$FixtureState.RunId;ExpectedRuntimeHead=$Runtime}
    if ($Explicit) { $parameters.ContinuationRunDirectory=$FixtureState.Directory; $parameters.ExpectedProtectedHead=$Protected }
    $expectedCalls = if ($Explicit) {@('food_resume')} else {@('guarded_runner_sync','food_source_watch','food_resume_after_capped_source_watch')}
    $expectedGit = if ($Explicit) {0} else {1}
    $expectedExit=$FixtureState.ResumeExit; $errorPattern=$null
    switch ($Mutation) {
        'missing-head' { $parameters.Remove('ExpectedRuntimeHead'); $expectedCalls=@();$expectedGit=0;$expectedExit=1;$errorPattern='explicit 40-hex ExpectedRuntimeHead' }
        'invalid-head' { $parameters.ExpectedRuntimeHead='invalid';$expectedCalls=@();$expectedGit=0;$expectedExit=1;$errorPattern='explicit 40-hex ExpectedRuntimeHead' }
        'wrong-source' { $FixtureState.Source.source_commit=('b'*40);$expectedCalls=@('guarded_runner_sync','food_source_watch');$expectedExit=1;$errorPattern='source identity differs' }
        'mixed-source' { $expectedCalls=@('guarded_runner_sync','food_source_watch');$expectedExit=1;$errorPattern='invalid JSON receipt' }
        'missing-plan' { Remove-Item -LiteralPath (Join-Path $FixtureState.Directory 'continuation-query-plan.json');$expectedCalls=@('guarded_runner_sync','food_source_watch');$expectedExit=1;$errorPattern='plan/ledger is incomplete' }
        'wrong-resume' { $FixtureState.Resume.runtime_source_commit=('b'*40);$expectedExit=1;$errorPattern='runtime identity differs' }
        'mixed-resume' { $expectedExit=1;$errorPattern='invalid JSON receipt' }
        'source-exit-one' { $FixtureState.SourceExit=1;$expectedCalls=@('guarded_runner_sync','food_source_watch');$expectedExit=1;$errorPattern='food_source_watch failed with exit code 1' }
        'sync-failure' { $FixtureState.SyncExit=1;$expectedCalls=@('guarded_runner_sync');$expectedExit=1;$errorPattern='guarded_runner_sync failed with exit code 1' }
        'explicit-missing-runtime' { $parameters.Remove('ExpectedRuntimeHead');$expectedCalls=@();$expectedExit=1;$errorPattern='explicit continuation requires a 40-hex ExpectedRuntimeHead' }
    }
    Write-FixtureJson $FixtureState.SourceReceipt $FixtureState.Source
    Write-FixtureJson $FixtureState.ResumeReceipt $FixtureState.Resume
    if ($Mutation -eq 'mixed-source') { [IO.File]::WriteAllText($FixtureState.SourceReceipt,"diagnostic`n"+[IO.File]::ReadAllText($FixtureState.SourceReceipt)) }
    if ($Mutation -eq 'mixed-resume') { [IO.File]::WriteAllText($FixtureState.ResumeReceipt,"diagnostic`n"+[IO.File]::ReadAllText($FixtureState.ResumeReceipt)) }
    # The original catch writes to Console.Error; the actual finally block
    # persists its receipt. Assert that receipt and the captured final exit.
    $captured = @(& $ScriptPath @parameters 2>&1)
    $receipts = @(Get-ChildItem -LiteralPath $FixtureState.Roots.OperatorRoot -Recurse -Filter 'food-care-proof-continuation-receipt.json')
    Assert-Equal $receipts.Count 1 'One actual wrapper receipt'
    $receipt = Get-Content -LiteralPath $receipts[0].FullName -Raw | ConvertFrom-Json
    $observedCalls = @($FixtureState.Calls | ForEach-Object {$_.name})
    Assert-Equal ($observedCalls -join '|') ($expectedCalls -join '|') 'Actual boundary call order'
    Assert-Equal (@($receipt.steps | ForEach-Object {$_.name}) -join '|') ($expectedCalls -join '|') 'Actual receipt step order'
    Assert-Equal $FixtureState.GitCalls.Count $expectedGit 'Actual Git boundary count'
    Assert-Equal ([int]$receipt.exit_code) $expectedExit 'Actual receipt exit code'
    Assert-Equal $FixtureState.ExitCode $expectedExit 'Captured final wrapper exit code'
    foreach ($forbidden in @('food_current_intake','care_approved_release_proof_only','final_operational_status_export')) {
        if ($observedCalls -contains $forbidden) { throw "Forbidden downstream step: $forbidden" }
    }
    if ($null -ne $errorPattern) {
        if ([string]$receipt.error -notmatch [regex]::Escape($errorPattern)) { throw "Expected wrapper error: $errorPattern; actual=$($receipt.error)" }
    } else {
        $outcome = if ($Status -eq 'completed') {'COLLECTION_ONLY_COMPLETE'} else {'COLLECTION_ONLY_NONQUALIFYING'}
        Assert-Equal $receipt.outcome $outcome 'Collection-only outcome'
        Assert-Equal $receipt.public_side_effects.collection_triggered $true 'Collection marker'
        Assert-Equal $receipt.error $null 'No unexpected wrapper error'
    }
    foreach ($field in @('publication_triggered','pages_mutation','scheduler_mutation','cascadia_activation','evidence_deletion')) { Assert-Equal $receipt.public_side_effects.$field $false $field }
    $Results.Add([pscustomobject]@{name=$Name;status='passed';calls=$observedCalls;git_calls=$FixtureState.GitCalls.Count;receipt=$receipts[0].FullName;exit_code=$expectedExit}) | Out-Null
}

Test-Scenario -Name 'missing-head' -Mutation 'missing-head'
Test-Scenario -Name 'invalid-head' -Mutation 'invalid-head'
Test-Scenario -Name 'capped-complete'
Test-Scenario -Name 'capped-pending' -Status 'incomplete_pending'
Test-Scenario -Name 'explicit-complete' -Explicit $true
Test-Scenario -Name 'explicit-pending' -Explicit $true -Status 'incomplete_pending'
Test-Scenario -Name 'explicit-missing-runtime' -Explicit $true -Mutation 'explicit-missing-runtime'
Test-Scenario -Name 'wrong-source' -Mutation 'wrong-source'
Test-Scenario -Name 'mixed-source' -Mutation 'mixed-source'
Test-Scenario -Name 'missing-plan' -Mutation 'missing-plan'
Test-Scenario -Name 'wrong-resume' -Mutation 'wrong-resume'
Test-Scenario -Name 'mixed-resume' -Mutation 'mixed-resume'
Test-Scenario -Name 'source-exit-one' -Mutation 'source-exit-one'
Test-Scenario -Name 'sync-failure' -Mutation 'sync-failure'

# Mutation sensitivity: these are private copies of the instrumented candidate,
# never modifications to the exact source. They must break behavioral assertions.
$sourceText = [IO.File]::ReadAllText($Candidate)
$guard = '        if ([string]::IsNullOrWhiteSpace($ExpectedRuntimeHead) -or $ExpectedRuntimeHead -notmatch ''^[0-9a-fA-F]{40}$'') { throw "default proof requires an explicit 40-hex ExpectedRuntimeHead before source-watch" }'
if (-not $sourceText.Contains($guard)) { throw 'Mutation guard anchor not found' }
$mutantPath = Join-Path $SuiteRoot 'mutant-no-default-guard.ps1'
[IO.File]::WriteAllText($mutantPath,$sourceText.Replace($guard,'        # Removed for mutation sensitivity only'))
$killed=$false
$mutantParseErrors=$null
[void][Management.Automation.Language.Parser]::ParseFile($mutantPath,[ref]$null,[ref]$mutantParseErrors)
if (@($mutantParseErrors).Count -ne 0) { throw 'Mutation syntax invalid; not a valid sensitivity result' }
try { Test-Scenario -Name 'mutation-removed-head-guard' -Mutation 'missing-head' -ScriptPath $mutantPath } catch {
    if ($_.Exception.Message -notlike 'Assertion failed: Actual boundary call order;*') { throw }
    $killed=$true
    $MutationEvidence.Add([pscustomobject]@{name='removed-head-guard';reason='actual_boundary_call_order';message=$_.Exception.Message}) | Out-Null
}
if (-not $killed) { throw 'Missing-head assertions did not detect removed guard' }
# Inject an inert downstream boundary attempt after successful source validation.
$anchor = '            $sourceReceipt = Read-SourceWatchCappedReceipt -Step $Steps[$Steps.Count - 1] -ExpectedRunId $sourceRunId -ExpectedDirectory $canonicalContinuation'
if (-not $sourceText.Contains($anchor)) { throw 'Mutation downstream anchor not found' }
$injected = $anchor + "`n" + '            Invoke-ContinuationStep -Name "food_current_intake" -WorkingDirectory $FoodRoot -FilePath "powershell.exe" -Arguments @("-File", $OperatorWrapper)'
$mutantPath = Join-Path $SuiteRoot 'mutant-downstream-call.ps1'
[IO.File]::WriteAllText($mutantPath,$sourceText.Replace($anchor,$injected))
$killed=$false
$mutantParseErrors=$null
[void][Management.Automation.Language.Parser]::ParseFile($mutantPath,[ref]$null,[ref]$mutantParseErrors)
if (@($mutantParseErrors).Count -ne 0) { throw 'Mutation syntax invalid; not a valid sensitivity result' }
try { Test-Scenario -Name 'mutation-downstream-call' -ScriptPath $mutantPath } catch {
    if ($_.Exception.Message -notlike 'Assertion failed: Actual boundary call order;*') { throw }
    $killed=$true
    $MutationEvidence.Add([pscustomobject]@{name='downstream-call';reason='actual_boundary_call_order';message=$_.Exception.Message}) | Out-Null
}
if (-not $killed) { throw 'Call-order assertions did not detect a downstream call' }
$result=[ordered]@{status='passed';candidate_sha256=$manifest.candidate_sha256;instrumented_sha256=$manifest.instrumented_sha256;scenarios=@($Results.ToArray());mutation_tests_killed=2;mutation_evidence=@($MutationEvidence.ToArray());external_child_processes_invoked=$false;git_executable_invoked=$false;providers_invoked=$false;production_paths_touched=$false;scope='Actual wrapper control flow, receipt validators, step accounting, and receipt serialization. Final process exit replaced by captured exit value. Child helper implementations are NOT executed.'}
Write-FixtureJson (Join-Path $SuiteRoot 'native-result.json') $result
$result | ConvertTo-Json -Depth 20
