[CmdletBinding()]
param(
    [string]$CandidateRoot = '',
    [Parameter(Mandatory = $true)][string]$EvidenceRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Expected = @(
    [ordered]@{ relative = 'scripts\food_line_daily_scheduler.py'; bytes = 131280; sha256 = 'D9C72B89E6C49E451E5D11BBAC119BB16BEFF0493F2D60C97C68008E122E2384'; canonical_lf_bytes = 128573; canonical_lf_sha256 = '4567DC157D004AE9AAAF7472B45956A477122AAF6E81D6AB1CCAD6CE50552EC3' },
    [ordered]@{ relative = 'scripts\food_line_continuation_ledger.py'; bytes = 35707; sha256 = 'CC20CBF6DF6366418F8DC8BD63B4524923D9EF9A2C6715CA90040C7E6DC8419D'; canonical_lf_bytes = 34943; canonical_lf_sha256 = '8CF226BD81B96ADE1C7107D4D01687F9F5AC76C469A6525674E0677A97C808B7' },
    [ordered]@{ relative = 'scripts\run_food_line_continuation.py'; bytes = 22740; sha256 = '27ED434AE868AD913167CC263BCBBADBF55B6C58BD43D2556F03AF804B1B10BB'; canonical_lf_bytes = 22242; canonical_lf_sha256 = '5282BF69E4615CBA8F1A554D0EEE8E7C760CDE8BBC1D89A64CCD6557C335207C' },
    [ordered]@{ relative = 'scripts\windows\resume_food_line_daily_current.ps1'; bytes = 2125; sha256 = 'E5FA1257B876BF5C08A5DC60C10AC42C172AE9D5E774766F43EA8DF6D3880E98'; canonical_lf_bytes = 2087; canonical_lf_sha256 = '6F31BF50F17F660DE03289A80213B664F73B8171D434084BECE2A08F3F91D6AC' },
    [ordered]@{ relative = 'scripts\windows\run_food_care_proof_continuation.ps1'; bytes = 17099; sha256 = 'CBBA242BABF5A186538C591A1BA8AF940630C880B9D8719A37BAC58788E0509D'; canonical_lf_bytes = 16883; canonical_lf_sha256 = 'B502601A251762D58B72FE3FC6D3503D835D1DDEFB0AD93116DFC0C1D0717D2A' }
)

function ConvertTo-CanonicalUtf8Lf {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    $strict = [Text.UTF8Encoding]::new($false, $true)
    try { $text = $strict.GetString($Bytes) } catch { throw 'invalid UTF-8 in candidate bytes' }
    $roundTrip = [Text.UTF8Encoding]::new($false).GetBytes($text)
    if (-not [Linq.Enumerable]::SequenceEqual($Bytes, $roundTrip)) { throw 'UTF-8 bytes do not round-trip exactly' }
    $canonicalText = $text.Replace("`r`n", "`n")
    if ($canonicalText.Contains("`r")) { throw 'lone carriage return is not a permitted checkout conversion' }
    return ,([byte[]][Text.UTF8Encoding]::new($false).GetBytes($canonicalText))
}

function Assert-CandidateClosure {
    param([Parameter(Mandatory)][string]$Root)
    $rows = @()
    foreach ($spec in $Expected) {
        $path = Join-Path $Root $spec.relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "candidate file is absent: $path" }
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "candidate reparse file refused: $path" }
        $bytes = [IO.File]::ReadAllBytes($path)
        $rawBytes = [int64]$bytes.Length
        $rawHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToUpperInvariant()
        $canonical = ConvertTo-CanonicalUtf8Lf -Bytes $bytes
        $canonicalHash = ([BitConverter]::ToString(([Security.Cryptography.SHA256]::Create().ComputeHash($canonical)))).Replace('-', '').ToUpperInvariant()
        if ($canonical.Length -ne $spec.canonical_lf_bytes -or $canonicalHash -cne $spec.canonical_lf_sha256) { throw "candidate canonical pin mismatch: $($spec.relative)" }
        $rows += [ordered]@{ relative = $spec.relative; raw_bytes = $rawBytes; raw_sha256 = $rawHash; canonical_lf_bytes = [int64]$canonical.Length; canonical_lf_sha256 = $canonicalHash }
    }
    return $rows
}

function Get-ReceiptParser {
    param([Parameter(Mandatory)][string]$OperatorPath)
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($OperatorPath, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "operator parser source has PowerShell parse errors" }
    $function = $ast.Find(
        { param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-StructuredContinuationReceipt' },
        $true
    )
    if ($null -eq $function) { throw 'Read-StructuredContinuationReceipt was not found' }
    return [scriptblock]::Create($function.Extent.Text)
}

function New-Envelope {
    param([Parameter(Mandatory)][string]$SourceIdentity,[bool]$CollectionTriggered)
    $binding = [ordered]@{
        edition_date = $EditionDate
        run_id = 'fixture-selected-run'
        source_identity = $SourceIdentity
        source_module_sha256 = ('b' * 64)
        config_sha256 = ('c' * 64)
        plan_sha256 = ('d' * 64)
    }
    $handoff = [ordered]@{
        status = 'not_requested'
        intake_eligible = $false
        public_export_status = 'not_requested'
        publication_attempted = $false
        publication_side_effects = @{}
    }
    $child = [ordered]@{
        schema = 'food_line_continuation_receipt_v1'
        status = 'completed'
        execution_mode = 'collection_only'
        source_stage_handoff = $handoff
        legacy_run_state_path = $null
        gates = [ordered]@{
            publication_attempted = $false
            publication_side_effects = @{}
            intake_eligible = $false
            release_ready = $false
            export_status = 'not_requested'
            public_export_status = 'not_requested'
            source_stage_handoff = $handoff
        }
    }
    foreach ($key in $binding.Keys) { $child[$key] = $binding[$key] }
    return [ordered]@{
        schema_version = 'food_line_source_watch_receipt_v1'
        action = 'status_resume'
        edition_date = $EditionDate
        run_id = $binding.run_id
        continuation_run_directory = $ContinuationRunDirectory
        runtime_source_commit = $ExpectedRuntimeHead
        ledger_binding = $binding
        child_command_exit_code = 0
        command_exit_code = 0
        exit_code = 0
        execution_mode = 'collection_only'
        source_commit = $SourceIdentity
        publication_attempted = $false
        publication_side_effects = @{}
        intake_eligible = $false
        release_ready = $false
        export_status = 'not_requested'
        inbox_export_path = $null
        continuation_progress = [ordered]@{
            collection_triggered = $CollectionTriggered
            durable_progress = $CollectionTriggered
        }
        child_continuation_receipt = $child
    }
}

if ([string]::IsNullOrWhiteSpace($CandidateRoot)) {
    $CandidateRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
}
if ([string]::IsNullOrWhiteSpace($env:DISPATCHES_TEST_ROOT)) {
    throw 'DISPATCHES_TEST_ROOT is required for the portable evidence root'
}

$candidate = [IO.Path]::GetFullPath($CandidateRoot)
$testRoot = ([IO.Path]::GetFullPath($env:DISPATCHES_TEST_ROOT)).TrimEnd('\') + '\'
$evidence = [IO.Path]::GetFullPath($EvidenceRoot)
if (-not $evidence.StartsWith($testRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "evidence root must be beneath DISPATCHES_TEST_ROOT: $evidence"
}
if (Test-Path -LiteralPath $evidence) { throw "evidence root already exists; refusing overwrite: $evidence" }
New-Item -ItemType Directory -Path $evidence | Out-Null
$pins = Assert-CandidateClosure -Root $candidate
$operatorPath = Join-Path $candidate 'scripts\windows\run_food_care_proof_continuation.ps1'
$ExpectedProtectedHead = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$ExpectedRuntimeHead = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
$EditionDate = '2026-11-02'
$ContinuationRunDirectory = Join-Path $evidence 'selected-run'
$parserDefinition = Get-ReceiptParser -OperatorPath $operatorPath
. $parserDefinition

$validPath = Join-Path $evidence 'valid.stdout.log'
$valid = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
($valid | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $validPath -Encoding UTF8
$validStep = [pscustomobject]@{ stdout_path = $validPath; exit_code = 0 }
$parsed = Read-StructuredContinuationReceipt -Step $validStep
if ($parsed.child_continuation_receipt.status -ne 'completed' -or $parsed.continuation_progress.collection_triggered -ne $true) { throw 'valid scheduler envelope was not accepted' }

$prettyPath = Join-Path $evidence 'valid-pretty.stdout.log'
$prettyJson = $valid | ConvertTo-Json -Depth 12
$compactJson = $valid | ConvertTo-Json -Depth 12 -Compress
$prettyJson | Set-Content -LiteralPath $prettyPath -Encoding UTF8
$prettyParsed = Read-StructuredContinuationReceipt -Step ([pscustomobject]@{ stdout_path = $prettyPath; exit_code = 0 })
if ($prettyParsed.child_continuation_receipt.status -ne 'completed') { throw 'pretty-printed scheduler envelope was not accepted' }

$framingCases = [ordered]@{
    'leading-junk' = "diagnostic output`n" + $prettyJson
    'trailing-junk' = $prettyJson + "`ndiagnostic output"
    'truncated' = $prettyJson.TrimEnd().Substring(0, $prettyJson.TrimEnd().Length - 1)
    'multiple-documents' = $compactJson + "`n" + $compactJson
    'leading-comment' = "/* diagnostic output */`n" + $prettyJson
    'trailing-comment' = $prettyJson + "`n/* diagnostic output */"
}
$framingRejected = @()
foreach ($case in $framingCases.Keys) {
    $badFramingPath = Join-Path $evidence ("framing-{0}.stdout.log" -f $case)
    $framingCases[$case] | Set-Content -LiteralPath $badFramingPath -Encoding UTF8
    $rejected = $false
    try { Read-StructuredContinuationReceipt -Step ([pscustomobject]@{ stdout_path = $badFramingPath; exit_code = 0 }) | Out-Null } catch {
        if ($_.Exception.Message -like '*invalid JSON receipt*') { $rejected = $true } else { throw }
    }
    if (-not $rejected) { throw "invalid $case framing was accepted" }
    $framingRejected += $case
}

$missingPath = Join-Path $evidence 'missing-child.stdout.log'
$missing = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
$missing.Remove('child_continuation_receipt')
($missing | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $missingPath -Encoding UTF8
$missingStep = [pscustomobject]@{ stdout_path = $missingPath; exit_code = 0 }
$missingRejected = $false
try { Read-StructuredContinuationReceipt -Step $missingStep | Out-Null } catch {
    if ($_.Exception.Message -like '*lacks the bound child continuation receipt*') { $missingRejected = $true } else { throw }
}
if (-not $missingRejected) { throw 'missing child receipt was not rejected by the bounded parser' }

$rejectedCases = @()
foreach ($case in @('runtime', 'run', 'plan', 'intake', 'handoff', 'exit', 'null-effects', 'string-bool')) {
    $bad = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
    switch ($case) {
        'runtime' { $bad.runtime_source_commit = ('f' * 40) }
        'run' { $bad.child_continuation_receipt.run_id = 'foreign-run' }
        'plan' { $bad.child_continuation_receipt.plan_sha256 = ('f' * 64) }
        'intake' { $bad.child_continuation_receipt.gates.intake_eligible = $true }
        'handoff' { $bad.child_continuation_receipt.source_stage_handoff.status = 'eligible' }
        'exit' { $bad.child_command_exit_code = 1 }
        'null-effects' { $bad.child_continuation_receipt.gates.publication_side_effects = $null }
        'string-bool' { $bad.child_continuation_receipt.gates.intake_eligible = 'false' }
    }
    $badPath = Join-Path $evidence ("rejected-{0}.stdout.log" -f $case)
    ($bad | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $badPath -Encoding UTF8
    $rejected = $false
    try { Read-StructuredContinuationReceipt -Step ([pscustomobject]@{ stdout_path = $badPath; exit_code = 0 }) | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "contradictory $case envelope was accepted" }
    $rejectedCases += $case
}
$pending = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
$pending.child_continuation_receipt.status = 'incomplete_pending'
$pending.child_command_exit_code = 2
$pending.command_exit_code = 2
$pending.exit_code = 2
$pendingPath = Join-Path $evidence 'pending.stdout.log'
($pending | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $pendingPath -Encoding UTF8
Read-StructuredContinuationReceipt -Step ([pscustomobject]@{ stdout_path = $pendingPath; exit_code = 2 }) | Out-Null

$result = [ordered]@{
    status = 'passed'
    candidate_root = $candidate
    candidate_pins = $pins
    valid_envelope = 'accepted'
    pretty_envelope = 'accepted'
    invalid_framing_rejected = $framingRejected
    missing_child_envelope = 'rejected'
    contradictory_envelopes_rejected = $rejectedCases
    pending_envelope = 'accepted'
    providers_invoked = $false
    installed_runners_touched = $false
    scheduler_changed = $false
    evidence_root = $evidence
}
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidence 'result.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 12
