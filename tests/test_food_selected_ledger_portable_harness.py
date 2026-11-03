from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "windows" / "food-selected-ledger-native-offline-parser-test-portable-20261103.ps1"
MANIFEST = ROOT / "food-selected-ledger-countfix-manifest-portable.json"
WORKFLOW = ROOT / ".github" / "workflows" / "dispatch-validation.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_portable_harness_is_separate_and_has_no_private_machine_roots() -> None:
    portable = _text(HARNESS)
    assert portable
    assert "C:\\Users\\" not in portable
    assert "C:\\BlueFernRunner\\" not in portable
    assert "D:\\GazaTest" not in portable
    assert "DISPATCHES_TEST_ROOT" in portable
    assert "[Parameter(Mandatory = $true)][string]$EvidenceRoot" in portable
    assert "evidence root must be beneath DISPATCHES_TEST_ROOT" in portable
    assert "Read-StructuredContinuationReceipt" in portable
    assert "ConvertTo-CanonicalUtf8Lf" in portable
    assert "invalid UTF-8" in portable
    assert "canonical_lf_sha256" in portable
    for forbidden in ("Start-Process", "Invoke-WebRequest", "Invoke-RestMethod", "git "):
        assert forbidden not in portable


def test_portable_harness_binds_all_manifest_candidate_hashes() -> None:
    manifest = json.loads(_text(MANIFEST))
    portable = _text(HARNESS)
    rows = manifest["runtime_files"]
    assert len(rows) == 5
    operator = next(row for row in rows if row["repository_path"].endswith("run_food_care_proof_continuation.ps1"))
    assert operator["candidate_path"] == "scripts/windows/run_food_care_proof_continuation.ps1"
    assert not (ROOT / "run_food_care_proof_continuation.ps1").exists()
    assert (ROOT / operator["candidate_path"]).is_file()
    for row in rows:
        assert row["candidate_sha256"] in portable
        assert row["canonical_lf_sha256"] in portable
        assert row["candidate_bytes"] > 0
        assert row["canonical_lf_bytes"] > 0


def test_portable_harness_accepts_checkout_eol_conversion_but_rejects_raw_mutation() -> None:
    portable = _text(HARNESS)
    start = portable.index("function Assert-CandidateClosure")
    end = portable.index("function Get-ReceiptParser", start)
    closure = portable[start:end]
    assert "canonical.Length -ne $spec.canonical_lf_bytes" in closure
    assert "$canonicalHash -cne $spec.canonical_lf_sha256" in closure
    assert "$item.Length -ne $spec.bytes" not in closure
    assert "$hash -cne $spec.sha256" not in closure
    assert "raw_bytes = $rawBytes" in closure
    assert "raw_sha256 = $rawHash" in closure


def test_private_workflow_selects_only_portable_checks_and_existing_root() -> None:
    workflow = _text(WORKFLOW)
    assert "name: Care scheduler wrapper (Windows)" in workflow
    assert "Run complete scheduler wrapper suite on Windows" in workflow
    assert "DISPATCHES_TEST_ROOT/food-ledger-pytest" in workflow
    for test_name in (
        "tests/test_food_selected_ledger_manifest_portable.py",
        "tests/test_food_selected_ledger_scheduler_integration.py",
        "tests/test_food_selected_ledger_portable_harness.py",
    ):
        assert workflow.count(test_name) == 1
    assert workflow.count("Run portable Food selected-ledger tests on Windows") == 1
    assert workflow.count("Run portable Food receipt parser harness on Windows") == 1
    assert "food-selected-ledger-native-offline-parser-test-portable-20261103.ps1" in workflow
    assert "$env:GITHUB_WORKSPACE" in workflow
    assert "contents: write" not in workflow
    assert "Invoke-WebRequest" not in workflow
    assert "Invoke-RestMethod" not in workflow
    assert "providers_invoked" not in workflow
    assert "food-selected-ledger-step.patch" not in workflow
    assert "native-offline-parser-test-20261102.ps1" not in workflow
