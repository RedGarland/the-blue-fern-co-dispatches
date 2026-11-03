[CmdletBinding()]
param(
    [string]$CandidateRoot = '',
    [Parameter(Mandatory = $true)][string]$EvidenceRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Expected = @(
    [ordered]@{ relative = 'scripts\food_line_daily_scheduler.py'; bytes = 126634; sha256 = 'F050887CAA99EF5CED1BFC7015009CFA93E04CE5D81DD82CD296A32B8C95EA3E'; canonical_lf_bytes = 124001; canonical_lf_sha256 = '142499013D338B06F732F2384BA3D88B25F89686EA32168F40AA9B90BB78C757' },
    [ordered]@{ relative = 'scripts\food_line_continuation_ledger.py'; bytes = 35707; sha256 = 'CC20CBF6DF6366418F8DC8BD63B4524923D9EF9A2C6715CA90040C7E6DC8419D'; canonical_lf_bytes = 34943; canonical_lf_sha256 = '8CF226BD81B96ADE1C7107D4D01687F9F5AC76C469A6525674E0677A97C808B7' },
    [ordered]@{ relative = 'scripts\run_food_line_continuation.py'; bytes = 22806; sha256 = '959A34466AEE3B32A6D7EF3E448688C5F37F34397A1393FED6B3136796A3EC0B'; canonical_lf_bytes = 22307; canonical_lf_sha256 = 'E46B1CD6F158335BEF2C2610F3782E681C17F7A1A7DFED8BA32FDFE6422B3387' },
    [ordered]@{ relative = 'scripts\windows\resume_food_line_daily_current.ps1'; bytes = 1989; sha256 = 'E9E655C28EA2830E5EDD18D7380A0F56EF142F1DB857A4A3F513E15C69C08163'; canonical_lf_bytes = 1953; canonical_lf_sha256 = 'BF3149F2B0EFBA9ABD294E2422D7C4BB97753F29C3E9720E56BC5D69A104FEFC' },
    [ordered]@{ relative = 'scripts\windows\run_food_care_proof_continuation.ps1'; bytes = 13757; sha256 = '98994C8A75C7F9EB8DC23BA0EB19A3B8DADA4BF35F9B4DC5472CCC55FA3DB2C7'; canonical_lf_bytes = 13561; canonical_lf_sha256 = 'ADC0358A7C405C8524724116D477BC17DFF2D598E1DF5D5DA18CE726FD65E619' }
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
    $child = [ordered]@{
        schema = 'food_line_continuation_receipt_v1'
        status = 'completed'
        source_identity = $SourceIdentity
        execution_mode = 'collection_only'
        gates = [ordered]@{
            publication_attempted = $false
            publication_side_effects = @{}
            release_ready = $false
            export_status = 'not_requested'
            public_export_status = 'not_requested'
        }
    }
    return [ordered]@{
        schema_version = 'food_line_source_watch_receipt_v1'
        action = 'status_resume'
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
$parserDefinition = Get-ReceiptParser -OperatorPath $operatorPath
. $parserDefinition

$validPath = Join-Path $evidence 'valid.stdout.log'
$valid = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
($valid | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $validPath -Encoding UTF8
$validStep = [pscustomobject]@{ stdout_path = $validPath }
$parsed = Read-StructuredContinuationReceipt -Step $validStep
if ($parsed.child_continuation_receipt.status -ne 'completed' -or $parsed.continuation_progress.collection_triggered -ne $true) { throw 'valid scheduler envelope was not accepted' }

$missingPath = Join-Path $evidence 'missing-child.stdout.log'
$missing = New-Envelope -SourceIdentity $ExpectedProtectedHead -CollectionTriggered $true
$missing.Remove('child_continuation_receipt')
($missing | ConvertTo-Json -Depth 12 -Compress) | Set-Content -LiteralPath $missingPath -Encoding UTF8
$missingStep = [pscustomobject]@{ stdout_path = $missingPath }
$missingRejected = $false
try { Read-StructuredContinuationReceipt -Step $missingStep | Out-Null } catch {
    if ($_.Exception.Message -like '*lacks the bound child continuation receipt*') { $missingRejected = $true } else { throw }
}
if (-not $missingRejected) { throw 'missing child receipt was not rejected by the bounded parser' }

$result = [ordered]@{
    status = 'passed'
    candidate_root = $candidate
    candidate_pins = $pins
    valid_envelope = 'accepted'
    missing_child_envelope = 'rejected'
    providers_invoked = $false
    installed_runners_touched = $false
    scheduler_changed = $false
    evidence_root = $evidence
}
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidence 'result.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 12
