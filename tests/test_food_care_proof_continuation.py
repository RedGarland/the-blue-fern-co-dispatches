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
