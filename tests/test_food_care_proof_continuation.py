from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTINUATION = ROOT / "scripts" / "windows" / "run_food_care_proof_continuation.ps1"
REGISTER = ROOT / "scripts" / "windows" / "register_food_care_proof_continuation_task.ps1"


def test_food_care_continuation_is_non_public_proof_only() -> None:
    text = CONTINUATION.read_text(encoding="utf-8")

    assert "-ProofOnly" in text
    assert "run_food_line_daily_publish.ps1" not in text
    assert "--publish" not in text
    assert "--push" not in text
    assert "git clean" not in text.lower()
    assert "reset --hard" not in text.lower()
    assert "Register-ScheduledTask" not in text
    assert "publication_triggered = $false" in text
    assert "pages_mutation = $false" in text
    assert "scheduler_mutation = $false" in text
    assert "evidence_deletion = $false" in text


def test_food_care_continuation_registration_is_bounded() -> None:
    text = REGISTER.read_text(encoding="utf-8")

    assert "Register-ScheduledTask" in text
    assert "MultipleInstances IgnoreNew" in text
    assert "ExecutionTimeLimit" in text
    assert "run_food_care_proof_continuation.ps1" in text
    assert "IntervalMinutes must be at least 5" in text


def test_food_care_continuation_startup_receipt_accepts_null_completed_at() -> None:
    text = CONTINUATION.read_text(encoding="utf-8")

    assert "param([AllowNull()][object]$CompletedAt)" in text
    assert "$completedValue = $null" in text
    assert "if ($null -ne $CompletedAt)" in text
    assert "completed_at = $completedValue" in text
    assert "Write-Receipt -CompletedAt $null" in text


def test_food_care_continuation_sync_defers_status_export_to_final_step() -> None:
    text = CONTINUATION.read_text(encoding="utf-8")

    sync_line = next(
        line for line in text.splitlines()
        if 'Invoke-ContinuationStep -Name "guarded_runner_sync"' in line
    )
    final_export_line = next(
        line for line in text.splitlines()
        if 'Invoke-ContinuationStep -Name "final_operational_status_export"' in line
    )

    assert "-ProveStatusExport" not in sync_line
    assert "run_operational_status_export.ps1" in final_export_line
